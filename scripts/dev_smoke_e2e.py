"""End-to-end smoke test (task book v2.0, T6.2) — no network, temp DB.

Drives the full v3 participant flow with Streamlit's AppTest:
consent → screening → R1(C one-shot) → R2(D revise + hand-edit, attention check)
→ R3(E guidance round-1 → script → follow-up guidance → hand-edit) → done,
then asserts the database contents (guidance_json / revision_requests /
script_versions / behavioral counters / timing columns).

Run:  .venv/bin/python scripts/dev_smoke_e2e.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

from core import db, llm  # noqa: E402

# ---------- stub LLM ----------

_SCRIPT = (
    "1. 【景别】特写 【画面描写】闹钟显示23:00,主角猛地抬头 【台词/音效】\"完了完了\" 【时长 3 秒】\n"
    "2. 【景别】中景 【画面描写】书页快速翻动,荧光笔乱涂 【台词/音效】纸张哗哗声 【时长 8 秒】\n"
    "3. 【景别】远景 【画面描写】天亮,主角趴在书堆里睡着 【台词/音效】鸟叫 【时长 4 秒】"
)
_SCRIPT_REV = _SCRIPT.replace("完了完了", "哈哈哈,有意思")

_R1_QUESTIONS = {
    "questions": [
        {"dimension": "psychology", "question": "主角此刻最强烈的感受是?",
         "options": ["恐慌", "自嘲", "冷静"], "why": "情绪锚点决定表演方向"},
        {"dimension": "turning_point", "question": "故事在哪里转折?",
         "options": ["开场", "中段", "结尾"], "why": "决定节奏"},
        {"dimension": "key_shot", "question": "最想让观众记住哪个画面?",
         "options": ["翻书", "睡着", "天亮"], "why": "视觉重点"},
        {"dimension": "tone", "question": "整体基调?",
         "options": ["喜剧", "悬疑", "写实"], "why": "统一气质"},
        {"dimension": "ending", "question": "结局走向?",
         "options": ["失败", "反转", "开放"], "why": "余味"},
        {"dimension": "sound", "question": "突出哪种声音?",
         "options": ["钟表声", "独白", "环境音"], "why": "氛围"},
    ]
}
_FU_QUESTIONS = {
    "questions": [
        {"dimension": "pacing", "question": "第二镜是否太长?",
         "options": ["缩短", "保持", "拆成两镜"], "why": "节奏"},
        {"dimension": "dialogue", "question": "要不要加一句结尾台词?",
         "options": ["要", "不要"], "why": "收束"},
    ]
}


def _stub_stream(system, user, *, group, user_id="", temperature=None):
    yield _SCRIPT_REV if "revise" in group else _SCRIPT


def _stub_json(system, user, *, group, user_id="", retries=None, temperature=None):
    return _R1_QUESTIONS if group == "E-guidance-r1" else _FU_QUESTIONS


llm.generate_stream = _stub_stream
llm.generate_json = _stub_json
llm._client = lambda: None

# ---------- temp DB ----------

db.DB_PATH = Path(tempfile.mkdtemp()) / "smoke.db"
print(f"temp db: {db.DB_PATH}")

_injected: dict[str, object] = {}

EDIT_MARK = "\n(我手改的:结尾加一个彩蛋镜头)"


def inject(at, key, value):
    """Set a text/checkbox widget value via session_state and remember it for
    backfilling across reruns."""
    _injected[key] = value
    at.session_state[key] = value


def set_sc(at, key, value):
    """Set a st.segmented_control (AppTest ButtonGroup) reliably by key.

    Direct session injection of segmented_control is fragile — acceptance
    depends on widget-tree state — so drive it through the real accessor.
    """
    for bg in at.get("button_group"):
        if bg.key == key:
            bg.set_value(value)
            return
    raise AssertionError(
        f"segmented_control {key!r} not on page (have {[b.key for b in at.get('button_group')]})"
    )


def safe_run(at):
    """AppTest workaround: after a st.rerun page transition the element tree
    retains stale widgets whose session keys Streamlit already dropped, and
    the next run crashes while serializing them. Backfill missing keys."""
    for r in list(at.radio) + list(at.selectbox) + list(at.get("button_group")):
        if r.key and r.key not in at.session_state:
            at.session_state[r.key] = None
    for w in list(at.text_input) + list(at.text_area):
        if w.key and w.key not in at.session_state:
            at.session_state[w.key] = ""
    for k, v in _injected.items():  # segmented_control & friends
        if k not in at.session_state:
            at.session_state[k] = v
    at.run()


def btn_click(at, label):
    hits = [b for b in at.button if b.label == label]
    assert hits, f"button not found: {label!r} (have: {[b.label for b in at.button]})"
    hits[0].click()
    safe_run(at)


def run() -> None:
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()
    # Participants default to ja; the researcher (and this test) work in zh.
    at.session_state["lang"] = "zh"

    # --- consent + screening (everyone proceeds; novice recorded) ---
    at.checkbox(key="_consent_agree").check().run()
    btn_click(at, "同意并开始")
    at.selectbox[0].select("18-24")
    at.selectbox[1].select("不愿透露")
    at.selectbox[2].select("偶尔(每月几次)")
    at.radio[0].set_value("从未发布过")
    at.radio[1].set_value("否")
    at.radio[2].set_value("否")
    at.radio[3].set_value("我不知道")
    at.radio[4].set_value("我不知道")
    set_sc(at, "_scr_self", 1)
    btn_click(at, "提交并继续")
    assert at.session_state["stage"] == "rounds" and at.session_state["seq"] == 0

    # seq 0 → conditions C, D, E with topics 1, 2, 3
    # --- R1: C — one-shot ---
    at.text_area(key="_intent_input").set_value("主角发现卷子上的题目昨晚全梦到过")
    btn_click(at, "确定,开始创作")          # auto-generates, lands in postgen (readonly)
    btn_click(at, "满意了,提交这一版")
    _answer_questionnaire(at, round_idx=1)

    # --- R2: D — revise via chat + hand-edit (attention round) ---
    at.text_area(key="_intent_input").set_value("末班车开走后他跟着夜跑团回家")
    btn_click(at, "确定,开始创作")
    inject(at, "_revision_input", "更搞笑一点")
    btn_click(at, "让 AI 修改")             # → v2 (ai)
    assert at.session_state["r_n_ai_rounds"] == 1
    inject(at, "_script_edit", _SCRIPT_REV + EDIT_MARK)
    btn_click(at, "满意了,提交这一版")       # persist → v3 (user_edit) → questionnaire
    _answer_questionnaire(at, round_idx=2, attention=True)

    # --- R3: E — guidance round-1 → script → follow-up → hand-edit ---
    at.text_area(key="_intent_input").set_value("告白的话写在毕业帽内侧被风吹走")
    btn_click(at, "确定,开始创作")          # → guidance Q1 shown
    _answer_guidance(at, rnd=1, n=6, custom_idx=1, ai_idx=2)
    assert at.session_state["r_phase"] == "postgen"
    btn_click(at, "让 AI 继续引导")          # → follow-up questions
    _answer_guidance(at, rnd=2, n=2)
    assert at.session_state["r_n_ai_rounds"] == 1
    inject(at, "_script_edit", _SCRIPT_REV + EDIT_MARK)
    btn_click(at, "满意了,提交这一版")
    _answer_questionnaire(at, round_idx=3)

    # --- done ---
    codes = [el.value for el in at.code]
    assert codes and len(codes[0]) == 8, f"completion code missing: {codes}"
    print("completion code:", codes[0])

    _assert_db()
    print("E2E SMOKE PASSED")


def _answer_guidance(at, rnd: int, n: int, custom_idx: int = -1, ai_idx: int = -1) -> None:
    # Answer via direct session injection + jump to the last question, mirroring
    # the researcher devtools path (reliable: per-question widget navigation with
    # segmented_control set_value loses values across the advance-rerun).
    qs = at.session_state["r_g_questions"]
    assert len(qs) == n, f"expected {n} questions, got {len(qs)}"
    ai_decide_zh = "交给 AI 决定"  # researcher tests in zh; == t("guidance.ai_decide")
    answers = {}
    for i in range(n):
        if i == custom_idx:
            answers[i] = {"opt": None, "custom": "我自己写的:荒诞但温柔"}
        elif i == ai_idx:
            answers[i] = {"opt": ai_decide_zh, "custom": ""}
        elif qs[i]["options"]:
            answers[i] = {"opt": qs[i]["options"][0], "custom": ""}
    at.session_state["r_g_answers"] = answers
    at.session_state["r_g_idx"] = n - 1
    safe_run(at)  # render the last question so its finish button appears
    btn_click(at, "完成作答,生成脚本")


def _answer_questionnaire(at, round_idx: int, attention: bool = False) -> None:
    for i in range(1, 4):
        set_sc(at, f"_q_own{i}_{round_idx}", 5)
    for i in range(1, 3):
        set_sc(at, f"_q_soa{i}_{round_idx}", 4)
    if attention:
        set_sc(at, f"_q_attention_{round_idx}", 2)
    set_sc(at, f"_q_tlx1_{round_idx}", 3)
    set_sc(at, f"_q_violation_{round_idx}", 2)
    set_sc(at, f"_q_imagine_{round_idx}", 6)
    for idx in (1, 2, 3):  # stub scripts parse into 3 shots; option[0] == "mine" label
        bg = next(b for b in at.get("button_group") if b.key == f"_q_shot{idx}_{round_idx}")
        bg.set_value(bg.options[0])
    btn_click(at, "提交本轮问卷")


def _assert_db() -> None:
    p = db.load_table("participants")
    tr = db.load_table("trials").sort_values("round_idx")
    q = db.load_table("questionnaires")
    ev = db.load_table("events")

    assert len(p) == 1 and p.iloc[0]["passed"] == 1 and p.iloc[0]["seq"] == 0
    assert p.iloc[0]["attention_ok"] == 1 and p.iloc[0]["status"] == "done"
    assert json.loads(p.iloc[0]["screening_json"])["is_novice"] is True

    assert list(tr["condition"]) == ["C", "D", "E"]
    assert tr["model"].notna().all() and tr["t_total"].notna().all()

    c, d, e = tr.iloc[0], tr.iloc[1], tr.iloc[2]

    import pandas as pd

    cv = json.loads(c["script_versions"])
    assert [x["author"] for x in cv] == ["ai"] and c["n_hand_edits"] == 0
    assert pd.isna(c["guidance_json"]) and pd.isna(c["revision_requests"])

    dv = json.loads(d["script_versions"])
    assert [x["author"] for x in dv] == ["ai", "ai", "user_edit"], dv
    reqs = json.loads(d["revision_requests"])
    assert len(reqs) == 1 and reqs[0]["text"] == "更搞笑一点"
    assert d["n_ai_rounds"] == 1 and d["n_hand_edits"] == 1 and d["hand_edit_chars"] > 0
    assert d["t_postgen"] is not None and EDIT_MARK.strip() in d["final_output"]

    g = json.loads(e["guidance_json"])["rounds"]
    assert len(g) == 2
    r1, r2 = g[0], g[1]
    assert r1["source"] == "fixed3+ai_supplement" and len(r1["items"]) == 6
    dims = [it["dimension"] for it in r1["items"]]
    assert {"psychology", "turning_point", "key_shot"}.issubset(set(dims))
    assert sum(it["is_custom"] for it in r1["items"]) == 1
    assert sum(it["ai_decided"] for it in r1["items"]) == 1
    assert r2["source"] == "ai_from_draft" and len(r2["items"]) == 2
    assert "draft_snapshot_ref" in r2
    ev_versions = json.loads(e["script_versions"])
    assert [x["author"] for x in ev_versions] == ["ai", "ai", "user_edit"]
    assert e["n_ai_rounds"] == 1 and e["n_hand_edits"] == 1
    assert e["t_pregen"] is not None and e["t_postgen"] is not None

    assert len(q) == 3 and q["imagine_match"].notna().all()

    r3 = set(ev[ev["round_idx"] == 3]["type"])
    for needed in ("round_start", "intent_submit", "guidance_shown", "guidance_answer",
                   "guidance_submit", "script_shown", "continue_guidance_click",
                   "hand_edit_saved", "trial_submit", "questionnaire_submit"):
        assert needed in r3, f"missing event {needed} in round 3: {r3}"
    r2ev = set(ev[ev["round_idx"] == 2]["type"])
    assert "revision_request" in r2ev and "hand_edit_saved" in r2ev
    print(f"db ok: trials={len(tr)} questionnaires={len(q)} events={len(ev)}")


if __name__ == "__main__":
    run()
