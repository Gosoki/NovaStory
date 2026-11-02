from __future__ import annotations

import html

import streamlit as st

from core import prompts, state
from i18n import get_lang, t
from views import _trial
from views._streaming import script_view, stream_llm, verbatim

# The post-generation loop (paper/7 §2, D24 revision): the script lives in an
# always-editable textarea; the condition's AI channel sits alongside; hand
# edits are snapshotted automatically before submit / any AI action.
#
# Streamlit constraint: a mounted widget key cannot be reassigned in the same
# run, so after an AI action produces a new version we set a refresh flag and
# update "_script_edit" at the top of the next run, before instantiation.


_HISTORY_HEIGHT = 480   # px; scrollable height of the chat-history pane (tunable)
# 对话头像:被试 = 人形,AI = 星芒(与品牌标记同一个意象)。v1 是 Streamlit 默认的橙色机器人 / 红色笑脸。
AVATAR_USER = ":material/person:"
AVATAR_AI = ":material/auto_awesome:"


def render_editable(topic: dict, cond: str) -> None:
    """D / E / F:可编辑的看稿循环(render_readonly 是 C 的只读兄弟)。"""
    _refresh_editor_if_flagged()
    if "_script_edit" not in st.session_state:
        st.session_state["_script_edit"] = state.current_script()
    notice = st.session_state.pop("_postgen_notice", None)
    if notice:
        st.warning(t(notice))

    st.subheader(t("postgen.title", v=len(st.session_state["r_versions"])))
    st.text_area(
        label=t("postgen.title", v=len(st.session_state["r_versions"])),
        key="_script_edit",
        height=320,
        label_visibility="collapsed",
        # 上限必须高到 AI 稿绝无可能触到。本函数开头那句 `if "_script_edit" not in st.session_state`
        # 把 current_script() 灌进这个 key,
        # 而 text_area 会按 max_chars **截断**灌进来的值并写回 session_state ——
        # 于是 persist_pending_edit 看到「内容变了」,把模型话痨造成的截断记成一次
        # user_edit,hand_edit_chars 凭空多出几千字。那是「努力再分配」主图的指标,
        # 被伪造了不会有任何报错。原值 4000 离一份 3 镜分镜(≈300-600 字)太近。
        # 取 20000 与 state._edit_chars 的 difflib 退化阈值对齐:防整段粘贴的作用还在,
        # 而二次方爆炸本来就由那道退化分支兜底,不靠这个上限。
        max_chars=20000,
    )
    st.caption(t("postgen.edit_hint_f" if cond == "F" else "postgen.edit_hint"))   # F 没有 AI 修改通道

    if cond == "D":
        _render_revision_channel(topic)
    elif cond == "E":
        _render_guidance_channel()

    if st.button(t("postgen.submit"), type="primary", width="stretch"):
        if not (st.session_state.get("_script_edit") or "").strip():
            # 编辑框被清空:persist_pending_edit 会静默回落成 AI 原稿 —— 被试以为交了空稿
            # 或想重写,库里却是 AI 版且 n_hand_edits 不增,页面还留着一个空框。把原稿放回去、
            # 说明一句、不提交。
            st.session_state["_postgen_notice"] = "errors.script_empty"
            request_editor_refresh()
            st.rerun()
        final = persist_pending_edit()
        _trial.submit_trial(final)
        st.rerun()


def render_readonly(topic: dict) -> None:
    """Condition C: zero-loop — own idea, generated script, submit.

    Echoing the idea back matters in C specifically: it is the one condition with
    no loop, so it never renders the chat history pane, and without this the
    script the participant is about to judge ("is this what I meant?") sits on
    screen next to nothing they wrote (§8). The submit label is neutral here too
    — "I'm happy with it, submit this version" implies a version they could have
    rejected, and in C there is exactly one."""
    intent = (st.session_state.get("r_intent") or "").strip()
    if intent:
        with st.container(key="ns_card_intent"):
            st.caption(t("postgen.readonly_intent"))
            # 逐字回显:被试写的「1. 起きる 2. 走る」经 markdown 会变成有序列表,
            # 排版上与紧邻其下的 AI 稿难以区分 —— 而这段回显存在的意义就是分开两者。
            st.markdown(verbatim(intent), unsafe_allow_html=True)
    st.subheader(t("postgen.readonly_title"))
    with st.container(key="ns_card_script"):
        st.markdown(script_view(state.current_script()), unsafe_allow_html=True)
    if st.button(t("postgen.submit_c"), type="primary", width="stretch"):
        _trial.submit_trial(state.current_script())
        st.rerun()


def persist_pending_edit() -> str:
    """Snapshot the editor content as a user_edit version if it differs from
    the current version (the snapshot hard rule). Returns the live text."""
    cur = state.current_script()
    edited = (st.session_state.get("_script_edit") or cur).strip()
    if edited and edited != cur.strip():
        state.add_version(edited, "user_edit")
        return edited
    return cur


def request_editor_refresh() -> None:
    st.session_state["_postgen_refresh"] = True


def _refresh_editor_if_flagged() -> None:
    if st.session_state.pop("_postgen_refresh", False):
        st.session_state["_script_edit"] = state.current_script()


