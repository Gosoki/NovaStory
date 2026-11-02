from __future__ import annotations

import json
import re

import streamlit as st

from core import db, state
from i18n import get_lang, t
from views import _scale, _scroll

# Whole-study questionnaire shown once, after all rounds are finished and before
# the completion code. Captures cross-condition preference + behavioral intent +
# overall satisfaction (paper/10 H7). Rounds are referred to by number + topic
# title (a neutral memory aid); analysis maps round_idx → condition via trials.


# 「最後のひと口（分け合う/独り占め）」→「最後のひと口」。The parenthetical gloss is a
# creation-step hint; repeated here as a radio option it reads as if the survey
# were asking about that choice rather than about the round (§11).
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def _plain_title(topic: dict, lang: str) -> str:
    return _PAREN_RE.sub("", state.topic_text(topic, "title", lang)).strip()


def render() -> None:
    pid = st.session_state["participant_id"]
    # 总问卷在所有轮次之外:像 contact_submit 一样写到 round_idx=0(intake 段的约定),否则它会
    # 带着第 3 轮的 round_idx / attempt 落进第 3 轮的事件窗口(n_resumes、最后一个事件都被拉长)。
    # log_intake_event 每个浏览器会话只记一次 —— 页面每次按键都 rerun,只要一个到达戳。
    state.log_intake_event("final_survey_shown")
    st.header(t("final_survey.title"))
    st.caption(t("final_survey.hint"))

    plan = st.session_state["round_plan"]
    lang = get_lang()
    rounds = list(range(1, state.rounds_total() + 1))

    # Options are the localized label strings (round # + topic title as a neutral
    # memory aid); a format_func radio isn't reliably test-drivable. Map back to
    # the round number on read; analysis maps round_idx → condition via trials.
    labels = [
        f"{t('final_survey.round_n', i=i)}:{_plain_title(plan[i - 1]['topic'], lang)}"
        for i in rounds
    ]
    lbl2round = dict(zip(labels, rounds))
    def pick(name: str, label: str, opts: list[str]):
        # 一题一个容器(.st-key-ns_fs_*),「下一题」框按它找题
        with st.container(key=f"ns_fs_{name}"):
            return st.radio(label, opts, index=None)

    pref_sel = pick("pref", t("final_survey.q_pref"), labels)
    reuse_sel = pick("reuse", t("final_survey.q_reuse"), labels)
    # 跨轮强制选择。轮内的 imagine / effort 都是 7 点自评,受个人答题风格影响
    # (有人从不用 7),而且 imagine 比的是**被 E 自己重塑过的记忆**;做完全部轮次后是
    # 几个成品摆在一起比,偏差结构不同 —— 作为主终点的收敛证据,不是替代。
    closest_sel = pick("closest", t("final_survey.q_closest"), labels)
    effort_sel = pick("effort", t("final_survey.q_effort"), labels)
    # 2026-11-02 加(06 ㉙,日本开采前):所有权是主终点却没有跨轮强制选择版 —— 中国 Likert 41.5%
    # 落在中点,强制选择不受「总选中间」影响。放在原四题之后,原四题的相对顺序与中国相同。
    # 中国没有这两题 → 只作日本内部的探索性分析(prereg.JA_FINAL_SURVEY_EXPLORATORY)。
    owned_sel = pick("owned", t("final_survey.q_owned"), labels)
    with st.container(key="ns_fs_reason"):
        pref_reason = st.text_area(t("final_survey.q_pref_reason"), key="_fs_pref_reason", max_chars=1000)
    sat = _scale.likert(t("final_survey.q_overall"), "_fs_sat", anchors="satisfied")
    # 操纵察觉:论文里回答「被试会不会猜到假设」这类质疑的唯一材料。放在所有测量之后,
    # 怎么答都不影响任何一个数据点。2026-09-03 由开放题改为 4 档有序单选(察觉深度):
    # 自由文本要人工编码、多数人只写一两个字,而这四档正是论文要报的那个梯度;
    # 想补充的话下面的「その他」栏还在。存**选项序号**(语言无关,与其它 categorical 一致)。
    noticed_opts = [t(f"final_survey.q_noticed_opt{i}") for i in range(1, 5)]
    noticed_sel = pick("noticed", t("final_survey.q_noticed"), noticed_opts)
    comment = st.text_area(t("final_survey.comment"), key="_fs_comment", max_chars=1000)

    # 下一步在哪:与每轮问卷同一做法(views/questionnaire.py)—— 第一道没答的**必答题**套一圈细框,
    # 答一题挪一题,全答完就不圈。选填的「理由」框不进这个顺序(圈了它就会停在那里像是必答),
    # 改用一圈静态虚线框标出「可以写、不写也行」,不闪、不动,与「下一题」的实线呼吸框区分开。
    pending = [k for k, v in (("ns_fs_pref", pref_sel), ("ns_fs_reuse", reuse_sel),
                              ("ns_fs_closest", closest_sel), ("ns_fs_effort", effort_sel),
                              ("ns_fs_owned", owned_sel), (_scale.container_key("_fs_sat"), sat),
                              ("ns_fs_noticed", noticed_sel)) if v is None]
    css = (".st-key-ns_fs_reason{border:1.5px dashed var(--ns-accent-line);border-radius:12px;"
           "padding:.6rem .8rem .2rem}")
    if pending:
        nxt = f".st-key-{pending[0]}"
        css += (f"{nxt}{{outline:2px solid var(--ns-accent-line);outline-offset:6px;border-radius:12px;"
                f"animation:ns-ring 1.6s ease-out 2}}"
                f"@media (prefers-reduced-motion: reduce){{{nxt}{{animation:none}}}}")
    st.markdown(f'<span class="ns-css"></span><style>{css}</style>', unsafe_allow_html=True)

    if st.button(t("final_survey.submit"), type="primary", width="stretch"):
        # Name the unanswered items back, same as every other form in the study.
        missing = [_scale.truncate_label(lbl) for lbl, v in (
            (t("final_survey.q_pref"), pref_sel),
            (t("final_survey.q_reuse"), reuse_sel),
            (t("final_survey.q_closest"), closest_sel),
            (t("final_survey.q_effort"), effort_sel),
            (t("final_survey.q_owned"), owned_sel),
            (t("final_survey.q_overall"), sat),
            (t("final_survey.q_noticed"), noticed_sel),
        ) if v is None]
        if missing:
            st.error(t("errors.unanswered", items=" / ".join(missing)))
            return
        db.update_participant(
            pid,
            final_survey_json=json.dumps(
                {
                    "pref_round": lbl2round[pref_sel],
                    "reuse_round": lbl2round[reuse_sel],
                    "closest_round": lbl2round[closest_sel],
                    "effort_round": lbl2round[effort_sel],
                    "owned_round": lbl2round[owned_sel],
                    "pref_reason": (pref_reason or "").strip(),
                    "overall_sat": int(sat),
                    "noticed_idx": noticed_opts.index(noticed_sel),   # 0=没注意到 … 3=猜到研究目的
                    "comment": (comment or "").strip(),
                },
                ensure_ascii=False,
            ),
        )
        state.log_intake_event("final_survey_submit")
        st.session_state["stage"] = "done"
        _scroll.request()   # 总问卷 → 完成页:整页换内容
        st.rerun()
