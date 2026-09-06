from __future__ import annotations

import json
import re

import streamlit as st

from core import config, db, state
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
    rounds = list(range(1, config.N_ROUNDS + 1))

    # Options are the localized label strings (round # + topic title as a neutral
    # memory aid); a format_func radio isn't reliably test-drivable. Map back to
    # the round number on read; analysis maps round_idx → condition via trials.
    labels = [
        f"{t('final_survey.round_n', i=i)}:{_plain_title(plan[i - 1]['topic'], lang)}"
        for i in rounds
    ]
    lbl2round = dict(zip(labels, rounds))
    pref_sel = st.radio(t("final_survey.q_pref"), labels, index=None)
    reuse_sel = st.radio(t("final_survey.q_reuse"), labels, index=None)
    # 跨轮强制选择。轮内的 imagine / effort 都是 7 点自评,受个人答题风格影响
    # (有人从不用 7),而且 imagine 比的是**被 E 自己重塑过的记忆**;做完三轮后是
    # 三个成品摆在一起比,偏差结构不同 —— 作为主终点的收敛证据,不是替代。
    closest_sel = st.radio(t("final_survey.q_closest"), labels, index=None)
    effort_sel = st.radio(t("final_survey.q_effort"), labels, index=None)
    sat = _scale.likert(t("final_survey.q_overall"), "_fs_sat", anchors="satisfied")
    # 操纵察觉:论文里回答「被试会不会猜到假设」这类质疑的唯一材料。放在所有测量之后,
    # 怎么答都不影响任何一个数据点。2026-09-03 由开放题改为 4 档有序单选(察觉深度):
    # 自由文本要人工编码、多数人只写一两个字,而这四档正是论文要报的那个梯度;
    # 想补充的话下面的「その他」栏还在。存**选项序号**(语言无关,与其它 categorical 一致)。
    noticed_opts = [t(f"final_survey.q_noticed_opt{i}") for i in range(1, 5)]
    noticed_sel = st.radio(t("final_survey.q_noticed"), noticed_opts, index=None)
    comment = st.text_area(t("final_survey.comment"), key="_fs_comment", max_chars=1000)

    if st.button(t("final_survey.submit"), type="primary", width="stretch"):
        # Name the unanswered items back, same as every other form in the study.
        missing = [_scale.truncate_label(lbl) for lbl, v in (
            (t("final_survey.q_pref"), pref_sel),
            (t("final_survey.q_reuse"), reuse_sel),
            (t("final_survey.q_closest"), closest_sel),
            (t("final_survey.q_effort"), effort_sel),
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
