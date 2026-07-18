#!/usr/bin/env python
"""主题开放度 norming(paper/14 §3)—— 冻结题面前检查三题开放度/难度是否可比。

输入:`scripts/baseline_gen.py` 产出的 data/baseline/topic{0,1,2}.jsonl(每题 N 份机器稿)。
指标(纯文本、无 API):
  compliance  parse_ok 率 / 平均镜数 / 字段齐全率 / 3镜达标率  → 难度代理
  openness    gzip 压缩比 CR(高=同质=开放度低) / distinct-2(高=发散) / self-rep(高=同质)
              + 平均字数
判读:三题的 CR 与 distinct-2 应落在相近区间;离群题(明显更同质/更发散)需改措辞或换题。

用法:.venv/bin/python analysis/norming.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import textstats  # noqa: E402
from core.shots import parse_shots, strip_format  # noqa: E402

BASELINE = ROOT / "data" / "baseline"
TOPICS = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
_FIELDS = ("shot_type", "visual", "audio", "duration")


def _load_texts(i: int) -> list[str]:
    p = BASELINE / f"topic{i}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line).get("text", ""))
    return out


def _compliance(texts: list[str]) -> dict:
    parsed = [parse_shots(t or "") for t in texts]
    ok = [p for p in parsed if p]
    return {
        "parse_ok_rate": len(ok) / len(texts) if texts else float("nan"),
        "mean_shots": float(np.mean([len(p) for p in parsed])) if parsed else float("nan"),
        "shots3_rate": np.mean([len(p) == 3 for p in parsed]) if parsed else float("nan"),
        "field_complete": float(np.mean([
            sum(bool(s.get(f)) for f in _FIELDS) / len(_FIELDS)
            for p in ok for s in p])) if ok else float("nan"),
    }


def main() -> None:
    rows = []
    for i, topic in enumerate(TOPICS[:3]):
        texts = _load_texts(i)
        title = topic.get("title", {}).get("ja", f"topic{i}")
        if not texts:
            print(f"[topic{i}] {title}: 无基线数据(先跑 scripts/baseline_gen.py)")
            continue
        stripped = [strip_format(t) for t in texts]
        row = {"topic": f"{i}:{title}", "n": len(texts),
               "gzip_cr": textstats.gzip_cr(stripped),
               "distinct2": textstats.distinct_n(stripped, 2),
               "self_rep4": textstats.self_repetition(stripped, 4),
               "mean_len": float(np.mean([len(t) for t in texts]))}
        row.update(_compliance(texts))
        rows.append(row)

    if not rows:
        print("没有任何基线数据。先运行: .venv/bin/python scripts/baseline_gen.py --n 12 --lang ja")
        return

    print(f"{'指标':<16}", *[f"{r['topic'][:18]:>20}" for r in rows], sep="")
    keys = ["n", "gzip_cr", "distinct2", "self_rep4", "mean_len",
            "parse_ok_rate", "mean_shots", "shots3_rate", "field_complete"]
    for k in keys:
        vals = "".join(f"{r[k]:>20.3f}" if isinstance(r[k], float) else f"{r[k]:>20}"
                       for r in rows)
        print(f"{k:<16}{vals}")

    # 离群提示:CR / distinct2 的题间极差(粗判)
    if len(rows) >= 2:
        for metric in ("gzip_cr", "distinct2"):
            vals = [r[metric] for r in rows if not np.isnan(r[metric])]
            if len(vals) >= 2:
                spread = max(vals) - min(vals)
                mean = np.mean(vals)
                flag = "⚠️ 题间差异较大,建议调措辞/换题" if mean and spread / mean > 0.15 else "✓ 大致可比"
                print(f"\n{metric}: 极差 {spread:.3f} / 均值 {mean:.3f} → {flag}")
    print("\n注:开放度相对比较用同一模型即可;绝对值随模型变。冻结题面前若离群则改题。")


if __name__ == "__main__":
    main()
