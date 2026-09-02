from __future__ import annotations

import re

"""Best-effort parsing of generated storyboard markdown into per-shot dicts.

The system prompt enforces numbered shots with bracketed field labels, laid out
over two lines per shot — 【画面描写】 alone, then 【时长】【拍法】【台词/音效】 —
in zh, or ja (【画面】 / 【秒数】【カメラ】【セリフ・音】). Labels are kept
language-aware so a Japanese storyboard parses the same way a Chinese one does
(the field LABELS are the parse anchor; the field CONTENT is in the
participant's language).
Callers must handle an empty result (-> whole-text fallback, parse_ok=0).
"""

# Field label (inside 【】) → canonical key; covers zh + ja labels and aliases.
# Shot-type labels (景别/拍法/カメラ/サイズ…) map to shot_type — surfaced in the
# pre-questionnaire storyboard table (景别/カメラ column); they also act as split
# anchors so their text never bleeds into the visual/audio fields.
# Labels are matched case-insensitively so English 【Duration】/【duration】 both hit.
_FIELD_RE = re.compile(
    r"【\s*(景别|拍法|画面描写|台词/音效|台词|音效|时长"
    r"|サイズ|ショットサイズ|カメラ|映像|画面|ビジュアル|セリフ・音|セリフ|音声|効果音|音|尺|秒数|長さ|時間"
    r"|Duration|Seconds|Sec|Shot|Camera|Visual|Action|Audio|Dialogue|Sound)"
    r"[^】]*】\s*[::]?\s*",
    re.IGNORECASE,
)
_FIELD_MAP = {
    # zh
    "景别": "shot_type", "拍法": "shot_type", "画面描写": "visual",
    "台词/音效": "audio", "台词": "audio", "音效": "audio", "时长": "duration",
    # ja
    "サイズ": "shot_type", "ショットサイズ": "shot_type", "カメラ": "shot_type",
    "映像": "visual", "画面": "visual", "ビジュアル": "visual",
    "セリフ・音": "audio", "セリフ": "audio", "音声": "audio", "効果音": "audio", "音": "audio",
    "尺": "duration", "秒数": "duration", "長さ": "duration", "時間": "duration",
    # en (canonical Title-case; _field_key() folds other casings onto these)
    "Duration": "duration", "Seconds": "duration", "Sec": "duration",
    "Shot": "shot_type", "Camera": "shot_type",
    "Visual": "visual", "Action": "visual",
    "Audio": "audio", "Dialogue": "audio", "Sound": "audio",
}


def _field_key(label: str) -> str | None:
    """Canonical field key for a matched label; case-folds English labels
    (【DURATION】/【duration】 → 'duration') while leaving CJK labels untouched."""
    label = label.strip()
    return _FIELD_MAP.get(label) or _FIELD_MAP.get(label.title())


# Shot boundary: "1." / "1、" / "镜头1" / "カット1" / "Shot 1" / "**1." / "#### 镜头 1"
_SHOT_SPLIT_RE = re.compile(
    r"(?m)^\s*(?:[#*>\-\s]*)?(?:镜头|カット|ショット|Shot|Cut)?\s*(\d{1,2})\s*[\.、::|]",
    re.IGNORECASE,
)
# Fallback shot boundaries for when the model omits the numbering _SHOT_SPLIT_RE
# keys off of: whichever field each shot LEADS with is a reliable cut point.
# Since 2026-09-01 (§9) that is the visual field; scripts written in the older
# duration-first order are still parsed by the second pattern, so a hand-edited
# or resumed script from either era survives.
_VISUAL_START_RE = re.compile(
    r"【\s*(?:画面描写|画面|映像|ビジュアル|Visual|Action)[^】]*】", re.IGNORECASE
)
_DURATION_START_RE = re.compile(
    r"【\s*(?:时长|秒数|尺|長さ|時間|Duration|Seconds|Sec)[^】]*】", re.IGNORECASE
)
# A bare next-shot number dangling at a field-block tail ("2." alone) — trimmed
# so it doesn't pollute the previous shot's audio/raw. Anchored to a whole line
# and requiring the punctuation: without both, it also ate any 1-2 digit number
# that legitimately ENDS an audio field (「【台词/音效】倒计时 10」→「倒计时」),
# and since the two-line layout made this fallback the ordinary path for
# un-numbered scripts, that deletion would land in stored + embedded content.
_TRAILING_NUM_RE = re.compile(
    r"\n[\s\-*#>]*(?:镜头|カット|ショット|Shot|Cut)?\s*\d{1,2}\s*[\.、::|]\s*$", re.IGNORECASE
)


def _split_numbered(text: str) -> list[tuple[int, int, int]]:
    """Boundaries from ascending shot numbers (1, 2, 3 …); skips stray numbers."""
    bounds = [(m.start(), int(m.group(1))) for m in _SHOT_SPLIT_RE.finditer(text)]
    blocks: list[tuple[int, int, int]] = []  # (start, end, idx)
    expect = 1
    for pos, num in bounds:
        if num == expect:
            if blocks:
                blocks[-1] = (blocks[-1][0], pos, blocks[-1][2])
            blocks.append((pos, len(text), num))
            expect += 1
    return blocks


