"""End-to-end smoke test (task book M9/T9.1) — no network, temp DB.

Drives the full participant flow with Streamlit's AppTest:
consent → screening → R1(C) → R2(D, attention check) → R3(E, ModeMirror)
→ done, then asserts the database contents.

Run:  .venv/bin/python scripts/dev_smoke_e2e.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

from core import db, llm  # noqa: E402

# ---------- stub LLM ----------

_OUTLINE = "1. 主角深夜打开书,发现一页没看。\n2. 他试图临时抱佛脚,越看越绝望。\n3. 天亮进考场,题目竟然全是梦里见过的。"
_FINAL = (
    "1. 【景别】特写 【画面描写】闹钟显示23:00,主角猛地抬头 【台词/音效】\"完了完了\" 【时长 3 秒】\n"
    "2. 【景别】中景 【画面描写】书页快速翻动,荧光笔乱涂 【台词/音效】纸张哗哗声 【时长 8 秒】\n"
    "3. 【景别】远景 【画面描写】天亮,主角趴在书堆里睡着 【台词/音效】鸟叫 【时长 4 秒】"
)
_DISSENT = "【重合】你的大纲和默认版本都落在\"临时抱佛脚\"。\n【提问】你真正想让观众记住哪个画面?\n【反提案】把基调反转:考场上他笑着交了白卷。"


def _stub_stream(system, user, *, group, user_id="", temperature=None):
    text = _OUTLINE if "outline" in group else _FINAL
    yield text


def _stub_generate(system, user, *, group, user_id="", n=1, temperature=None):
    if "dissent" in group:
        return [_DISSENT]
    return [_OUTLINE + f"(变体{i})" for i in range(n)]


llm.generate_stream = _stub_stream
llm.generate = _stub_generate
llm._client = lambda: None

# ---------- temp DB ----------

db.DB_PATH = Path(tempfile.mkdtemp()) / "smoke.db"
print(f"temp db: {db.DB_PATH}")


_injected: dict[str, object] = {}


def inject(at, key, value):
    """Set a widget value via session_state (works for segmented_control,
    which AppTest has no accessor for) and remember it for backfilling."""
    _injected[key] = value
    at.session_state[key] = value


def safe_run(at):
    """AppTest workaround: after a st.rerun page transition the element tree
    retains stale widgets whose session keys Streamlit already dropped, and
    the next run crashes while serializing them. Backfill missing keys."""
    for r in list(at.radio) + list(at.selectbox):
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


def radio_by_key(at, key):
    hits = [r for r in at.radio if r.key == key]
    assert hits, f"radio not found: {key!r}"
    return hits[0]


def run() -> None:
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()

    # --- consent ---
    at.checkbox(key="_consent_agree").check().run()
    btn_click(at, "同意并开始")

    # --- screening (pass) ---
    at.selectbox[0].select("18-24")            # age
    at.selectbox[1].select("不愿透露")          # gender
    at.selectbox[2].select("偶尔(每月几次)")    # ai freq
    at.radio[0].set_value("从未发布过")          # published
    at.radio[1].set_value("否")                 # background
    at.radio[2].set_value("否")                 # written
    at.radio[3].set_value("我不知道")            # quiz1
    at.radio[4].set_value("我不知道")            # quiz2
    inject(at, "_scr_self", 1)                  # self rating (segmented_control)
    btn_click(at, "提交并继续")

    # seq 0 → conditions C, D, E with topics 1, 2, 3
    # --- R1: condition C ---
    at.text_area(key="_intent_input").set_value("主角发现卷子上的题目昨晚全梦到过")
    btn_click(at, "确定,开始创作")  # pipeline auto-generates final via stub
    btn_click(at, "确认这一版,继续")
    _answer_questionnaire(at, round_idx=1)

    # --- R2: condition D (attention check round) ---
    at.text_area(key="_intent_input").set_value("末班车开走后他决定跟着夜跑团回家")
    btn_click(at, "确定,开始创作")  # outline auto-generated
    at.text_area(key="_outline_edit").set_value(_OUTLINE + "\n4.(我自己加的)结局他走进了便利店打工。")
    btn_click(at, "确认大纲,生成最终脚本")
    btn_click(at, "确认这一版,继续")
    _answer_questionnaire(at, round_idx=2, attention=True)

    # --- R3: condition E (ModeMirror) ---
    at.text_area(key="_intent_input").set_value("告白前他把要说的话写在了毕业帽内侧")
    btn_click(at, "确定,开始创作")  # outline + defaults
    at.text_area(key="_outline_edit").set_value(_OUTLINE + "\n(改:告白对象先开口了)")
    btn_click(at, "完成编辑,听听 AI 的不同意见")  # dissent generated
    radio_by_key(at, "_mm_choice").set_value("transform")  # raw option; AppTest re-applies format_func
    at.text_input(key="_mm_reason").set_value("反转结局可以,但基调想保留")
    btn_click(at, "确定我的决定")
    at.text_area(key="_outline_edit").set_value(_OUTLINE + "\n(终稿大纲:保留温情但交白卷)")
    btn_click(at, "确认大纲,生成最终脚本")
    btn_click(at, "确认这一版,继续")
    _answer_questionnaire(at, round_idx=3)

    # --- done ---
    codes = [el.value for el in at.code]
    assert codes and len(codes[0]) == 8, f"completion code missing: {codes}"
    print("completion code:", codes[0])

    _assert_db()
    print("E2E SMOKE PASSED")


def _answer_questionnaire(at, round_idx: int, attention: bool = False) -> None:
    # Likert items are segmented_control widgets; AppTest has no accessor for
    # them yet, so values are injected via session_state (applied before the
    # next run instantiates the widgets — same mechanism as views/devtools.py).
    for i in range(1, 4):
        inject(at, f"_q_own{i}_{round_idx}", 5)
    for i in range(1, 3):
        inject(at, f"_q_soa{i}_{round_idx}", 4)
    if attention:
        inject(at, f"_q_attention_{round_idx}", 2)
    inject(at, f"_q_tlx1_{round_idx}", 3)
    inject(at, f"_q_violation_{round_idx}", 2)
    inject(at, f"_q_imagine_{round_idx}", 6)
    for idx in (1, 2, 3):  # stub final always parses into 3 shots
        inject(at, f"_q_shot{idx}_{round_idx}", "mine")
    btn_click(at, "提交本轮问卷")


def _assert_db() -> None:
    p = db.load_table("participants")
    tr = db.load_table("trials")
    q = db.load_table("questionnaires")
    ev = db.load_table("events")
    assert len(p) == 1 and p.iloc[0]["passed"] == 1 and p.iloc[0]["seq"] == 0
    assert p.iloc[0]["attention_ok"] == 1 and p.iloc[0]["status"] == "done"
    assert list(tr["condition"]) == ["C", "D", "E"], list(tr["condition"])
    assert tr["model"].notna().all() and tr["t_total"].notna().all()
    assert tr.iloc[2]["adjudication"] == "transform"
    assert tr["parse_ok"].sum() == 3
    assert len(q) == 3
    assert q["imagine_match"].notna().all()
    assert len(ev) >= 25, len(ev)
    e_types = set(ev[ev["round_idx"] == 3]["type"])
    for needed in ("round_start", "intent_submit", "outline_shown", "dissent_request",
                   "dissent_shown", "adjudicate", "final_click", "final_shown",
                   "trial_submit", "questionnaire_submit"):
        assert needed in e_types, f"missing event {needed} in round 3: {e_types}"
    print(f"db ok: participants=1 trials={len(tr)} questionnaires={len(q)} events={len(ev)}")


if __name__ == "__main__":
    run()
