#!/usr/bin/env python
"""A6 分析管线 v3(基础层)—— 从 SQLite 计算客观评价栈里【确定性、无需 API】的指标。

覆盖 paper/14 §5 的可立即计算部分:
  结构完整度 / 逐镜头保真 / 版本演化 / 努力再分配 / 主观复合 / 条件×题目多样性。
待补(需真数据或 API,后续增量):
  embedding 相对基线保真 Δ(需 embedding + baseline_gen)、LMM/TOST 统计检验、图表。

用法:
  .venv/bin/python analysis/v3.py [--db data/novastory.db] [--out data/analysis/v3_per_trial.csv]

设计:纯读、不改库;对旧库/未完成轮容忍(缺列/缺问卷 → NaN)。取代 v2 的
metrics.py/stats.py(HLZ 时代);全部 A6 完成并在真数据上验收后再删旧文件。
"""
from __future__ import annotations

import argparse
import difflib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import textstats  # noqa: E402
from core.shots import parse_shots, strip_format  # noqa: E402

DEFAULT_DB = ROOT / "data" / "novastory.db"
_SHOT_FIELDS = ("shot_type", "visual", "audio", "duration")
_TAGS = ("mine", "ai_ok", "ai_against")


# ---------------- load ----------------

def load(db_path: Path) -> pd.DataFrame:
    """trials ⟕ questionnaires,按 (participant_id, round_idx);容忍缺列。

    **排除研究员/dev 注入的测试被试**(`screening_json.dev == true`,devtools 的
    「跳过同意+筛查」)——它们不是真被试,绝不能进任何分析(保真/所有权/TOST/功效/
    试测健康)。此前无过滤,dev 走查行会静默污染毕业数据(深度评审 2026-07-19,#20)。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        trials = pd.read_sql("SELECT * FROM trials", con)
        quest = pd.read_sql("SELECT * FROM questionnaires", con)
        parts = pd.read_sql("SELECT id, screening_json FROM participants", con)
    finally:
        con.close()
    dev_ids = {int(r.id) for r in parts.itertuples()
               if _loads(r.screening_json, {}).get("dev")}
    if dev_ids:
        trials = trials[~trials["participant_id"].isin(dev_ids)]
    # avoid column collisions on merge (id/created_at exist in both)
    quest = quest.drop(columns=[c for c in ("id", "created_at") if c in quest], errors="ignore")
    df = trials.merge(quest, on=["participant_id", "round_idx"], how="left",
                      suffixes=("", "_q"))
    return df


def _loads(x, default):
    if not isinstance(x, str) or not x.strip():
        return default
    try:
        return json.loads(x)
    except (ValueError, TypeError):
        return default


def _num(x):
    return x if isinstance(x, (int, float)) and not pd.isna(x) else np.nan


# ---------------- per-trial deterministic metrics ----------------

def structural(final_output: str) -> dict:
    """任务规格符合度(客观质量下界):镜数、字段齐全率、是否达标。"""
    shots = parse_shots(final_output or "")
    n = len(shots)
    if not n:
        return {"n_shots": 0, "field_completeness": np.nan, "parse_ok": 0, "shots_ok": 0}
    comp = np.mean([sum(bool(s.get(f)) for f in _SHOT_FIELDS) / len(_SHOT_FIELDS)
                    for s in shots])
    return {"n_shots": n, "field_completeness": float(comp),
            "parse_ok": int(any(s.get("visual") for s in shots)),
            "shots_ok": int(n == 3)}


def shot_fidelity(shot_annotations_json) -> dict:
    """逐镜头保真:mine / ai_ok / ai_against 占比(自评但离散、逐镜头)。"""
    ann = _loads(shot_annotations_json, [])
    tags = [a.get("tag") for a in ann if isinstance(a, dict) and a.get("tag") in _TAGS]
    if not tags:
        return {f"{t}_ratio": np.nan for t in _TAGS}
    return {f"{t}_ratio": tags.count(t) / len(tags) for t in _TAGS}


def version_evo(script_versions, final_output: str) -> dict:
    """版本演化:终稿 vs 首个 AI 版的相似度(改了多少)、AI/人改版数。"""
    vs = _loads(script_versions, [])
    ai_texts = [v.get("text", "") for v in vs if v.get("author") == "ai"]
    n_ai = len(ai_texts)
    n_user = sum(1 for v in vs if v.get("author") == "user_edit")
    first_ai = ai_texts[0] if ai_texts else ""
    final = final_output or (vs[-1].get("text", "") if vs else "")
    if first_ai and final:
        ratio = difflib.SequenceMatcher(
            None, strip_format(first_ai), strip_format(final)).ratio()
    else:
        ratio = np.nan
    return {"n_ai_versions": n_ai, "n_user_versions": n_user,
            "final_vs_firstai_sim": ratio}  # 1=没改, 低=改得多


def subjective(row: pd.Series) -> dict:
    own = _loads(row.get("ownership_json"), {})
    soa = _loads(row.get("soa_json"), {})
    tlx = _loads(row.get("tlx_json"), {})
    own_vals = [own.get(f"own{i}") for i in (1, 2, 3) if own.get(f"own{i}") is not None]
    soa_vals = [soa.get(f"soa{i}") for i in (1, 2) if soa.get(f"soa{i}") is not None]
    # straight-lining careless-response flag: every item in the Likert block
    # (own1-3 + soa1-2 + tlx1) identical, with ≥4 items answered (deep-review #31).
    block = own_vals + soa_vals + ([tlx.get("tlx1")] if tlx.get("tlx1") is not None else [])
    straightline = int(len(block) >= 4 and len(set(block)) == 1)
    return {
        "own_mean": float(np.mean(own_vals)) if own_vals else np.nan,
        "soa_mean": float(np.mean(soa_vals)) if soa_vals else np.nan,
        "violation": _num(row.get("intent_violation")),
        "imagine": _num(row.get("imagine_match")),
        "satisfaction": _num(row.get("satisfaction")),
        "ai_q_quality": _num(row.get("ai_q_quality")),  # E only
        "straightline": straightline,
    }


def behavioral(row: pd.Series) -> dict:
    """努力再分配:事前投入(E 引导答题) vs 事后返工;三口径并报(paper/9 §4)。"""
    pre = _num(row.get("t_pregen"))
    post = _num(row.get("t_postgen"))
    pre0 = 0.0 if pd.isna(pre) else pre        # C/D 无事前引导 → 记 0
    total = pre0 + (0.0 if pd.isna(post) else post)
    return {
        "pre_investment": pre0,
        "post_investment": post,
        "total_investment": total,
        "n_ai_rounds": _num(row.get("n_ai_rounds")),
        "n_hand_edits": _num(row.get("n_hand_edits")),
        "hand_edit_chars": _num(row.get("hand_edit_chars")),
        "t_total": _num(row.get("t_total")),
    }


def per_trial(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        m = {
            "participant_id": r.get("participant_id"),
            "round_idx": r.get("round_idx"),
            "condition": r.get("condition"),
            "topic": _topic_title(r.get("topic_json")),
        }
        m.update(structural(r.get("final_output")))
        m.update(shot_fidelity(r.get("shot_annotations_json")))
        m.update(version_evo(r.get("script_versions"), r.get("final_output")))
        m.update(subjective(r))
        m.update(behavioral(r))
        rows.append(m)
    return pd.DataFrame(rows)


def _topic_title(topic_json) -> str:
    t = _loads(topic_json, {})
    title = t.get("title", "") if isinstance(t, dict) else ""
    if isinstance(title, dict):
        return title.get("ja") or title.get("zh") or ""
    return title or ""


# ---------------- condition × topic diversity (group-level) ----------------

def diversity_by_group(df: pd.DataFrame) -> pd.DataFrame:
    """同题内、按条件分组算成稿多样性(越同质 → CR 高 / distinct 低)。
    回答'哪种流水线让新手产出更同质化'(paper/14 §5)。"""
    out = []
    df = df.copy()
    df["topic"] = df["topic_json"].map(_topic_title)
    for (cond, topic), g in df.groupby(["condition", "topic"]):
        finals = [strip_format(x or "") for x in g["final_output"].tolist()]
        finals = [x for x in finals if x]
        if len(finals) < 2:
            continue
        out.append({
            "condition": cond, "topic": topic, "n": len(finals),
            "gzip_cr": textstats.gzip_cr(finals),
            "distinct2": textstats.distinct_n(finals, 2),
            "self_rep4": textstats.self_repetition(finals, 4),
        })
    return pd.DataFrame(out)


# ---------------- CLI ----------------

_SUMMARY_COLS = [
    "parse_ok", "field_completeness", "shots_ok",
    "own_mean", "soa_mean", "satisfaction", "imagine", "violation", "ai_q_quality",
    "mine_ratio", "ai_against_ratio", "final_vs_firstai_sim",
    "pre_investment", "post_investment", "total_investment",
    "n_ai_rounds", "hand_edit_chars", "straightline",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 v3 确定性指标(无 API)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "analysis" / "v3_per_trial.csv")
    args = ap.parse_args()

    raw = load(args.db)
    pt = per_trial(raw)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pt.to_csv(args.out, index=False)

    print(f"载入 {len(raw)} 行 trials;逐 trial 指标 {len(pt)} 行 → {args.out}\n")
    have = [c for c in _SUMMARY_COLS if c in pt.columns]
    print("=== 按条件均值(核心对比 C/D/E)===")
    with pd.option_context("display.width", 200, "display.max_columns", 40,
                           "display.float_format", lambda x: f"{x:.2f}"):
        print(pt.groupby("condition")[have].mean(numeric_only=True).T)
        print("\n=== 条件×题目 多样性(CR 高=更同质)===")
        div = diversity_by_group(raw)
        print(div.to_string(index=False) if len(div) else "(每组 <2 稿,略)")
    print("\n待补(需真数据/API):embedding 相对基线保真 Δ、LMM/TOST、图表。")


if __name__ == "__main__":
    main()
