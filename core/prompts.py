from __future__ import annotations

from core import config


def _shots(topic: dict) -> int:
    return int(topic.get("shot_count", 3))


def _total(topic: dict) -> int:
    return int(topic.get("total_seconds", 15))


def _topic_block(topic: dict, intent: str) -> str:
    return (
        f"主题:{topic.get('title', '').strip()}\n"
        f"情境:{topic.get('scenario', '').strip()}\n"
        f"用户的核心创意:{(intent or '').strip()}"
    )


# ---------------- script generation (all conditions) ----------------

def build_system_script(topic: dict) -> str:
    n = _shots(topic)
    total = _total(topic)
    return (
        "你是一名专业短视频分镜脚本助手。\n"
        f"必须严格输出 {n} 个镜头,总时长 {total} 秒。\n"
        "每个镜头的时长由你按内容节奏自主分配(特写/插入镜头可短至 1-2 秒,"
        "动作/对白镜头可长至 6-8 秒甚至更长),但所有镜头的时长之和**必须严格等于** "
        f"{total} 秒。\n"
        "每个镜头需包含 4 个字段:【景别】、【画面描写】、【台词/音效】、"
        "【时长 X 秒】(X 是你为该镜分配的具体秒数)。\n"
        "用 Markdown 编号列表输出,禁止添加解释、前言或总结。\n"
        "输出语言:中文。"
    )


def build_user_script(topic: dict, intent: str) -> str:
    """C / D first generation: straight from the intent."""
    n, total = _shots(topic), _total(topic)
    return (
        f"{_topic_block(topic, intent)}\n\n"
        f"请围绕以上条件生成 {n} 个镜头、总时长 {total} 秒的分镜脚本,"
        "单镜时长请你自己按节奏分配(加总须等于总时长)。"
    )


def build_user_script_from_answers(topic: dict, intent: str, answers: list[dict]) -> str:
    """E first generation: intent + the user's confirmed Q&A answers."""
    n, total = _shots(topic), _total(topic)
    lines = "\n".join(
        f"- {a['question']} → {a['chosen']}"
        for a in answers
        if a.get("chosen") and not a.get("ai_decided")
    )
    delegated = [a["question"] for a in answers if a.get("ai_decided")]
    parts = [_topic_block(topic, intent)]
    if lines:
        parts.append(f"用户通过问答确认的设定(必须严格体现在脚本中):\n{lines}")
    if delegated:
        parts.append("以下方面用户交给你自由发挥:\n" + "\n".join(f"- {q}" for q in delegated))
    parts.append(
        f"请围绕以上条件生成 {n} 个镜头、总时长 {total} 秒的分镜脚本,"
        "单镜时长请你自己按节奏分配(加总须等于总时长)。"
    )
    return "\n\n".join(parts)


# ---------------- revision (post-generation loop) ----------------

def build_system_revision(topic: dict) -> str:
    n, total = _shots(topic), _total(topic)
    return (
        "你是一名专业短视频分镜脚本修订助手。用户会给你当前的分镜脚本和修改要求。\n"
        "规则:\n"
        "1. 严格按用户的要求修改;**没有被要求修改的部分尽量保持原样**(包括用户自己改过的措辞)。\n"
        "2. 修改要求可能很抽象(如『更搞笑』『更炸裂』),请把它落实为具体的画面/台词改动。\n"
        f"3. 输出完整的修订后脚本:{n} 个镜头,总时长严格等于 {total} 秒,"
        "每镜含【景别】【画面描写】【台词/音效】【时长 X 秒】四个字段,Markdown 编号列表。\n"
        "4. 只输出脚本本身,禁止解释或前言。输出语言:中文。"
    )


def build_user_revision(topic: dict, intent: str, current_script: str, request_text: str) -> str:
    """D: free-form revision request against the user's latest script."""
    return (
        f"{_topic_block(topic, intent)}\n\n"
        f"当前脚本:\n{current_script.strip()}\n\n"
        f"用户的修改要求:{request_text.strip()}"
    )


def build_user_revision_from_answers(
    topic: dict, intent: str, current_script: str, answers: list[dict]
) -> str:
    """E follow-up rounds: revise the latest script per the new Q&A answers."""
    lines = "\n".join(
        f"- {a['question']} → {a['chosen']}"
        for a in answers
        if a.get("chosen") and not a.get("ai_decided")
    )
    delegated = [a["question"] for a in answers if a.get("ai_decided")]
    parts = [
        _topic_block(topic, intent),
        f"当前脚本:\n{current_script.strip()}",
    ]
    if lines:
        parts.append(f"用户通过新一轮问答确认的修改方向(必须落实):\n{lines}")
    if delegated:
        parts.append("以下方面用户交给你自由发挥:\n" + "\n".join(f"- {q}" for q in delegated))
    return "\n\n".join(parts)


# ---------------- guided elicitation (condition E, JSON) ----------------

_JSON_SPEC = (
    "只输出 JSON,不要任何其他文字、不要 markdown 代码块。格式:"
    '{"questions":[{"dimension":"...","question":"...","options":["...","..."],"why":"..."}]}'
)


def build_system_guidance_round1() -> str:
    lo, hi = config.SUPPLEMENT_RANGE
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
        f"4. {_JSON_SPEC}\n"
        "5. 全部使用中文(dimension 除外)。"
    )


def build_user_guidance_round1(topic: dict, intent: str) -> str:
    return _topic_block(topic, intent)


def build_system_guidance_followup() -> str:
    lo, hi = config.FOLLOWUP_RANGE
    return (
        "你是一名短视频分镜创作向导。用户已经有了一版分镜稿,并主动要求你继续引导。"
        f"请阅读当前稿子,找出 {lo}-{hi} 个最值得追问的薄弱处,生成引导问题。\n"
        "每个问题:dimension 用简短英文 slug;question 紧扣稿子具体内容;"
        "options 为 3-4 个具体、互斥、可直接选用的选项;why 为一句话说明追问理由。\n"
        f"{_JSON_SPEC}\n"
        "全部使用中文(dimension 除外)。"
    )


def build_user_guidance_followup(topic: dict, intent: str, current_script: str) -> str:
    return f"{_topic_block(topic, intent)}\n\n当前分镜稿:\n{current_script.strip()}"
