#!/usr/bin/env python
# ============================================================================
# ⚠️ v2(ModeMirror/HLZ)时代脚本 — 与 v3 schema 系统性脱节,收数前必须重写。
# 已知问题(2026-07-02 第三轮审查):主 DV 绑定已废弃的 hlz_z;读 v3 中不存在/
# 恒空的 v2 列;v3 新列(guidance_json/t_pregen/t_postgen/n_*)与 questionnaires
# 表(主观量表=主要终点)完全没进管线。重写待 A4(主要终点组合)拍板后进行。
# 详见 paper/8「分析层」。
# ============================================================================
"""T7.5 统计分析 — LMM / Wilcoxon / 置换检验 / TOST / 剂量-反应 → stats_report.md。

输入: data/analysis/metrics_per_trial.csv(metrics.py 产出)、
      data/analysis/pairwise_sims.json、data/analysis/judge.jsonl(可选)。
数据不足时逐项优雅降级,报告中明示哪些检验被跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "data" / "analysis"

CONTRASTS = (("D", "C"), ("E", "D"))  # 计划对比 D−C、E−D


# ---------------- 可单测的纯统计函数 ----------------


def holm(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni 校正(保持单调)。"""
    m = len(pvals)
    order = np.argsort(pvals)
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(1.0, running)
    return adj


def tost_paired(diffs: np.ndarray, bound_dz: float = 0.4) -> dict:
    """配对 TOST 等价检验,等价界 ±bound_dz(dz 单位,scipy 手写)。

    返回 {n, dz, p_lower, p_upper, p, equivalent};n<3 或零方差时 p 为 nan。"""
    from scipy import stats as ss

    diffs = np.asarray(diffs, dtype=float)
    diffs = diffs[np.isfinite(diffs)]
    n = len(diffs)
    if n < 3 or diffs.std(ddof=1) == 0:
        return {"n": n, "dz": float("nan"), "p_lower": float("nan"),
                "p_upper": float("nan"), "p": float("nan"), "equivalent": False}
    dz = diffs.mean() / diffs.std(ddof=1)
    se = 1.0 / np.sqrt(n)  # dz 的近似标准误
    t_lower = (dz + bound_dz) / se  # H0: dz <= −bound → 期望显著大于
    t_upper = (dz - bound_dz) / se  # H0: dz >= +bound → 期望显著小于
    p_lower = float(ss.t.sf(t_lower, df=n - 1))
    p_upper = float(ss.t.cdf(t_upper, df=n - 1))
    p = max(p_lower, p_upper)
    return {"n": n, "dz": float(dz), "p_lower": p_lower, "p_upper": p_upper,
            "p": p, "equivalent": p < 0.05}


def diversity_from_sims(sims: np.ndarray, idx: list[int]) -> float:
    """给定相似度矩阵与组内下标,返回 1 − 平均两两相似度;|idx|<2 时 nan。"""
    if len(idx) < 2:
        return float("nan")
    sub = sims[np.ix_(idx, idx)]
    iu = np.triu_indices(len(idx), k=1)
    return float(1.0 - sub[iu].mean())


def permutation_diversity_test(
    sims_by_topic: dict,
    cond_a: str,
    cond_b: str,
    n_perm: int = 10000,
    seed: int = 42,
) -> dict:
    """组内多样性置换检验:重排条件标签,统计量 = 跨题目平均的
    diversity(a) − diversity(b)。返回 {observed, p, n_perm, n_topics}。"""
    rng = np.random.default_rng(seed)
    mats, labels = [], []
    for d in sims_by_topic.values():
        conds = list(d["conditions"])
        if sum(c == cond_a for c in conds) >= 2 and sum(c == cond_b for c in conds) >= 2:
            mats.append(np.asarray(d["sims"], dtype=float))
            labels.append(conds)
    if not mats:
        return {"observed": float("nan"), "p": float("nan"), "n_perm": 0, "n_topics": 0}

    def stat(label_sets: list[list[str]]) -> float:
        vals = []
        for m, ls in zip(mats, label_sets):
            ia = [i for i, c in enumerate(ls) if c == cond_a]
            ib = [i for i, c in enumerate(ls) if c == cond_b]
            da, db = diversity_from_sims(m, ia), diversity_from_sims(m, ib)
            if np.isfinite(da) and np.isfinite(db):
                vals.append(da - db)
        return float(np.mean(vals)) if vals else float("nan")

    observed = stat(labels)
    count = 0
    for _ in range(n_perm):
        perm = [list(rng.permutation(ls)) for ls in labels]
        t = stat(perm)
        if np.isfinite(t) and abs(t) >= abs(observed):
            count += 1
    p = (count + 1) / (n_perm + 1)
    return {"observed": observed, "p": p, "n_perm": n_perm, "n_topics": len(mats)}


