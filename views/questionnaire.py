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
                       sketches=imagegen.frame_htmls(pid, ridx, n, t("storyboard.generating")),
                       pin=True)
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
                                   sketches=imagegen.frame_htmls(pid, ridx, n, t("storyboard.generating")),
                                   pin=True)
            else:
                imagegen.ensure_started(pid, ridx, parsed,
                                        st.session_state.get("api_key", ""),
                                        st.session_state.get("base_url", ""))
                _live_storyboard(script, subtitle, pid, ridx, n)
            return
    _storyboard.render(script, subtitle, pin=True)


# Fake-inflow-tracking pin observer for the questionnaire storyboard.
# The sheet is `position:fixed` (see _storyboard.py — this bypasses the
# `position:sticky` failure caused by Streamlit ancestors having overflow
# constraints). Its `.sb-slot` PARENT (the sheet is nested inside it, not a
# sibling) stays in flow, and every tick we size that slot to the sheet's own
# height so the flow keeps reserving exactly the space the fixed sheet occupies,
# then set the sheet's `transform` to translate to the slot's viewport coords →
# the sheet visually TRACKS the slot, looking exactly like an in-flow element.
# If the slot ever loses that height the questions slide up underneath the sheet
# and it covers them — which is why measure() re-applies it on EVERY tick rather
# than once (Streamlit replaces these nodes on every rerun).
#
# Once half the sheet (PIN_FRAC of its height) has scrolled above the viewport
# top we swap to a corner target (`translate(cornerX, CORNER_Y) scale(.32)`)
# and briefly add `.is-animating` so CSS transitions the transform over ANIM_MS
# — smooth in both directions. During plain in-flow tracking `is-animating` is
# OFF so scroll updates don't interpolate (else the sheet would lag behind).
# The flight always starts from the sheet's real current position and runs
# faded (see the `.is-animating` opacity rule) so it never appears to cover a
# question on its way to the corner.
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
  var CORNER_SCALE = .32;           // must match .sb-sheet.sb-pin.is-corner in _storyboard.py
  var PIN_FRAC  = 0.5;              // pin the corner after the sheet is this fraction scrolled past
  var UNPIN_FRAC= 0.2;              // ...and only release it once it scrolls back this far (hysteresis)
  var ANIM_MS   = 320;              // must match .is-animating CSS transition

  var atCorner = false;
  var natW = 0;
  var natH = 0;
  var animTimer = null;
  var lastSheet = null;   // node identity, to detect a freshly rendered sheet

  function measure(sheet, slot) {{
    // Re-sync EVERY tick — never measure-once-and-cache. Streamlit hands us
    // brand-new .sb-slot / .sb-sheet nodes on every rerun (next round, the
    // devtools condition switch, the 2s _live_storyboard fragment), and a fresh
    // slot carries no inline height. With the sheet `position:fixed` the slot
    // then collapses to 0, the questionnaire items slide up UNDER the sheet and
    // it covers them for the rest of the page. Re-measuring also keeps the
    // placeholder honest when the sheet GROWS in place (generated images
    // arriving) or when this round's script is longer than the last one's.
    var w = slot.clientWidth;
    if (w < 20) return false;                    // not laid out yet
    natW = w;
    if (sheet.style.width !== w + 'px') sheet.style.width = w + 'px';
    // offsetHeight is the LAYOUT height: transforms (the corner scale) don't
    // affect it, so this stays the sheet's natural height even while pinned.
    var h = sheet.offsetHeight;
    if (h > 20) natH = h;
    // Compare against the slot's OWN inline value, not against the previous
    // measurement — a new slot whose sheet happens to be exactly as tall as the
    // last round's would otherwise never get its placeholder height back.
    if (natH && slot.style.height !== natH + 'px') slot.style.height = natH + 'px';
    return natH > 0;
  }}

  function cornerX() {{
    // clientWidth, not innerWidth: the latter counts the scrollbar, which would
    // tuck the thumbnail's edge underneath it.
    return document.documentElement.clientWidth - GAP - natW;
  }}

  function updateCornerVars(sheet) {{
    // The corner transform lives in CSS (so :hover can override to scale .95
    // and animate smoothly). JS only supplies the translate target as vars.
    sheet.style.setProperty('--sb-tx', cornerX() + 'px');
    sheet.style.setProperty('--sb-ty', CORNER_Y + 'px');
  }}

  function cornerTransform() {{
    // Byte-for-byte the same transform as the CSS `.sb-sheet.sb-pin.is-corner`
    // rule (keep CORNER_SCALE in sync with it) so that swapping between the
    // inline value and the CSS one never moves the sheet.
    return 'translate(' + cornerX() + 'px, ' + CORNER_Y + 'px) scale('
         + CORNER_SCALE + ')';
  }}

  function releaseToCss(sheet) {{
    // Drop the inline transform so the CSS rule owns it again — that is what
    // lets :hover animate the thumbnail up to scale .95. Only safe once the
    // flight is over: while .is-animating the inline value IS the animation's
    // target, and clearing it mid-flight resets transform to `none`, which
    // parks the full-size sheet on the viewport's top-left corner (over the
    // questions) for the rest of the transition.
    if (sheet.classList.contains('is-animating')) return;
    if (sheet.style.transform) sheet.style.transform = '';
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
      // Flight over → let CSS own the corner transform again (needed for the
      // :hover zoom). No-op when we landed back in flow.
      if (atCorner) releaseToCss(sheet);
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
    // `.sb-pin` = the questionnaire's sheet specifically; the intro page renders
    // a plain in-flow sheet that this observer must not touch. Take the LAST
    // match: a rerun can briefly leave the outgoing node in the DOM alongside
    // its replacement, and driving the stale one would leave the live sheet
    // unstyled (width-less position:fixed = "变得巨大" at the top-left).
    var sheets = document.querySelectorAll('.sb-sheet.sb-pin');
    var slots = document.querySelectorAll('.sb-slot.sb-pin');
    if (!sheets.length || !slots.length) return;
    var sheet = sheets[sheets.length - 1];
    var slot = slots[slots.length - 1];
    // measure() re-applies width AND the slot's placeholder height every tick,
    // so a fragment rerun that replaced the DOM can never leave a width-less
    // position:fixed sheet ("变得巨大") or a collapsed slot (sheet covering the
    // questions). It returns false while the nodes are not laid out yet.
    if (!measure(sheet, slot)) return;

    // Hand the layout to JS only now that measuring works — this is what turns
    // on the whole `:root.sb-js` block in _storyboard.py. Until then the sheet
    // stays a plain visible in-flow table, so a page where this script never
    // runs still shows the participant their storyboard. Placement happens
    // below in the same tick, before the browser paints, so the switch from
    // in-flow to `position:fixed` is not visible.
    var root = document.documentElement;
    if (!root.classList.contains('sb-js')) {{
      root.classList.add('sb-js');
      measure(sheet, slot);   // re-measure: the sheet is `position:fixed` now
    }}

    // A node the observer has never placed starts at transform:none, which for
    // a fixed sheet is the viewport's top-left corner — right on top of the
    // questions. Suppress transitions while this tick places it, so it appears
    // where it belongs instead of gliding there from the corner of the screen.
    // Without this every fragment swap (every 2s while illustrations generate)
    // would drag a full-size sheet across the questionnaire.
    var fresh = (sheet !== lastSheet);
    if (fresh) {{
      lastSheet = sheet;
      sheet.classList.add('sb-noanim');
    }}
    try {{
      place(sheet, slot);
    }} finally {{
      if (fresh) {{
        void sheet.offsetWidth;              // commit the placement…
        sheet.classList.remove('sb-noanim'); // …before transitions come back
      }}
    }}
    if (!sheet.classList.contains('is-positioned')) sheet.classList.add('is-positioned');
  }}

  function place(sheet, slot) {{
    var slotRect = slot.getBoundingClientRect();

    // Safety net #1: at the very top of the page (slot top at or below the
    // viewport top → sheet fully in view) force a hard reset. Handles the
    // case where any of the transient state (is-corner, hover-cached scale,
    // stale inline transform) drifted out of sync.
    if (slotRect.top >= 0) {{
      forceFlow(sheet, slotRect);
      return;
    }}

    // Pin when at least PIN_FRAC of the sheet has scrolled above the viewport
    // top (slot.top < 0 means the top edge is already above; -natH*PIN_FRAC
    // means that much of the sheet is off-screen). Keeps the sheet visible at
    // natural size for a beat longer instead of snapping immediately.
    // Hysteresis: once pinned it takes a scroll back up to UNPIN_FRAC to
    // release. Without the gap, scrolling up and down around the single
    // threshold re-triggers the shrink/expand animation over and over,
    // and each replay sweeps the full-size sheet across the questions below.
    var wantsCorner = slotRect.top < -natH * (atCorner ? UNPIN_FRAC : PIN_FRAC);

    if (wantsCorner && !atCorner) {{
      // Snap to the sheet's TRUE current position first, with the transition
      // still off (CSS only arms it under .is-animating / .is-corner), then
      // force a style flush so the browser adopts this as the animation's
      // starting value. A jump-scroll — dragging the scrollbar, PageDown,
      // a fast wheel flick — fires a single scroll event, so without this the
      // flight would start from wherever the previous tick left the sheet
      // (often still up at the top of the page) and drag it full-size straight
      // across the questions. This is the "小概率" cover users hit while
      // scrolling briskly.
      sheet.style.transform = 'translate(' + slotRect.left + 'px, ' + slotRect.top + 'px) scale(1)';
      void sheet.offsetWidth;   // reflow: commit the start value
      atCorner = true;
      updateCornerVars(sheet);
      sheet.classList.add('is-corner');
      // Fly to the corner on an EXPLICIT inline transform rather than clearing
      // it and letting the CSS .is-corner rule take over. Clearing it here is
      // what used to make the storyboard cover the questions: for one moment
      // transform is `none`, and since the sheet is `position:fixed;top:0;left:0`
      // that parks the full-size sheet over the viewport's top-left corner —
      // exactly where the questions are — and the transition then sweeps it
      // across them from there. Interpolating from the sheet's real position
      // keeps the flight along the short path. releaseToCss() hands control back
      // to CSS (which :hover needs) once the flight is done.
      sheet.style.transform = cornerTransform();
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
      releaseToCss(sheet);
    }}
  }}

  // capture:true so scroll events on any inner container (Streamlit's
  // .stMain, .stMainBlockContainer, etc.) still fire the listener.
  // resize needs no special handling: measure() re-derives width and height
  // from the live layout on every tick, so one extra tick is enough.
  document.addEventListener('scroll', tick, {{ passive: true, capture: true }});
  window.addEventListener('scroll', tick, {{ passive: true }});
  window.addEventListener('resize', tick, {{ passive: true }});
  new MutationObserver(tick).observe(document.body, {{ childList: true, subtree: true }});
  // Web fonts landing later re-flow the table taller. That changes no DOM and
  // fires no scroll, so nothing else here would notice, and the slot would keep
  // reserving the old (too short) height with the questions tucked under the
  // overhang.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(tick);
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
