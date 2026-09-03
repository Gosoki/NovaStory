#!/usr/bin/env python
"""LLM-judge —— 盲评保真(docs/paper/03 §4「LLM-judge 的定位」B5;留痕 docs/paper/10)。

评审只看(被试自己写的 1-2 句创意 + 终稿),按"该脚本多大程度实现了这个创意"打 1-7;
不给条件信息、不评创意质量(TTCW:LLM 评创意与专家不相关)。judge 全用 OpenAI 同一型号
×3 次(B5,异家族协议作废);无真人评审(B5:不引入人评效度锚),以重复评分间的自一致性
ICC(1) 作可靠性代理。**定位:次要终点/收敛证据,不进主复合**
(保真主复合的四条腿 = imagine / violation / not_against / embed_fidelity,见
docs/paper/04 §2.1)。被试是日本人、脚本是日语,故提示为日语(JP3)。

❌ **专业质量四维 rubric(A1)已弃**(2026-08-03,B5):四维全是审美判断,正落在 TTCW 证明
LLM 与专家相关≈0 的地方;质量改由「结构完整度(客观下界)+ TOST 非劣」承担。本脚本
**只做盲评保真**,不含也不会再加质量评分模式。

定位:**次要 / 收敛证据,不进主复合**。盲评保真判的是「脚本多大程度实现了这句创意」,属
结构化比对(Zheng 2023 适用域),不是创意评分(TTCW 禁区)——这是它与 A1 的关键区别。

输出 data/analysis/judge.jsonl,按 (participant_id, round_idx, rep) 断点续跑
(PARSE_FAIL 不算完成,重跑会重试);评完把 judge_fidelity 列合入
data/analysis/v3_per_trial.csv(与 analysis/embed.py 合入 embed_fidelity 同法)。

用法: .venv/bin/python scripts/judge.py                     # 真评分并合入(用 secrets 的 OpenAI 配置)
      .venv/bin/python scripts/judge.py --selftest --db X   # 桩自测(解析/续跑/聚合/自一致性/合入,无 API)
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
CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"

JUDGE_SYSTEM = (
    "あなたは映像制作の評価者です。作成者が最初に書いた「作りたい映像の意図」(1〜2文)と、"
    "それをもとに作られた最終的な絵コンテ台本を読み、\n"
    "その台本が**その意図をどの程度実現できているか**だけを、1〜7 の整数で評価してください。\n"
    "1 = 意図とほとんど関係がない / 4 = 意図の主要な部分は実現されている / 7 = 意図を完全に実現している\n"
    "面白さ・独創性・文章の巧みさ・撮影の難易度は評価しません。意図との一致だけを見てください。\n"
    '出力は JSON オブジェクト 1 個のみ。例: {"fidelity": 5}  他の文字は一切出力しないでください。'
)

# 大小写不敏感:temperature 0.3 下"Fidelity: 5"这种固定习惯会让整轮永久无分
_SCORE_RE = re.compile(r'"?fidelity"?\s*[:：]\s*([1-7])(?![0-9])', re.IGNORECASE)


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
    # 纳入规则(同 analysis/v3):dev 被试 + 未走完 N_ROUNDS 轮的离脱者都不发。
    # 这里尤其要紧 —— judge 会把被试原文**再送一次给 OpenAI**,而同意书写着
    # 中止者的回答不用于分析。共用 v3.included_participants,不再各写一份 dev 过滤。
    from analysis import v3 as _v3  # noqa: PLC0415 — 避免模块级循环依赖
    keep = _v3.included_participants(db_path)   # 已含 dev 过滤 + 走完全部轮次
    excluded = {i for i, _ in parts if i not in keep}
    if excluded:
        print(f"纳入规则:排除 {len(excluded)} 人(dev 测试被试 / 未走完全部轮次)")
    return [r for r in rows if r[0] not in excluded]


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
    """ICC(1) 单向随机:k 次重复评分的自一致性 = (MSB−MSW)/(MSB+(k−1)MSW)。

    n<2(没有轮间方差)或 k<2(没有轮内方差)时方差分解无定义 → nan,不抛除零。"""
    n, k = len(groups), len(groups[0])
    if n < 2 or k < 2:
        return float("nan")
    grand = mean(x for g in groups for x in g)
    msb = k * sum((mean(g) - grand) ** 2 for g in groups) / (n - 1)
    msw = sum((x - mean(g)) ** 2 for g in groups for x in g) / (n * (k - 1))
    den = msb + (k - 1) * msw
    return (msb - msw) / den if den else float("nan")


def _by_trial(out_path: Path) -> dict[tuple, list[int]]:
    """(participant_id, round_idx) → 该轮各次重复评分。"""
    out: dict[tuple, list[int]] = {}
    for (pid, ridx, _), s in sorted(load_ok(out_path).items()):
        out.setdefault((pid, ridx), []).append(s)
    return out


def report(out_path: Path, reps: int) -> None:
    """按 (participant_id, round_idx) 聚合 judge_fidelity + 自一致性(可靠性代理)。

    试测规模(1 轮 / --reps 1)下**算得出的照报,算不出的明说**——过去这里整段静默跳过。"""
    by_trial = _by_trial(out_path)
    full = [g for g in by_trial.values() if len(g) == reps]
    print(f"聚合:{len(by_trial)} 轮有分,其中满 {reps} 次的 {len(full)} 轮")
    if not full:
        print("  没有满次的轮 —— 均值/一致性都不可计算(先跑完评分)。")
        return
    print(f"judge_fidelity 均值 {mean(mean(g) for g in full):.2f}")
    if reps < 2:
        print(f"  轮内 SD / 完全一致率 / ICC(1) 需要重复评分,--reps {reps} 时不可计算。")
        return
    print(f"  轮内 SD 均值 {mean(pstdev(g) for g in full):.2f}"
          f"  完全一致率 {sum(len(set(g)) == 1 for g in full) / len(full):.0%}")
    if len(full) < 2:
        print("  ICC(1) 需要 ≥2 轮(要有轮间方差),当前 1 轮,不可计算。")
        return
    print(f"  自一致性 ICC(1)={icc1(full):.3f}")


def merge(out_path: Path, csv: Path) -> None:
    """把 judge_fidelity(该轮各次重复评分的均值)合入 per-trial CSV。

    做法完全同 analysis/embed.py 的 embed_fidelity:按 (participant_id, round_idx)
    左连、先删同名旧列、全 NaN 拒绝写入(空列写进去=次要证据悄悄消失而输出看着还成功)。
    ⚠️ **次要 / 收敛证据,不进保真主复合**(docs/paper/03 §4 B5)。"""
    import pandas as pd
    if not csv.exists():
        raise SystemExit(f"{csv} 不存在 —— 先跑 analysis/v3.py 再重跑本脚本"
                         f"(分数已落盘 {out_path},续跑不会重复调 API)。")
    # judge_n_reps 一并合入:report() 只用满次的轮算 ICC,而这里 1 次成功也出「均值」——
    # 没有这一列,CSV 里分不出哪些 judge_fidelity 是 3 次的均值、哪些只是 1 次评分。
    rows = [{"participant_id": pid, "round_idx": ridx, "judge_fidelity": mean(g), "judge_n_reps": len(g)}
            for (pid, ridx), g in _by_trial(out_path).items()]
    pt = pd.read_csv(csv).drop(columns=["judge_fidelity", "judge_n_reps"], errors="ignore").merge(
        pd.DataFrame(rows, columns=["participant_id", "round_idx", "judge_fidelity", "judge_n_reps"]),
        on=["participant_id", "round_idx"], how="left")
    n_ok = int(pt["judge_fidelity"].notna().sum())
    if not n_ok:
        raise SystemExit("judge_fidelity 全为 NaN,拒绝写入空列 —— 检查 judge.jsonl 是否全是 "
                         "PARSE_FAIL、被试/轮次键是否与 v3_per_trial.csv 对得上。")
    pt.to_csv(csv, index=False)
    print(f"judge_fidelity 已合入 {csv}(非空 {n_ok}/{len(pt)})")


def openai_rater(temperature: float):
    """B5:judge 固定用 secrets 里的 OpenAI 配置(选法同 analysis/embed.py)。"""
    cfgs = tomllib.loads(SECRETS_PATH.read_text()).get("api_configs", [])
    cfg = next((c for c in cfgs
                if "openai.com" in c.get("base_url", "") and c.get("api_key")), None)
    if cfg is None:
        sys.exit("secrets 里没有可用的 OpenAI 配置(B5:judge 全用 OpenAI 同型号)。")
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
              "Fidelity: 5", '{"Fidelity": 4}',      # 大小写变体也必须出分
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
    n_fail = sum(1 for k in (7, 14) if k <= n1)
    print(f"调用次数:第1遍={n1} 第2遍={calls['n'] - n1}(应等于第1遍的 PARSE_FAIL 数 {n_fail})")
    assert calls["n"] - n1 == n_fail, "续跑没有只重试 PARSE_FAIL —— 自测失败"   # 自测只 print 等于没跑
    report(out, reps)

    import pandas as pd                      # 合入路径:桩 CSV(列名同 v3_per_trial.csv 的键)
    csv = out.parent / "v3_per_trial.csv"
    pd.DataFrame([{"participant_id": p, "round_idx": r} for p, r, _, _ in trials]
                 ).to_csv(csv, index=False)
    merge(out, csv)
    print(pd.read_csv(csv).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="盲评保真 LLM-judge(OpenAI×3 + 自一致性)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--reps", type=int, default=3, help="每轮独立评分次数(B5:同型号 ×3)")
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
    merge(OUT_PATH, CSV)


if __name__ == "__main__":
    main()
