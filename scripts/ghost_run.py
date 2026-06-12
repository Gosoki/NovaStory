# DEPRECATED (paper/7 D10, 2026-06-13): per-participant ghost-run counterfactuals
# were dropped with the pivot to guided co-creation; kept for possible reuse.
#!/usr/bin/env python
"""T7.3 Ghost-run — 对每个 trial 用其真实中间输入跑同一 pipeline 生成 K 份纯机器版本。

C 式: 意图直通成稿;
D/E 式: 先生成 AI 大纲(不编辑)再按大纲生成成稿(零编辑直通),
        每份 ghost 独立采样自己的大纲。

写 data/ghosts/trial{id}.jsonl,每行:
{trial_id, participant_id, condition, ghost_idx, outline, text, model, temperature, base_url, ts}
断点续跑: 已有行数直接跳过。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import prompts  # noqa: E402
from core.llm_batch import BatchClient  # noqa: E402

DB_PATH = ROOT / "data" / "novastory.db"
OUT_DIR = ROOT / "data" / "ghosts"


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def load_trials() -> list[sqlite3.Row]:
    if not DB_PATH.exists():
        sys.exit(f"{DB_PATH} 不存在 — 还没有试验数据,先跑实验或 pilot")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, participant_id, condition, topic_json, intent_statement"
        " FROM trials ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def gen_ghost(client: BatchClient, topic: dict, intent: str, condition: str) -> dict:
    """单份 ghost:返回 {outline, text}(C 式 outline 为 None)。"""
    if condition == "C":
        text = client.generate(
            prompts.build_system_script(topic), prompts.build_user(topic, intent)
        )[0]
        return {"outline": None, "text": text}
    # D/E:大纲零编辑直通
    outline = client.generate(
        prompts.build_system_outline(topic), prompts.build_user(topic, intent)
    )[0]
    text = client.generate(
        prompts.build_system_script(topic),
        prompts.build_user(topic, intent, base_outline=outline),
    )[0]
    return {"outline": outline, "text": text}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="对 trials 表的每个 trial 生成 K 份 ghost(纯机器对照)"
    )
    ap.add_argument("--k", type=int, default=20, help="每 trial ghost 数(默认 20)")
    ap.add_argument(
        "--config-index", type=int, default=0,
        help="secrets.toml 中 api_configs 序号(默认 0)",
    )
    ap.add_argument("--temperature", type=float, default=0.8)
    args = ap.parse_args()

    trials = load_trials()
    if not trials:
        sys.exit("trials 表为空 — 没有可跑的 trial")
    client = BatchClient.from_secrets(args.config_index, temperature=args.temperature)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"模型: {client.model}  K={args.k}  trials={len(trials)}")

    for tr in trials:
        out_path = OUT_DIR / f"trial{tr['id']}.jsonl"
        done = count_lines(out_path)
        if done >= args.k:
            print(f"[trial{tr['id']}] 已有 {done} 份,跳过")
            continue
        topic = json.loads(tr["topic_json"] or "{}")
        intent = (tr["intent_statement"] or "").strip()
        with out_path.open("a", encoding="utf-8") as f:
            for g in range(done, args.k):
                ghost = gen_ghost(client, topic, intent, tr["condition"])
                rec = {
                    "trial_id": tr["id"],
                    "participant_id": tr["participant_id"],
                    "condition": tr["condition"],
                    "ghost_idx": g,
                    **ghost,
                    **client.meta(),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                print(f"[trial{tr['id']}|{tr['condition']}] ghost {g + 1}/{args.k}")
    print("ghost-run 完毕 →", OUT_DIR)


if __name__ == "__main__":
    main()
