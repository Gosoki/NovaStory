#!/usr/bin/env python
"""A6: events 事件流 → 逐 trial 指标(paper/12 §1②③④、§2 事件级的「可算」部分)。

覆盖 trials 便捷列算不出的四件事:
  问卷时长(trial_submit→questionnaire_submit)、每轮 AI 调用次数/失败次数、
  token 成本(llm_done.usage.total_tokens)、最长单次等待、续接次数。

重做轮切段(paper/12 §0):同 (participant, round) 下按 `attempt` 分组,进了论文的
那段 = 含 trial_id 非空行的那段;取该段**全部**事件(不用 WHERE trial_id IS NOT NULL
过滤,否则丢掉 questionnaire_submit 等提交后事件)。旧行(7-02 前)attempt 全 NULL、
ts 只有秒级 → 退化为按 (participant, round) 整取,指标照算(精度降到秒)。

纯读、不改库;与 v3.load() 一样排除 dev 注入被试。像 analysis/embed.py 那样把列
**合入** v3_per_trial.csv,而不是做 v3.per_trial() 的硬依赖(v3 必须能在无 events
的纯 trials 库上跑通)。

用法: .venv/bin/python analysis/events.py [--db data/novastory.db]
                                          [--csv data/analysis/v3_per_trial.csv]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB = ROOT / "data" / "novastory.db"
DEFAULT_CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
_COLS = ["t_questionnaire", "n_llm_calls", "n_llm_errors",
         "llm_total_tokens", "llm_wait_max", "n_resumes"]


def load_events(db_path: Path) -> pd.DataFrame:
    """events(排除 dev 被试),按 (ts, id) 排序;ts 解析为 datetime,旧秒级行也吃。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ev = pd.read_sql("SELECT * FROM events", con)
        parts = pd.read_sql("SELECT id, screening_json FROM participants", con)
    finally:
        con.close()
    dev_ids = {int(r.id) for r in parts.itertuples()
               if _loads(r.screening_json).get("dev")}
    if dev_ids:
        ev = ev[~ev["participant_id"].isin(dev_ids)]
    ev = ev.dropna(subset=["participant_id", "round_idx"])
    ev["ts"] = pd.to_datetime(ev["ts"], format="ISO8601", errors="coerce")
    return ev.sort_values(["ts", "id"])


def _loads(x) -> dict:
    if not isinstance(x, str) or not x.strip():
        return {}
    try:
        d = json.loads(x)
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def winning_attempt(g: pd.DataFrame) -> pd.DataFrame:
    """一轮内进了论文的那段 attempt 的全部事件(含提交后的 questionnaire_submit)。"""
    won = g.loc[g["trial_id"].notna(), "attempt"].dropna().unique()
    return g[g["attempt"].isin(won)] if len(won) else g


def round_metrics(g: pd.DataFrame) -> dict:
    """g = 一个 (participant, round) 的全部事件(已排序)。"""
    n_resumes = int((g["type"] == "session_resumed").sum())  # 续接跨段发生,按整轮计
    w = winning_attempt(g)
    pay = w["payload_json"].map(_loads)

    sub = w.loc[w["type"] == "trial_submit", "ts"]
    q = w.loc[w["type"] == "questionnaire_submit", "ts"]
    t_q = np.nan
    if len(sub) and len(q) and pd.notna(sub.max()) and pd.notna(q.max()):
        t_q = (q.max() - sub.max()).total_seconds()

    done = pay[w["type"] == "llm_done"]
    waits = [p.get("elapsed") for p in pay[w["type"].isin(("llm_done", "llm_error"))]
             if isinstance(p.get("elapsed"), (int, float))]
    toks = [(p.get("usage") or {}).get("total_tokens") for p in done]
    toks = [t for t in toks if isinstance(t, (int, float))]
    return {
        "t_questionnaire": t_q,
        "n_llm_calls": int((w["type"] == "llm_start").sum()),
        "n_llm_errors": int((w["type"] == "llm_error").sum()),
        "llm_total_tokens": float(sum(toks)) if toks else np.nan,
        "llm_wait_max": float(max(waits)) if waits else np.nan,
        "n_resumes": n_resumes,
    }


def per_trial(ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (pid, ridx), g in ev.groupby(["participant_id", "round_idx"], sort=True):
        rows.append({"participant_id": int(pid), "round_idx": int(ridx), **round_metrics(g)})
    return pd.DataFrame(rows, columns=["participant_id", "round_idx", *_COLS])


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 events 逐 trial 指标")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="合入的 v3 逐 trial CSV")
    args = ap.parse_args()

    ev = load_events(args.db)
    pt = per_trial(ev)
    if pt.empty:
        print(f"{args.db} 里还没有可用 events(N=0)。")
        return

    with pd.option_context("display.width", 200, "display.max_columns", 20,
                           "display.float_format", lambda x: f"{x:.2f}"):
        print(f"events {len(ev)} 行 → 逐 trial {len(pt)} 行\n")
        print("=== 事件级指标 均值/非空 ===")
        print(pd.DataFrame({"mean": pt[_COLS].mean(numeric_only=True),
                            "n_notna": pt[_COLS].notna().sum()}))

    if not args.csv.exists():
        print(f"\n(未合入:{args.csv} 不存在,先跑 analysis/v3.py)")
        return
    base = pd.read_csv(args.csv)
    base = base.drop(columns=_COLS, errors="ignore").merge(
        pt, on=["participant_id", "round_idx"], how="left")
    base.to_csv(args.csv, index=False)
    print(f"\n事件级列已合入 {args.csv}"
          f"(匹配上 {base['n_llm_calls'].notna().sum()}/{len(base)} 行)")


if __name__ == "__main__":
    main()
