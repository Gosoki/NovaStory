"""研究员后台的「回答倾向」面板 —— 同屏看每位被试在 C / D / E 里各题怎么答。

只读:数据走 `v3.load` / `v3.per_trial`(与分析同一纳入口径,不含 dev 与名单剔除的会话),
另从 participants 只取年龄档与入组日期。不显示任何被试原文、邮箱或续接 token。
"""
from __future__ import annotations

import json
import sqlite3

import streamlit as st

from core import db
from i18n import t

CONDS = ("C", "D", "E")
# 题目代码对应 q.<code> 的题目原文;own_mean 是 own1-3 的均分(所有权主终点)
ITEMS = ("own_mean", "own1", "own2", "own3", "soa1", "soa2",
         "imagine", "violation", "satisfaction", "tlx1")
E_ITEMS = ("ai_q_quality", "ai_q_amount")
BEHAVIOR = ("t_total", "pre_investment", "post_investment", "n_ai_rounds")
FS = ("fs_pref_cond", "fs_reuse_cond", "fs_closest_cond", "fs_effort_cond")
# 7 点色阶(PuOr):橙 = 低分、紫 = 高分,不带好坏含义(violation / tlx1 高 = 更违背 / 更累)
PALETTE = ("#b35806", "#f1a340", "#fee0b6", "#f7f7f7", "#d8daeb", "#998ec3", "#542788")


def _cell(v) -> str:
    """1–7 分的底色;差值视图先把差平移到 4 ± 3 再调用。"""
    try:
        i = min(7, max(1, round(float(v)))) - 1
    except (TypeError, ValueError):  # None / NaN
        return ""
    fg = "#fff" if i in (0, 5, 6) else "#111"
    return f"background-color: {PALETTE[i]}; color: {fg}"


def _fmt(col: str) -> str:
    base = col.split("·")[0].split(" ")[0]
    if base == "ai_q_helpful":
        return "{:.0%}"
    dec = 1 if base == "own_mean" else 0
    return f"{{:+.{dec}f}}" if "−" in col else f"{{:.{dec}f}}"


def _load():
    """(每人每条件一行的长表, 每人一行的被试表)。"""
    import pandas as pd

    from analysis import v3

    raw = v3.load(db.DB_PATH)
    if raw.empty:
        return None, None
    pt = v3.per_trial(raw)
    for col, key in (("ownership_json", ("own1", "own2", "own3")),
                     ("soa_json", ("soa1", "soa2")), ("tlx_json", ("tlx1",))):
        parsed = raw[col].map(lambda s: json.loads(s) if isinstance(s, str) and s else {})
        for k in key:
            raw[k] = parsed.map(lambda d, _k=k: d.get(_k))
    shots = raw["shot_annotations_json"].map(
        lambda s: [a.get("tag") for a in json.loads(s)] if isinstance(s, str) and s else [])
    for tag in ("mine", "ai_ok", "ai_against"):
        raw[f"shot_{tag}"] = shots.map(lambda ts, _t=tag: ts.count(_t) if ts else None)
    keys = ["participant_id", "round_idx"]
    extra = ["own1", "own2", "own3", "soa1", "soa2", "shot_mine", "shot_ai_ok", "shot_ai_against"]
    long = pt.merge(raw[keys + extra], on=keys, how="left")
    long[extra] = long[extra].apply(pd.to_numeric, errors="coerce")   # 缺答时 JSON 拆出来是 object 列
    long["ai_q_helpful"] = long["n_ai_q_helpful"] / long["g_n_questions"]

    con = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    try:
        meta = pd.read_sql("SELECT id AS participant_id, created_at, demographics_json FROM participants", con)
    finally:
        con.close()
    meta["age_idx"] = meta["demographics_json"].map(
        lambda s: (json.loads(s) if isinstance(s, str) and s else {}).get("age_idx"))
    meta["date"] = meta["created_at"].str[5:10]
    order = (long.sort_values("round_idx").groupby("participant_id")["condition"]
                 .agg(lambda s: "→".join(s)).rename("order"))
    first = long.drop_duplicates("participant_id").set_index("participant_id")
    pp = (first[["lang", "seq", "attention_ok", "fs_overall_sat", *FS]]
          .join(order).join(meta.set_index("participant_id")[["age_idx", "date"]]).reset_index())
    return long, pp


