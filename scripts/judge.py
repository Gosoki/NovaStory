#!/usr/bin/env python
"""LLM-judge —— 盲评保真(docs/paper/03 §4;2026-07-02 拍板④⑥)。

评审只看(被试自己写的 1-2 句创意 + 终稿),按"该脚本多大程度实现了这个创意"打 1-7;
不给条件信息、不评创意质量(TTCW:LLM 评创意与专家不相关)。judge 全用 OpenAI 同一型号
×3 次(拍板⑥,异家族协议作废);无真人评审(拍板④),以重复评分间的自一致性 ICC(1)
作可靠性代理。**定位:次要终点/收敛证据,不进主复合**(A4)。被试是日本人、脚本是
日语,故提示为日语(JP3)。

❌ **专业质量四维 rubric(A1)已弃**(2026-08-03,B5):四维全是审美判断,正落在 TTCW 证明
LLM 与专家相关≈0 的地方;质量改由「结构完整度(客观下界)+ TOST 非劣」承担。本脚本
**只做盲评保真**,不含也不会再加质量评分模式。

定位:**次要 / 收敛证据,不进主复合**。盲评保真判的是「脚本多大程度实现了这句创意」,属
结构化比对(Zheng 2023 适用域),不是创意评分(TTCW 禁区)——这是它与 A1 的关键区别。

输出 data/analysis/judge.jsonl,按 (participant_id, round_idx, rep) 断点续跑
(PARSE_FAIL 不算完成,重跑会重试);键可与 v3_per_trial.csv join。

用法: .venv/bin/python scripts/judge.py                     # 真评分(用 secrets 的 OpenAI 配置)
      .venv/bin/python scripts/judge.py --selftest --db X   # 桩自测(解析/续跑/聚合/自一致性,无 API)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm_batch import SECRETS_PATH, BatchClient  # noqa: E402

DB_PATH = ROOT / "data" / "novastory.db"
OUT_PATH = ROOT / "data" / "analysis" / "judge.jsonl"

JUDGE_SYSTEM = (
    "あなたは映像制作の評価者です。作成者が最初に書いた「作りたい映像の意図」(1〜2文)と、"
    "それをもとに作られた最終的な絵コンテ台本を読み、\n"
    "その台本が**その意図をどの程度実現できているか**だけを、1〜7 の整数で評価してください。\n"
    "1 = 意図とほとんど関係がない / 4 = 意図の主要な部分は実現されている / 7 = 意図を完全に実現している\n"
    "面白さ・独創性・文章の巧みさ・撮影の難易度は評価しません。意図との一致だけを見てください。\n"
    '出力は JSON オブジェクト 1 個のみ。例: {"fidelity": 5}  他の文字は一切出力しないでください。'
)

_SCORE_RE = re.compile(r'"?fidelity"?\s*[:：]\s*([1-7])(?![0-9])')


def build_user(intent: str, script: str) -> str:
    """盲评输入:只有创意与终稿,不含条件/被试/轮次信息。"""
    return f"【作成者が書いた意図】\n{intent.strip()}\n\n【最終台本】\n{script.strip()}"


def parse_rating(text: str) -> int | None:
    """解析 1-7 分;先试 JSON,再退回正则/裸数字;失败返回 None。"""
    m = re.search(r"\{.*?\}", text or "", re.DOTALL)
    if m:
        try:
            v = int(json.loads(m.group(0))["fidelity"])
            if 1 <= v <= 7:
                return v
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    m = _SCORE_RE.search(text or "") or re.fullmatch(r"\s*([1-7])\s*", text or "")
    return int(m.group(1)) if m else None


def _loads(s) -> dict:
    try:
        return json.loads(s or "{}")
    except (ValueError, TypeError):
        return {}


def load_trials(db_path: Path) -> list[tuple]:
    """(participant_id, round_idx, 创意, 终稿);**排除 dev 测试被试**(同 analysis/v3.load)。"""
    if not db_path.exists():
        sys.exit(f"{db_path} 不存在 — 还没有试验数据")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        parts = con.execute("SELECT id, screening_json FROM participants").fetchall()
        rows = con.execute(
            "SELECT participant_id, round_idx, intent_statement, final_output FROM trials"
            " WHERE intent_statement IS NOT NULL AND final_output IS NOT NULL"
            " ORDER BY participant_id, round_idx"
        ).fetchall()
    finally:
        con.close()
    dev = {i for i, s in parts if _loads(s).get("dev")}
    if dev:
        print(f"排除 dev 测试被试 {len(dev)} 人")
    return [r for r in rows if r[0] not in dev]


def load_ok(path: Path) -> dict[tuple, int]:
    """已成功评分的 (pid, round, rep) → 分数;PARSE_FAIL 不算完成,重跑会重试。"""
    out: dict[tuple, int] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r["score"] is not None:
                    out[(r["participant_id"], r["round_idx"], r["rep"])] = r["score"]
    return out


def run(trials: list[tuple], rate, meta: dict, reps: int, out_path: Path) -> None:
    """对每轮评 reps 次,逐条落盘(可中断续跑)。rate = (system, user) → 原文。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_ok(out_path)
    print(f"待评 {len(trials)} 轮 × {reps} 次,已完成 {len(done)} 条 → {out_path}")
    with out_path.open("a", encoding="utf-8") as f:
        for pid, ridx, intent, final in trials:
            for rep in range(reps):
                if (pid, ridx, rep) in done:
                    continue
                raw = rate(JUDGE_SYSTEM, build_user(intent, final))
                score = parse_rating(raw)
                rec = {
                    "participant_id": pid, "round_idx": ridx, "rep": rep,
                    "score": score, "raw": raw if score is None else None,
                    **meta, "ts": datetime.now().isoformat(timespec="seconds"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                print(f"[p{pid} r{ridx}] rep {rep + 1}/{reps} "
                      + ("PARSE_FAIL" if score is None else str(score)))


def icc1(groups: list[list[int]]) -> float:
    """ICC(1) 单向随机:k 次重复评分的自一致性 = (MSB−MSW)/(MSB+(k−1)MSW)。"""
    n, k = len(groups), len(groups[0])
    grand = mean(x for g in groups for x in g)
    msb = k * sum((mean(g) - grand) ** 2 for g in groups) / (n - 1)
    msw = sum((x - mean(g)) ** 2 for g in groups for x in g) / (n * (k - 1))
    den = msb + (k - 1) * msw
    return (msb - msw) / den if den else float("nan")


def report(out_path: Path, reps: int) -> None:
    """按 (participant_id, round_idx) 聚合 judge_fidelity + 自一致性(可靠性代理)。"""
    by_trial: dict[tuple, list[int]] = {}
    for (pid, ridx, _), s in sorted(load_ok(out_path).items()):
        by_trial.setdefault((pid, ridx), []).append(s)
    full = [g for g in by_trial.values() if len(g) == reps]
    print(f"聚合:{len(by_trial)} 轮有分,其中满 {reps} 次的 {len(full)} 轮")
    if len(full) >= 2 and reps >= 2:
        print(f"judge_fidelity 均值 {mean(mean(g) for g in full):.2f}"
              f"  轮内 SD 均值 {mean(pstdev(g) for g in full):.2f}"
              f"  完全一致率 {sum(len(set(g)) == 1 for g in full) / len(full):.0%}"
              f"  自一致性 ICC(1)={icc1(full):.3f}")


def openai_rater(temperature: float):
    """拍板⑥:judge 固定用 secrets 里的 OpenAI 配置(选法同 analysis/embed.py)。"""
    cfgs = tomllib.loads(SECRETS_PATH.read_text()).get("api_configs", [])
    cfg = next((c for c in cfgs
                if "openai.com" in c.get("base_url", "") and c.get("api_key")), None)
    if cfg is None:
        sys.exit("secrets 里没有可用的 OpenAI 配置(拍板⑥:judge 全用 OpenAI)。")
    client = BatchClient(api_key=cfg["api_key"], base_url=cfg["base_url"],
                         model=cfg["model"], temperature=temperature)
    return (lambda system, user: client.generate(system, user)[0]), client.meta()


def selftest(db_path: Path, reps: int) -> None:
    """桩自测:提示生成 / 解析 / 续跑去重 / 聚合 / 自一致性,全程不联网。"""
    trials = load_trials(db_path)[:6]
    if not trials:
        sys.exit(f"{db_path} 里没有可评的终稿 — 自测请指定合成库(--db)")
    print("—— 日语提示(第 1 轮,只打印不发送)——\n" + JUDGE_SYSTEM)
    print("---- user ----\n" + build_user(trials[0][2], trials[0][3]))
    print("\n—— 解析自测 ——")
    for t in ('{"fidelity": 5}', '```json\n{"fidelity":7}\n```', "fidelity: 3 です",
              "6", "評価できません", '{"fidelity": 9}'):
        print(f"  {t!r} → {parse_rating(t)}")

    calls = {"n": 0}

    def stub(system: str, user: str) -> str:
        calls["n"] += 1
        if calls["n"] in (7, 14):          # 故意不可解析,验证「PARSE_FAIL 会被重试」
            return "うまく評価できません"
        base = random.Random(user).randint(3, 6)          # 同一轮围绕同一真值
        noise = random.Random(calls["n"]).choice((-1, 0, 0, 1))
        return json.dumps({"fidelity": max(1, min(7, base + noise))})

    out = Path(tempfile.mkdtemp()) / "judge.jsonl"
    print("\n—— 第 1 遍 ——")
    run(trials, stub, {"model": "stub"}, reps, out)
    n1 = calls["n"]
    print("—— 第 2 遍(续跑:只该重试 PARSE_FAIL)——")
    run(trials, stub, {"model": "stub"}, reps, out)
    print(f"调用次数:第1遍={n1} 第2遍={calls['n'] - n1}(应等于第1遍的 PARSE_FAIL 数)")
    report(out, reps)


def main() -> None:
    ap = argparse.ArgumentParser(description="盲评保真 LLM-judge(OpenAI×3 + 自一致性)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--reps", type=int, default=3, help="每轮独立评分次数(拍板⑥:同型号 ×3)")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--selftest", action="store_true", help="桩自测(无 API)")
    args = ap.parse_args()

    if args.selftest:
        selftest(args.db, args.reps)
        return
    trials = load_trials(args.db)
    if not trials:
        sys.exit("trials 表为空 — 没有可评的终稿")
    rate, meta = openai_rater(args.temperature)
    run(trials, rate, meta, args.reps, OUT_PATH)
    report(OUT_PATH, args.reps)


if __name__ == "__main__":
    main()
