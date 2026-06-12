#!/usr/bin/env python
"""T7.7 LLM-judge — 对 trials 的 final_output 按 4 维 rubric 各评 3 次。

维度: 连贯性 / 画面感 / 新颖性 / 可拍摄性,1-7 分。
judge 模型须与生成模型异家族,用 --config-index 指定(不指定则列出配置提示)。
输出 data/analysis/judge.jsonl,断点续跑(按 trial_id × rep 去重)。
--human-sample N 导出人评校验用的盲化随机子样本 CSV(不调用 API)。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
import sys
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm_batch import SECRETS_PATH, BatchClient  # noqa: E402

DB_PATH = ROOT / "data" / "novastory.db"
ANALYSIS_DIR = ROOT / "data" / "analysis"
OUT_PATH = ANALYSIS_DIR / "judge.jsonl"

DIMS = ("coherence", "visual", "novelty", "filmability")

JUDGE_SYSTEM = (
    "你是一名严格的短视频分镜脚本评审。对给出的分镜脚本按以下 4 个维度独立打分,"
    "每个维度 1-7 分(1=很差,4=及格,7=极佳):\n"
    "- coherence(连贯性):叙事是否完整、镜头间逻辑是否顺畅;\n"
    "- visual(画面感):画面描写是否具体、有视觉冲击;\n"
    "- novelty(新颖性):构思是否跳出俗套;\n"
    "- filmability(可拍摄性):新手用手机能否实际拍出。\n"
    '只输出一个 JSON 对象,如 {"coherence": 4, "visual": 5, "novelty": 3, "filmability": 6},'
    "不要输出任何其他内容。"
)

_SCORE_RE = {d: re.compile(rf'"?{d}"?\s*[::]\s*([1-7])') for d in DIMS}


def parse_scores(text: str) -> dict | None:
    """解析 4 维分数;先试 JSON,再退回正则;失败返回 None。"""
    m = re.search(r"\{.*?\}", text or "", re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            scores = {d: int(obj[d]) for d in DIMS if d in obj}
            if len(scores) == len(DIMS) and all(1 <= v <= 7 for v in scores.values()):
                return scores
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    scores = {}
    for d, rx in _SCORE_RE.items():
        m = rx.search(text or "")
        if m:
            scores[d] = int(m.group(1))
    return scores if len(scores) == len(DIMS) else None


def load_trials() -> list[sqlite3.Row]:
    if not DB_PATH.exists():
        sys.exit(f"{DB_PATH} 不存在 — 还没有试验数据")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, condition, final_output FROM trials"
        " WHERE final_output IS NOT NULL ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def done_keys() -> set[tuple[int, int]]:
    if not OUT_PATH.exists():
        return set()
    keys = set()
    with OUT_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                keys.add((int(r["trial_id"]), int(r["rep"])))
    return keys


def export_human_sample(trials: list[sqlite3.Row], n: int, seed: int) -> None:
    """导出盲化随机子样本:评分表(无条件信息)+ 单独的钥匙文件。"""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    sample = rng.sample(list(trials), min(n, len(trials)))
    rng.shuffle(sample)
    sheet = ANALYSIS_DIR / "human_eval_sample.csv"
    key = ANALYSIS_DIR / "human_eval_key.csv"
    with sheet.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["blind_id", "final_output", *DIMS])
        for i, tr in enumerate(sample, 1):
            w.writerow([i, tr["final_output"], "", "", "", ""])
    with key.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["blind_id", "trial_id", "condition"])
        for i, tr in enumerate(sample, 1):
            w.writerow([i, tr["id"], tr["condition"]])
    print(f"人评样本 {len(sample)} 份 → {sheet}(评分表)+ {key}(钥匙,勿发给评分者)")


def list_configs() -> str:
    try:
        with SECRETS_PATH.open("rb") as f:
            configs = tomllib.load(f).get("api_configs", [])
        return "\n".join(f"  [{i}] {c.get('name', c.get('model', '?'))}" for i, c in enumerate(configs))
    except OSError:
        return "  (secrets.toml 不存在)"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="异家族 LLM-judge:4 维 rubric × 3 次评分 → judge.jsonl"
    )
    ap.add_argument(
        "--config-index", type=int, default=None,
        help="judge 模型的 api_configs 序号(必须与生成模型异家族)",
    )
    ap.add_argument("--reps", type=int, default=3, help="每份评分次数(默认 3)")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument(
        "--human-sample", type=int, default=None, metavar="N",
        help="只导出 N 份人评校验用随机子样本 CSV(不调用 API)",
    )
    ap.add_argument("--seed", type=int, default=42, help="人评抽样种子")
    args = ap.parse_args()

    trials = load_trials()
    if not trials:
        sys.exit("trials 表为空 — 没有可评的 final_output")

    if args.human_sample is not None:
        export_human_sample(trials, args.human_sample, args.seed)
        return

    if args.config_index is None:
        sys.exit(
            "请用 --config-index 指定 judge 模型(须与生成模型异家族)。可用配置:\n"
            + list_configs()
        )
    client = BatchClient.from_secrets(args.config_index, temperature=args.temperature)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    done = done_keys()
    print(f"judge 模型: {client.model}  reps={args.reps}  已完成 {len(done)} 条")

    with OUT_PATH.open("a", encoding="utf-8") as f:
        for tr in trials:
            for rep in range(args.reps):
                if (tr["id"], rep) in done:
                    continue
                raw = client.generate(JUDGE_SYSTEM, tr["final_output"])[0]
                scores = parse_scores(raw)
                rec = {
                    "trial_id": tr["id"],
                    "rep": rep,
                    "scores": scores,
                    "parse_ok": int(scores is not None),
                    "raw": raw if scores is None else None,
                    **client.meta(),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                tag = "ok" if scores else "PARSE_FAIL"
                print(f"[trial{tr['id']}] rep {rep + 1}/{args.reps} {tag}")
    print("judge 评分完毕 →", OUT_PATH)


if __name__ == "__main__":
    main()
