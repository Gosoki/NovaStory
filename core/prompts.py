from __future__ import annotations

from core import config

# Pure functions, no Streamlit dependency. The participant's language is passed
# in explicitly by the view layer (lang="ja" for the formal study, "zh" for the
# researcher's testing). Output language + field labels follow `lang` so a
# Japanese participant never sees Chinese AI output; the storyboard field
# labels stay in sync with core/shots.py's parser for both languages.

_DEFAULT_LANG = "ja"


def _norm(lang: str) -> str:
    return lang if lang in ("zh", "ja", "en") else "ja"


def _loc(value, lang: str) -> str:
    """Topic fields may be a plain str (legacy) or {"ja":..,"zh":..} dict."""
    if isinstance(value, dict):
        return value.get(lang) or value.get("ja") or value.get("zh") or ""
    return value or ""


def _int_or(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _shots(topic: dict) -> int:
    return _int_or(topic.get("shot_count"), 3)


def _total(topic: dict) -> int:
    return _int_or(topic.get("total_seconds"), 15)


def _raw(value, lang: str) -> str:
    """The value actually present for `lang`, with NO fallback (legacy plain-str
    fields count as present in every language — they are all there is)."""
    if isinstance(value, dict):
        return (value.get(lang) or "").strip()
    return (value or "").strip()


def situation_lang(topic: dict, lang: str) -> str:
    """The one language BOTH halves of the situation exist in.

    `scenario` and `choices` are separate fields since 2026-09-01 (§15), so
    `_loc`'s ja/zh fallback applies to each of them independently — a topic
    translated only halfway would otherwise glue an English setup to a Japanese
    choice list, both on the topic card and inside the model prompt (「any pick
    yields a single-language session」, views/_lang.py, would silently stop being
    true at field granularity). Resolve once, use everywhere."""
    sc, ch = topic.get("scenario", ""), topic.get("choices", "")
    has_choices = any(_raw(ch, c) for c in ("ja", "zh", "en"))
    for cand in (_norm(lang), "ja", "zh", "en"):
        if _raw(sc, cand) and (not has_choices or _raw(ch, cand)):
            return cand
    return _norm(lang)


def scenario_text(topic: dict, lang: str, *, with_note: bool = True) -> str:
    """Full situation text = the setup sentence + the "which will you do?" list.

    They are two fields in topics.json since 2026-09-01 (§15) so the UI can grey
    the choice list out and mark it as non-binding; the model still sees the same
    sentence sequence it saw before, so generation is unchanged."""
    key = situation_lang(topic, lang)
    scenario = _loc(topic.get("scenario", ""), key).strip()
    choices = _loc(topic.get("choices", ""), key).strip()
    if not choices:
        return scenario
    # 选择列表要**明确标成例子**。题目卡自 2026-09-01(§15)起把它括进浅色括号并注明
    # 「别的展开也可以」,而 prompt 这边原样断言,两边就不一致了:被试写了列表之外的
    # 走向,模型仍被锚在列表上,生成结果会往回拉。这个偏差还与条件相关 —— D/E 能把稿子
    # 改回来,C 不能 —— 于是会**系统性地压低 C 的保真**,而保真恰好是 H1 的主终点。
    sep = " " if key == "en" else ""
    if not with_note:
        # with_note=False 给的是「这道题的情境本身」,不含那句写给模型看的元指令。
        # 机器基线用它当**假装的用户创意**(scripts/baseline_gen.py 的 seed),
        # embed 又靠这个字符串反查基线属于哪道题 —— 把「以上的分支只是例子」当成
        # 用户的创意喂进去,基线质心量的就不是「纯 AI 会写成什么样」了。
        return f"{scenario}{sep}{choices}"
    note = _CHOICE_NOTE[key if key in _CHOICE_NOTE else "ja"]
    return f"{scenario}{sep}{choices}{sep}{note}"


_CHOICE_NOTE = {
    "zh": "(以上的分支只是例子;若用户的创意走向别处,以用户的创意为准。)",
    "en": "(Those branches are only examples; if the user's idea goes somewhere else, follow the user's idea.)",
    "ja": "(上の分岐はあくまで例です。ユーザーのアイデアが別の展開なら、そちらを優先してください。)",
}


def _topic_block(topic: dict, intent: str, lang: str) -> str:
    # 题名与情境必须解析成同一种语言。情境走 situation_lang(两半同语言),题名若还用
    # 会话语言,半译的题库就会给模型一个「日文题名 + 英文情境」的混语前提 ——
    # 题目卡那边已经这么修过一次,这里是同一个坑的另一半。
    key = situation_lang(topic, lang)
    title = _loc(topic.get("title", ""), key).strip()
    scenario = scenario_text(topic, lang)
    intent = (intent or "").strip()
    if _norm(lang) == "zh":
        return f"主题:{title}\n情境:{scenario}\n用户的核心创意:{intent}"
    if _norm(lang) == "en":
        return f"Theme: {title}\nSituation: {scenario}\nThe user's core idea: {intent}"
    return f"テーマ:{title}\n状況:{scenario}\nユーザーの核となるアイデア:{intent}"


# ---------------- script generation (all conditions) ----------------

def build_system_script(topic: dict, lang: str = _DEFAULT_LANG) -> str:
    n, total = _shots(topic), _total(topic)
    if _norm(lang) == "zh":
        return (
            "你是一名专业短视频分镜脚本助手。\n"
            f"必须严格输出 {n} 个镜头,总时长 {total} 秒。\n"
            "每个镜头的时长由你按内容节奏自主分配(特写/插入镜头可短至 1–2 秒,"
            "动作/对白镜头可长至 6–8 秒甚至更长),但所有镜头的时长之和**必须严格等于** "
            f"{total} 秒。\n"
            "排版必须严格照下面来:序号**单独占一行**(`1.`、`2.`、`3.` …),序号下面**分两行**写"
            "—— 第一行只写【画面描写】;第二行写【时长】X 秒(X 是你为该镜分配的具体秒数)、"
            "【拍法】(推/拉/远近等)、【台词/音效】。镜头与镜头之间空一行。\n"
            "格式示例:\n"
            "1.\n"
            "【画面描写】…\n"
            "【时长】3 秒 【拍法】特写 【台词/音效】…\n"
            "\n"
            "2.\n"
            "【画面描写】…\n"
            "【时长】5 秒 【拍法】中景 【台词/音效】…\n"
            "\n"
            "只输出这个编号列表,禁止添加解释、前言或总结。\n"
            "输出语言:中文。"
        )
    if _norm(lang) == "en":
        return (
            "You are a professional short-video storyboard writer.\n"
            f"You must output exactly {n} shots, {total} seconds in total.\n"
            "You decide each shot's length by the pacing of the content (a close-up/"
            "insert can be as short as 1–2 seconds; an action/dialogue shot can run "
            "6–8 seconds or longer), but the durations of all shots MUST sum to "
            f"**exactly {total} seconds**.\n"
            "Follow this layout exactly: the shot number sits **alone on its own line** "
            "(`1.`, `2.`, `3.` …), and beneath it the shot is written **over two lines** — "
            "the first line carries only 【Visual】; the second carries 【Duration】X s (X is "
            "the number of seconds you assigned to that shot), 【Shot】(push/pull, "
            "wide/tight, etc.) and 【Audio】. Leave a blank line between shots.\n"
            "Format example:\n"
            "1.\n"
            "【Visual】…\n"
            "【Duration】3 s 【Shot】close-up 【Audio】…\n"
            "\n"
            "2.\n"
            "【Visual】…\n"
            "【Duration】5 s 【Shot】wide 【Audio】…\n"
            "\n"
            "Output only this numbered list; add no explanation, preamble, or summary.\n"
            "Output language: English."
        )
    return (
        "あなたはプロのショート動画の絵コンテ作成アシスタントです。\n"
        f"必ず {n} カット、合計 {total} 秒で出力してください。\n"
        "各カットの長さは内容のテンポに応じて自由に配分してください"
        "(クローズアップ/インサートは1〜2秒、アクション/セリフは6〜8秒以上でも可)。"
        f"ただし全カットの長さの合計は必ず {total} 秒ちょうどにしてください。\n"
        "レイアウトは必ず次のとおりにしてください。番号は**それだけで1行**(`1.`、`2.`、`3.` …)、"
        "その下を**2行に分けて**書きます —— 1行目は【画面】だけ、2行目に【秒数】X秒"
        "(Xはそのカットに割り当てた具体的な秒数)、【カメラ】(寄り・引き・動きなど)、【セリフ・音】。"
        "カットとカットのあいだは1行空けてください。\n"
        "形式の例:\n"
        "1.\n"
        "【画面】…\n"
        "【秒数】3秒 【カメラ】クローズアップ 【セリフ・音】…\n"
        "\n"
        "2.\n"
        "【画面】…\n"
        "【秒数】5秒 【カメラ】引き 【セリフ・音】…\n"
        "\n"
        "この番号付きリストのみを出力し、説明・前置き・まとめは一切加えないでください。\n"
        "出力言語:日本語。"
    )


def build_user_script(topic: dict, intent: str, lang: str = _DEFAULT_LANG) -> str:
    """C / D first generation: straight from the intent."""
    n, total = _shots(topic), _total(topic)
    block = _topic_block(topic, intent, lang)
    if _norm(lang) == "zh":
        return (
            f"{block}\n\n"
            f"请围绕以上条件生成 {n} 个镜头、总时长 {total} 秒的分镜脚本,"
            "单镜时长请你自己按节奏分配(加总须等于总时长)。"
        )
    if _norm(lang) == "en":
        return (
            f"{block}\n\n"
            f"Based on the conditions above, create a storyboard of {n} shots totaling "
            f"{total} seconds; you allocate each shot's length by pacing (the total must "
            "equal the overall duration)."
        )
    return (
        f"{block}\n\n"
        f"以上の条件にもとづき、{n} カット・合計 {total} 秒の絵コンテを作成してください。"
        "各カットの長さはテンポに合わせて配分してください(合計は総尺と一致させること)。"
    )


def _answer_lines(answers: list[dict]) -> tuple[str, list[str]]:
    lines = "\n".join(
        f"- {a['question']} → {a['chosen']}"
        for a in answers
        if a.get("chosen") and not a.get("ai_decided")
    )
    delegated = [a["question"] for a in answers if a.get("ai_decided")]
    return lines, delegated


def build_user_script_from_answers(
    topic: dict, intent: str, answers: list[dict], lang: str = _DEFAULT_LANG
) -> str:
    """E first generation: intent + the user's confirmed Q&A answers."""
    n, total = _shots(topic), _total(topic)
    lines, delegated = _answer_lines(answers)
    block = _topic_block(topic, intent, lang)
    if _norm(lang) == "zh":
        parts = [block]
        if lines:
            parts.append(f"用户通过问答确认的设定(必须严格体现在脚本中):\n{lines}")
        if delegated:
            parts.append("以下方面用户交给你自由发挥:\n" + "\n".join(f"- {q}" for q in delegated))
        parts.append(
            f"请围绕以上条件生成 {n} 个镜头、总时长 {total} 秒的分镜脚本,"
            "单镜时长请你自己按节奏分配(加总须等于总时长)。"
        )
        return "\n\n".join(parts)
    if _norm(lang) == "en":
        parts = [block]
        if lines:
            parts.append(f"Settings the user confirmed via the Q&A (these MUST be reflected in the script):\n{lines}")
        if delegated:
            parts.append("The user leaves the following up to you:\n" + "\n".join(f"- {q}" for q in delegated))
        parts.append(
            f"Based on the conditions above, create a storyboard of {n} shots totaling "
            f"{total} seconds; you allocate each shot's length by pacing (the total must "
            "equal the overall duration)."
        )
        return "\n\n".join(parts)
    parts = [block]
    if lines:
        parts.append(f"ユーザーが質問への回答で確定した設定(必ず脚本に反映すること):\n{lines}")
    if delegated:
        parts.append("以下の点はユーザーがあなたに一任しています:\n" + "\n".join(f"- {q}" for q in delegated))
    parts.append(
        f"以上の条件にもとづき、{n} カット・合計 {total} 秒の絵コンテを作成してください。"
        "各カットの長さはテンポに合わせて配分してください(合計は総尺と一致させること)。"
    )
    return "\n\n".join(parts)


# ---------------- revision (post-generation loop) ----------------

def build_system_revision(topic: dict, lang: str = _DEFAULT_LANG) -> str:
    n, total = _shots(topic), _total(topic)
    if _norm(lang) == "zh":
        return (
            "你是一名专业短视频分镜脚本修订助手。用户会给你当前的分镜脚本和修改要求。\n"
            "规则:\n"
            "1. 严格按用户的要求修改;**没有被要求修改的部分尽量保持原样**(包括用户自己改过的措辞)。\n"
            "2. 修改要求可能很抽象(如『更搞笑』『更炸裂』),请把它落实为具体的画面/台词改动。\n"
            f"3. 输出完整的修订后脚本:{n} 个镜头,总时长严格等于 {total} 秒。"
            "排版必须是:序号**单独占一行**(`1.`、`2.`、`3.` …,即使原稿没有编号也要补上),"
            "序号下面第一行只写【画面描写】,第二行写【时长】X 秒 【拍法】 【台词/音效】,镜头之间空一行,"
            "例:\n"
            "1.\n"
            "【画面描写】…\n"
            "【时长】3 秒 【拍法】特写 【台词/音效】…\n"
            "\n"
            "4. 只输出脚本本身,禁止解释或前言。输出语言:中文。"
        )
    if _norm(lang) == "en":
        return (
            "You are a professional short-video storyboard revision assistant. The user "
            "will give you the current storyboard and a revision request.\n"
            "Rules:\n"
            "1. Revise strictly per the user's request; **leave the parts they did not ask "
            "to change as they are** (including wording the user edited themselves).\n"
            "2. The request may be abstract (e.g. 'make it funnier', 'make it more "
            "explosive'); turn it into concrete changes to the visuals/dialogue.\n"
            f"3. Output the complete revised script: {n} shots, totaling exactly {total} "
            "seconds. The layout must be: the shot number alone on its own line (`1.`, "
            "`2.`, `3.` …, add numbering even if the original lacked it), then a line "
            "carrying only 【Visual】, then a line carrying 【Duration】X s 【Shot】"
            "【Audio】, with a blank line between shots, e.g.\n"
            "1.\n"
            "【Visual】…\n"
            "【Duration】3 s 【Shot】close-up 【Audio】…\n"
            "\n"
            "4. Output only the script itself, no explanation or preamble. Output "
            "language: English."
        )
    return (
        "あなたはプロのショート動画の絵コンテ修正アシスタントです。"
        "ユーザーが現在の絵コンテと修正の要望を渡します。\n"
        "ルール:\n"
        "1. ユーザーの要望どおりに修正し、**要望されていない部分はできるだけそのまま残す**"
        "(ユーザー自身が書き換えた表現も含む)。\n"
        "2. 修正の要望は抽象的(「もっと面白く」「もっと派手に」など)なこともあります。"
        "それを具体的な映像/セリフの変更に落とし込んでください。\n"
        f"3. 修正後の完成版を出力してください:{n} カット、合計はちょうど {total} 秒。"
        "レイアウトは、番号を**それだけで1行**にし(`1.`、`2.`、`3.` …、元の原稿に番号が無くても付けること)、"
        "次の行に【画面】だけ、その次の行に【秒数】X秒 【カメラ】 【セリフ・音】、"
        "カットのあいだは1行空ける。例:\n"
        "1.\n"
        "【画面】…\n"
        "【秒数】3秒 【カメラ】クローズアップ 【セリフ・音】…\n"
        "\n"
        "4. 脚本本体のみを出力し、説明や前置きは禁止。出力言語:日本語。"
    )


def build_user_revision(
    topic: dict, intent: str, current_script: str, request_text: str, lang: str = _DEFAULT_LANG
) -> str:
    """D: free-form revision request against the user's latest script."""
    block = _topic_block(topic, intent, lang)
    if _norm(lang) == "zh":
        return (
            f"{block}\n\n当前脚本:\n{current_script.strip()}\n\n"
            f"用户的修改要求:{request_text.strip()}"
        )
    if _norm(lang) == "en":
        return (
            f"{block}\n\nCurrent script:\n{current_script.strip()}\n\n"
            f"The user's revision request: {request_text.strip()}"
        )
    return (
        f"{block}\n\n現在の脚本:\n{current_script.strip()}\n\n"
        f"ユーザーの修正の要望:{request_text.strip()}"
    )


def build_user_revision_from_answers(
    topic: dict, intent: str, current_script: str, answers: list[dict], lang: str = _DEFAULT_LANG
) -> str:
    """E follow-up rounds: revise the latest script per the new Q&A answers."""
    lines, delegated = _answer_lines(answers)
    block = _topic_block(topic, intent, lang)
    if _norm(lang) == "zh":
        parts = [block, f"当前脚本:\n{current_script.strip()}"]
        if lines:
            parts.append(f"用户通过新一轮问答确认的修改方向(必须落实):\n{lines}")
        if delegated:
            parts.append("以下方面用户交给你自由发挥:\n" + "\n".join(f"- {q}" for q in delegated))
        return "\n\n".join(parts)
    if _norm(lang) == "en":
        parts = [block, f"Current script:\n{current_script.strip()}"]
        if lines:
            parts.append(f"The revision direction the user confirmed via this new round of Q&A (must be applied):\n{lines}")
        if delegated:
            parts.append("The user leaves the following up to you:\n" + "\n".join(f"- {q}" for q in delegated))
        return "\n\n".join(parts)
    parts = [block, f"現在の脚本:\n{current_script.strip()}"]
    if lines:
        parts.append(f"ユーザーが新たな質問への回答で確定した修正の方向(必ず反映すること):\n{lines}")
    if delegated:
        parts.append("以下の点はユーザーがあなたに一任しています:\n" + "\n".join(f"- {q}" for q in delegated))
    return "\n\n".join(parts)


# ---------------- guided elicitation (condition E, JSON) ----------------

_JSON_SPEC_ZH = (
    "只输出 JSON,不要任何其他文字、不要 markdown 代码块。格式:"
    '{"questions":[{"dimension":"...","question":"...","options":["...","..."],"why":"..."}]}'
)
_JSON_SPEC_JA = (
    "JSONのみを出力してください。それ以外の文章やmarkdownのコードブロックは不要です。形式:"
    '{"questions":[{"dimension":"...","question":"...","options":["...","..."],"why":"..."}]}'
)
_JSON_SPEC_EN = (
    "Output JSON only — no other text and no markdown code block. Format:"
    '{"questions":[{"dimension":"...","question":"...","options":["...","..."],"why":"..."}]}'
)


def build_system_guidance_round1(lang: str = _DEFAULT_LANG) -> str:
    lo, hi = config.SUPPLEMENT_RANGE
    if _norm(lang) == "zh":
        return (
            "你是一名短视频分镜创作向导。用户是没有任何影视经验的新手,刚提交了一个故事创意。"
            "你的任务是生成一组引导问题,帮用户把心中的想法说清楚。\n"
            "要求:\n"
            "1. 必须包含三个固定维度,dimension 字段分别为 \"psychology\"(主角此刻最想得到/最想避免的是什么——欲望·动机,不是单纯的心情)、"
            "\"turning_point\"(这 15 秒里情势/价值发生翻转的一瞬在哪——如 期待→落空)、\"key_shot\"(用哪一格画面·什么构图撑起整段的戏剧核心)。\n"
            f"2. 再根据这个创意的具体内容,补充 {lo}-{hi} 个你认为对这个故事最重要的其他维度"
            "(dimension 用简短英文 slug,如 \"tone\"、\"ending\"、\"sound\"、\"detail\")。"
            "**镜头时长/秒数由你自行分配,不要就时长或秒数如何构成向用户提问。**\n"
            "3. 每个问题:question 为一句具体问题——**必须点到用户创意里的具体人物/物件/选择,不能是放到任何故事都成立的空话**"
            "(反例『主角最想得到什么?』→正例『捡到钱包的主角,心里最强的是哪个念头:“想当好人”/“怕麻烦想装没看见”/“好奇里面有多少钱”?』);"
            "options 必须为 3-4 个(不得少于 3 个)具体、互斥、可直接选用的选项;"
            "why 用一句大白话说清这问题为什么重要(避免专业术语)。\n"
            f"4. {_JSON_SPEC_ZH}\n"
            "5. question / options / why 全部使用中文(dimension 除外)。"
        )
    if _norm(lang) == "en":
        return (
            "You are a guide for short-video storyboard creation. The user is a complete "
            "novice with no filmmaking experience and has just submitted a story idea. "
            "Your job is to generate a set of guiding questions that help the user put "
            "what is in their head into words.\n"
            "Requirements:\n"
            "1. You must include three fixed dimensions, with the dimension field set to "
            "\"psychology\" (what the protagonist most wants to get / most wants to avoid "
            "right now — desire and motivation, not merely a mood), \"turning_point\" "
            "(where, within these 15 seconds, the situation/value flips — e.g. "
            "hope→disappointment), \"key_shot\" (which single frame · what composition "
            "carries the dramatic center of the whole piece).\n"
            f"2. Then, based on the specifics of this idea, add {lo}-{hi} other dimensions "
            "you think matter most for this story (use a short English slug for dimension, "
            "e.g. \"tone\", \"ending\", \"sound\", \"detail\"). "
            "**You decide each shot's length/seconds yourself — do NOT ask the user about "
            "timing or how the seconds are split.**\n"
            "3. For each question: question is one concrete question — **it MUST name the "
            "specific character/object/choice in the user's idea, never generic wording "
            "that would fit any story** (bad: 'What does the protagonist most want?' → "
            "good: 'When the hero picks up the wallet, what pulls at them most — "
            "\"wanting to look like a good person,\" \"worrying the owner is upset,\" or "
            "\"just not wanting the hassle\"?'); "
            "options MUST be 3-4 (never fewer than 3) concrete, mutually exclusive, "
            "directly selectable choices; why is one plain-language sentence (no jargon) "
            "on why it matters.\n"
            f"4. {_JSON_SPEC_EN}\n"
            "5. question / options / why must all be in English (except dimension)."
        )
    return (
        "あなたはショート動画の絵コンテ制作のガイドです。"
        "ユーザーは映像経験のまったくない初心者で、物語のアイデアを送ってきたところです。"
        "あなたの仕事は、ユーザーが頭の中の考えを言葉にできるよう、ガイドとなる質問を作ることです。\n"
        "要件:\n"
        "1. 必ず3つの固定の観点を含めること。dimension フィールドはそれぞれ "
        "\"psychology\"(主人公が今、何を一番手に入れたい/避けたいか=欲求・動機。単なる気分ではない)、\"turning_point\"(この15秒の中で流れ・価値がひっくり返る一瞬はどこか。例:期待→裏切り)、"
        "\"key_shot\"(どの一枚・どんな構図でこの物語の見せ場=ドラマの中心を見せるか)。\n"
        f"2. さらにこのアイデアの内容に応じて、この物語にとって重要だと思う他の観点を {lo}〜{hi} 個補ってください"
        "(dimension は短い英語スラッグ、例:\"tone\"、\"ending\"、\"sound\"、\"detail\")。"
        "**各カットの尺・秒数の配分はあなたが決めます。時間配分や秒数の構成についてユーザーに質問しないこと。**\n"
        "3. 各質問:question は具体的な一文——**必ずユーザーのアイデアに出てくる具体的な人物・物・選択に触れること。どんな物語にも当てはまる一般論は禁止**"
        "(悪い例『主人公が一番手に入れたいものは?』→良い例『財布を拾った主人公の心を一番動かしているのは?「いい人でいたい」「落とし主が気の毒」「面倒を避けたい」など』);"
        "options は必ず3〜4個(2個は不可)の具体的で互いに排他的な、そのまま選べる選択肢;"
        "why は専門用語を避け、初心者に語りかけるやさしい一文で書き、文体は敬体(です・ます)で統一すること。\n"
        f"4. {_JSON_SPEC_JA}\n"
        "5. question / options / why はすべて日本語で(dimension を除く)。"
    )


def build_user_guidance_round1(topic: dict, intent: str, lang: str = _DEFAULT_LANG) -> str:
    return _topic_block(topic, intent, lang)


def build_system_guidance_followup(lang: str = _DEFAULT_LANG) -> str:
    lo, hi = config.FOLLOWUP_RANGE
    if _norm(lang) == "zh":
        return (
            "你是一名短视频分镜创作向导。用户已经有了一版分镜稿,并主动要求你继续引导。"
            f"请阅读当前稿子,找出 {lo}-{hi} 个最值得追问的薄弱处,生成引导问题。\n"
            "每个问题:dimension 用简短英文 slug;question 紧扣稿子具体内容;"
            "options 为 3-4 个具体、互斥、可直接选用的选项;why 为一句话说明追问理由。\n"
            "镜头时长/秒数由你自行分配,不要就时长或秒数如何构成向用户提问。\n"
            f"{_JSON_SPEC_ZH}\n"
            "question / options / why 全部使用中文(dimension 除外)。"
        )
    if _norm(lang) == "en":
        return (
            "You are a guide for short-video storyboard creation. The user already has a "
            "draft storyboard and has asked you to keep guiding them. "
            f"Read the current draft, find the {lo}-{hi} weak spots most worth probing, "
            "and generate guiding questions.\n"
            "For each question: use a short English slug for dimension; question ties to "
            "the specifics of the draft; options are 3-4 concrete, mutually exclusive, "
            "directly selectable choices; why is one sentence giving the reason for "
            "probing.\n"
            "You decide shot durations yourself — do NOT ask the user about timing or "
            "how the seconds are split.\n"
            f"{_JSON_SPEC_EN}\n"
            "question / options / why must all be in English (except dimension)."
        )
    return (
        "あなたはショート動画の絵コンテ制作のガイドです。"
        "ユーザーはすでに絵コンテの草案を持っていて、続けてガイドしてほしいと求めています。"
        f"現在の草案を読み、最も掘り下げる価値のある弱点を {lo}〜{hi} 個見つけ、ガイドの質問を作ってください。\n"
        "各質問:dimension は短い英語スラッグ;question は草案の具体的な内容に即して;"
        "options は3〜4個の具体的で互いに排他的な、そのまま選べる選択肢;why は掘り下げる理由を一文で。\n"
        "各カットの尺・秒数の配分はあなたが決めます。時間配分や秒数の構成についてユーザーに質問しないこと。\n"
        f"{_JSON_SPEC_JA}\n"
        "question / options / why はすべて日本語で(dimension を除く)。"
    )


def build_user_guidance_followup(
    topic: dict, intent: str, current_script: str, lang: str = _DEFAULT_LANG
) -> str:
    block = _topic_block(topic, intent, lang)
    if _norm(lang) == "zh":
        return f"{block}\n\n当前分镜稿:\n{current_script.strip()}"
    if _norm(lang) == "en":
        return f"{block}\n\nCurrent storyboard draft:\n{current_script.strip()}"
    return f"{block}\n\n現在の絵コンテ草案:\n{current_script.strip()}"
