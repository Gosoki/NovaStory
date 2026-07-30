from __future__ import annotations

import json

import streamlit as st

from core import db, imagegen, shots, state
from i18n import get_lang, t
from views import _storyboard

_SCALE = list(range(1, 8))
_SCALE_WIDTH = 600      # px; width of the 1-7 button row + aligned anchors (tunable)
_OWN_ITEMS = 3          # q.own1..own3 (trimmed for session length)
_SOA_ITEMS = 2          # q.soa1..soa2
_TLX_ITEMS = 1          # q.tlx1
_ATTENTION_ROUND = 2    # attention check embedded in round 2's questionnaire
_ATTENTION_EXPECTED = 2
_SHOT_TAGS = ("mine", "ai_ok", "ai_against")

# Endpoint (+ optional midpoint) labels shown under each 1-7 scale. Most items
# are agreement-type; violation/imagine are intensity scales with their own poles.
_ANCHOR_SETS = {
    "agree": ("anchor_disagree", "anchor_neutral", "anchor_agree"),
    "violation": ("anchor_viol_low", "anchor_neutral", "anchor_viol_high"),
    "imagine": ("anchor_imag_low", "anchor_neutral", "anchor_imag_high"),
    "amount": ("anchor_amt_low", "anchor_amt_mid", "anchor_amt_high"),
}


def _anchor_html(text: str, align: str) -> str:
    return (
        f"<div style='text-align:{align};color:rgba(140,140,140,0.95);"
        f"font-size:0.78rem;line-height:1.15'>{text}</div>"
    )


def _scale_anchors(kind: str | None) -> None:
    """Anchor labels aligned under the scale: left / (center) / right, inside a
    container the same width as the button row so they line up with 1 / mid / 7."""
    if not kind:
        return
    left, mid, right = _ANCHOR_SETS[kind]
    with st.container(width=_SCALE_WIDTH):
        c1, c2, c3 = st.columns(3)
        c1.markdown(_anchor_html(t(f"q.{left}"), "left"), unsafe_allow_html=True)
        if mid:
            c2.markdown(_anchor_html(t(f"q.{mid}"), "center"), unsafe_allow_html=True)
        c3.markdown(_anchor_html(t(f"q.{right}"), "right"), unsafe_allow_html=True)


def _short(label: str, n: int = 18) -> str:
    """A compact, recognizable form of a long item label for the unanswered list."""
    label = label.strip()
    return label if len(label) <= n else label[:n] + "…"




def _ai_q_best_pick(ridx: int):
    """E-only, optional: let the subject flag the guiding questions that helped —
    multi-select over every question asked this trial, each shown as
    'question → the answer you gave' so it is recognizable. Returns a list of
    {idx, dimension, question, chosen} (empty if none flagged), or None when <2
    were asked. Not added to `missing` — it is optional."""
    asked = [
        it for r in st.session_state.get("r_guidance_rounds", [])
        for it in r.get("items", [])
        if it.get("question")
    ]
    if len(asked) < 2:
        return None

    def _answer(it: dict) -> str:
        if it.get("ai_decided"):
            return t("guidance.ai_decide")
        return (it.get("chosen") or "").strip()

    st.markdown(t("q.ai_q_best"))
    st.caption(t("q.ai_q_best_ph"))
    chosen = []
    for i, it in enumerate(asked):
        ans = _answer(it)
        label = f"{it['question']} → {ans}" if ans else it["question"]
        if st.checkbox(label, key=f"_q_aqbest_{ridx}_{i}"):
            chosen.append({"idx": i, "dimension": it.get("dimension", "other"),
                           "question": it.get("question", ""), "chosen": ans})
    st.divider()
    return chosen


