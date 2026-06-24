from __future__ import annotations

import re

"""Best-effort parsing of generated storyboard markdown into per-shot dicts.

The system prompt enforces numbered shots with bracketed field labels, in zh
(【时长】X秒【景别】【画面描写】【台词/音效】) or ja (【秒数】X秒【サイズ】
【画面】【セリフ・音】). Labels are kept language-aware so a Japanese
storyboard parses the same way a Chinese one does (the field LABELS are the
parse anchor; the field CONTENT is in the participant's language).
Callers must handle an empty result (-> whole-text fallback, parse_ok=0).
"""

# Field label (inside 【】) → canonical key; covers zh + ja labels and aliases.
_FIELD_RE = re.compile(
    r"【\s*(景别|画面描写|台词/音效|台词|音效|时长"
    r"|サイズ|ショットサイズ|映像|画面|ビジュアル|セリフ・音|セリフ|音声|効果音|音|尺|秒数|長さ)"
    r"[^】]*】\s*[::]?\s*"
)
_FIELD_MAP = {
    # zh
    "景别": "shot_type", "画面描写": "visual",
    "台词/音效": "audio", "台词": "audio", "音效": "audio", "时长": "duration",
    # ja
    "サイズ": "shot_type", "ショットサイズ": "shot_type",
    "映像": "visual", "画面": "visual", "ビジュアル": "visual",
    "セリフ・音": "audio", "セリフ": "audio", "音声": "audio", "効果音": "audio", "音": "audio",
    "尺": "duration", "秒数": "duration", "長さ": "duration",
}
# Shot boundary: "1." / "1、" / "镜头1" / "カット1" / "**1." / "#### 镜头 1" / "| 1 |"
_SHOT_SPLIT_RE = re.compile(
    r"(?m)^\s*(?:[#*>\-\s]*)?(?:镜头|カット|ショット)?\s*(\d{1,2})\s*[\.、::|]"
)


def parse_shots(text: str) -> list[dict]:
    """Parse into [{idx, shot_type, visual, audio, duration, raw}]; [] on failure."""
    text = (text or "").strip()
    if not text:
        return []

    bounds = [(m.start(), int(m.group(1))) for m in _SHOT_SPLIT_RE.finditer(text)]
    # keep only a sane ascending shot numbering (1, 2, 3 …) to skip stray numbers
    blocks: list[tuple[int, int, int]] = []  # (start, end, idx)
    expect = 1
    for pos, num in bounds:
        if num == expect:
            if blocks:
                blocks[-1] = (blocks[-1][0], pos, blocks[-1][2])
            blocks.append((pos, len(text), num))
            expect += 1
    if not blocks:
        return []

    shots = []
    for start, end, idx in blocks:
        raw = text[start:end].strip()
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
