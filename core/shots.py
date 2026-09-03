"""Best-effort parsing of generated storyboard markdown into per-shot dicts.

The system prompt enforces numbered shots with bracketed field labels, laid out
over two lines per shot — 【画面描写】 alone, then 【时长】【拍法】【台词/音效】 —
in zh, or ja (【画面】 / 【秒数】【カメラ】【セリフ・音】). Labels are kept
language-aware so a Japanese storyboard parses the same way a Chinese one does
(the field LABELS are the parse anchor; the field CONTENT is in the
participant's language).
Callers must handle an empty result (-> whole-text fallback, parse_ok=0).
"""

from __future__ import annotations

import re

# Field label (inside 【】) → canonical key; covers zh + ja labels and aliases.
# Shot-type labels (景别/拍法/カメラ/サイズ…) map to shot_type — surfaced in the
# pre-questionnaire storyboard table (景别/カメラ column); they also act as split
# anchors so their text never bleeds into the visual/audio fields.
# Labels are matched case-insensitively so English 【Duration】/【duration】 both hit.
_FIELD_RE = re.compile(
    r"【\s*(景别|拍法|运镜|画面描写|台词/音效|台词|对白|音效|声音|时长|时间"
    r"|サイズ|ショットサイズ|カメラ|映像|画面外の音|画面|ビジュアル|セリフ・音|セリフ|ナレーション|音声|効果音|BGM|SE|音|尺|秒数|秒|長さ|時間"
    r"|Duration|Seconds|Sec|Time|Length|Shot|Camera|Visual|Action|Audio|Dialogue|Narration|SFX|Sound)"
    # 标签后只允许「附注」式后缀(括号 / 冒号 / 斜杠 / 中点开头,如【时长(秒)】【秒数/長さ】),
    # 不再是任意 [^】]*:那会把【時間帯】吞成 duration、把「朝」当秒数 → dur_total NaN → spec_ok=0。
    r"(?:\s*[（(::/・][^】]*)?\s*】\s*[::]?\s*",
    re.IGNORECASE,
)
_FIELD_MAP = {
    # zh
    "景别": "shot_type", "拍法": "shot_type", "运镜": "shot_type", "画面描写": "visual",
    "台词/音效": "audio", "台词": "audio", "对白": "audio", "音效": "audio", "声音": "audio",
    "时长": "duration", "时间": "duration",
    # ja(「画面外の音」= 画外音,必须排在「画面」之前且归 audio)
    "サイズ": "shot_type", "ショットサイズ": "shot_type", "カメラ": "shot_type",
    "映像": "visual", "画面": "visual", "ビジュアル": "visual", "画面外の音": "audio",
    "セリフ・音": "audio", "セリフ": "audio", "ナレーション": "audio", "音声": "audio",
    "効果音": "audio", "BGM": "audio", "SE": "audio", "音": "audio",
    "尺": "duration", "秒数": "duration", "秒": "duration", "長さ": "duration", "時間": "duration",
    # en (canonical Title-case; _field_key() folds other casings onto these)
    "Duration": "duration", "Seconds": "duration", "Sec": "duration", "Time": "duration", "Length": "duration",
    "Shot": "shot_type", "Camera": "shot_type",
    "Visual": "visual", "Action": "visual",
    "Audio": "audio", "Dialogue": "audio", "Narration": "audio", "Sfx": "audio", "Sound": "audio",
}


def _field_key(label: str) -> str | None:
    """Canonical field key for a matched label; case-folds English labels
    (【DURATION】/【duration】 → 'duration') while leaving CJK labels untouched."""
    label = label.strip()
    return _FIELD_MAP.get(label) or _FIELD_MAP.get(label.upper()) or _FIELD_MAP.get(label.title())


# Shot boundary: "1." / "1、" / "镜头1" / "カット1" / "Shot 1" / "**1." / "#### 镜头 1"
# 标点类含全角句点「．」与半/全角右括号:日本被试用 IME 手改稿子极易打出「１．」「２）」;
# \d 在 str 模式下本就匹配全角数字,int() 也认;圆圈数字 ①-⑩ 另列(_shot_no 转成 int)。
# 占有量词(*+ / ?+,Python ≥3.11):以前 `^\s*(?:[#*>\-\s]*)?\s*` 三段可互相吞让的空白在长空白串上
# 灾难性回溯 —— 任何持链接的人粘贴 10 万个空格就能让整台服务冻结几分钟(ReDoS)。
_SHOT_SPLIT_RE = re.compile(
    r"(?m)^\s*+(?:[#*>\-]\s*+)*+(?:镜头|カット|ショット|Shot|Cut)?+\s*+(\d{1,2}|[①-⑩])\s*+[\.．、::|)）]",
    re.IGNORECASE,
)
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _shot_no(tok: str) -> int:
    return _CIRCLED.index(tok) + 1 if tok in _CIRCLED else int(tok)
