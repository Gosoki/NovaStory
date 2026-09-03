from __future__ import annotations

import streamlit as st

from i18n import AVAILABLE_LANGS, DEFAULT_LANG, LANG_LABELS, t

_CODE_BY_LABEL = {LANG_LABELS[c]: c for c in AVAILABLE_LANGS}   # 显示标签 → 语言码


def _commit_lang_choice(widget_key: str) -> None:
    """把单选控件里选中的标签写成本次会话的语言(整个 i18n 的唯一写入点)。"""
    sel = st.session_state.get(widget_key)
    if sel in _CODE_BY_LABEL:
        st.session_state["lang"] = _CODE_BY_LABEL[sel]


def language_radio(widget_key: str, *, collapsed: bool = False) -> None:
    """Language picker shared by the consent page (subject, before agreeing) and
    the admin tools (JP6).

    Writes the plain `lang` session value — no widget is keyed "lang", so a
    picker unmounting on the next page never clears the language. Label options
    (not format_func) keep it test-drivable; on_change means that if two pickers
    render at once (admin on the consent page) only the clicked one updates,
    avoiding a rerun fight.

    All three (ja/zh/en) are fully wired end-to-end — UI, LLM prompts
    (prompts._norm passes each through), topics, and the shot parser — so any
    pick yields a single-language session.
    """
    cur = st.session_state.get("lang", DEFAULT_LANG)
    langs = AVAILABLE_LANGS
    st.radio(
        t("sidebar.language"),
        [LANG_LABELS[c] for c in langs],
        index=langs.index(cur) if cur in langs else 0,
        horizontal=True,
        label_visibility="collapsed" if collapsed else "visible",
        key=widget_key,
        on_change=_commit_lang_choice,
        args=(widget_key,),
    )