def render_history() -> None:
    """Chat-style transcript of the whole round — the idea, every revision request
    / guidance answer / hand-edit and every AI version — in order, like a chat
    agent keeping the full conversation."""
    versions = st.session_state["r_versions"]
    if not versions:
        return
    requests = st.session_state["r_revision_requests"]   # condition D
    guidance_rounds = st.session_state["r_guidance_rounds"]   # condition E(别叫 guidance:与下面局部 import 的模块同名)
    # 「内容多高就多高、最高 _HISTORY_HEIGHT」:st.container(height=) 是写死的高度,只有一版时下面一大块空白
    st.markdown(f'<span class="ns-css"></span><style>.st-key-ns_history{{height:auto!important;'
                f'max-height:{_HISTORY_HEIGHT}px}}</style>', unsafe_allow_html=True)
    with st.expander(t("history.title"), expanded=True):
        with st.container(height=_HISTORY_HEIGHT, border=False, key="ns_history"):   # 外面的展开框就是框,不再套一层
            intent = (st.session_state.get("r_intent") or "").strip()
            if intent:
                with st.chat_message("user", avatar=AVATAR_USER):
                    st.caption(t("history.intent"))
                    st.markdown(verbatim(intent), unsafe_allow_html=True)
            ai_seen = 0
            for v in versions:
                if v["author"] == "ai":
                    _history_user_turn(ai_seen, requests, guidance_rounds)
                    with st.chat_message("assistant", avatar=AVATAR_AI):
                        st.caption(t("history.ai", v=v["v"]))
                        st.markdown(script_view(v["text"]), unsafe_allow_html=True)
                    ai_seen += 1
                else:  # user_edit
                    with st.chat_message("user", avatar=AVATAR_USER):
                        st.caption(t("history.edit"))
                        st.markdown(script_view(v["text"]), unsafe_allow_html=True)


def _history_user_turn(ai_idx: int, requests: list, guidance_rounds: list) -> None:
    """The user message that triggered the ai_idx-th AI version: guidance answers
    for E; the free-text request for D's 2nd+ generation; nothing for the first."""
    if guidance_rounds and ai_idx < len(guidance_rounds):
        with st.chat_message("user", avatar=AVATAR_USER):
            st.caption(t("history.guide"))
            for it in guidance_rounds[ai_idx]["items"]:
                answer = (
                    t("guidance.ai_decide") if it.get("ai_decided")
                    else (it.get("chosen") or "—")
                )
                st.markdown(f'<div class="ns-hq">{html.escape(it["question"])}</div>',   # 模型写的题面:逐字
                            unsafe_allow_html=True)
                st.markdown(verbatim(f"→ {answer}"), unsafe_allow_html=True)   # 被试写的字逐字显示,不按 markdown 排
    elif requests and 1 <= ai_idx <= len(requests):
        with st.chat_message("user", avatar=AVATAR_USER):
            st.caption(t("history.req"))
            st.markdown(verbatim(requests[ai_idx - 1]["text"]), unsafe_allow_html=True)


def _render_revision_channel(topic: dict) -> None:
    if st.session_state.pop("_revision_clear", False):
        st.session_state["_revision_input"] = ""
    # 与 v1 一样平铺,不单独套卡片:「请 AI 改」与「就这版提交」是被试的两条路,哪一条有额外的框
    # 都是在推它(n_ai_rounds 是因变量;审查实测卡片还让两颗按钮宽度不一)
    st.text_area(
        t("d.revision_label"),
        key="_revision_input",
        placeholder=t("d.revision_placeholder"),
        height=80,
        max_chars=600,
    )
    st.caption(t("d.revision_hint"))
    if st.button(t("d.revision_send"), type="secondary", width="stretch", key="btn_more_ai"):
        request = (st.session_state.get("_revision_input") or "").strip()
        if not request:
            st.error(t("errors.revision_empty"))
            return
        base = persist_pending_edit()
        lang = get_lang()
        out = stream_llm(
            prompts.build_system_revision(topic, lang),
            prompts.build_user_revision(
                topic, st.session_state["r_intent"], base, request, lang
            ),
            group="D-revise",
        )
        # Mirror E's _finish_round: only commit the request + version once
        # generation actually succeeds. Committing beforehand left an orphan
        # request on failure (empty/None out), which misaligned the history
        # timeline (requests[ai_idx-1]) and the persisted revision_requests.
        if not (out and out.strip()):
            return
        st.session_state["r_revision_requests"].append(
            {"round": st.session_state["r_n_ai_rounds"] + 1, "text": request}
        )
        state.log_event("revision_request", {"chars": len(request)})
        state.add_version(out, "ai")
        st.session_state["r_n_ai_rounds"] += 1
        request_editor_refresh()
        st.session_state["_revision_clear"] = True
        st.rerun()


def _render_guidance_channel() -> None:
    if st.button(t("guidance.continue_btn"), type="secondary", width="stretch", key="btn_more_ai"):
        persist_pending_edit()
        state.log_event("continue_guidance_click")
        from views import guidance  # local import: guidance imports this module

        guidance.begin_round("ai_from_draft")
        st.rerun()