def render() -> None:
    import pandas as pd

    st.divider()
    st.subheader(t("tendency.title"))
    st.caption(t("tendency.caption"))
    # 研究员每天都要进这个页面做监控:默认不展开、不取数,免得日本采集期间顺手看到结果
    if not st.toggle(t("tendency.open"), key="_td_open"):
        return

    long, pp = _load()
    if long is None:
        st.info(t("tendency.empty"))
        return
    age_lab = {i: t(f"screening.age_opt{i + 1}") for i in range(6)}
    pp["age_key"] = pp["age_idx"].map(lambda i: int(i) if pd.notna(i) else -1)
    pp["age"] = pp["age_key"].map(lambda i: age_lab.get(i, "—"))

    # ---- 筛选 ----
    # 选项用语言代码 / 年龄档序号,不用译文 —— 译文作选项时一切换界面语言,已选值就对不上、全被清空
    f1, f2, f3 = st.columns(3)
    all_langs = sorted(pp["lang"].dropna().unique())
    langs = f1.multiselect(t("tendency.f_lang"), all_langs, key="_td_lang",
                           default=[x for x in all_langs if x != "ja"])   # ja 默认不显示(防偷看)
    drop_ages = f2.multiselect(t("tendency.f_age"), sorted(pp["age_key"].unique()), default=[],
                               format_func=lambda i: age_lab.get(i, "—"), key="_td_age")
    drop_attn = f3.checkbox(t("tendency.f_drop_attn"), key="_td_attn")
    blank_sl = f3.checkbox(t("tendency.f_blank_straight"), key="_td_sl")
    keep = pp["lang"].isin(langs) & ~pp["age_key"].isin(drop_ages)
    if drop_attn:
        keep &= pp["attention_ok"] != 0
    pp = pp[keep]
    long = long[long["participant_id"].isin(pp["participant_id"])].copy()
    if blank_sl:
        long.loc[long["straightline"] == 1, list(ITEMS)] = float("nan")
    st.caption(t("tendency.n_line", n=len(pp), trials=len(long)))
    if pp.empty:
        return

    # ---- 总览:每题三个条件的 1–7 分布 + 均值 ----
    st.markdown(f"**{t('tendency.dist_title')}**")
    _distribution(long)

    # ---- 逐人表 ----
    st.markdown(f"**{t('tendency.table_title')}**")
    views = {"single": t("tendency.view_single"), "side": t("tendency.view_side"),
             "diff": t("tendency.view_diff")}
    c_view, c_pick, c_beh = st.columns([2, 1, 1])
    view = c_view.radio(t("tendency.view"), list(views), format_func=views.get,
                    horizontal=True, key="_td_view")
    show_beh = c_beh.checkbox(t("tendency.show_behavior"), key="_td_beh")
    base = pp[["participant_id", "age", "date", "order"]]

    if view == "single":
        cond = c_pick.selectbox(t("tendency.cond"), CONDS, key="_td_cond")
        cols = ["round_idx", "topic", *ITEMS, *((*E_ITEMS, "ai_q_helpful") if cond == "E" else ()),
                "shot_mine", "shot_ai_ok", "shot_ai_against", "straightline",
                *(BEHAVIOR if show_beh else ())]
        tab = base.merge(long.loc[long["condition"] == cond, ["participant_id", *cols]],
                         on="participant_id", how="left")
        colored = [*ITEMS, *(E_ITEMS if cond == "E" else ())]
    elif view == "side":
        wide = long.pivot(index="participant_id", columns="condition",
                          values=[*ITEMS, *(BEHAVIOR if show_beh else ())])
        wide.columns = [f"{c}·{k}" for c, k in wide.columns]
        ordered = [f"{c}·{k}" for c in (*ITEMS, *(BEHAVIOR if show_beh else ())) for k in CONDS]
        tab = base.merge(wide[ordered].reset_index(), on="participant_id", how="left").merge(
            pp[["participant_id", *FS, "fs_overall_sat"]], on="participant_id", how="left")
        colored = [f"{c}·{k}" for c in ITEMS for k in CONDS] + ["fs_overall_sat"]
    else:
        pair = c_pick.selectbox(t("tendency.diff"), ("E−D", "E−C", "D−C"), key="_td_pair")
        a, b = pair.split("−")
        wide = long.pivot(index="participant_id", columns="condition", values=list(ITEMS))
        diff = pd.DataFrame({f"{c} {pair}": wide[(c, a)] - wide[(c, b)] for c in ITEMS})
        tab = base.merge(diff.reset_index(), on="participant_id", how="left")
        colored = []

    tab = tab.rename(columns={"participant_id": "pid"})
    sty = tab.style.map(_cell, subset=[c for c in colored if c in tab.columns])
    if view == "diff":
        sty = sty.map(lambda d: _cell(4 + max(-3, min(3, d))) if d == d else "",
                      subset=[c for c in tab.columns if "−" in c])
    sty = sty.format({c: _fmt(c) for c in tab.columns if pd.api.types.is_numeric_dtype(tab[c])},
                     na_rep="")
    st.dataframe(sty, hide_index=True, width="stretch", height=min(38 * (len(tab) + 1), 900))

    with st.expander(t("tendency.legend_title")):
        st.markdown(t("tendency.legend_colors"))
        for code in (*ITEMS[1:], *E_ITEMS):
            st.markdown(f"- `{code}`：{t(f'q.{code}')}")
        st.markdown(f"- `own_mean`：{t('tendency.legend_own_mean')}")
        st.markdown(f"- `shot_*`：{t('q.tag_mine')} / {t('q.tag_ai_ok')} / {t('q.tag_ai_against')}")
        st.markdown(t("tendency.legend_other"))


def _distribution(long) -> None:
    """每题一行:C / D / E 三条 100% 堆叠条(1–7 分的占比),右侧均值表。"""
    import altair as alt
    import pandas as pd

    items = [c for c in ITEMS if c != "own_mean"]
    dist = long.melt(id_vars=["participant_id", "condition"], value_vars=items,
                     var_name="item", value_name="score").dropna()
    dist["score"] = dist["score"].round().astype(int)
    chart = (alt.Chart(dist).mark_bar().encode(
        x=alt.X("count():Q", stack="normalize", title=None, axis=alt.Axis(format="%")),
        y=alt.Y("condition:N", title=None),
        color=alt.Color("score:O", scale=alt.Scale(domain=list(range(1, 8)), range=list(PALETTE)),
                        legend=alt.Legend(orient="top", title=None)),
        row=alt.Row("item:N", title=None, sort=items, header=alt.Header(labelAngle=0, labelAlign="left")),
        tooltip=["item", "condition", "score", "count()"],
    ).properties(height=54, width=420))
    c1, c2 = st.columns([3, 2])
    c1.altair_chart(chart)
    means = long.groupby("condition")[list(ITEMS)].mean().T.reindex(columns=list(CONDS))
    means["E−D"] = means["E"] - means["D"]
    means.index.name = "item"
    c2.dataframe(means.style.map(_cell, subset=list(CONDS)).format(precision=2, na_rep=""),
                 width="stretch")
    c2.caption(t("tendency.means_caption", n=long["participant_id"].nunique()))
