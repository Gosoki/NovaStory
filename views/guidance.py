from __future__ import annotations

import streamlit as st

from core import config, prompts, state
from i18n import get_lang, t
from views import _postgen
from views._streaming import call_llm_json, stream_llm

# Guided elicitation (condition E): one questionnaire component serves both
# round 1 (3 fixed dims + 2-4 AI supplements) and user-triggered follow-up
# rounds (1-3 questions on the current draft). Questions are shown one at a
# time (AskUserQuestion-style); answers are submitted together at the end.

def _AI_DECIDE() -> str:
    """The localized "leave it to the AI" option label (also its stored value)."""
    return t("guidance.ai_decide")


def begin_round(source: str) -> None:
    """source: "fixed3+ai_supplement" (round 1) | "ai_from_draft" (follow-up)."""
    st.session_state["r_g_source"] = source
    st.session_state["r_g_questions"] = []
    st.session_state["r_g_answers"] = {}
    st.session_state["r_g_idx"] = 0
    st.session_state["r_g_fallback"] = False
    for k in list(st.session_state.keys()):
        if k.startswith("_g_"):
            st.session_state.pop(k, None)
    st.session_state["r_phase"] = "guidance"


def render(topic: dict) -> None:
    if not st.session_state["r_g_questions"]:
        _generate_questions(topic)
        if not st.session_state["r_g_questions"]:
            # transient call/config error: offer an explicit retry (never hard-stuck)
            if st.button(t("round.retry"), type="primary", width="stretch"):
                st.rerun()
            # Follow-up rounds have a draft to fall back to — offer the same
            # escape hatch as below, or a persistently busy gateway locks the
            # participant on this screen with nothing but a retry button.
            if st.session_state["r_versions"] and st.button(t("guidance.cancel"), width="stretch"):
                st.session_state["r_phase"] = "postgen"
                st.rerun()
            return

    qs = st.session_state["r_g_questions"]
    idx = st.session_state["r_g_idx"]
    q = qs[idx]
    rnd = len(st.session_state["r_guidance_rounds"]) + 1

    st.subheader(t("guidance.title"))
    st.caption(t("guidance.progress", i=idx + 1, n=len(qs)))
    st.markdown(f"**{q['question']}**")
    if q.get("why"):
        st.caption(q["why"])

    saved = st.session_state["r_g_answers"].get(idx, {})
    options = list(q.get("options") or [])
    if options:
        # The "leave it to the AI" choice is a real localized option (not a
        # format_func sentinel): segmented_control + format_func is not reliably
        # AppTest-drivable across reruns. _AI_DECIDE() detects it on read.
        opt_key = f"_g_opt_{rnd}_{idx}"
        opts = options + [_AI_DECIDE()]
        # Restore a prior choice when this question is re-mounted (Streamlit
        # cleared its widget key while it was off-screen during pagination).
        if opt_key not in st.session_state and saved.get("opt") in opts:
            st.session_state[opt_key] = saved["opt"]
        st.segmented_control(
            t("guidance.pick_label"), opts,
            selection_mode="single",
            key=opt_key,
            label_visibility="collapsed",
        )
    cust_key = f"_g_custom_{rnd}_{idx}"
    if cust_key not in st.session_state and saved.get("custom"):
        st.session_state[cust_key] = saved["custom"]
    st.text_input(t("guidance.custom_label"), key=cust_key)

    cols = st.columns(2)
    if idx > 0 and cols[0].button(t("guidance.prev"), width="stretch"):
        _save_current(rnd, idx)
        st.session_state["r_g_idx"] -= 1
        st.rerun()
    last = idx == len(qs) - 1
    label = t("guidance.finish") if last else t("guidance.next")
    if cols[1].button(label, type="primary", width="stretch"):
        if not _answered(rnd, idx, q):
            st.error(t("errors.answer_or_skip"))
            return
        _save_current(rnd, idx)
        if last:
            _finish_round(topic, rnd)
        else:
            st.session_state["r_g_idx"] += 1
            st.rerun()

    # Follow-up guidance (a draft already exists): always offer an escape hatch
    # back to the draft, so a failing/looping final generation can't trap the
    # user on the guidance screen with no way to submit their existing script.
    if st.session_state["r_versions"] and st.button(t("guidance.cancel"), width="stretch"):
        st.session_state["r_phase"] = "postgen"
        st.rerun()


def _answered(rnd: int, idx: int, q: dict) -> bool:
    custom = (st.session_state.get(f"_g_custom_{rnd}_{idx}") or "").strip()
    chosen = st.session_state.get(f"_g_opt_{rnd}_{idx}")
    if not q.get("options"):
        # Open/fallback question (no options, no "leave it to the AI" choice):
        # require typed text, else an empty answer would be recorded as a valid
        # guidance round and pollute condition E's core IV.
        return bool(custom)
    return bool(custom) or chosen is not None


