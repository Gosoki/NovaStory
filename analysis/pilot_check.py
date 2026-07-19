#!/usr/bin/env python
"""A6: 试测健康检查 —— 把「4 个生死问题」变成自动 🟢🟡🔴 读数(paper/16 后手手册的触发器)。

试测(几个人)数据一进 DB,跑 `make pilot` 立刻回答:
  ① D 地板效应   D 条件返工有没有空间(若≈0 → 招牌图「返工↓」落空)
  ② C 天花板     一发生成是否已贴合(若 C 已顶且 E≈C → 保真差检不出)
  ③ novice 占比  真新手比例(唯一区隔 APE 的人群卖点)
  ④ 量表信度     own α / soa 相关 / 中点应答方差压缩
每项给读数 + 旗标 + 触发的「后手」分支(详见 paper/16)。阈值为经验参考,正式阈值预注册。

用法: .venv/bin/python analysis/pilot_check.py [--db data/novastory.db]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import prereg, v3  # noqa: E402

DEFAULT_DB = ROOT / "data" / "novastory.db"
G, Y, R = "🟢", "🟡", "🔴"


def _flag(val, green, yellow, higher_better=True):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return Y
    if higher_better:
        return G if val >= green else (Y if val >= yellow else R)
    return G if val <= green else (Y if val <= yellow else R)


def cronbach_alpha(items: pd.DataFrame) -> float:
    items = items.dropna()
    k = items.shape[1]
    if k < 2 or len(items) < 3:
        return float("nan")
    iv = items.var(ddof=1, axis=0).sum()
    tv = items.sum(axis=1).var(ddof=1)
    return float((k / (k - 1)) * (1 - iv / tv)) if tv else float("nan")


def _items(df: pd.DataFrame, col: str, keys: list[str]) -> pd.DataFrame:
    def g(x, key):
        d = v3._loads(x, {})
        return d.get(key)
    return pd.DataFrame({key: df[col].map(lambda x: g(x, key)) for key in keys})


def check_d_floor(df: pd.DataFrame) -> None:
    d = df[df["condition"] == "D"]
    print(f"\n① D 地板效应(返工有没有空间)  n(D)={len(d)}")
    if not len(d):
        print(f"   {Y} 无 D 数据"); return
    nai = d["n_ai_rounds"].fillna(0)
    hec = d["hand_edit_chars"].fillna(0)
    zero = ((nai == 0) & (hec == 0)).mean()
    print(f"   n_ai_rounds 中位={nai.median():.1f}  手改字符中位={hec.median():.0f}  "
          f"t_postgen 中位={d['t_postgen'].median():.0f}s  零返工比例={zero:.0%}")
    fl = _flag(zero, prereg.D_FLOOR_ZERO_GREEN, prereg.D_FLOOR_ZERO_YELLOW, higher_better=False)
    print(f"   {fl}  {'返工充足' if fl==G else '返工偏少' if fl==Y else '地板!返工≈0'}")
    if fl == R:
        print("   → 后手A(paper/16):招牌叙事从「返工↓」移到「保真/所有权↑ + 努力再分配」"
              "(E 事前投入不依赖 D 返工空间);「新手被动接受」本身作发现,报 acceptance 率。")


def check_c_ceiling(df: pd.DataFrame) -> None:
    print("\n② C 天花板(一发生成是否已贴合)")
    g = df.groupby("condition")
    stat = g[["imagine_match", "intent_violation"]].agg(["mean", "std"])
    have = [c for c in ("C", "D", "E") if c in df["condition"].unique()]
    for c in have:
        im = df[df.condition == c]["imagine_match"]
        print(f"   {c}: imagine_match {im.mean():.2f}±{im.std():.2f}  "
              f"violation {df[df.condition==c]['intent_violation'].mean():.2f}")
    if "C" in have:
        cim = df[df.condition == "C"]["imagine_match"]
        eim = df[df.condition == "E"]["imagine_match"] if "E" in have else pd.Series(dtype=float)
        gap = (eim.mean() - cim.mean()) if len(eim) else np.nan
        ceil = cim.mean() >= prereg.C_CEIL_MEAN and cim.std() < prereg.C_CEIL_SD
        fl = R if (ceil and (np.isnan(gap) or gap < prereg.C_CEIL_GAP)) else (
            Y if cim.mean() >= prereg.C_CEIL_YELLOW_MEAN else G)
        print(f"   {fl}  C 均值={cim.mean():.2f} SD={cim.std():.2f}  E−C 差={gap:.2f}"
              if not np.isnan(gap) else f"   {fl}  C 均值={cim.mean():.2f} SD={cim.std():.2f}")
        if fl == R:
            print("   → 后手B(paper/16):embedding 保真降次要,逐镜头标注 + imagine_match 升主;"
                  "主张改「E 在保真不劣于 C、但所有权/努力再分配更优」(与灵魂句一致)。")


def check_novice(con: sqlite3.Connection) -> None:
    p = pd.read_sql("SELECT screening_json, status FROM participants", con)
    # exclude dev/test participants (#20): they are not real subjects
    p = p[~p["screening_json"].map(lambda x: bool(v3._loads(x, {}).get("dev")))]
    p = p[p["status"] == "done"] if "status" in p and (p["status"] == "done").any() else p
    print(f"\n③ novice 占比  N(完成)={len(p)}")
    if not len(p):
        print(f"   {Y} 无完成被试"); return
    isnov = p["screening_json"].map(lambda x: bool(v3._loads(x, {}).get("is_novice")))
    share = isnov.mean()
    fl = _flag(share, prereg.NOVICE_SHARE_GREEN, prereg.NOVICE_SHARE_YELLOW)
    print(f"   达标 novice = {isnov.sum()}/{len(p)} = {share:.0%}   {fl}")
    if fl != G:
        print("   → 后手C(paper/16):预注册把「仅 novice 子集」前置为主分析群体(非事后稳健性);"
              "占比不足则措辞从「novice-专属」弱化为「以 novice 为主体」+ 经验作调节。招募端加门槛。")


def check_reliability(df: pd.DataFrame) -> None:
    print("\n④ 量表信度")
    q1 = df.dropna(subset=["ownership_json"]).drop_duplicates(["participant_id", "round_idx"])
    own = _items(q1, "ownership_json", ["own1", "own2", "own3"]).apply(pd.to_numeric, errors="coerce")
    soa = _items(q1, "soa_json", ["soa1", "soa2"]).apply(pd.to_numeric, errors="coerce")
    a_own = cronbach_alpha(own)
    r_soa = soa.dropna().corr().iloc[0, 1] if soa.dropna().shape[0] >= 3 else float("nan")
    allv = pd.to_numeric(
        pd.Series(pd.concat([own, soa], axis=1).values.ravel()), errors="coerce"
    ).dropna().to_numpy()
    mid = float((allv == 4).mean()) if len(allv) else float("nan")
    print(f"   own1-3 Cronbach α={a_own:.2f}   soa1-2 相关 r={r_soa:.2f}   "
          f"中点(4)应答比={mid:.0%}   总方差 SD={np.nanstd(allv):.2f}")
    fl = _flag(min([x for x in (a_own, r_soa) if not np.isnan(x)] or [np.nan]),
               prereg.RELIABILITY_GREEN, prereg.RELIABILITY_YELLOW)
    print(f"   {fl}  {'信度良好' if fl==G else '信度勉强' if fl==Y else '信度崩!'}")
    if fl == R or (not np.isnan(a_own) and a_own < prereg.OWN_ALPHA_FLOOR):
        print("   → 后手D(paper/16):所有权主终点改用已验证的 J-SoAS SoPA(soa),own 降次要并报;"
              "所有分析用被试内差分(消中点应答偏差);own 若 α 崩考虑补第 4 题。")


def run(db_path: Path = DEFAULT_DB) -> None:
    """跑完 4 项检查并 print 报告(供 CLI 与网站研究员面板共用)。"""
    df = v3.load(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        n = df.groupby("condition").size().to_dict() if len(df) else {}
        print("=" * 56)
        print(f"试测健康检查 · 4 个生死问题   trials 各条件: {n}")
        print("=" * 56)
        check_d_floor(df)
        check_c_ceiling(df)
        check_novice(con)
        check_reliability(df)
    finally:
        con.close()
    print("\n" + "=" * 56)
    print("旗标:🟢 放行 / 🟡 留意 / 🔴 触发后手(见 paper/16 试测决策树)。"
          "\n阈值为经验参考;正式 go/no-go 阈值预注册前锁定。")


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 试测健康检查(4 生死问题)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    run(ap.parse_args().db)


if __name__ == "__main__":
    main()
