from __future__ import annotations

import re

"""Best-effort parsing of generated storyboard markdown into per-shot dicts.

The system prompt enforces numbered shots with bracketed field labels, in zh
(【时长】X秒【拍法】【画面描写】【台词/音效】) or ja (【秒数】X秒【カメラ】
【画面】【セリフ・音】). Labels are kept language-aware so a Japanese
storyboard parses the same way a Chinese one does (the field LABELS are the
parse anchor; the field CONTENT is in the participant's language).
Callers must handle an empty result (-> whole-text fallback, parse_ok=0).
"""

# Field label (inside 【】) → canonical key; covers zh + ja labels and aliases.
# Shot-type labels (景别/拍法/カメラ/サイズ…) stay in _FIELD_RE as split anchors
# (so their text never bleeds into visual/audio content) but map to no key —
# nothing consumes a shot_type field (v3 measures use visual/audio only).
_FIELD_RE = re.compile(
    r"【\s*(景别|拍法|画面描写|台词/音效|台词|音效|时长"
    r"|サイズ|ショットサイズ|カメラ|映像|画面|ビジュアル|セリフ・音|セリフ|音声|効果音|音|尺|秒数|長さ|時間)"
    r"[^】]*】\s*[::]?\s*"
)
_FIELD_MAP = {
    # zh
    "画面描写": "visual",
    "台词/音效": "audio", "台词": "audio", "音效": "audio", "时长": "duration",
    # ja
    "映像": "visual", "画面": "visual", "ビジュアル": "visual",
    "セリフ・音": "audio", "セリフ": "audio", "音声": "audio", "効果音": "audio", "音": "audio",
    "尺": "duration", "秒数": "duration", "長さ": "duration", "時間": "duration",
}
# Shot boundary: "1." / "1、" / "镜头1" / "カット1" / "**1." / "#### 镜头 1"
_SHOT_SPLIT_RE = re.compile(
    r"(?m)^\s*(?:[#*>\-\s]*)?(?:镜头|カット|ショット)?\s*(\d{1,2})\s*[\.、::|]"
)
# Every shot leads with its duration field, so it's a reliable fallback boundary
# when the model omits the numbering _SHOT_SPLIT_RE keys off of.
_DURATION_START_RE = re.compile(r"【\s*(?:时长|秒数|尺|長さ|時間)[^】]*】")
# A bare next-shot number dangling at a duration-block tail ("2." alone) —
# trimmed so it doesn't pollute the previous shot's audio/raw.
_TRAILING_NUM_RE = re.compile(r"[\s\-*#>]*(?:镜头|カット|ショット)?\s*\d{1,2}\s*[\.、::|]?\s*$")


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


def _has_visual(seg: str) -> bool:
    return any(_FIELD_MAP.get(m.group(1)) == "visual" for m in _FIELD_RE.finditer(seg))


def _split_by_duration(text: str) -> list[tuple[int, int, int]]:
    """Fallback boundaries: each shot starts with its duration field. Needs >= 2
    markers to count as a real multi-shot split.

    Two guards keep this fallback from beating a correct split with a wrong one:
    - any field label BEFORE the first marker means durations trail their shots,
      so cutting at markers would misalign every field → refuse (an honest
      parse_ok=0 beats silently wrong per-shot data);
    - a marker block with no visual field (a 合計 line, a stray label) is folded
      into the previous block instead of becoming a bogus extra shot.
    """
    marks = [m.start() for m in _DURATION_START_RE.finditer(text)]
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
    if not all(_has_visual(text[s:e]) for s, e in spans):
        return []
    return [(s, e, i + 1) for i, (s, e) in enumerate(spans)]


def parse_shots(text: str) -> list[dict]:
    """Parse into [{idx, visual, audio, duration, raw}]; [] on failure."""
    text = (text or "").strip()
    if not text:
        return []

    # Prefer numbered boundaries; fall back to (or upgrade to) duration-field
    # boundaries when numbering is missing or only partial — more shots wins, so a
    # script that dropped its "1. 2. 3." still splits instead of collapsing to one.
    blocks = _split_numbered(text)
    dur_blocks = _split_by_duration(text)
    from_duration = len(dur_blocks) > len(blocks)
    if from_duration:
        blocks = dur_blocks
    if not blocks:
        return []

    shots = []
    for start, end, idx in blocks:
        raw = text[start:end].strip()
        if from_duration:
            # a dangling next-shot number at the tail belongs to the next block
            raw = _TRAILING_NUM_RE.sub("", raw)
        shot: dict = {"idx": idx, "raw": raw}
        # split() with one capture group returns [prefix, label1, content1, label2, content2, …]
        parts = _FIELD_RE.split(raw)
        for label, content in zip(parts[1::2], parts[2::2]):
            key = _FIELD_MAP.get(label)
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