@st.fragment(run_every=2)
def _live_storyboard(script: str, subtitle: str, pid: int, ridx: int, n: int) -> None:
    """Poll the archive folder every 2s and show images as they finish; one full
    rerun once all are done so the outer render goes static and polling stops."""
    _storyboard.render(script, subtitle,
                       sketches=imagegen.frame_htmls(pid, ridx, n, t("storyboard.generating")))
    if imagegen.all_done(pid, ridx, n):
        st.rerun()  # full rerun → outer renders static, polling stops


def _render_storyboard_area(ridx: int) -> None:
    """Storyboard preview above the questionnaire. With OpenAI, the 画面 column
    fills with gpt-image-1 illustrations generated in the background AFTER submit
    (not during the creative task, so it doesn't touch t_pregen/t_postgen). The
    sheet starts inline at natural size; _install_sb_scroll_observer wires a
    scroll listener that pins it to the top-right thumbnail once scrolled past."""
    script = state.current_script()
    subtitle = state.topic_text(state.current_round()["topic"], "title", get_lang())
    if imagegen.enabled():
        parsed = shots.parse_shots(script)
        if parsed:
            pid, n = st.session_state["participant_id"], len(parsed)
            if imagegen.all_done(pid, ridx, n):
                _storyboard.render(script, subtitle,
                                   sketches=imagegen.frame_htmls(pid, ridx, n, t("storyboard.generating")))
            else:
                imagegen.ensure_started(pid, ridx, parsed,
                                        st.session_state.get("api_key", ""),
                                        st.session_state.get("base_url", ""))
                _live_storyboard(script, subtitle, pid, ridx, n)
            return
    _storyboard.render(script, subtitle)


