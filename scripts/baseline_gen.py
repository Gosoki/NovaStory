#!/usr/bin/env python
"""T7.2 采样地板 — 每题 N 份纯机器 C 式输出 → data/baseline/topic{i}.jsonl。

每行: {topic_idx, sample_idx, seed, text, model, temperature, base_url, ts}
断点续跑: 已有行数直接跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import prompts  # noqa: E402
from core.llm_batch import BatchClient  # noqa: E402

TOPICS_PATH = ROOT / "data" / "topics.json"
OUT_DIR = ROOT / "data" / "baseline"


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def load_seeds(path: Path | None) -> list[str]:
    """意图列表(每行一条,空行忽略);为空时调用方退回题目情境。"""
    if path is None:
        return []
    lines = [s.strip() for s in path.read_text(encoding="utf-8").splitlines()]
    return [s for s in lines if s]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="生成机器基线(采样地板):对前 3 题各生成 N 份 C 式纯机器输出"
    )
    ap.add_argument("--n", type=int, default=30, help="每题样本数(默认 30)")
    ap.add_argument(
        "--seeds-file", type=Path, default=None,
        help="意图列表文件,每行一条,循环使用;缺省用题目 scenario 作为 seed",
    )
    ap.add_argument(
        "--config-index", type=int, default=0,
        help="secrets.toml 中 api_configs 序号(默认 0)",
    )
    ap.add_argument("--temperature", type=float, default=0.8)
    args = ap.parse_args()

    topics = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))[:3]
    seeds = load_seeds(args.seeds_file)
    client = BatchClient.from_secrets(args.config_index, temperature=args.temperature)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"模型: {client.model}  温度: {client.temperature}  每题 N={args.n}")

    for i, topic in enumerate(topics):
        out_path = OUT_DIR / f"topic{i}.jsonl"
        done = count_lines(out_path)
        if done >= args.n:
            print(f"[topic{i}] 已有 {done} 份,跳过")
            continue
        if done:
            print(f"[topic{i}] 续跑:已有 {done} 份,补到 {args.n}")
        system = prompts.build_system_script(topic)
        with out_path.open("a", encoding="utf-8") as f:
            for j in range(done, args.n):
                seed = seeds[j % len(seeds)] if seeds else topic.get("scenario", "")
                text = client.generate(system, prompts.build_user(topic, seed))[0]
                rec = {
                    "topic_idx": i,
                    "sample_idx": j,
                    "seed": seed,
                    "text": text,
                    **client.meta(),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                print(f"[topic{i}] {j + 1}/{args.n} 完成 ({len(text)} 字)")
    print("基线生成完毕 →", OUT_DIR)


if __name__ == "__main__":
    main()
