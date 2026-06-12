from __future__ import annotations

from typing import Optional


def _shots(topic: dict) -> int:
    return int(topic.get("shot_count", 3))


def _total(topic: dict) -> int:
    return int(topic.get("total_seconds", 15))


def build_system_script(topic: dict) -> str:
    n = _shots(topic)
    total = _total(topic)
    return (
        "你是一名专业短视频分镜脚本助手。\n"
        f"必须严格输出 {n} 个镜头，总时长 {total} 秒。\n"
        "每个镜头的时长由你按内容节奏自主分配（特写/插入镜头可短至 1–2 秒，"
        "动作/对白镜头可长至 6–8 秒甚至更长），但所有镜头的时长之和**必须严格等于** "
        f"{total} 秒。\n"
        "每个镜头需包含 4 个字段：【景别】、【画面描写】、【台词/音效】、"
        "【时长 X 秒】（X 是你为该镜分配的具体秒数）。\n"
        "用 Markdown 编号列表或表格输出，禁止添加解释、前言或总结。\n"
        "输出语言：中文。"
    )


def build_system_outline(topic: dict) -> str:
    n = _shots(topic)
    return (
        f"你是一名短视频故事大纲助手。仅输出 {n} 句话的故事大纲，每句对应一个镜头，"
        "按时间顺序推进。不要写分镜、不要写台词、不要标注时长，"
        "把具体细节留给用户后续介入。\n"
        "输出语言：中文。"
    )


def build_system_dissent(topic: dict, divergence_dim: str) -> str:
    n = _shots(topic)
    return (
        "你是一名建设性的创作异议者。用户正在创作一个短视频故事大纲,"
        "你手里有若干份『默认版本』——即同样的创意输入下,AI 不经人工干预时最典型的产出。\n"
        "你的任务是对照默认版本审视用户当前的大纲,并严格按以下三段输出(保留标记):\n"
        f"【重合】指出用户大纲与默认版本实质重合的 1-2 处(具体到情节/设定,而不是泛泛而谈);"
        "若几乎没有重合,如实说明并指出用户大纲最独特的一点。\n"
        "【提问】提出一个挑战性的问题,逼用户想清楚自己真正想表达什么。\n"
        f"【反提案】沿『{divergence_dim}』的方向,给出一条远离默认版本、可直接落地为 {n} 镜头故事的具体替代构思(2-3 句)。\n"
        "语气坦率但尊重,总字数不超过 220 字。不要重写整份大纲,不要输出三段以外的内容。\n"
        "输出语言:中文。"
    )


def build_user_dissent(
    topic: dict,
    seed: str,
    user_outline: str,
    default_outlines: list[str],
) -> str:
    defaults = "\n\n".join(
        f"--- 默认版本 {i + 1} ---\n{o.strip()}" for i, o in enumerate(default_outlines)
    )
    return (
        f"主题:{topic.get('title', '').strip()}\n"
        f"情境:{topic.get('scenario', '').strip()}\n"
        f"用户的核心创意:{(seed or '').strip()}\n\n"
        f"用户当前的大纲:\n{user_outline.strip()}\n\n"
        f"AI 不经干预时的默认版本(共 {len(default_outlines)} 份):\n{defaults}"
    )


def build_user(
    topic: dict,
    seed: str,
    *,
    extra: Optional[str] = None,
    base_outline: Optional[str] = None,
) -> str:
    n = _shots(topic)
    total = _total(topic)
    parts = [
        f"主题：{topic.get('title', '').strip()}",
        f"情境：{topic.get('scenario', '').strip()}",
        f"核心创意梗：{(seed or '').strip()}",
    ]
    if base_outline:
        parts.append(f"已确认的故事大纲（请严格依据它生成最终分镜）：\n{base_outline.strip()}")
    if extra:
        parts.append(f"补充要求：{extra.strip()}")
    parts.append(
        f"请围绕以上条件生成 {n} 个镜头、总时长 {total} 秒的分镜脚本，"
        "单镜时长请你自己按节奏分配（加总须等于总时长）。"
    )
    return "\n\n".join(parts)
