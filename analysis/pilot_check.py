#!/usr/bin/env python
"""A6: 试测健康检查 —— 把「3 个生死问题 + 1 个描述项」变成自动 🟢🟡🔴 读数(docs/paper/05 §2 后手手册的触发器)。

试测(几个人)数据一进 DB,跑 `make pilot` 立刻回答:
  ① D 地板效应   D 条件返工有没有空间(若≈0 → 招牌图「返工↓」落空)
  ② C 天花板     一发生成是否已贴合(若 C 已顶且 E≈C → 保真差检不出)
  ③ novice 占比  真新手比例 —— **2026-09-07 起不再是生死问题**(主分析人群已是全样本);
                 保留为描述项:样本经验构成 + 事后探索性切分还有没有解释力
  ④ 量表信度     own α / soa 相关 / 中点应答方差压缩
每项给读数 + 旗标 + 触发的「后手」分支(详见 docs/paper/05 §2)。阈值在采数前冻结于 analysis/prereg.py(内部冻结,非第三方预注册)。

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


def _flag(val, green_thr, yellow_thr, *, higher_better=True):
    """读数 → 🟢🟡🔴;两个参数是**阈值**,不是颜色。"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return Y
    if higher_better:
        return G if val >= green_thr else (Y if val >= yellow_thr else R)
    return G if val <= green_thr else (Y if val <= yellow_thr else R)


def cronbach_alpha(items: pd.DataFrame) -> float:
    items = items.dropna()
    k = items.shape[1]
    if k < 2 or len(items) < 3:
        return float("nan")
    iv = items.var(ddof=1, axis=0).sum()
    tv = items.sum(axis=1).var(ddof=1)
    return float((k / (k - 1)) * (1 - iv / tv)) if tv else float("nan")


def _likert_items(df: pd.DataFrame, col: str, keys: list[str]) -> pd.DataFrame:
    """从一个 JSON 列里把指定 key 抽成一张 Likert 题项表(行=trial,列=题)。"""
    def g(x, key):
        d = v3._loads(x, {})
        return d.get(key)
    return pd.DataFrame({key: df[col].map(lambda x: g(x, key)) for key in keys})


def check_d_floor(df: pd.DataFrame) -> None:
    d = df[df["condition"] == "D"]
    print(f"\n① D 地板效应(返工有没有空间)  n(D)={len(d)}")
    if not len(d):
        print(f"   {Y} 无 D 数据"); return
    # 只在**有返工数据**的行上算零返工比例:fillna(0) 会把「缺数据」当成「没返工」,把读数推向 🔴
    # 地板 → 触发后手 A(招牌叙事整体搬家)。缺失与零是两件事,这条闸门尤其不能混。
    has = d["n_ai_rounds"].notna() | d["hand_edit_chars"].notna()
    nai = d.loc[has, "n_ai_rounds"].fillna(0)
    hec = d.loc[has, "hand_edit_chars"].fillna(0)
    zero = ((nai == 0) & (hec == 0)).mean() if has.any() else float("nan")
    print(f"   n_ai_rounds 中位={nai.median():.1f}  手改字符中位={hec.median():.0f}  "
          f"t_postgen 中位={d['t_postgen'].median():.0f}s  零返工比例={zero:.0%}(有数据的 {int(has.sum())} 行)")
    fl = _flag(zero, prereg.D_FLOOR_ZERO_GREEN, prereg.D_FLOOR_ZERO_YELLOW, higher_better=False)
    print(f"   {fl}  {'返工充足' if fl==G else '返工偏少' if fl==Y else '地板!返工≈0'}")
    if fl == R:
        print("   → 后手A(docs/paper/05):招牌叙事从「返工↓」移到「保真/所有权↑ + 努力再分配」"
              "(E 事前投入不依赖 D 返工空间);「新手被动接受」本身作发现,报 acceptance 率。")


def check_c_ceiling(df: pd.DataFrame) -> None:
    print("\n② C 天花板(一发生成是否已贴合)")
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
            print("   → 后手B(docs/paper/05):embedding 保真降次要,逐镜头标注 + imagine_match 升主;"
                  "主张改「E 在保真不劣于 C、但所有权/努力再分配更优」(与灵魂句一致)。")


