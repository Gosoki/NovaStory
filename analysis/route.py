"""论文主推 E 还是 F(2026-11-03,docs/paper/06 ㉛)—— 只执行 prereg.PROMOTION_RULE,不改任何数字。

规则在任何日本数据进库之前写死,不看结果再挑:
  主推 F ⇔ F_PRIMARY 判定「显著:F 高于 E」,且 own_mean 的 F−E 没有显著为负
  主推 E ⇔ 其余一切情况(E 是本来的提案);宣称的强弱按方向复核 P1–P3 成功的条数分三档(≥2 / 1 / 0)
日本不满一整轮时路线照走,日本侧的宣称一律加「探索的」。

日本数据进库后:make stats-f stats-ja-pred route → 按这里打印的路线合并 pr/主推E 或 pr/主推F,
把数字填进分支留的〔〕,另一条分支删掉。

用法:  .venv/bin/python analysis/route.py [--csv data/analysis/v3_per_trial.csv] [--db data/novastory.db]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import prereg  # noqa: E402
from analysis.f_analysis import CSV, DB, paired, verdict  # noqa: E402
from analysis.ja_predictions import dist_base, one_sided  # noqa: E402
from core import config  # noqa: E402


def decide_route(prim: dict | None, own: dict | None, n_replicated: int,
                 alpha: float = prereg.ALPHA) -> tuple[str, str | None, str]:
    """(路线 "E" / "F", E 的宣称档位 strong / partial / design_only(F 时为 None), 理由)。纯函数,便于单测。"""
    f_wins = bool(prim and "p_t" in prim and prim["p_t"] < alpha and prim["mean"] > 0)
    own_loss = bool(own and "p_t" in own and own["p_t"] < alpha and own["mean"] < 0)
    if f_wins and not own_loss:
        return "F", None, "F_PRIMARY 显著 F 高于 E,且 own_mean F−E 没有显著为负"
    why = ("F 赢了想法来源归属,但 own_mean F−E 显著为负" if f_wins else
           "F_PRIMARY 没有判出「F 高于 E」(不显著 / E 高于 F / 数据不足)")
    tier = "strong" if n_replicated >= 2 else "partial" if n_replicated == 1 else "design_only"
    return "E", tier, why


def replications(df: pd.DataFrame, db_path: Path) -> list[tuple[str, bool | None]]:
    """P1–P3 各自复核成功与否(None = 数据不足)。定义同 analysis/ja_predictions.py。"""
    cde = df[df["condition"].isin(prereg.CONFIRMATORY_CONDITIONS)]
    if db_path.exists():
        d, miss = dist_base(cde, db_path)
        cde = cde.merge(d, on=["participant_id", "round_idx"], how="left")
        if miss:
            print(f"  ⚠️ embedding 缓存缺 {miss} 条向量(终稿或基线)→ P1 只用到有向量的 trial;"
                  "补齐先跑 make embed(需授权调 OpenAI)")
    out = []
    for p in prereg.JA_DIRECTION_PREDICTIONS:
        rs = [one_sided(cde, p["dv"], a, b) if p["dv"] in cde else None for a, b in p["contrasts"]]
        out.append((p["id"], None if any(not r or "p" not in r for r in rs)
                    else all(r["p"] < prereg.ALPHA for r in rs)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, default=CSV)
    ap.add_argument("--db", type=Path, default=DB)
    args = ap.parse_args(argv)
    rule = prereg.PROMOTION_RULE
    print("=" * 78)
    print("论文主推 E 还是 F(prereg.PROMOTION_RULE,2026-11-03 写死;06 ㉛)")
    print(f"  主推 F ⇔ {rule['F']}")
    print(f"  主推 E ⇔ {rule['E']}")
    print("=" * 78)

    raw = pd.read_csv(args.csv)
    langs = sorted(config.FOUR_COND_COHORTS)   # = 日本队列(变量名沿用;06 ㉞ 起队列按招募地,不按界面语言)
    coh = raw["cohort"] if "cohort" in raw else raw.get("lang")   # 旧 CSV 没有 cohort 列就用 lang
    df = raw[coh.isin(langs)] if coh is not None else raw.iloc[0:0]
    have_f = set(df.loc[df["condition"] == prereg.F_CONDITION, "participant_id"])
    df = df[df["participant_id"].isin(have_f)]
    if not have_f:
        print("⛔ 还没有日本 F 的数据 —— 规则已写死,等数据进库后再跑(make v3 events embed 之后)。")
        return 1

    n_round = prereg.WILLIAMS_ROUND_N_BY_LANG.get(langs[0], 16)
    covered = (pd.to_numeric(df.drop_duplicates("participant_id")["seq"], errors="coerce").dropna()
               .astype(int).nunique() if "seq" in df else 0)
    explor = covered < n_round
    prim = paired(df, prereg.F_PRIMARY["dv"], "F", "E")
    own = paired(df, "own_mean", "F", "E")
    reps = replications(df, args.db)
    n_rep = sum(ok is True for _, ok in reps)
    route, tier, why = decide_route(prim, own, n_rep)

    print(f"\n日本 {len(have_f)} 人 · 覆盖 {covered}/{n_round} 个序列" + ("(不满一整轮 → 日本侧宣称加「探索的」)" if explor else ""))
    print(f"  F_PRIMARY mine_ratio F−E:{verdict(prim, prereg.F_PRIMARY['sesoi'], prereg.F_PRIMARY['alpha'])}")
    if own and "mean" in own:
        print(f"  own_mean F−E:差 {own['mean']:+.3f}  95%CI[{own['ci_lo']:+.3f}, {own['ci_hi']:+.3f}]  p={own['p_t']:.4f}")
    print("  方向复核:" + " · ".join(f"{i} " + ("成功" if ok else "数据不足" if ok is None else "未成功")
                                    for i, ok in reps))
    if any(ok is None for _, ok in reps):
        print("  ⚠️ 有复核项数据不足(P1 要先跑 make embed)→ 「数据不足」不算复核成功,下面 E 的宣称档位是**下界**")

    branch = rule["branches"][route]
    print(f"\n→ 路线:**主推 {route}**({why})")
    if tier:
        print(f"  宣称档位:{tier} = {rule['e_tiers'][tier]}")
    print(f"  合并:git merge {branch}  →  填分支里留的〔〕数字  →  git branch -D {rule['branches']['F' if route == 'E' else 'E']}")
    print("  ⚠️ 无论哪条路线:中国的确证结论、F_PRIMARY、P1–P3、F−D、提问价值、制作負担全部照报;"
          "「主推」不等于宣称「更好」(14 §7、01 §3.2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