def _field_keys(seg: str) -> list[str]:
    return [k for k in (_field_key(m.group(1)) for m in _FIELD_RE.finditer(seg)) if k]


def _has_visual(seg: str) -> bool:
    return "visual" in _field_keys(seg)


def _split_by_lead(text: str, lead_re: re.Pattern) -> list[tuple[int, int, int]]:
    """Fallback boundaries: each shot starts with `lead_re`'s field. Needs >= 2
    markers to count as a real multi-shot split.

    Two guards keep this fallback from beating a correct split with a wrong one:
    - any field label BEFORE the first marker means this field trails its shot
      rather than leading it, so cutting at the markers would misalign every
      field → refuse (an honest parse_ok=0 beats silently wrong per-shot data).
      This is also what makes trying both lead patterns safe: on a given script
      at most one of them can lead, the other is vetoed here;
    - a marker block with no visual field (a 合計 line, a stray label) is folded
      into the previous block instead of becoming a bogus extra shot.

    Two further guards exist because the lead field is now the VISUAL one, which
    (unlike duration) is content-bearing and may legitimately appear twice — the
    script textarea is freely editable and says nothing about keeping the 【】
    labels intact:
    - a span carrying ONLY the lead field is a continuation line, not a shot.
      It must NOT be folded into the previous span either: it belongs to the
      shot that FOLLOWS it, so folding backwards would attribute one shot's
      words to its predecessor. Refuse, and let the numbered split win;
    - a span carrying two duration fields is two shots glued together by a lost
      lead label (duration is the one field that is exactly once per shot —
      audio and shot_type each have several aliases that can co-occur).
      Cutting it anywhere is guesswork, so refuse.
    """
    marks = [m.start() for m in lead_re.finditer(text)]
    if len(marks) < 2:
        return []
    if _FIELD_RE.search(text[: marks[0]]):
        return []
    spans: list[list[int]] = []
    for i, s in enumerate(marks):
        e = marks[i + 1] if i + 1 < len(marks) else len(text)
        if spans and not _has_visual(text[s:e]):
            spans[-1][1] = e
        else:
            spans.append([s, e])
    for s, e in spans:
        keys = _field_keys(text[s:e])
        others = [k for k in keys if k != "visual"]
        if "visual" not in keys or not others:
            return []      # no visual at all / lead-only continuation line
        # A shot glued to the next one repeats the WHOLE trailing field set
        # (duration + shot type + audio). One repeated field is ordinary: a
        # 合計 line adds a second duration, and 【台词】+【音效】 or 【景别】+【拍法】
        # both fold onto one key. So require >= 2 distinct repeats before
        # calling it two shots — and then refuse rather than cut it wrong.
        if sum(1 for k in set(others) if others.count(k) > 1) >= 2:
            return []
    return [(s, e, i + 1) for i, (s, e) in enumerate(spans)]


def parse_shots(text: str) -> list[dict]:
    """Parse into [{idx, shot_type, visual, audio, duration, raw}]; [] on failure."""
    text = (text or "").strip()
    if not text:
        return []

    # Prefer numbered boundaries; fall back to (or upgrade to) duration-field
    # boundaries when numbering is missing or only partial — more shots wins, so a
    # script that dropped its "1. 2. 3." still splits instead of collapsing to one.
    blocks = _split_numbered(text)
    from_field = False
    for lead_re in (_VISUAL_START_RE, _DURATION_START_RE):
        alt = _split_by_lead(text, lead_re)
        if len(alt) > len(blocks):
            blocks, from_field = alt, True
    if not blocks:
        return []

    shots = []
    for start, end, idx in blocks:
        raw = text[start:end].strip()
        if from_field:
            # a dangling next-shot number at the tail belongs to the next block
            raw = _TRAILING_NUM_RE.sub("", raw)
        shot: dict = {"idx": idx, "raw": raw}
        # split() with one capture group returns [prefix, label1, content1, label2, content2, …]
        parts = _FIELD_RE.split(raw)
        for label, content in zip(parts[1::2], parts[2::2]):
            key = _field_key(label)
            if not key:
                continue
            content = content.strip().strip("|").strip()
            # cut at the next markdown table cell / line group if huge
            if key in shot and shot[key]:
                shot[key] = f"{shot[key]} {content}"
            else:
                shot[key] = content
        shots.append(shot)

    # require at least the visual field on the majority of shots
    ok = sum(1 for s in shots if s.get("visual"))
    return shots if ok >= max(1, len(shots) // 2 + 1) else []


def strip_format(text: str) -> str:
    """Content-only text (visual + audio) for embedding; falls back to
    label-stripped raw text when structured parsing fails."""
    shots = parse_shots(text)
    if shots:
        parts = []
        for s in shots:
            for key in ("visual", "audio"):
                if s.get(key):
                    parts.append(s[key])
        return "\n".join(parts)
    # fallback: drop field labels, shot numbering and markdown table pipes
    out = _FIELD_RE.sub(" ", text or "")
    out = _SHOT_SPLIT_RE.sub(" ", out)
    out = re.sub(r"[|#*`\-]{1,}", " ", out)
    return re.sub(r"\s+", " ", out).strip()