# Fake-inflow-tracking pin observer for the questionnaire storyboard.
# The sheet is `position:fixed` (see _storyboard.py — this bypasses the
# `position:sticky` failure caused by Streamlit ancestors having overflow
# constraints). A sibling `.sb-slot` placeholder stays in flow at the sheet's
# natural size, and every scroll tick we set the sheet's `transform` to
# translate to the slot's viewport coords → the sheet visually TRACKS the slot,
# looking exactly like an in-flow element.
#
# Once half the sheet (PIN_FRAC of its height) has scrolled above the viewport
# top we swap to a corner target (`translate(cornerX, CORNER_Y) scale(.32)`)
# and briefly add `.is-animating` so CSS transitions the transform over 0.45s
# — smooth in both directions. During plain in-flow tracking `is-animating` is
# OFF so scroll updates don't interpolate (else the sheet would lag behind).
#
# Scroll listener uses capture:true because Streamlit scrolls an inner
# container (.stMain / .stMainBlockContainer); the guard flag on window makes
# every rerun (incl. _live_storyboard's 2s fragment) a no-op. MutationObserver
# re-runs the tick and reattaches hover handlers when fragments swap the sheet.
_SB_STICKY_TOP = 64
_SB_SCROLL_JS = f"""
<script>
(function(){{
  if (window.__sb_scroll_installed) return;
  window.__sb_scroll_installed = true;

  var CORNER_Y  = {_SB_STICKY_TOP}; // where the pinned corner sits (y from viewport top)
  var GAP       = 12;               // viewport-right gap for the thumbnail
  var PIN_FRAC  = 0.5;              // pin the corner after the sheet is this fraction scrolled past
  var ANIM_MS   = 450;              // must match .is-animating CSS transition

  var atCorner = false;
  var natW = 0;
  var natH = 0;
  var animTimer = null;

  function measure(sheet, slot) {{
    // Snapshot the slot's natural width/height once (used forever as the
    // sheet's rendered dimensions in fake-inflow). Slot's height reserves
    // layout space so removing the sheet from flow (fixed) doesn't collapse.
    if (natW && natH) return;
    var slotRect = slot.getBoundingClientRect();
    if (slotRect.width < 20) return;   // not laid out yet
    natW = slotRect.width;
    sheet.style.width = natW + 'px';
    natH = sheet.offsetHeight || 400;
    slot.style.height = natH + 'px';
  }}

  function updateCornerVars(sheet) {{
    // The corner transform lives in CSS (so :hover can override to scale .95
    // and animate smoothly). JS only supplies the translate target as vars.
    sheet.style.setProperty('--sb-tx', (window.innerWidth - GAP - natW) + 'px');
    sheet.style.setProperty('--sb-ty', CORNER_Y + 'px');
  }}

  function armAnim(sheet) {{
    // Toggle .is-animating for ANIM_MS so:
    //   (1) the transform transition is active during the flow↔corner swap;
    //   (2) pointer-events stays disabled (per CSS) — no stray mouseenter can
    //       hijack the animation mid-way.
    sheet.classList.add('is-animating');
    if (animTimer) clearTimeout(animTimer);
    animTimer = setTimeout(function(){{
      sheet.classList.remove('is-animating');
      animTimer = null;
    }}, ANIM_MS + 30);
  }}

  function forceFlow(sheet, slotRect) {{
    // Hard reset to in-flow state — no animation, no transient class flags.
    // Used as a safety net (e.g. at scroll ≈ top) and after fragment DOM
    // replacements that would otherwise leave the sheet mis-styled.
    atCorner = false;
    sheet.classList.remove('is-corner');
    sheet.classList.remove('is-animating');
    if (animTimer) {{ clearTimeout(animTimer); animTimer = null; }}
    sheet.style.transform = 'translate(' + slotRect.left + 'px, ' + slotRect.top + 'px) scale(1)';
  }}

  function tick() {{
    var sheet = document.querySelector('.sb-sheet');
    var slot = document.querySelector('.sb-slot');
    if (!sheet || !slot) return;
    measure(sheet, slot);
    if (!natW) return;                     // still not laid out
    // Fragment reruns replace the sheet DOM without our styles/classes; a
    // width-less position:fixed sheet renders full-viewport-wide at top-left
    // ("变得巨大"). Re-apply width every tick — idempotent.
    if (sheet.style.width !== natW + 'px') sheet.style.width = natW + 'px';

    var slotRect = slot.getBoundingClientRect();

    // Safety net #1: at the very top of the page (slot top at or below the
    // viewport top → sheet fully in view) force a hard reset. Handles the
    // case where any of the transient state (is-corner, hover-cached scale,
    // stale inline transform) drifted out of sync.
    if (slotRect.top >= 0) {{
      forceFlow(sheet, slotRect);
      if (!sheet.classList.contains('is-positioned')) sheet.classList.add('is-positioned');
      return;
    }}

    // Pin when at least PIN_FRAC of the sheet has scrolled above the viewport
    // top (slot.top < 0 means the top edge is already above; -natH*PIN_FRAC
    // means that much of the sheet is off-screen). Keeps the sheet visible at
    // natural size for a beat longer instead of snapping immediately.
    var wantsCorner = slotRect.top < -natH * PIN_FRAC;

    if (wantsCorner && !atCorner) {{
      atCorner = true;
      updateCornerVars(sheet);
      // Clear inline transform so the CSS .is-corner rule (which reads the
      // vars we just set) takes over — that's what enables CSS :hover to
      // override to scale .95 later, animated by the corner-active transition.
      sheet.style.transform = '';
      sheet.classList.add('is-corner');
      armAnim(sheet);
    }} else if (!wantsCorner && atCorner) {{
      atCorner = false;
      sheet.classList.remove('is-corner');
      // Explicit scale(1) so CSS interpolates the scale factor cleanly from
      // whatever the corner state was (.32 or hovered .95) back to natural —
      // without it CSS falls back to matrix interpolation and the sheet can
      // look wrong-sized mid-transition.
      sheet.style.transform = 'translate(' + slotRect.left + 'px, ' + slotRect.top + 'px) scale(1)';
      armAnim(sheet);
    }} else if (!atCorner) {{
      // In-flow tracking — no transition (CSS transition off in this state),
      // snap-follow the slot every tick.
      sheet.style.transform = 'translate(' + slotRect.left + 'px, ' + slotRect.top + 'px) scale(1)';
      // Reconcile class: fragment replacement may have left a stray is-corner.
      if (sheet.classList.contains('is-corner')) sheet.classList.remove('is-corner');
    }} else {{
      // At corner — keep --sb-tx fresh in case viewport width changed.
      updateCornerVars(sheet);
      // Reconcile class: fragment replacement wipes the class from the new
      // DOM element (this is the root cause of "变得巨大" — width-less
      // position:fixed with no is-corner sheets at top-left).
      if (!sheet.classList.contains('is-corner')) sheet.classList.add('is-corner');
      // Also clear stale inline transform so CSS .is-corner rule wins.
      if (sheet.style.transform) sheet.style.transform = '';
    }}

    if (!sheet.classList.contains('is-positioned')) sheet.classList.add('is-positioned');
  }}

  function onResize() {{
    // Slot layout may have changed → invalidate cached natural dimensions.
    natW = 0; natH = 0;
    var slot = document.querySelector('.sb-slot');
    if (slot) slot.style.height = '';    // let it re-lay out to natural
    tick();
  }}

  // capture:true so scroll events on any inner container (Streamlit's
  // .stMain, .stMainBlockContainer, etc.) still fire the listener.
  document.addEventListener('scroll', tick, {{ passive: true, capture: true }});
  window.addEventListener('scroll', tick, {{ passive: true }});
  window.addEventListener('resize', onResize, {{ passive: true }});
  new MutationObserver(tick).observe(document.body, {{ childList: true, subtree: true }});
  requestAnimationFrame(function(){{ tick(); tick(); }});  // 2 frames: layout, then position
}})();
</script>
"""