# ---------------- 数据整形 ----------------


def participant_means(df: pd.DataFrame, dv: str) -> pd.DataFrame:
    """被试 × 条件的均值宽表(列 = C/D/E)。"""
    sub = df.dropna(subset=[dv])
    return sub.pivot_table(index="participant_id", columns="condition", values=dv)


def paired_diffs(wide: pd.DataFrame, a: str, b: str) -> np.ndarray:
    """a−b 的被试级配对差(仅两条件齐全的被试)。"""
    if a not in wide.columns or b not in wide.columns:
        return np.array([])
    sub = wide[[a, b]].dropna()
    return (sub[a] - sub[b]).to_numpy()


# ---------------- 各检验(返回 markdown 片段或抛出跳过原因)----------------


class Skip(Exception):
    """数据不足,跳过该检验。"""


def section_lmm(df: pd.DataFrame, dv: str) -> str:
    import statsmodels.formula.api as smf

    sub = df.dropna(subset=[dv, "condition", "participant_id"]).copy()
    if sub["participant_id"].nunique() < 3 or sub["condition"].nunique() < 2:
        raise Skip(f"被试数或条件数不足(被试 {sub['participant_id'].nunique()})")
    model = smf.mixedlm(
        f"{dv} ~ C(condition, Treatment('C')) + C(topic_idx) + round_idx",
        data=sub,
        groups=sub["participant_id"],
    )
    res = model.fit(reml=True)
    params, cov = res.params, res.cov_params()
    name_d = "C(condition, Treatment('C'))[T.D]"
    name_e = "C(condition, Treatment('C'))[T.E]"
    if name_d not in params.index or name_e not in params.index:
        raise Skip("条件 D/E 在数据中缺失")

    def contrast(vec: dict[str, float]) -> tuple[float, float]:
        L = np.array([vec.get(k, 0.0) for k in params.index])
        est = float(L @ params.to_numpy())
        se = float(np.sqrt(L @ cov.to_numpy() @ L))
        return est, se

    from scipy import stats as ss

    rows, pvals = [], []
    for label, vec in (
        ("D − C", {name_d: 1.0}),
        ("E − D", {name_e: 1.0, name_d: -1.0}),
    ):
        est, se = contrast(vec)
        z = est / se if se > 0 else float("nan")
        p = 2 * float(ss.norm.sf(abs(z)))
        rows.append([label, est, se, z, p])
        pvals.append(p)
    adj = holm(pvals)
    lines = [
        f"LMM: `{dv} ~ 条件 + 题目 + 顺序 + (1|被试)`"
        f"(N 被试 = {sub['participant_id'].nunique()},N 观测 = {len(sub)})",
        "",
        "| 对比 | 估计 | SE | z | p | p(Holm) |",
        "|---|---|---|---|---|---|",
    ]
    for (label, est, se, z, p), pa in zip(rows, adj):
        lines.append(f"| {label} | {est:.3f} | {se:.3f} | {z:.2f} | {p:.4f} | {pa:.4f} |")
    return "\n".join(lines)