def _save_current(rnd: int, idx: int) -> None:
    """Persist the mounted question's answer to a non-widget store; Streamlit
    clears a widget's key once it stops being rendered (paginated questions)."""
    opt = st.session_state.get(f"_g_opt_{rnd}_{idx}")
    st.session_state["r_g_answers"][idx] = {
        "opt": opt,
        "custom": (st.session_state.get(f"_g_custom_{rnd}_{idx}") or ""),
        # Freeze "leave it to the AI" as a language-invariant flag at save time:
        # the option label is localized, so re-comparing the stored string later
        # (e.g. after a language switch) is unreliable.
        "ai_decided": opt == _AI_DECIDE(),
    }
    # Per-question timing anchor (log batch 7-02): with ms timestamps, the gap
    # between consecutive saves ≈ time spent on each question (dose analysis).
    state.log_event("guidance_answer_saved", {"round": rnd, "q": idx})


def _generate_questions(topic: dict) -> None:
    source = st.session_state["r_g_source"]
    intent = st.session_state["r_intent"]
    lang = get_lang()
    if source == "fixed3+ai_supplement":
        system = prompts.build_system_guidance_round1(lang)
        user = prompts.build_user_guidance_round1(topic, intent, lang)
        group = "E-guidance-r1"
    else:
        system = prompts.build_system_guidance_followup(lang)
        user = prompts.build_user_guidance_followup(topic, intent, state.current_script(), lang)
        group = "E-guidance-fu"

    data = call_llm_json(system, user, group=group)
    if data == "RETRY":
        return  # transient failure; render() will show a retry button
    qs = _validate(data)
    if qs is None:
        # Degrade: one open question, free text only (paper/7 §2).
        qs = [{
            "dimension": "fallback",
            "question": t("guidance.fallback_q"),
            "options": [],
            "why": "",
        }]
        st.session_state["r_g_fallback"] = True
    st.session_state["r_g_questions"] = qs
    state.log_event(
        "guidance_shown",
        {"round": len(st.session_state["r_guidance_rounds"]) + 1,
         "source": source, "n_questions": len(qs),
         "fallback": st.session_state["r_g_fallback"]},
    )
    st.rerun()


def _validate(data) -> list[dict] | None:
    if not isinstance(data, dict):
        return None
    qs = data.get("questions")
    if not isinstance(qs, list) or not qs:
        return None
    out = []
    for q in qs:
        if not isinstance(q, dict) or not (q.get("question") or "").strip():
            continue
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        out.append({
            "dimension": str(q.get("dimension") or "other"),
            "question": str(q["question"]).strip(),
            "options": options[:4],
            "why": str(q.get("why") or "").strip(),
        })
    return out or None


def _finish_round(topic: dict, rnd: int) -> None:
    qs = st.session_state["r_g_questions"]
    saved = st.session_state["r_g_answers"]
    items = []
    for i, q in enumerate(qs):
        a = saved.get(i, {})
        custom = (a.get("custom") or "").strip()
        chosen_opt = a.get("opt")
        ai_decided = bool(a.get("ai_decided")) and not custom
        chosen = custom or ("" if ai_decided or chosen_opt is None else str(chosen_opt))
        items.append({
            "dimension": q["dimension"],
            "question": q["question"],
            "options": q["options"],
            "chosen": chosen,
            "is_custom": bool(custom),
            "ai_decided": ai_decided,
            "fallback": st.session_state["r_g_fallback"],
        })

    source = st.session_state["r_g_source"]
    intent = st.session_state["r_intent"]
    lang = get_lang()
    if source == "fixed3+ai_supplement":
        out = stream_llm(
            prompts.build_system_script(topic, lang),
            prompts.build_user_script_from_answers(topic, intent, items, lang),
            group="E-final",
        )
    else:
        out = stream_llm(
            prompts.build_system_revision(topic, lang),
            prompts.build_user_revision_from_answers(
                topic, intent, state.current_script(), items, lang
            ),
            group="E-revise",
        )
    # Only commit the round once generation succeeds, so a failed-then-retried
    # generation never double-appends the round / re-logs answers.
    if not (out and out.strip()):
        return
    for it in items:
        state.log_event(
            "guidance_answer",
            {"dimension": it["dimension"], "is_custom": it["is_custom"],
             "ai_decided": it["ai_decided"]},
        )
    round_entry: dict = {"round": rnd, "source": source, "items": items}
    if source == "ai_from_draft":
        round_entry["draft_snapshot_ref"] = len(st.session_state["r_versions"])
    st.session_state["r_guidance_rounds"].append(round_entry)
    state.log_event("guidance_submit", {"round": rnd})
    state.add_version(out, "ai")
    if source == "ai_from_draft":
        st.session_state["r_n_ai_rounds"] += 1
    st.session_state["r_phase"] = "postgen"
    _postgen.request_editor_refresh()
    st.rerun()
