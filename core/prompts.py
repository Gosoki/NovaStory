from __future__ import annotations

from core import config

# Pure functions, no Streamlit dependency. The participant's language is passed
# in explicitly by the view layer (lang="ja" for the formal study, "zh" for the
# researcher's testing). Output language + field labels follow `lang` so a
# Japanese participant never sees Chinese AI output; the storyboard field
# labels stay in sync with core/shots.py's parser for both languages.

_DEFAULT_LANG = "ja"


def _norm(lang: str) -> str:
    return "zh" if lang == "zh" else "ja"


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


def _topic_block(topic: dict, intent: str, lang: str) -> str:
    title = _loc(topic.get("title", ""), lang).strip()
    scenario = _loc(topic.get("scenario", ""), lang).strip()
    intent = (intent or "").strip()
    if _norm(lang) == "zh":
        return f"主题:{title}\n情境:{scenario}\n用户的核心创意:{intent}"
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
            "每个镜头**必须以序号开头**(`1.`、`2.`、`3.` …),后接 4 个字段(按此顺序):"
            "【时长】X 秒(X 是你为该镜分配的具体秒数)、【拍法】(推/拉/远近等)、【画面描写】、【台词/音效】。\n"
            "格式示例:`1. 【时长】3 秒 【拍法】特写 【画面描写】… 【台词/音效】…`\n"
            "只输出这个编号列表,禁止添加解释、前言或总结。\n"
            "输出语言:中文。"
        )
    return (
        "あなたはプロのショート動画の絵コンテ作成アシスタントです。\n"
        f"必ず {n} カット、合計 {total} 秒で出力してください。\n"
        "各カットの長さは内容のテンポに応じて自由に配分してください"
        "(クローズアップ/インサートは1〜2秒、アクション/セリフは6〜8秒以上でも可)。"
        f"ただし全カットの長さの合計は必ず {total} 秒ちょうどにしてください。\n"
        "各カットは**必ず番号で始め**(`1.`、`2.`、`3.` …)、続けて4項目(この順番):【秒数】X秒"
        "(Xはそのカットに割り当てた具体的な秒数)、【カメラ】(寄り・引き・動きなど)、【画面】、【セリフ・音】。\n"
        "形式の例:`1. 【秒数】3秒 【カメラ】クローズアップ 【画面】… 【セリフ・音】…`\n"
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
            "**每个镜头必须以序号开头**(`1.`、`2.`、`3.` …,即使原稿没有编号也要补上),"
            "后接【时长】X 秒【拍法】【画面描写】【台词/音效】四个字段(此顺序),"
            "例:`1. 【时长】3 秒 【拍法】… 【画面描写】… 【台词/音效】…`。\n"
            "4. 只输出脚本本身,禁止解释或前言。输出语言:中文。"
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
        "**各カットは必ず番号で始め**(`1.`、`2.`、`3.` …、元の原稿に番号が無くても付けること)、"
        "続けて【秒数】X秒【カメラ】【画面】【セリフ・音】の4項目(この順番)、"
        "例:`1. 【秒数】3秒 【カメラ】… 【画面】… 【セリフ・音】…`。\n"
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


def build_system_guidance_round1(lang: str = _DEFAULT_LANG) -> str:
    lo, hi = config.SUPPLEMENT_RANGE
    if _norm(lang) == "zh":
        return (
            "你是一名短视频分镜创作向导。用户是没有任何影视经验的新手,刚提交了一个故事创意。"
            "你的任务是生成一组引导问题,帮用户把心中的想法说清楚。\n"
            "要求:\n"
            "1. 必须包含三个固定维度,dimension 字段分别为 \"psychology\"(主角此刻的内心感受/动机)、"
            "\"turning_point\"(故事在哪里转折/抓住观众)、\"key_shot\"(最想让观众记住的画面)。\n"
            f"2. 再根据这个创意的具体内容,补充 {lo}-{hi} 个你认为对这个故事最重要的其他维度"
            "(dimension 用简短英文 slug,如 \"tone\"、\"ending\"、\"sound\")。\n"
            "3. 每个问题:question 为一句具体问题(必须紧扣用户创意的内容,不要泛泛而谈);"
            "options 为 3-4 个具体、互斥、可直接选用的选项;why 为一句话说明为什么这个问题对这个故事重要。\n"
            f"4. {_JSON_SPEC_ZH}\n"
            "5. question / options / why 全部使用中文(dimension 除外)。"
        )
    return (
        "あなたはショート動画の絵コンテ制作のガイドです。"
        "ユーザーは映像経験のまったくない初心者で、物語のアイデアを送ってきたところです。"
        "あなたの仕事は、ユーザーが頭の中の考えを言葉にできるよう、ガイドとなる質問を作ることです。\n"
        "要件:\n"
        "1. 必ず3つの固定の観点を含めること。dimension フィールドはそれぞれ "
        "\"psychology\"(主人公の今の感情/動機)、\"turning_point\"(物語のどこで転換し観客を惹きつけるか)、"
        "\"key_shot\"(最も観客の記憶に残したい画面)。\n"
        f"2. さらにこのアイデアの内容に応じて、この物語にとって重要だと思う他の観点を {lo}〜{hi} 個補ってください"
        "(dimension は短い英語スラッグ、例:\"tone\"、\"ending\"、\"sound\")。\n"
        "3. 各質問:question は具体的な一文(必ずユーザーのアイデアの内容に即し、抽象的にしないこと);"
        "options は3〜4個の具体的で互いに排他的な、そのまま選べる選択肢;"
        "why はなぜこの質問がこの物語に重要かを一文で。\n"
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
            f"{_JSON_SPEC_ZH}\n"
            "question / options / why 全部使用中文(dimension 除外)。"
        )
    return (
        "あなたはショート動画の絵コンテ制作のガイドです。"
        "ユーザーはすでに絵コンテの草案を持っていて、続けてガイドしてほしいと求めています。"
        f"現在の草案を読み、最も掘り下げる価値のある弱点を {lo}〜{hi} 個見つけ、ガイドの質問を作ってください。\n"
        "各質問:dimension は短い英語スラッグ;question は草案の具体的な内容に即して;"
        "options は3〜4個の具体的で互いに排他的な、そのまま選べる選択肢;why は掘り下げる理由を一文で。\n"
        f"{_JSON_SPEC_JA}\n"
        "question / options / why はすべて日本語で(dimension を除く)。"
    )


def build_user_guidance_followup(
    topic: dict, intent: str, current_script: str, lang: str = _DEFAULT_LANG
) -> str:
    block = _topic_block(topic, intent, lang)
    if _norm(lang) == "zh":
        return f"{block}\n\n当前分镜稿:\n{current_script.strip()}"
    return f"{block}\n\n現在の絵コンテ草案:\n{current_script.strip()}"