def _install_sb_scroll_observer() -> None:
    """Inject the scroll observer once per Streamlit rerun. Uses st.html (not
    st.components.v1.html) so the script runs in the MAIN page, not an
    iframe — the iframe path in 1.57 uses `sandbox` without `allow-same-origin`
    which blocks `window.parent.document` access."""
    st.html(_SB_SCROLL_JS, unsafe_allow_javascript=True)


def render() -> None:
    ridx = st.session_state["round_idx"]
    # Show the finished script as a storyboard table above the questionnaire so
    # the participant can refer to it while answering. The scroll observer
    # pins it to the top-right thumbnail once they scroll past it.
    _render_storyboard_area(ridx)
    _install_sb_scroll_observer()
    st.subheader(t("q.title"))
    st.caption(t("q.hint"))

    answers: dict[str, object] = {}
    missing: list[str] = []  # labels of unanswered items, named back to the user

    def likert(key: str, label: str, anchors: str | None = "agree") -> None:
        st.markdown(label)
        val = st.segmented_control(
            label, _SCALE, selection_mode="single", key=f"_q_{key}_{ridx}",
            width=_SCALE_WIDTH, label_visibility="collapsed",
        )
        _scale_anchors(anchors)
        if val is None:
            missing.append(_short(label))
        answers[key] = val
        st.divider()

    for i in range(1, _OWN_ITEMS + 1):
        likert(f"own{i}", t(f"q.own{i}"))
    for i in range(1, _SOA_ITEMS + 1):
        likert(f"soa{i}", t(f"q.soa{i}"))
    if ridx == _ATTENTION_ROUND:
        likert("attention", t("q.attention"), anchors=None)
    for i in range(1, _TLX_ITEMS + 1):
        likert(f"tlx{i}", t(f"q.tlx{i}"))
    likert("violation", t("q.violation"), anchors="violation")
    likert("imagine", t("q.imagine"), anchors="imagine")
    likert("sat", t("q.satisfaction"))
    # E-only: rate the quality of the AI's guiding questions (E is the only
    # condition where the AI asks structured questions). ai_q_amount = did it ask
    # too few / just right / too many; ai_q_best = which single question landed
    # (optional). Both exploratory, stored E-only (NULL for C/D).
    if state.current_round()["condition"] == "E":
        likert("ai_q_quality", t("q.ai_q_quality"))
        likert("ai_q_amount", t("q.ai_q_amount"), anchors="amount")
        answers["ai_q_best"] = _ai_q_best_pick(ridx)

    # ---- per-shot intent annotation ----
    # Options are the localized labels themselves (no format_func): a
    # segmented_control with format_func inside a loop is not reliably
    # AppTest-drivable across reruns. Selection is mapped back to the tag key.
    st.subheader(t("q.shots_title"))
    tag_labels = [t(f"q.tag_{c}") for c in _SHOT_TAGS]
    lbl2tag = dict(zip(tag_labels, _SHOT_TAGS))
    parsed = shots.parse_shots(state.current_script())
    shot_annotations: list[dict] = []
    if parsed:
        st.caption(t("q.shots_hint"))
        for s in parsed:
            with st.container(border=True):
                st.markdown(_shot_preview(s))
            sel = st.segmented_control(
                t("q.shot_tag_label"), tag_labels, selection_mode="single",
                key=f"_q_shot{s['idx']}_{ridx}",
            )
            if sel is None:
                missing.append(t("q.shot_label", i=s["idx"]))
            shot_annotations.append({"shot": s["idx"], "tag": lbl2tag.get(sel)})
            st.divider()
    else:
        sel = st.segmented_control(
            t("q.whole_tag_label"), tag_labels, selection_mode="single",
            key=f"_q_whole_{ridx}",
        )
        if sel is None:
            missing.append(_short(t("q.whole_tag_label")))
        shot_annotations.append({"shot": 0, "tag": lbl2tag.get(sel)})

    if st.button(t("q.submit"), type="primary", width="stretch"):
        if missing:
            st.error(t("errors.unanswered", items=" / ".join(missing)))
            return
        _submit(ridx, answers, shot_annotations)
        st.rerun()


