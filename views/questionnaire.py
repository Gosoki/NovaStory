from __future__ import annotations

import html
import json

import streamlit as st

from core import db, shots, state
from i18n import get_lang, t

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


# Storyboard preview sheet (shown above the questionnaire), styled like a real
# 絵コンテ / 分镜纸: circled cut numbers, a 16:9 empty picture frame per shot
# (the 画面 column — a generated sketch there is future work, "coming soon"),
# then action / dialogue columns, on a paper-like sheet. Falls back to plain
# text when the script doesn't parse into shots.
_SB_CSS = """
<style>
.sb-sheet{background:#fbfaf3;border:2px solid #3f3f3f;border-radius:5px;
  padding:12px 14px 16px;margin:.2rem 0 .9rem;box-shadow:0 1px 7px rgba(0,0,0,.13);overflow-x:auto}
.sb-sheet .sb-hd{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  border-bottom:2px solid #3f3f3f;padding-bottom:7px;margin-bottom:11px}
.sb-sheet .sb-ttl{font-size:1.06rem;font-weight:700;letter-spacing:.18em;color:#2a2a2a}
.sb-sheet .sb-sub{color:#7a7a7a;font-size:.83rem}
table.sb-tbl{border-collapse:collapse;width:100%;table-layout:fixed;font-size:.86rem;color:#2a2a2a}
table.sb-tbl th,table.sb-tbl td{border:1px solid #9a958c;padding:6px 8px;vertical-align:top}
table.sb-tbl thead th{background:rgba(63,63,63,.08);text-align:center;font-weight:600;letter-spacing:.05em}
table.sb-tbl th:nth-child(1){width:7%}table.sb-tbl th:nth-child(2){width:14%}
table.sb-tbl th:nth-child(3){width:33%}table.sb-tbl th:nth-child(4){width:28%}
table.sb-tbl th:nth-child(5){width:18%}
td.sb-no{text-align:center;vertical-align:middle}
td.sb-no .cut{display:inline-flex;align-items:center;justify-content:center;width:27px;height:27px;
  border:1.5px solid #3f3f3f;border-radius:50%;font-weight:700;font-size:1rem;font-family:Georgia,serif}
td.sb-pic{vertical-align:middle;padding:7px}
.sb-frame{position:relative;aspect-ratio:16/9;border:1.5px solid #3f3f3f;background:#fff;
  display:flex;align-items:center;justify-content:center;
  box-shadow:inset 0 0 0 3px #fff,inset 0 0 0 4px #ececec}
.sb-frame .lbl{color:#c3bfb4;font-style:italic;font-size:.82rem}
.sb-frame .secs{position:absolute;right:5px;bottom:4px;font-size:.72rem;color:#6a6a6a;
  background:rgba(255,255,255,.85);padding:0 3px;border-radius:2px}
table.sb-tbl .cell{white-space:pre-wrap;word-break:break-word;line-height:1.42}
tr.sb-blank td{height:74px}
tr.sb-blank .sb-frame .lbl{display:none}
</style>
"""


def _frame(label: str, secs: str) -> str:
    lbl = f'<span class="lbl">{html.escape(label)}</span>' if label else ""
    sec = f'<span class="secs">{html.escape(secs)}</span>' if secs else ""
    return f'<div class="sb-frame">{lbl}{sec}</div>'


def _render_storyboard(script: str) -> None:
    """Render the finished script as a storyboard sheet above the questionnaire."""
    parsed = shots.parse_shots(script)
    if not parsed:
        st.caption(t("q.script_review"))
        with st.container(border=True):
            st.markdown(script or "—")
        return

    todo = t("storyboard.frame_todo")
    body = ""
    for i, s in enumerate(parsed, 1):
        body += (
            f'<tr><td class="sb-no"><span class="cut">{i}</span></td>'
            f'<td><div class="cell">{html.escape(s.get("shot_type") or "—")}</div></td>'
            f'<td class="sb-pic">{_frame(todo, (s.get("duration") or "").strip())}</td>'
            f'<td><div class="cell">{html.escape(s.get("visual") or "")}</div></td>'
            f'<td><div class="cell">{html.escape(s.get("audio") or "")}</div></td></tr>'
        )
    # Blank row after the numbered shots (row 4): an empty picture cell.
    body += (
        f'<tr class="sb-blank"><td class="sb-no"></td><td></td>'
        f'<td class="sb-pic">{_frame("", "")}</td><td></td><td></td></tr>'
    )
    header = "".join(
        f"<th>{html.escape(h)}</th>"
        for h in (t("storyboard.col_no"), t("storyboard.col_shot"), t("storyboard.col_frame"),
                  t("storyboard.col_plot"), t("storyboard.col_line"))
    )
    rd = state.current_round()
    subtitle = html.escape(state.topic_text(rd["topic"], "title", get_lang()))
    st.markdown(
        f'{_SB_CSS}<div class="sb-sheet">'
        f'<div class="sb-hd"><span class="sb-ttl">{html.escape(t("storyboard.title"))}</span>'
        f'<span class="sb-sub">{subtitle}</span></div>'
        f'<table class="sb-tbl"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'
        f"</div>",
        unsafe_allow_html=True,
    )


def render() -> None:
    ridx = st.session_state["round_idx"]
    # Show the finished script as a storyboard table above the questionnaire so
    # the participant can refer to it while answering.
    _render_storyboard(state.current_script())
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
    # condition where the AI asks structured questions).
    if state.current_round()["condition"] == "E":
        likert("ai_q_quality", t("q.ai_q_quality"))

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
        shot_annotations_json=json.dumps(shot_annotations, ensure_ascii=False),
    )
    if ridx == _ATTENTION_ROUND:
        db.update_participant(
            pid, attention_ok=int(answers.get("attention") == _ATTENTION_EXPECTED)
        )
    state.log_event("questionnaire_submit")
    state.advance_round()