# Fallback shot boundaries for when the model omits the numbering _SHOT_SPLIT_RE
# keys off of: whichever field each shot LEADS with is a reliable cut point.
# Since 2026-09-01 (§9) that is the visual field; scripts written in the older
# duration-first order are still parsed by the second pattern, so a hand-edited
# or resumed script from either era survives.
_LABEL_TAIL = r"(?:\s*[（(::/・][^】]*)?\s*】"   # 与 _FIELD_RE 同一条后缀规则
_VISUAL_START_RE = re.compile(
    r"【\s*(?:画面描写|画面|映像|ビジュアル|Visual|Action)" + _LABEL_TAIL, re.IGNORECASE
)
_DURATION_START_RE = re.compile(
    r"【\s*(?:时长|秒数|尺|長さ|時間|Duration|Seconds|Sec)" + _LABEL_TAIL, re.IGNORECASE
)
# A bare next-shot number dangling at a field-block tail ("2." alone) — trimmed
# so it doesn't pollute the previous shot's audio/raw. Anchored to a whole line
# and requiring the punctuation: without both, it also ate any 1-2 digit number
# that legitimately ENDS an audio field (「【台词/音效】倒计时 10」→「倒计时」),
# and since the two-line layout made this fallback the ordinary path for
# un-numbered scripts, that deletion would land in stored + embedded content.
_TRAILING_NUM_RE = re.compile(
    r"\n[\s\-*#>]*+(?:镜头|カット|ショット|Shot|Cut)?+\s*+(?:\d{1,2}|[①-⑩])\s*+[\.．、::|)）]\s*+$", re.IGNORECASE
)
# 模型偶尔在列表末尾补一行「合計:15秒」/「Total: 15 s」。提示词明令只输出编号列表,但它还是会来,
# 而它会整段并进最后一镜的 audio(编号排版)或 duration(无编号排版)—— audio 经 strip_format
# 进 embedding,等于给保真 Δ 掺了非内容文本。只剪块尾、只剪不含【】标签的那一行。
_SUMMARY_LINE_RE = re.compile(
    r"\n[\s\-*#>（(]*+(?:(?:\d{1,2}|[①-⑩])\s*+[\.．、::|)）]\s*+)?+(?:合計|合计|总计|総計|総尺|Total)[^\n【]*+$",
    re.IGNORECASE,
)


def _split_numbered(text: str) -> list[tuple[int, int, int]]:
    """Boundaries from ascending shot numbers (1, 2, 3 …); skips stray numbers."""
    bounds = [(m.start(), _shot_no(m.group(1))) for m in _SHOT_SPLIT_RE.finditer(text)]
    blocks: list[tuple[int, int, int]] = []  # (start, end, idx)
    expect = 1
    for pos, num in bounds:
        if num == expect:
            if blocks:
                blocks[-1] = (blocks[-1][0], pos, blocks[-1][2])
            blocks.append((pos, len(text), num))
            expect += 1
    # 幻影镜头守卫:一个编号块里连一个【字段】都没有(「4. 合計:15秒」「3. 以上です」),它不是镜头,
    # 并回前一块(首块则并入后一块)。以前它会成为第 4 镜,parse_ok 照样为 1,被试要多标一镜归属。
    if len(blocks) > 1:
        kept: list[tuple[int, int, int]] = []
        for s, e, idx in blocks:
            if _field_keys(text[s:e]):
                kept.append((s, e, idx))
            elif kept:
                ks, _, ki = kept[-1]
                kept[-1] = (ks, e, ki)
            else:
                blocks_head = (s, e)   # 首块无字段:让下一块从这里开始
                continue
        if kept and kept[0][0] != blocks[0][0]:
            kept[0] = (blocks[0][0], kept[0][1], kept[0][2])
        blocks = [(s, e, i + 1) for i, (s, e, _) in enumerate(kept)]
    return blocks


def _field_keys(seg: str) -> list[str]:
    return [k for k in (_field_key(m.group(1)) for m in _FIELD_RE.finditer(seg)) if k]


def _has_visual(seg: str) -> bool:
    return "visual" in _field_keys(seg)


def _split_by_leading_field(text: str, lead_re: re.Pattern) -> list[tuple[int, int, int]]:
    """Fallback boundaries: each shot starts with `lead_re`'s field. Needs >= 2
    markers to count as a real multi-shot split — and four veto guards (below)
    refuse rather than cut wrong.

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
        alt = _split_by_leading_field(text, lead_re)
        if len(alt) > len(blocks):
            blocks, from_field = alt, True
    if not blocks:
        return []

    shots = []
    for start, end, idx in blocks:
        raw = _SUMMARY_LINE_RE.sub("", text[start:end].strip()).strip()
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
