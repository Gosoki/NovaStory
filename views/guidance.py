from __future__ import annotations

import streamlit as st

from core import config, prompts, state
from i18n import t
from views import _postgen
from views._streaming import call_llm_json, stream_llm

# Guided elicitation (condition E): one questionnaire component serves both
# round 1 (3 fixed dims + 2-4 AI supplements) and user-triggered follow-up
# rounds (1-3 questions on the current draft). Questions are shown one at a
# time (AskUserQuestion-style); answers are submitted together at the end.

_AI_DECIDE = "__ai__"  # sentinel option: "leave this one to the AI"


def begin_round(source: str) -> None:
    """source: "fixed3+ai_supplement" (round 1) | "ai_from_draft" (follow-up)."""
    st.session_state["r_g_source"] = source
    st.session_state["r_g_questions"] = []
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
            return  # config error surfaced by call_llm_json; user can retry via rerun

    qs = st.session_state["r_g_questions"]
    idx = st.session_state["r_g_idx"]
    q = qs[idx]
    rnd = len(st.session_state["r_guidance_rounds"]) + 1

    st.subheader(t("guidance.title"))
    st.caption(t("guidance.progress", i=idx + 1, n=len(qs)))
    st.markdown(f"**{q['question']}**")
    if q.get("why"):
        st.caption(q["why"])

    options = list(q.get("options") or [])
    if options:
        st.segmented_control(
            t("guidance.pick_label"),
            options + [_AI_DECIDE],
            selection_mode="single",
            format_func=lambda o: t("guidance.ai_decide") if o == _AI_DECIDE else o,
            key=f"_g_opt_{rnd}_{idx}",
            label_visibility="collapsed",
        )
    st.text_input(t("guidance.custom_label"), key=f"_g_custom_{rnd}_{idx}")

    cols = st.columns(2)
    if idx > 0 and cols[0].button(t("guidance.prev"), width="stretch"):
        st.session_state["r_g_idx"] -= 1
        st.rerun()
    last = idx == len(qs) - 1
    label = t("guidance.finish") if last else t("guidance.next")
    if cols[1].button(label, type="primary", width="stretch"):
        if not _answered(rnd, idx, q):
            st.error(t("errors.answer_or_skip"))
            return
        if last:
            _finish_round(topic, rnd)
        else:
            st.session_state["r_g_idx"] += 1
            st.rerun()


def _answered(rnd: int, idx: int, q: dict) -> bool:
    custom = (st.session_state.get(f"_g_custom_{rnd}_{idx}") or "").strip()
    chosen = st.session_state.get(f"_g_opt_{rnd}_{idx}")
    return bool(custom) or chosen is not None or not q.get("options")


def _generate_questions(topic: dict) -> None:
    source = st.session_state["r_g_source"]
    intent = st.session_state["r_intent"]
    if source == "fixed3+ai_supplement":
        system = prompts.build_system_guidance_round1()
        user = prompts.build_user_guidance_round1(topic, intent)
        group = "E-guidance-r1"
    else:
        system = prompts.build_system_guidance_followup()
        user = prompts.build_user_guidance_followup(topic, intent, state.current_script())
        group = "E-guidance-fu"

    data = call_llm_json(system, user, group=group)
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
    items = []
    for i, q in enumerate(qs):
        custom = (st.session_state.get(f"_g_custom_{rnd}_{i}") or "").strip()
        chosen_opt = st.session_state.get(f"_g_opt_{rnd}_{i}")
        ai_decided = chosen_opt == _AI_DECIDE and not custom
        chosen = custom or ("" if chosen_opt in (None, _AI_DECIDE) else str(chosen_opt))
        items.append({
            "dimension": q["dimension"],
            "question": q["question"],
            "options": q["options"],
            "chosen": chosen,
            "is_custom": bool(custom),
            "ai_decided": ai_decided,
            "fallback": st.session_state["r_g_fallback"],
        })
        state.log_event(
            "guidance_answer",
            {"dimension": q["dimension"], "is_custom": bool(custom), "ai_decided": ai_decided},
        )

    source = st.session_state["r_g_source"]
    round_entry: dict = {"round": rnd, "source": source, "items": items}
    if source == "ai_from_draft":
        round_entry["draft_snapshot_ref"] = len(st.session_state["r_versions"])
    st.session_state["r_guidance_rounds"].append(round_entry)
    state.log_event("guidance_submit", {"round": rnd})

    intent = st.session_state["r_intent"]
    if source == "fixed3+ai_supplement":
        out = stream_llm(
            prompts.build_system_script(topic),
            prompts.build_user_script_from_answers(topic, intent, items),
            group="E-final",
        )
    else:
        out = stream_llm(
            prompts.build_system_revision(topic),
            prompts.build_user_revision_from_answers(
                topic, intent, state.current_script(), items
            ),
            group="E-revise",
        )
    if out is not None:
        state.add_version(out, "ai")
        if source == "ai_from_draft":
            st.session_state["r_n_ai_rounds"] += 1
        st.session_state["r_phase"] = "postgen"
        _postgen.request_editor_refresh()
        st.rerun()