def section_wilcoxon(df: pd.DataFrame, dv: str) -> str:
    from scipy import stats as ss

    wide = participant_means(df, dv)
    lines = ["Wilcoxon 符号秩(被试级条件均值,稳健性):", "",
             "| 对比 | n 配对 | W | p |", "|---|---|---|---|"]
    any_run = False
    for a, b in CONTRASTS:
        diffs = paired_diffs(wide, a, b)
        diffs = diffs[diffs != 0]
        if len(diffs) < 5:
            lines.append(f"| {a} − {b} | {len(diffs)} | — | 跳过(配对数 <5) |")
            continue
        w, p = ss.wilcoxon(diffs)
        lines.append(f"| {a} − {b} | {len(diffs)} | {w:.1f} | {p:.4f} |")
        any_run = True
    if not any_run:
        raise Skip("所有对比的配对被试数 <5")
    return "\n".join(lines)


def section_permutation(n_perm: int, seed: int) -> str:
    path = ANALYSIS_DIR / "pairwise_sims.json"
    if not path.exists():
        raise Skip("pairwise_sims.json 不存在(先跑 metrics.py)")
    sims_by_topic = json.loads(path.read_text(encoding="utf-8"))
    if not sims_by_topic:
        raise Skip("pairwise_sims.json 为空")
    lines = [f"组内多样性置换检验({n_perm} 次重排条件标签;统计量 = 跨题目平均多样性差):",
             "", "| 对比 | 观测差 | p | 参与题目数 |", "|---|---|---|---|"]
    any_run = False
    for a, b in CONTRASTS:
        r = permutation_diversity_test(sims_by_topic, a, b, n_perm=n_perm, seed=seed)
        if r["n_topics"] == 0:
            lines.append(f"| {a} − {b} | — | — | 跳过(每格 <2 份成稿) |")
            continue
        lines.append(f"| {a} − {b} | {r['observed']:.4f} | {r['p']:.4f} | {r['n_topics']} |")
        any_run = True
    if not any_run:
        raise Skip("没有任何题目在两个条件下都有 ≥2 份成稿")
    return "\n".join(lines)