def _shot_preview(s: dict) -> str:
    head = f"**{t('q.shot_label', i=s['idx'])}**"
    body = s.get("visual") or s.get("raw", "")
    if len(body) > 120:
        body = body[:120] + "…"
    return f"{head} {body}"


def _submit(ridx: int, answers: dict, shot_annotations: list[dict]) -> None:
    pid = st.session_state["participant_id"]
    db.insert_questionnaire(
        participant_id=pid,
        round_idx=ridx,
        ownership_json=json.dumps(
            {f"own{i}": answers[f"own{i}"] for i in range(1, _OWN_ITEMS + 1)}
        ),
        soa_json=json.dumps(
            {f"soa{i}": answers[f"soa{i}"] for i in range(1, _SOA_ITEMS + 1)}
        ),
        tlx_json=json.dumps(
            {f"tlx{i}": answers[f"tlx{i}"] for i in range(1, _TLX_ITEMS + 1)}
        ),
        intent_violation=answers["violation"],
        imagine_match=answers["imagine"],
        satisfaction=answers["sat"],
        ai_q_quality=answers.get("ai_q_quality"),  # E only; NULL for C/D
        ai_q_amount=answers.get("ai_q_amount"),    # E only; NULL for C/D
        ai_q_best_json=(json.dumps(answers["ai_q_best"], ensure_ascii=False)
                        if answers.get("ai_q_best") else None),
        shot_annotations_json=json.dumps(shot_annotations, ensure_ascii=False),
    )
    if ridx == _ATTENTION_ROUND:
        db.update_participant(
            pid,
            attention_ok=int(answers.get("attention") == _ATTENTION_EXPECTED),
            attention_raw=answers.get("attention"),  # keep the raw value (#31)
        )
    state.log_event("questionnaire_submit")
    state.advance_round()