def check_novice(con: sqlite3.Connection, db_path: Path) -> None:
    # 口径必须与 ①②④ 一致:统一走 v3.included_participants(问卷 3 件基准)。
    # 此前这里单独用 `status == "done"`,于是同一份报告里 ③ 的分母与其余三项不同。
    # ③ 2026-09-07 起已不是决策闸门(见文件头),但分母仍须与其余三项同源,
    # 否则同一份报告里两个「N(纳入)」对不上,读的人会以为数据有问题。
    p = pd.read_sql("SELECT id, screening_json FROM participants", con)
    p = p[p["id"].isin(v3.included_participants(db_path))]
    print(f"\n③ novice 占比  N(纳入)={len(p)}")
    if not len(p):
        print(f"   {Y} 无完成被试"); return
    # 与 v3.load 一致:从原始 5 项**重算**,不信任入库时写下的布尔
    isnov = p["screening_json"].map(lambda x: prereg.is_novice(v3._loads(x, {})))
    share = isnov.mean()
    fl = _flag(share, prereg.NOVICE_SHARE_GREEN, prereg.NOVICE_SHARE_YELLOW)
    print(f"   达标 novice = {isnov.sum()}/{len(p)} = {share:.0%}   {fl}")
    if fl != G:
        print("   → 2026-09-07 人群反转后这**不再是 go/no-go**:主分析人群已是全样本,"
              "占比低不影响主分析可行性,也不需要收紧招募。")
        print("     它只影响**事后探索性**切分的解释力 —— 子集 N 太小时,"
              "改用连续经验度作调节而非二分子集,并在论文里标 exploratory。")


def check_reliability(df: pd.DataFrame) -> None:
    print("\n④ 量表信度")
    q1 = df.dropna(subset=["ownership_json"]).drop_duplicates(["participant_id", "round_idx"])
    own = _likert_items(q1, "ownership_json", ["own1", "own2", "own3"]).apply(pd.to_numeric, errors="coerce")
    soa = _likert_items(q1, "soa_json", ["soa1", "soa2"]).apply(pd.to_numeric, errors="coerce")
    a_own = cronbach_alpha(own)
    r_soa = soa.dropna().corr().iloc[0, 1] if soa.dropna().shape[0] >= 3 else float("nan")
    allv = pd.to_numeric(
        pd.Series(pd.concat([own, soa], axis=1).values.ravel()), errors="coerce"
    ).dropna().to_numpy()
    mid_point = (prereg.LIKERT_POINTS + 1) // 2
    mid = float((allv == mid_point).mean()) if len(allv) else float("nan")
    print(f"   own1-3 Cronbach α={a_own:.2f}   soa1-2 相关 r={r_soa:.2f}   "
          f"中点({mid_point})应答比={mid:.0%}   总方差 SD={np.nanstd(allv):.2f}")
    # own(3 题 Cronbach α)与 soa(2 题 Pearson r)分开判:两个量的抽样分布不同,以前 min(α, r)
    # 套同一组阈值,r 在 0.3-0.6 这种常见值会把 ④ 判成 🔴、触发后手 D 换主终点。
    fl_own = _flag(a_own, prereg.RELIABILITY_GREEN, prereg.RELIABILITY_YELLOW)
    fl_soa = _flag(r_soa, prereg.SOA_R_GREEN, prereg.SOA_R_YELLOW)
    fl = R if R in (fl_own, fl_soa) else (Y if Y in (fl_own, fl_soa) else G)
    print(f"   own α {fl_own}   soa r {fl_soa}   → {fl}  {'信度良好' if fl==G else '信度勉强' if fl==Y else '信度崩!'}")
    # 后手 D 的门只看 own 的 α:soa 自己红了却去「改用 soa」是把主终点换成恰好更弱的那把尺
    if fl_own == R or (not np.isnan(a_own) and a_own < prereg.OWN_ALPHA_FLOOR):
        print("   → 后手D(docs/paper/05):所有权主终点改用已验证的 J-SoAS SoPA(soa),own 降次要并报;"
              "所有分析用被试内差分(消中点应答偏差);own 若 α 崩考虑补第 4 题。")
    if fl_soa == R:
        print("   ⚠️ soa1-2 相关过低:SoPA 两题在本样本上不一致,**不适合**作为后手 D 的替代主终点;own 维持主终点。")


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
        check_novice(con, db_path)
        check_reliability(df)
        n_mis = int(df["q_trial_mismatch"].sum()) if "q_trial_mismatch" in df else 0
        if n_mis:
            print(f"\n⚠️ {n_mis} 行问卷评的不是现行终稿(另一会话重做过这一轮,q_trial_mismatch)—— 主分析前须决定剔除或改用被评的那版")
    finally:
        con.close()
    print("\n" + "=" * 56)
    print("旗标:🟢 放行 / 🟡 留意 / 🔴 触发后手(见 docs/paper/05 试测决策树)。"
          "\n阈值来自 analysis/prereg.py(采数前冻结,锁定后不改)。")


def main() -> None:
    ap = argparse.ArgumentParser(description="A6 试测健康检查(4 生死问题)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    run(ap.parse_args().db)


if __name__ == "__main__":
    main()
