from __future__ import annotations

import streamlit as st

from i18n import AVAILABLE_LANGS, LANG_LABELS, t

_BY_LABEL = {LANG_LABELS[c]: c for c in AVAILABLE_LANGS}


def _apply(widget_key: str) -> None:
    sel = st.session_state.get(widget_key)
    if sel in _BY_LABEL:
        st.session_state["lang"] = _BY_LABEL[sel]


def language_radio(widget_key: str, *, collapsed: bool = False) -> None:
    """Language picker shared by the consent page (subject, before agreeing) and
    the admin tools (JP6).

    Writes the plain `lang` session value — no widget is keyed "lang", so a
    picker unmounting on the next page never clears the language. Label options
    (not format_func) keep it test-drivable; on_change means that if two pickers
    render at once (admin on the consent page) only the clicked one updates,
    avoiding a rerun fight.
    """
    cur = st.session_state.get("lang", AVAILABLE_LANGS[0])
    st.radio(
        t("sidebar.language"),
        [LANG_LABELS[c] for c in AVAILABLE_LANGS],
        index=AVAILABLE_LANGS.index(cur) if cur in AVAILABLE_LANGS else 0,
        horizontal=True,
        label_visibility="collapsed" if collapsed else "visible",
        key=widget_key,
        on_change=_apply,
        args=(widget_key,),
    )
