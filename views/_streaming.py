from __future__ import annotations

from typing import Optional

import streamlit as st

from core import llm
from i18n import t


def stream_llm(system: str, user: str, *, group: str) -> Optional[str]:
    """Render endpoint info, stream tokens via st.write_stream, log lifecycle.

    Returns the full string on success, None on config/call error (already shown via st.error).
    Side effects (logged to data/llm.log): start / first_token / done / error.
    """
    try:
        llm._client()  # early config check
    except llm.LLMConfigError:
        st.error(t("errors.no_api_key"))
        return None

    status = st.status(t("common.loading"), expanded=True)
    status.write(
        t("llm.endpoint", name=st.session_state.get("api_preset_name", "—"))
    )
    status.caption(t("llm.log_hint"))

    try:
        out = st.write_stream(
            llm.generate_stream(
                system,
                user,
                group=group,
                user_id=st.session_state.get("subject_id", ""),
            )
        )
    except llm.LLMCallError as e:
        status.update(label=t("llm.failed"), state="error")
        st.error(t("errors.llm_failed", error=str(e)))
        return None

    status.update(label=t("llm.completed"), state="complete")
    return out if isinstance(out, str) else "".join(out)