def section_tost(df: pd.DataFrame, judge_path: Path, bound: float) -> str:
    dvs: list[tuple[str, pd.DataFrame, str]] = []
    if "t_net" in df.columns and df["t_net"].notna().any():
        dvs.append(("净创作时长 t_net", df, "t_net"))
    if judge_path.exists():
        recs = [json.loads(l) for l in judge_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows = [
            {"trial_id": r["trial_id"], "quality": float(np.mean(list(r["scores"].values())))}
            for r in recs if r.get("scores")
        ]
        if rows:
            jq = pd.DataFrame(rows).groupby("trial_id", as_index=False)["quality"].mean()
            merged = df.merge(jq, on="trial_id", how="inner")
            if not merged.empty:
                dvs.append(("质量(judge 四维均分)", merged, "quality"))
    if not dvs:
        raise Skip("既无 t_net 也无 judge 数据")
    lines = [f"TOST 等价检验(等价界 ±{bound} dz):", "",
             "| DV | 对比 | n | dz | p(TOST) | 等价? |", "|---|---|---|---|---|---|"]
    any_run = False
    for name, frame, dv_col in dvs:
        wide = participant_means(frame, dv_col)
        for a, b in CONTRASTS:
            diffs = paired_diffs(wide, a, b)
            r = tost_paired(diffs, bound)
            if not np.isfinite(r["p"]):
                lines.append(f"| {name} | {a} − {b} | {r['n']} | — | — | 跳过(n<3) |")
                continue
            lines.append(
                f"| {name} | {a} − {b} | {r['n']} | {r['dz']:.3f} "
                f"| {r['p']:.4f} | {'是' if r['equivalent'] else '否'} |"
            )
            any_run = True
    if not any_run:
        raise Skip("所有 DV × 对比的配对数都不足")
    return "\n".join(lines)


def section_dose_response(df: pd.DataFrame) -> str:
    import statsmodels.formula.api as smf

    sub = df.dropna(subset=["hlz_z", "edit_dist"]).copy()
    if len(sub) < 6 or sub["participant_id"].nunique() < 3:
        raise Skip(f"有编辑距离且有 HLZ 的观测不足(n={len(sub)})")
    # 被试内中心化的编辑距离
    sub["edit_c"] = sub["edit_dist"] - sub.groupby("participant_id")["edit_dist"].transform("mean")
    try:
        res = smf.mixedlm(
            "hlz_z ~ edit_c + C(condition)", data=sub, groups=sub["participant_id"]
        ).fit(reml=True)
        kind = "MixedLM(随机截距)"
    except Exception:  # noqa: BLE001 — 小样本下可能不收敛
        res = smf.ols("hlz_z ~ edit_c + C(condition)", data=sub).fit()
        kind = "OLS(MixedLM 不收敛,降级)"
    b = res.params.get("edit_c", float("nan"))
    p = res.pvalues.get("edit_c", float("nan"))
    lines = [
        f"剂量-反应回归({kind},n={len(sub)}):`HLZ ~ 编辑距离(被试内中心化) + 条件`",
        "",
        f"- 编辑距离斜率 b = {b:.3f},p = {p:.4f}",
    ]
    adj = df.dropna(subset=["hlz_z"])
    adj = adj[adj["adjudication"].notna()] if "adjudication" in df.columns else pd.DataFrame()
    if len(adj) >= 6 and adj["adjudication"].nunique() >= 2:
        means = adj.groupby("adjudication")["hlz_z"].agg(["mean", "count"])
        lines += ["", "E 条件裁决类别的 HLZ 均值:", "",
                  "| 裁决 | 均值 | n |", "|---|---|---|"]
        for k, r in means.iterrows():
            lines.append(f"| {k} | {r['mean']:.3f} | {int(r['count'])} |")
    return "\n".join(lines)


# ---------------- 报告组装 ----------------


def run(n_perm: int, seed: int, bound: float) -> None:
    per_trial_path = ANALYSIS_DIR / "metrics_per_trial.csv"
    if not per_trial_path.exists():
        sys.exit(f"{per_trial_path} 不存在 — 先跑 analysis/metrics.py")
    df = pd.read_csv(per_trial_path)

    sections: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    def attempt(title: str, fn, *args) -> None:
        try:
            sections.append((title, fn(*args)))
        except Skip as e:
            skipped.append((title, str(e)))
        except Exception as e:  # noqa: BLE001 — 单项失败不拖垮整个报告
            skipped.append((title, f"运行失败: {e!r}"))

    attempt("1. LMM(HLZ)", section_lmm, df, "hlz_z")
    attempt("2. Wilcoxon 稳健性(HLZ)", section_wilcoxon, df, "hlz_z")
    attempt("3. 组内多样性置换检验", section_permutation, n_perm, seed)
    attempt("4. TOST 等价检验", section_tost, df, ANALYSIS_DIR / "judge.jsonl", bound)
    attempt("5. 剂量-反应", section_dose_response, df)

    lines = [
        "# NovaStory 统计报告",
        "",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 输入: {per_trial_path.name}({len(df)} trials,"
        f"{df['participant_id'].nunique() if not df.empty else 0} 被试)",
        f"- 置换次数: {n_perm};TOST 等价界: ±{bound} dz",
        "",
    ]
    for title, body in sections:
        lines += [f"## {title}", "", body, ""]
    if skipped:
        lines += ["## 被跳过的检验", ""]
        lines += [f"- **{t}** — {reason}" for t, reason in skipped]
        lines.append("")

    out = ANALYSIS_DIR / "stats_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"完成 {len(sections)} 项检验,跳过 {len(skipped)} 项 → {out}")
    for t, reason in skipped:
        print(f"  跳过: {t} — {reason}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="统计分析:LMM / Wilcoxon / 置换 / TOST / 剂量-反应 → stats_report.md"
    )
    ap.add_argument("--n-perm", type=int, default=10000, help="置换次数(默认 10000)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tost-bound", type=float, default=0.4, help="TOST 等价界(dz)")
    args = ap.parse_args()
    run(args.n_perm, args.seed, args.tost_bound)


if __name__ == "__main__":
    main()
