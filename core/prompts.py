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
