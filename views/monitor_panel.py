"""研究员后台的「实验数据管理 · 监控」面板 —— 概览卡片 + 采数进度/平衡统计图。
用 Streamlit 原生图(浏览器渲染,日语标签不糊)。仅研究员可见。"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from core import db
from i18n import t

_TARGET_N = 36


def _cond_label(c: str) -> str:
    return t(f"monitor.cond_{c}") if c in ("C", "D", "E") else c


def _loads(x) -> dict:
    if not isinstance(x, str) or not x.strip():
        return {}
    try:
        return json.loads(x)
    except (ValueError, TypeError):
        return {}


def _topic_title(x) -> str:
    t = _loads(x).get("title", "")
    if isinstance(t, dict):
        return t.get("ja") or t.get("zh") or "?"
    return t or "?"


def _novice_share(parts: pd.DataFrame):
    if "screening_json" not in parts:
        return float("nan")
    done = parts[parts.get("status") == "done"] if "status" in parts else parts
    if done.empty:
        return float("nan")
    nov = done["screening_json"].map(lambda x: bool(_loads(x).get("is_novice")))
    return nov.mean()


def render() -> None:
    st.subheader(t("monitor.title"))
    parts = db.load_table("participants")
    trials = db.load_table("trials")
    if parts.empty:
        st.info(t("monitor.empty"))
        return

    _overview(parts)
    _balance(trials)
    _progress(parts)
    _descriptive(trials)


def _overview(parts: pd.DataFrame) -> None:
    status = parts["status"].value_counts() if "status" in parts else pd.Series(dtype=int)
    done, inprog, out = (int(status.get(k, 0)) for k in ("done", "in_progress", "screened_out"))
    nov = _novice_share(parts)
    with st.container(border=True):
        c = st.columns(4)
        c[0].metric(t("monitor.done"), done)
        c[1].metric(t("monitor.inprog"), inprog)
        c[2].metric(t("monitor.out"), out)
        c[3].metric(t("monitor.novice_share"), "—" if nov != nov else f"{nov:.0%}")
        st.progress(min(done / _TARGET_N, 1.0),
                    text=t("monitor.progress", n=_TARGET_N, done=done,
                           remain=max(_TARGET_N - done, 0)))


def _balance(trials: pd.DataFrame) -> None:
    if trials.empty:
        return
    col_topic, col_cond = t("monitor.col_topic"), t("monitor.col_cond")
    df = trials.copy()
    df[col_topic] = df["topic_json"].map(_topic_title)
    df[col_cond] = df["condition"].map(_cond_label)
    piv = df.pivot_table(index=col_cond, columns=col_topic, values="participant_id",
                         aggfunc="count", fill_value=0)
    st.markdown(t("monitor.balance_title"))
    try:
        st.dataframe(piv.style.background_gradient(cmap="Greens", axis=None), width="stretch")
    except Exception:  # noqa: BLE001
        st.dataframe(piv, width="stretch")


def _progress(parts: pd.DataFrame) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(t("monitor.seq_title"))
        if "seq" in parts:
            real = parts[parts.get("status") != "screened_out"] if "status" in parts else parts
            seqc = real["seq"].dropna().astype(int).value_counts().sort_index()
            seqc.index = seqc.index.map(lambda i: f"seq{i}")
            st.bar_chart(seqc, height=220) if len(seqc) else st.caption(t("monitor.none"))
        else:
            st.caption(t("monitor.no_seq"))
    with c2:
        st.markdown(t("monitor.daily_title"))
        if "created_at" in parts and (parts.get("status") == "done").any():
            date_col = t("monitor.col_date")
            d = parts[parts["status"] == "done"].copy()
            d[date_col] = pd.to_datetime(d["created_at"], errors="coerce").dt.date
            daily = d.dropna(subset=[date_col]).groupby(date_col).size()
            st.bar_chart(daily, height=220) if len(daily) else st.caption(t("monitor.none"))
        else:
            st.caption(t("monitor.no_done"))


def _descriptive(trials: pd.DataFrame) -> None:
    quest = db.load_table("questionnaires")
    if quest.empty or trials.empty:
        return
    cond = trials[["participant_id", "round_idx", "condition"]]
    q = quest.merge(cond, on=["participant_id", "round_idx"], how="left").dropna(subset=["condition"])
    if q.empty:
        return

    def _own(x):
        d = _loads(x)
        vs = [d.get(f"own{i}") for i in (1, 2, 3) if d.get(f"own{i}") is not None]
        return sum(vs) / len(vs) if vs else None

    own_col = t("monitor.ownership")
    if "ownership_json" in q:
        q[own_col] = q["ownership_json"].map(_own)
    cols = [c for c in ("satisfaction", "imagine_match", own_col) if c in q]
    if not cols:
        return
    agg = q.groupby("condition")[cols].mean(numeric_only=True)
    agg = agg.rename(columns={"satisfaction": t("monitor.satisfaction"),
                              "imagine_match": t("monitor.imagine")})
    agg.index = agg.index.map(_cond_label)
    st.markdown(t("monitor.descriptive_title"))
    st.bar_chart(agg, height=260)
