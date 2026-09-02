#!/usr/bin/env python
"""A6: events 事件流 → 逐 trial 指标(docs/paper/03 §6.1/§7 的事件级「可算」部分)。

覆盖 trials 便捷列算不出的四件事:
  问卷时长(trial_submit→questionnaire_submit)、每轮 AI 调用次数/失败次数、
  token 成本(llm_done.usage.total_tokens)、最长单次等待、续接次数。

**逐被试**另出一张 intake 时长表(`--out-participant`):同意页/说明页/背景问卷各停留多久、
总问卷用时、整场用时(participants.created_at→finished_at)。说明页停留 2 秒 = 标准化
onboarding 没生效,属数据质量信号,采数期就要看得见。intake 事件写在 round_idx=0
(见 core/state.log_intake_event),故逐 trial 表把 round_idx=0 整段排除。

重做轮切段(docs/paper/03 §6.1):同 (participant, round) 下按 `attempt` 分组,进了论文的
那段 = 含 trial_id 非空行的那段;取该段**全部**事件(不用 WHERE trial_id IS NOT NULL
过滤,否则丢掉 questionnaire_submit 等提交后事件)。旧行(7-02 前)attempt 全 NULL、
ts 只有秒级 → 退化为按 (participant, round) 整取,指标照算(精度降到秒)。

纯读、不改库;与 v3.load() 一样排除 dev 注入被试。像 analysis/embed.py 那样把列
**合入** v3_per_trial.csv,而不是做 v3.per_trial() 的硬依赖(v3 必须能在无 events
的纯 trials 库上跑通)。

用法: .venv/bin/python analysis/events.py [--db data/novastory.db]
                                          [--csv data/analysis/v3_per_trial.csv]
                                          [--out-participant <逐被试时长表>]
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
PARTICIPANT_CSV_NAME = "v3_per_participant.csv"   # 与 --csv 同目录(两张表是同一批产物)
_COLS = ["t_questionnaire", "n_llm_calls", "n_llm_errors",
         "llm_total_tokens", "llm_wait_max", "n_resumes"]


def load_events(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(events, participants),都已排除 dev 被试;ts 解析为 datetime,旧秒级行也吃。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ev = pd.read_sql("SELECT * FROM events", con)
        parts = pd.read_sql("SELECT * FROM participants", con)
    finally:
        con.close()
    dev_ids = {int(r.id) for r in parts.itertuples()
               if _loads(r.screening_json).get("dev")}
    if dev_ids:
        ev = ev[~ev["participant_id"].isin(dev_ids)]
        parts = parts[~parts["id"].isin(dev_ids)]
    ev = ev.dropna(subset=["participant_id", "round_idx"])
    ev["ts"] = pd.to_datetime(ev["ts"], format="ISO8601", errors="coerce")
    return ev.sort_values(["ts", "id"]), parts


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
    ev = ev[ev["round_idx"] != 0]  # round_idx=0 = intake stage, not a trial
    for (pid, ridx), g in ev.groupby(["participant_id", "round_idx"], sort=True):
        rows.append({"participant_id": int(pid), "round_idx": int(ridx), **round_metrics(g)})
    return pd.DataFrame(rows, columns=["participant_id", "round_idx", *_COLS])


_PCOLS = ["t_consent", "t_intro", "t_screening", "t_intake_total",
          "t_final_survey", "t_session_total"]


def _gap(g: pd.DataFrame, a: str, b: str, *, last_pass: bool = False) -> float:
    """b 的首次 − a 的首次(秒);任一缺失或倒序 → NaN。

    `last_pass=True` 改成「**最后一次** b − 紧邻它之前的那次 a」。说明页在 2026-09-01
    之后会因为续接被重看(`state._attempt_resume` 把未完成第 1 轮的人送回说明页),
    于是同一位被试可能有多对 intro_shown/intro_continue。首对配首对会把整段离开时间
    算进停留时长 —— 而这一列存在的意义恰恰是「说明页只停了 2 秒 = 没读」的质量信号,
    配错了会把最不认真的那一位报成最认真的。"""
    ta = g.loc[g["type"] == a, "ts"]
    tb = g.loc[g["type"] == b, "ts"]
    if not len(ta) or not len(tb):
        return np.nan
    if last_pass:
        end = tb.max()
        before = ta[ta <= end]
        if not len(before):
            return np.nan
        d = (end - before.max()).total_seconds()
    else:
        d = (tb.min() - ta.min()).total_seconds()
    return d if d >= 0 else np.nan


def per_participant(ev: pd.DataFrame, parts: pd.DataFrame) -> pd.DataFrame:
    """逐被试的 intake / 总问卷 / 整场时长。

    intake 三段来自 round_idx=0 的事件对(consent_shown→consent_agree 等);
    整场时长来自 participants 的 created_at→finished_at(唯一 events 补不了的一段:
    完成码签发那一刻)。老库(无 intake 埋点 / 无 finished_at)相应列为 NaN。"""
    rows = []
    for pid, g in ev.groupby("participant_id", sort=True):
        t_consent = _gap(g, "consent_shown", "consent_agree")
        t_intro = _gap(g, "intro_shown", "intro_continue", last_pass=True)
        t_screening = _gap(g, "screening_shown", "screening_submit")
        # intake 到哪里为止,取决于这一行是换序前还是换序后落的:
        # 2026-09-01(§4)之前是 同意→说明→筛查(收尾 = screening_submit),
        # 之后是 同意→筛查→说明(收尾 = intro_continue)。取两者中**较晚**的那个
        # 端点,两种排序下都对;若按新口径一刀切,旧库的行会漏掉整份背景问卷
        # (实测:98 秒 vs 真实 1436 秒)—— 而且不是 NaN,是一个看着挺像的错数。
        base = _gap(g, "consent_shown", "screening_submit")
        ts_i = g.loc[g["type"] == "intro_shown", "ts"]
        ts_s = g.loc[g["type"] == "screening_submit", "ts"]
        post_swap = bool(len(ts_i) and len(ts_s) and ts_i.max() > ts_s.min())
        if pd.isna(base):
            intake = _gap(g, "consent_shown", "intro_continue")
        elif post_swap:
            # 换序后:同意+背景问卷 的整段 **加上** 说明页那一趟的净停留。
            # 不能直接用 consent_shown→intro_continue 的跨度 —— 在说明页刷新过的人
            # (续接会把他送回说明页)会把中间离开的几十分钟整段折进 intake,
            # 而这一列正是用来对照同意书「約 10〜15 分」承诺的。
            intake = base + (t_intro if not pd.isna(t_intro) else 0.0)
        else:
            intake = base   # 换序前:说明页本来就在这段跨度之内
        rows.append({
            "participant_id": int(pid),
            "t_consent": t_consent,
            "t_intro": t_intro,
            "t_screening": t_screening,
            "t_intake_total": intake,
            "t_final_survey": _gap(g, "final_survey_shown", "final_survey_submit"),
        })
    out = pd.DataFrame(rows, columns=["participant_id", *_PCOLS])
    if parts.empty:
        return out
    p = parts.rename(columns={"id": "participant_id"}).copy()
    for c in ("created_at", "finished_at"):
        p[c] = pd.to_datetime(p[c], errors="coerce") if c in p else pd.NaT
    p["t_session_total"] = (p["finished_at"] - p["created_at"]).dt.total_seconds()
    return (out.drop(columns=["t_session_total"])
               .merge(p[["participant_id", "t_session_total"]],
                      on="participant_id", how="outer")
               .sort_values("participant_id"))


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 events 逐 trial 指标")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="合入的 v3 逐 trial CSV")
    ap.add_argument("--out-participant", type=Path, default=None,
                    help=f"逐被试 intake/整场时长表(默认 --csv 同目录的 {PARTICIPANT_CSV_NAME})")
    args = ap.parse_args()

    ev, parts = load_events(args.db)
    out_p = args.out_participant or args.csv.with_name(PARTICIPANT_CSV_NAME)
    pp = per_participant(ev, parts)
    if not pp.empty:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        pp.to_csv(out_p, index=False)
        with pd.option_context("display.width", 200, "display.float_format",
                               lambda x: f"{x:.1f}"):
            print("=== 逐被试 intake / 整场时长(秒)均值 ===")
            print(pd.DataFrame({"mean": pp[_PCOLS].mean(numeric_only=True),
                                "n_notna": pp[_PCOLS].notna().sum()}))
            print(f"→ {out_p}\n")
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
