from __future__ import annotations

import difflib
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from core import config, db
from i18n import AVAILABLE_LANGS, DEFAULT_LANG

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TOPICS_FILE = DATA_DIR / "topics.json"

# seq (0-17) = cond_row * 3 + topic_row.
# _COND_ORDERS = all 6 permutations of C/D/E (a Williams design): each condition
# is immediately preceded by each other condition equally often, so first-order
# carryover is balanced and the E−D contrast is not confounded with order (the 3
# cyclic orders CDE/DEC/ECD used before had D always after C, E always after D —
# deep-review 2026-07-19 #1). Each condition also sits in each round-position
# exactly twice. _TOPIC_ORDERS (3 rotations) balances condition×topic and
# topic×position. 6 × 3 = 18 seqs; N=36 → 2 completers each.
_COND_ORDERS = (
    ("C", "D", "E"), ("C", "E", "D"),
    ("D", "C", "E"), ("D", "E", "C"),
    ("E", "C", "D"), ("E", "D", "C"),
)
_TOPIC_ORDERS = ((0, 1, 2), (1, 2, 0), (2, 0, 1))
assert len(_COND_ORDERS) * len(_TOPIC_ORDERS) == config.LATIN_SQUARE_N

# Seed topics written to data/topics.json on first launch when the file is
# missing. Live session always reads from topics.json via load_topics().
# title/scenario/choices carry {"ja": ..., "zh": ..., "en": ...} — ja is the formal-study
# language, zh is for the researcher's testing, en for English sessions. Keep in
# sync with data/topics.json: a missing topics.json is REPLACED by this seed, and
# deploy_check requires all three languages.
# `scenario` is the setup; `choices` is the "which will you do?" list, kept
# separate since 2026-09-01 (§15) so the UI can grey it out and mark it as one
# suggestion among many rather than a required direction. The model still gets
# both, joined (prompts.scenario_text).
_SEED_TOPICS = [
    {
        "title": {"ja": "拾った切符（誰かの落とし物）", "zh": "捡到的失物（别人掉的东西）",
                  "en": "The Ticket You Picked Up (someone's lost item)"},
        "scenario": {
            "ja": "道で、誰かが落とした小さな物を拾う。",
            "zh": "在路上捡到别人掉落的小东西。",
            "en": "On the street you pick up something small that someone dropped.",
        },
        "choices": {
            "ja": "中を見るか、届けるか、それとも——。",
            "zh": "是打开看看、拿去归还，还是——。",
            "en": "Do you peek inside, hand it in, or——?",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": {"ja": "最後のひと口（分け合う/独り占め）", "zh": "最后一口（分享还是独享）",
                  "en": "The Last Bite (share it or keep it)"},
        "scenario": {
            "ja": "目の前に、好きなものがたったひとつだけ残っている。ほんの数秒の攻防。",
            "zh": "眼前只剩最后一口喜欢的东西——短短几秒的拉锯。",
            "en": "Just one bite of your favorite thing is left in front of you — a standoff of just a few seconds.",
        },
        "choices": {
            "ja": "誰かと分けるか、自分で食べるか。",
            "zh": "是分给别人，还是自己吃掉。",
            "en": "Share it with someone, or keep it for yourself?",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
    {
        "title": {"ja": "はじめての街の、最初の一歩", "zh": "陌生街道的第一步",
                  "en": "The First Step in an Unfamiliar Town"},
        "scenario": {
            "ja": "見慣れない街に降り立った、最初の一歩。",
            "zh": "降落在陌生的街道，迈出第一步。",
            "en": "Your first step after arriving in an unfamiliar town.",
        },
        "choices": {
            "ja": "地図を見るか、匂いをたどるか、誰かに声をかけるか。",
            "zh": "是看地图、循着气味走，还是开口问路。",
            "en": "Do you check a map, follow a scent, or call out to someone?",
        },
        "shot_count": 3,
        "total_seconds": 15,
    },
]


def topic_text(topic: dict, field: str, lang: str) -> str:
    """Localized topic field; tolerates legacy plain-str topics."""
    v = topic.get(field, "")
    if isinstance(v, dict):
        return v.get(lang) or v.get("ja") or v.get("zh") or ""
    return v or ""

# Per-round payload, reset at the start of every round.
ROUND_PAYLOAD_DEFAULTS: dict[str, Any] = {
    "r_phase": "intent",          # intent → pipeline → (guidance ⇄) postgen → questionnaire
    "r_intent": "",
    "r_versions": [],             # [{"v", "author": "ai"|"user_edit", "text"}]
    "r_guidance_rounds": [],      # guidance_json["rounds"] (condition E)
    "r_revision_requests": [],    # [{"round", "text"}] (condition D)
    "r_n_ai_rounds": 0,           # D revision rounds / E follow-up guidance rounds
    "r_n_hand_edits": 0,
    "r_hand_edit_chars": 0,
    # guidance working state (condition E)
    "r_g_source": "",             # "fixed3+ai_supplement" | "ai_from_draft"
    "r_g_questions": [],
    "r_g_answers": {},            # {q_idx: {"opt", "custom"}} — persists across
                                  # pagination (Streamlit clears unmounted widgets)
    "r_g_idx": 0,
    "r_g_fallback": False,
    "r_llm_wait": 0.0,
    "r_llm_wait_post": 0.0,       # waits occurring after the first script_shown
    "r_llm_wait_in_guidance": 0.0,  # E round-1: waits inside [guidance_shown, guidance_submit)
    "r_events": [],               # [(epoch_seconds, type)] session mirror for durations
    "r_trial_id": None,
    "r_attempt": "",              # session segment id (LOG4); fresh per round attempt
}

DEFAULTS: dict[str, Any] = {
    "lang": DEFAULT_LANG,
    # Opaque id for this browser session, minted before a participant row
    # exists so the intake stage (consent → intro → screening) can log events
    # and have them backfilled with the participant id at screening.
    "session_id": "",
    "api_key": "",
    "base_url": "",
    "model": "",
    "api_preset_name": "",
    "researcher_mode": False,
    "researcher_ok": False,
    "participant_id": None,
    "seq": None,
    "stage": "consent",        # consent → screening → intro → rounds → final_survey → done
    "round_idx": 1,
    "round_plan": [],          # [{"condition": str, "topic": dict}] × 3
    "completion_code": "",
    **ROUND_PAYLOAD_DEFAULTS,
}


def init_state() -> None:
    db.init_db()
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v if not isinstance(v, (dict, list)) else _json_copy(v)
    if not st.session_state["session_id"]:
        st.session_state["session_id"] = secrets.token_hex(4)
    _apply_url_lang()
    _ensure_api_defaults()
    _attempt_resume()


def _apply_url_lang() -> None:
    """`?lang=ja|zh|en` preselects the session language (testing / recruitment aid).

    Applied ONCE per browser session and only before a participant row exists,
    so it behaves exactly like arriving on the consent page with the picker
    already set: the subject can still change it there, and nothing can flip the
    language mid-study (JP6). An unknown value is ignored, leaving the ja
    default. The pick lands on `participants.lang`, so monitor_panel's
    language-mix warning still catches a session run in the wrong language."""
    if st.session_state.get("_url_lang_done"):
        return
    st.session_state["_url_lang_done"] = True
    if st.session_state.get("participant_id"):
        return
    try:
        v = (st.query_params.get("lang") or "").strip().lower()
    except Exception:  # query params unavailable (e.g. headless AppTest)
        return
    if v in AVAILABLE_LANGS:
        st.session_state["lang"] = v


def _resume_token() -> str:
    """Read the resume token from the URL (?t=…); '' if absent/unavailable."""
    try:
        return (st.query_params.get("t") or "").strip()
    except Exception:  # query params unavailable (e.g. headless AppTest)
        return ""


def _attempt_resume() -> None:
    """Restore an in-progress participant after a refresh/reconnect (AUD6).

    Without this, a wiped session_state sends the subject back to consent and a
    re-screening inserts a *second* passed row → a second Latin-square seq →
    broken balance + inflated sample. We key off an opaque URL token, rebuild the
    round plan from `seq`, and resume at the first round whose questionnaire
    hasn't been submitted yet (an unfinished trial is simply re-done; INSERT OR
    REPLACE keeps it from duplicating)."""
    if st.session_state.get("participant_id"):
        return  # already inside a live session
    p = db.get_participant_by_token(_resume_token())
    if not p or not p.get("passed"):
        return

    def _restore_identity() -> None:
        st.session_state["participant_id"] = p["id"]
        st.session_state["seq"] = p["seq"]
        st.session_state["lang"] = p.get("lang") or st.session_state.get("lang", DEFAULT_LANG)

    if p.get("status") == "done" or p.get("final_survey_json"):
        # finished — restore the completion screen. final_survey_json without status
        # 'done' = the submit landed but the done page never rendered (connection
        # dropped on that rerun); _render_done mints the code on arrival, so land
        # there instead of making them answer the whole survey again.
        _restore_identity()
        st.session_state["stage"] = "done"
        st.session_state["completion_code"] = p.get("completion_code") or ""
        return

    # Need the round plan from here on; bail (don't half-restore) if topics.json
    # has been edited below N_ROUNDS — same guard as enter_intro.
    topics = load_topics()
    if len(topics) < config.N_ROUNDS:
        # 以前直接 return:已入组、已消耗 seq 的被试会被丢回同意页,重填问卷后才撞错 —— 还可能
        # 再入组一次。恢复身份、停在一个明确的错误页上等研究员修题库。
        _restore_identity()
        st.session_state["stage"] = "blocked"
        return
    _restore_identity()
    st.session_state["round_plan"] = plan_for_seq(p["seq"], topics[: config.N_ROUNDS])
    done_rounds = db.count_questionnaires(p["id"])
    if done_rounds >= config.N_ROUNDS:
        # all rounds answered but status != done → final survey not submitted yet
        st.session_state["round_idx"] = config.N_ROUNDS
        st.session_state["stage"] = "final_survey"
        # 这里也要铸新段 id:否则 session_resumed / final_survey_* 会以 attempt=NULL 写进第 3 轮,
        # 破坏「每段都有 attempt」(LOG4),以后按段切的脚本会把它当旧格式行。
        reset_round_payload()
        log_event("session_resumed", {"stage": "final_survey"})
        return
    st.session_state["round_idx"] = done_rounds + 1
    # 终稿已提交、问卷还没交(问卷页刷新 / 断线):从 trial 行把这一轮**原样恢复到问卷页**。
    # 以前这里无条件从 intent 重做 —— 被试要再写一遍创意、AI 再生成一次(二次暴露),已落库的
    # 终稿被 OR REPLACE 掉;而 trials 行本来就存着恢复问卷页所需的全部状态。必须排在下面
    # 「done_rounds == 0 → 说明页」分支之前,否则第 1 轮的这种情形会被送回说明页。
    tr = db.get_trial(p["id"], done_rounds + 1)
    if tr and tr.get("final_output"):
        st.session_state["stage"] = "rounds"
        reset_round_payload()   # 铸新 r_attempt:问卷段是新的一段,不冒充产出终稿的那段(LOG4)
        _restore_round_from_trial(tr)
        log_event("session_resumed", {"round_idx": done_rounds + 1, "stage": "questionnaire"})
        return
    if done_rounds == 0:
        # Round 1 isn't finished, so they were either still on the briefing page
        # or partway through round 1 — and a resume wipes the round payload
        # either way, i.e. round 1 restarts from the intent step regardless.
        # Land on the briefing rather than in the round: someone who refreshed
        # ON the briefing would otherwise silently lose the standardized
        # onboarding, and its button is what starts the round clock (§4).
        st.session_state["stage"] = "intro"
        # Mint a fresh attempt segment here too, not only in start_rounds: two
        # tabs resumed from the same token must be tellable apart in the event
        # log from their very first event (LOG4), and `session_resumed` is it.
        reset_round_payload()
        log_event("session_resumed", {"stage": "intro"})
        return
    st.session_state["stage"] = "rounds"
    reset_round_payload()
    log_event("session_resumed", {"round_idx": done_rounds + 1})
    # Fresh timing origin: r_events was just wiped, and round_durations anchors
    # t_read_intent / t_total on round_start — without this the resumed round's
    # duration columns land NULL.
    log_event("round_start")


def _restore_round_from_trial(tr: dict) -> None:
    """把 trials 行装回本轮会话并直接进问卷:创意、全部版本、E 的问答、D 的修改请求与计数。
    时长列不恢复(已在 trial 行里);问卷用时由 events 的 trial_submit→questionnaire_submit 算,
    续接后跨两段 attempt,events.round_metrics 给 NaN、n_resumes 标记这一轮。"""
    def _j(x, default):
        try:
            v = json.loads(x) if isinstance(x, str) and x.strip() else default
        except ValueError:
            return default
        return v if isinstance(v, type(default)) else default
    st.session_state["r_intent"] = tr.get("intent_statement") or ""
    versions = _j(tr.get("script_versions"), [])
    if not versions:
        versions = [{"v": 1, "author": "ai", "text": tr.get("final_output") or ""}]
    st.session_state["r_versions"] = versions
    st.session_state["r_guidance_rounds"] = _j(tr.get("guidance_json"), {}).get("rounds") or []
    st.session_state["r_revision_requests"] = _j(tr.get("revision_requests"), [])
    for col in ("n_ai_rounds", "n_hand_edits", "hand_edit_chars"):
        st.session_state[f"r_{col}"] = int(tr.get(col) or 0)
    st.session_state["r_trial_id"] = tr.get("id")
    st.session_state["r_phase"] = "questionnaire"
    st.session_state["_scroll_top"] = True


def _json_copy(v):
    """JSON 往返深拷贝:只用于 DEFAULTS 里的 dict/list(元组会变列表,非 JSON 值会抛)。"""
    return json.loads(json.dumps(v))


def _ensure_api_defaults() -> None:
    """Auto-apply the first secrets preset so participants never see API config."""
    if (st.session_state.get("api_key") or "").strip():
        return
    cfgs = load_api_configs()
    if cfgs:
        chosen = cfgs[0]
        st.session_state["base_url"] = chosen["base_url"]
        st.session_state["model"] = chosen["model"]
        st.session_state["api_key"] = chosen["api_key"]
        st.session_state["api_preset_name"] = chosen.get("name", "")


# ---------------- resume across a closed tab (2026-09-06) ----------------
# 关掉标签页再扫码,URL 里没有 ?t=,screening 就会无条件 insert 第二行被试并吃掉
# 一个拉丁方 seq(Playwright 实测:12 段会话 = 12 行 = 12 个 seq)。教室里几十台手机
# 这几乎必然发生,后果是孤儿行 + 把后面所有人的条件轮转挪位。
# 对策:把带 token 的 URL 存进浏览器 localStorage,同意页无 token 时跳回去。
# 注:components 的 iframe 带 allow-same-origin,window.parent.localStorage 可用(已实测)。

_RESUME_KEY = "novastory_resume_url"

_RESUME_BAR_JS = """
try{
  var w = window.parent, d = w.document;
  var saved = w.localStorage.getItem('%(key)s');
  if (saved && w.location.href.indexOf('t=') < 0 && !d.getElementById('ns-resume-bar')) {
    var bar = d.createElement('div');
    bar.id = 'ns-resume-bar';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;'
      + 'background:#fff7ed;border-top:2px solid #ea580c;padding:10px 14px;'
      + 'font:400 13px/1.5 system-ui,-apple-system,sans-serif;text-align:center;'
      + 'color:#7c2d12;box-shadow:0 -4px 14px -8px rgba(0,0,0,.35)';
    var hint = d.createElement('div');
    hint.textContent = %(hint)s;
    var a = d.createElement('a');
    a.href = saved;
    a.textContent = %(label)s;
    a.style.cssText = 'color:#c2410c;font-weight:700;font-size:15px;text-decoration:none';
    bar.appendChild(hint);
    bar.appendChild(a);
    d.body.appendChild(bar);
  }
}catch(e){}
"""


def _bridge(script: str) -> None:
    """跑一段只操作 window.parent 的 JS。零高度,不占版面。

    用 `st.iframe` 而非 `components.v1.html` —— 后者 2026-06-01 起已标为待移除,
    每次渲染都会往日志里刷一条 deprecation。两者拿到的 sandbox 权限相同。
    height 最小是 1(0 会抛 StreamlitInvalidHeightError),那 1px 由 app.py 的
    `.ns-bridge` 规则收掉 —— 用 height:0 而不是 display:none,免得浏览器把
    不可见 iframe 里的脚本也一并优化掉。"""
    try:
        st.markdown('<div class="ns-bridge"></div>', unsafe_allow_html=True)
        st.iframe(f"<script>{script}</script>", height=1)
    except Exception:  # noqa: BLE001 — headless AppTest 没有 iframe 运行时
        pass


def remember_resume_url() -> None:
    """把当前带 ?t= 的 URL 记进 localStorage(每次渲染覆盖,总是最新的那条)。"""
    _bridge(
        "try{var u=window.parent.location.href;"
        f"if(u.indexOf('t=')>-1)window.parent.localStorage.setItem('{_RESUME_KEY}',u);"
        "}catch(e){}"
    )


def offer_resume(label: str, hint: str) -> None:
    """同意页:localStorage 有存档而 URL 没 token 时,在页面底部给一条「继续上次」。

    两个坑都踩过了,别改回去:
    ① **不能自动跳转** —— components 的 iframe sandbox 有 allow-same-origin(读得到
       parent 的 localStorage)却没有 allow-top-navigation,`parent.location.replace`
       会被拦(实测 "Unsafe attempt to initiate navigation")。
    ② **不能在 iframe 里画 UI** —— srcdoc 里只有 <script>,执行时 `document.body`
       还是 null,而且外层容器高度被 Python 侧的 height=0 钉死。
    所以把条子插进 **parent 的 body**:那是 Streamlit 重绘范围之外(#root 里才是),
    链接由 parent 自己导航,同源同页,不需要新标签。"""
    # 全程 createElement + textContent:不拼 HTML,就没有引号转义,也没有注入面。
    _bridge(_RESUME_BAR_JS % {
        "key": _RESUME_KEY,
        "label": json.dumps(label),
        "hint": json.dumps(hint),
    })


def forget_resume_url() -> None:
    """清掉存档。同一台设备换人做时必须清,否则第二个人会被跳进第一个人的会话。"""
    _bridge(
        "try{var w=window.parent;"
        f"w.localStorage.removeItem('{_RESUME_KEY}');"
        "}catch(e){}"
    )


# ---------------- assignment & round flow ----------------

def plan_for_seq(seq: int, topics: list[dict]) -> list[dict]:
    if not 0 <= seq < config.LATIN_SQUARE_N:
        # 负数会走 Python 负索引静默返回别的排列,越界才抛 —— 两种都不该静默。
        raise ValueError(f"seq {seq} out of range [0, {config.LATIN_SQUARE_N})")
    n_topic = len(_TOPIC_ORDERS)   # 3 = 题目轮转数,不是 N_ROUNDS(两者恰好相等,别混)
    conds = _COND_ORDERS[seq // n_topic]
    topic_idx = _TOPIC_ORDERS[seq % n_topic]
    return [{"condition": c, "topic": dict(topics[i])} for c, i in zip(conds, topic_idx)]


def enter_intro(participant_id: int, seq: int, token: str = "") -> None:
    """Screening is in; park the participant on the how-it-works page (§4).

    Identity and the resume token are installed NOW — a refresh on the briefing
    page must resume, not re-screen (which would insert a second passed row and
    burn a second Latin-square seq). What is deliberately NOT done here is
    `round_start`: t_read_intent is measured from it, so starting the clock
    before the briefing would fold the whole briefing into round 1's reading
    time. start_rounds (called by the intro page's button) starts it."""
    topics = load_topics()
    if len(topics) < config.N_ROUNDS:
        raise RuntimeError(f"topics.json needs >= {config.N_ROUNDS} topics")
    st.session_state["participant_id"] = participant_id
    st.session_state["seq"] = seq
    st.session_state["stage"] = "intro"
    st.session_state["round_idx"] = 1
    st.session_state["round_plan"] = plan_for_seq(seq, topics[: config.N_ROUNDS])
    if token:
        try:
            st.query_params["t"] = token
        except Exception:
            pass


def start_rounds() -> None:
    """离开说明页,进入第 1 轮。

    刻意**不**重建 round_plan:计划在 `enter_intro` 里就装好了,而这颗按钮被按下时
    被试行与拉丁方 seq 早已落库。若在这里再读一次 topics.json,只要研究员在被试读
    说明页的这两分钟里动了题库(哪怕只是存了个半截文件),`enter_intro` 就会抛
    RuntimeError —— 抛在一个已经吃掉一个 seq、又没有任何出路的被试脸上。"""
    st.session_state["stage"] = "rounds"
    st.session_state["round_idx"] = 1
    reset_round_payload()
    log_event("round_start")


def enter_rounds_directly(participant_id: int, seq: int, token: str = "") -> None:
    """身份 + 计划 + 开跑,一步到位(跳过说明页)。只有 devtools 的「跳过同意+筛查」在用 ——
    正式流程走的是 enter_intro(筛查提交)→ start_rounds(说明页的按钮)两步,
    好让说明页的停留不被算进第 1 轮的 t_read_intent。"""
    enter_intro(participant_id, seq, token)
    start_rounds()


def current_round() -> dict:
    return st.session_state["round_plan"][st.session_state["round_idx"] - 1]


def reset_round_payload() -> None:
    for k, v in ROUND_PAYLOAD_DEFAULTS.items():
        st.session_state[k] = v if not isinstance(v, (dict, list)) else _json_copy(v)
    # Every (re)start of a round gets its own segment id, so a redone round's
    # events can be told apart from the discarded attempt's (LOG4).
    st.session_state["r_attempt"] = secrets.token_hex(4)
    # Ephemeral widget keys (Streamlit usually cleans these on unmount; pop
    # defensively so a new round never inherits stale editor content).
    for k in list(st.session_state.keys()):
        # ⚠️ 别在这里清 _q_*:advance_round 在问卷提交的那次 rerun 里调用本函数,此时问卷 widget
        # 仍挂载,同一 run 内删它的 key 会让 Streamlit 炸;_q_ 键按轮次编号,下一轮天然不冲突。
        if k in ("_script_edit", "_intent_input", "_revision_input", "_gen_failed") or k.startswith("_g_"):
            st.session_state.pop(k, None)


def advance_round() -> None:
    if st.session_state["round_idx"] >= config.N_ROUNDS:
        st.session_state["stage"] = "final_survey"  # whole-study survey before done
        return
    st.session_state["round_idx"] += 1
    reset_round_payload()
    log_event("round_start")


def reset_for_next() -> None:
    """Local-testing convenience (researcher only): wipe the subject, keep config."""
    keep = {
        k: st.session_state.get(k)
        for k in (
            "lang", "api_key", "base_url", "model", "api_preset_name",
            "researcher_mode", "researcher_ok",
        )
    }
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    try:  # drop the resume token, else init would re-resume the wiped subject
        st.query_params.clear()
    except Exception:
        pass
    forget_resume_url()   # 同上,但清的是浏览器那份存档
    init_state()
    st.session_state.update(keep)


# ---------------- script versions (paper/7 §2: snapshot hard rule) ----------------

def current_script() -> str:
    versions = st.session_state["r_versions"]
    return versions[-1]["text"] if versions else ""


def add_version(text: str, author: str) -> None:
    """Append a script version (author: "ai" | "user_edit") with bookkeeping."""
    versions = st.session_state["r_versions"]
    prev = versions[-1]["text"] if versions else ""
    v = len(versions) + 1
    versions.append({"v": v, "author": author, "text": text})
    if author == "user_edit":
        delta = _edit_chars(prev, text)
        st.session_state["r_n_hand_edits"] += 1
        st.session_state["r_hand_edit_chars"] += delta
        log_event("hand_edit_saved", {"v": v, "chars_delta": delta})
    else:
        log_event("script_shown", {"v": v})


def _edit_chars(a: str, b: str) -> int:
    """Changed-character volume between two versions (difflib opcodes)."""
    if len(a) + len(b) > 20_000:
        # difflib 是二次方的:两次超长粘贴就能让一个 rerun 卡几十秒。编辑框已有 max_chars,
        # 这里是最后一道保险 —— 退化为长度差,不做逐字对齐。
        return abs(len(b) - len(a))
    total = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag != "equal":
            total += max(i2 - i1, j2 - j1)
    return total


# ---------------- events & timing ----------------

def log_event(type_: str, payload: Optional[dict] = None, *, once: bool = False) -> None:
    """Round-scoped event. `once=True` records at most one per round attempt —
    Streamlit re-runs the whole script on every interaction, so an unguarded
    page-view log would fire once per keystroke. `r_events` is the per-attempt
    mirror, so a redone round logs its own copy."""
    if once and any(ty == type_ for _, ty in st.session_state["r_events"]):
        return
    st.session_state["r_events"].append((time.time(), type_))
    db.insert_event(
        st.session_state.get("participant_id"),
        st.session_state.get("round_idx"),
        type_,
        payload,
        seq_in_round=len(st.session_state["r_events"]),  # LOG3: 1-based within attempt
        attempt=st.session_state.get("r_attempt") or None,
    )


def add_llm_wait(seconds: float) -> None:
    st.session_state["r_llm_wait"] += seconds
    types = [ty for _, ty in st.session_state["r_events"]]
    if "script_shown" in types:
        st.session_state["r_llm_wait_post"] += seconds
    # E round-1: the final-script generation runs inside the
    # [guidance_shown, guidance_submit) window and lands before the first
    # script_shown, so it escapes r_llm_wait_post. Track it separately so
    # round_durations can keep it out of t_pregen (a creative-time column).
    if "guidance_shown" in types and "guidance_submit" not in types:
        st.session_state["r_llm_wait_in_guidance"] += seconds


def log_intake_event(type_: str, payload: Optional[dict] = None) -> None:
    """Intake-stage event (consent → intro → screening), logged BEFORE a
    participant row exists — that stage is the one part of the study with no
    timing data otherwise, and "did they actually read the how-it-works page"
    is a data-quality question.

    Written at `round_idx=0` so it never lands in a round's event stream, keyed
    by `session_id` in `attempt`; `db.attach_intake_events` backfills the
    participant id at screening. Each type is recorded at most once per session
    (these are one-shot page views, and Streamlit re-runs on every keystroke)."""
    seen = st.session_state.setdefault("_intake_seen", [])
    if type_ in seen:
        return
    seen.append(type_)
    db.insert_event(
        st.session_state.get("participant_id"),
        0,
        type_,
        payload,
        seq_in_round=len(seen),
        attempt=st.session_state.get("session_id") or None,
    )


def _ts(type_: str) -> Optional[float]:
    """本轮 attempt 里 type_ 事件**首次**出现的时刻;没有则 None。"""
    hits = [ts for ts, ty in st.session_state["r_events"] if ty == type_]
    return hits[0] if hits else None


def round_durations(condition: str) -> dict:
    """Aggregate per-phase durations from the session event mirror.

    Fine-grained timestamps live in the events table; these aggregates are
    convenience columns on the trial row. LLM waits are excluded from the
    creative-time columns (t_pregen / t_postgen)."""
    out = {
        "t_read_intent": _delta("round_start", "intent_submit"),
        "t_pregen": None,
        "t_postgen": None,
        "t_llm_wait": round(st.session_state["r_llm_wait"], 2),
        "t_total": _delta("round_start", "trial_submit"),
    }
    if condition == "E":
        pre = _delta("guidance_shown", "guidance_submit")
        if pre is not None:  # net of the final-script generation wait in-window
            out["t_pregen"] = round(max(0.0, pre - st.session_state["r_llm_wait_in_guidance"]), 2)
    post = _delta("script_shown", "trial_submit")
    if post is not None:
        out["t_postgen"] = round(max(0.0, post - st.session_state["r_llm_wait_post"]), 2)
    return out


def _delta(a: str, b: str) -> Optional[float]:
    ta, tb = _ts(a), _ts(b)
    if ta is None or tb is None or tb < ta:
        return None
    return round(tb - ta, 2)


# ---------------- topics.json -----------------

def load_topics() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TOPICS_FILE.exists():
        TOPICS_FILE.write_text(
            json.dumps(_SEED_TOPICS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return [dict(tp) for tp in _SEED_TOPICS]
    try:
        data = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            topics = [_normalize_topic(tp) for tp in data if isinstance(tp, dict)]
            if topics:
                return topics
        _log.warning("topics.json 结构无效,回退种子主题 —— 被试跑的不是你以为的题目")
    except (ValueError, OSError) as e:
        _log.warning("topics.json 读取失败(%s),回退种子主题", type(e).__name__)
        # ValueError 盖住 JSONDecodeError **和** UnicodeDecodeError:日文题库被编辑器存成
        # Shift-JIS 时抛的是后者,以前只接 JSON 错,编码错会从 init_state 冒出来让每一页都崩。
        pass
    return [dict(tp) for tp in _SEED_TOPICS]


def _int_or(v, default: int) -> int:
    """Dirty legacy topics.json values (CG3) must never crash topic loading."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _normalize_topic(t: dict) -> dict:
    """Back-compat: presets saved before the schema change carried `shot_seconds`
    (per-shot duration). Translate it to the new `total_seconds` (whole clip)."""
    out = dict(t)
    if "total_seconds" not in out and "shot_seconds" in out:
        out["total_seconds"] = _int_or(out.pop("shot_seconds"), 5) * _int_or(out.get("shot_count", 1), 3)
    out["total_seconds"] = _int_or(out.get("total_seconds"), 15)
    out["shot_count"] = _int_or(out.get("shot_count"), 3)
    return out


# ---------------- api configs (from .streamlit/secrets.toml) -----------------

def load_api_configs() -> list[dict]:
    """Read [[api_configs]] entries from .streamlit/secrets.toml.

    Each entry should have keys: name, base_url, model, api_key.
    Returns [] if secrets file is missing or empty — sidebar handles that case.
    """
    try:
        raw = st.secrets.get("api_configs", [])
    except Exception:  # FileNotFoundError, StreamlitSecretNotFoundError, etc.
        return []
    out = []
    for item in raw:
        out.append({
            "name": item.get("name", "(unnamed)"),
            "base_url": item.get("base_url", ""),
            "model": item.get("model", ""),
            "api_key": item.get("api_key", ""),
        })
    return out
