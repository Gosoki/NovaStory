"""日本 4 条件数据的「采集 → 入库 → 分析」端到端自测(2026-11-03;make smoke-ja)。

dev_smoke_e2e 只走 1 名被试、只查入库;analysis_smoke 用合成 CSV、不经过真实界面。中间这一段 ——
**app 真实写进库的日本数据,整条分析管线能不能取出来** —— 以前没人测过。这里:

  1. 8 名 ja 被试(两个完整 4×4 方阵)走真实界面:C / D / E / F 各一轮 + 每轮问卷(随机答案)+ 总问卷;
     F 轮三种行为轮流 —— mixed(dev_smoke_e2e 的原流程)/ hint_pick(每题点「💡 ヒント」→「表示する」再点选)/
     hint_decline(每题点「💡 ヒント」→「自分で書く」再写)。
  2. 查库:被试、trial、问卷、总问卷新题、F 每题的 n_hint_clicks / revealed / n_reveal_clicks / n_hint_declines /
     fixed_q 及其相互一致、事件、同意书指纹。
  3. 跑管线:v3 → events → stats --lang ja → f_analysis → ja_predictions → question_value → route → audit_db,
     再渲染一次研究员后台(F 的统计 caption 必须出现)。

桩 LLM、不调任何 API、不出图;库与 CSV 都在临时目录,不碰 data/。embedding 那几项(P1 / QV2 / QV3)报「数据不足」是预期的。
用法:  NOVASTORY_NO_IMAGES=1 .venv/bin/python scripts/ja_pipeline_smoke.py   (或 make smoke-ja)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

os.environ["NOVASTORY_E2E_LANG"] = "ja"
os.environ["NOVASTORY_NO_IMAGES"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import dev_smoke_e2e as E  # noqa: E402 —— 导入即装好桩 LLM 与临时库(不会跑它自己的 run())
import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

N = 8
rng = random.Random(20261103)
# 06 ㉞:队列按招募地 —— 日本招募的人选了中文 / 英文界面也做 4 条件(cohort=ja)。8 人里放 1 个 zh、1 个 en 界面,
# 让「lang ≠ cohort」这条路径(题面 / 提示词语言、按队列筛、缺 zh 第 4 题 / en 基线时分析照常跑完)每次回归都走到。
LANGS = ["ja", "zh", "ja", "ja", "ja", "en", "ja", "ja"]
# zh / en 被试的桩稿(dev_smoke_e2e 导入时按 ja 装好桩;换语言时一并换掉,解析器按各自语言的字段标记读)
_STUB = {
    "zh": {"script": ("1.\n【画面描写】闹钟显示23:00，主角猛地抬头\n【时长】3 秒 【拍法】特写 【台词/音效】\"完了完了\"\n"
                      "2.\n【画面描写】书页快速翻动，荧光笔乱涂\n【时长】8 秒 【拍法】中景 【台词/音效】纸张哗哗声\n"
                      "3.\n【画面描写】天亮，主角趴在书堆里睡着\n【时长】4 秒 【拍法】远景 【台词/音效】鸟叫"),
           "rev": ("完了完了", "哈哈哈，有意思"),
           "q": [("psychology", "主角此刻最强烈的感受是？", ["恐慌", "自嘲", "冷静"]),
                 ("turning_point", "故事在哪里转折？", ["开场", "中段", "结尾"]),
                 ("key_shot", "最想让观众记住哪个画面？", ["翻书", "睡着", "天亮"]),
                 ("tone", "整体基调？", ["喜剧", "悬疑", "写实"]),
                 ("ending", "结局走向？", ["失败", "反转", "开放"]),
                 ("sound", "突出哪种声音？", ["钟表声", "独白", "环境音"])],
           "fu": [("pacing", "第二镜是否太长？", ["缩短", "保持", "拆成两镜"]),
                  ("dialogue", "要不要加一句结尾台词？", ["要", "不要"])],
           "input": {"intents": ("主角发现卷子上的题目昨晚全梦到过", "末班车开走后他跟着夜跑团回家",
                                 "告白的话写在毕业帽内侧被风吹走", "躲雨时和陌生人分享了一把伞"),
                     "request": "更搞笑一点", "edit": "\n（我手改的：结尾加一个彩蛋镜头）",
                     "custom": "我自己写的：荒诞但温柔", "f_custom": "我自己写的：先不敢相信，然后笑出来"}},
    "en": {"script": ("1.\n【Visual】The alarm clock shows 23:00 and the hero jerks awake\n"
                      "【Duration】3 s 【Shot】close-up 【Audio】\"Oh no, oh no\"\n"
                      "2.\n【Visual】Textbook pages flip fast, a highlighter races across\n"
                      "【Duration】8 s 【Shot】medium 【Audio】pages rustling\n"
                      "3.\n【Visual】Morning: the hero asleep face-down on a pile of books\n"
                      "【Duration】4 s 【Shot】wide 【Audio】birdsong"),
           "rev": ("Oh no, oh no", "Ha, that's funny"),
           "q": [("psychology", "What does the hero feel most strongly?", ["panic", "self-mockery", "calm"]),
                 ("turning_point", "Where does the story turn?", ["opening", "middle", "ending"]),
                 ("key_shot", "Which image should viewers remember?", ["flipping pages", "falling asleep", "dawn"]),
                 ("tone", "Overall tone?", ["comedy", "suspense", "realism"]),
                 ("ending", "Which way does it end?", ["failure", "twist", "open"]),
                 ("sound", "Which sound to feature?", ["ticking clock", "monologue", "ambience"])],
           "fu": [("pacing", "Is shot 2 too long?", ["shorten", "keep", "split in two"]),
                  ("dialogue", "Add a closing line?", ["yes", "no"])],
           "input": {"intents": ("She realises she dreamed every exam question last night",
                                 "After missing the last train he jogs home with a night-running club",
                                 "A confession written inside a graduation cap blows away",
                                 "Sheltering from rain, two strangers share one umbrella"),
                     "request": "Make it funnier", "edit": "\n(my own edit: add a bonus shot at the end)",
                     "custom": "My own words: absurd but gentle", "f_custom": "My own words: disbelief, then laughter"}},
}
_STUB["ja"] = {"script": E._SCRIPT, "rev": ("やばい、やばい", "ははっ、面白い"), "r1": E._R1_QUESTIONS,
               "fu_q": E._FU_QUESTIONS, "input": dict(E._INPUT)}


def _use_lang(lang: str) -> None:
    """下一位被试用 lang 界面:标签映射、桩稿、被试输入一起换;E2E 的模块级注入表也清掉(会跨被试残留)。"""
    s = _STUB[lang]
    E._injected.clear()
    E.E2E_LANG = lang
    E._TGT_TEXT = dict(E._walk(E._T._load(lang)))
    E._SCRIPT = s["script"]
    E._SCRIPT_REV = s["script"].replace(*s["rev"])
    E._R1_QUESTIONS = s.get("r1") or {"questions": [{"dimension": d, "question": q, "options": o, "why": "x"}
                                                     for d, q, o in s["q"]]}
    E._FU_QUESTIONS = s.get("fu_q") or {"questions": [{"dimension": d, "question": q, "options": o, "why": "x"}
                                                       for d, q, o in s["fu"]]}
    E._INPUT = s["input"]
    E.EDIT_MARK = s["input"]["edit"]


def _answer_questionnaire(at, round_idx, attention=False, ai_q=False):
    """同 dev_smoke_e2e 的问卷步骤,但答案随机(固定值没有方差,stats 拟合不了)。注意力题照样答对。"""
    for i in range(1, 4):
        E.set_sc(at, f"_q_own{i}_{round_idx}", rng.randint(1, 7))
    for i in range(1, 3):
        E.set_sc(at, f"_q_soa{i}_{round_idx}", rng.randint(1, 7))
    if attention:
        E.set_sc(at, f"_q_attention_{round_idx}", 2)
    for k in ("tlx1", "violation", "imagine", "sat"):
        E.set_sc(at, f"_q_{k}_{round_idx}", rng.randint(1, 7))
    if ai_q:
        E.set_sc(at, f"_q_ai_q_quality_{round_idx}", rng.randint(1, 7))
        E.set_sc(at, f"_q_ai_q_amount_{round_idx}", rng.randint(1, 7))
    for idx in (1, 2, 3):
        bg = next(b for b in at.get("button_group") if b.key == f"_q_shot{idx}_{round_idx}")
        bg.set_value(rng.choice(bg.options))
    E.btn_click(at, "提交本轮问卷")


E._answer_questionnaire = _answer_questionnaire   # E 的步骤函数按模块名查找,替换后生效


def _f_round(at, ridx: int, mode: str) -> None:
    """F 的一轮,和真实被试一样逐题操作、点「次の質問」翻页(每翻一页就核对上一题的答案存下来了):
    hint_pick    = 每题点「💡 ヒント」→「表示する」(等 2 秒)再在选项控件上点选;
    hint_decline = 每题点「💡 ヒント」→「自分で書く」再写;其中第 2 题先写完再点开(字必须还在,答案仍是自己写的)。"""
    at.text_area(key="_intent_input").set_value(E._INPUT["intents"][ridx - 1])
    E.btn_click(at, "确定，开始创作")
    assert at.session_state["r_phase"] == "guidance", at.session_state["r_phase"]
    qs = at.session_state["r_g_questions"]
    for i, q in enumerate(qs):
        assert at.session_state["r_g_idx"] == i, (at.session_state["r_g_idx"], i)
        assert not at.segmented_control, f"第 {i} 题没点ヒント就出了选项"
        if mode == "hint_pick":
            E.btn_click(at, "💡 提示")
            E.btn_click(at, "显示")
            assert at.session_state["r_g_reveal"][i]["shown"], i
            want = {"opt": q["options"][rng.randrange(len(q["options"]))], "custom": ""}
            E.set_sc(at, f"_g_opt_1_{i}", want["opt"])
        else:
            want = {"opt": None, "custom": f"自分で：案{i}-{rng.randint(1, 99)}"}
            if i == 1:   # 先写完再点开ヒント:字必须保住
                at.text_input(key=f"_g_custom_1_{i}").set_value(want["custom"])
                E.btn_click(at, "💡 提示")
                E.btn_click(at, "显示")
                assert at.text_input(key=f"_g_custom_1_{i}").value == want["custom"], "点开ヒント后自己写的字丢了"
                assert at.session_state["r_g_reveal"][i]["shown"], "点开后选项没出来"
            else:
                E.btn_click(at, "💡 提示")
                E.btn_click(at, "自己写")
                assert not at.session_state["r_g_reveal"][i]["asking"] and not at.segmented_control, i
                at.text_input(key=f"_g_custom_1_{i}").set_value(want["custom"])
        if i < len(qs) - 1:
            E.btn_click(at, "下一问")
            got = at.session_state["r_g_answers"].get(i) or {}
            assert {k: got.get(k) for k in ("opt", "custom")} == want, f"第 {i} 题翻页后答案没存对:{got} ≠ {want}"
        else:
            E.btn_click(at, "完成作答，生成脚本")
    assert at.session_state["r_phase"] == "postgen", at.session_state["r_phase"]
    E.inject(at, "_script_edit", E._SCRIPT + E.EDIT_MARK)
    E.btn_click(at, "满意了，提交这一版")
    _answer_questionnaire(at, ridx, ai_q=True, attention=ridx == E._q._ATTENTION_ROUND)


def _final_survey(at) -> None:
    for i in range(5):
        at.radio[i].set_value(rng.choice(at.radio[i].options))
    if rng.random() < 0.6:
        at.text_area(key="_fs_pref_reason").set_value(f"理由{rng.randint(1, 9)}")
    E.set_sc(at, "_fs_sat", rng.randint(1, 7))
    at.radio[5].set_value(rng.choice(at.radio[5].options))
    E.btn_click(at, "提交并完成")


def _participant(k: int) -> tuple[int, list[str], str]:
    _use_lang(LANGS[k])
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
    at.run()
    at.radio(key="_consent_lang").set_value(E.LANG_LABELS[LANGS[k]])
    at.run()
    at.checkbox(key="_consent_agree").check().run()
    E.btn_click(at, "同意并开始")
    at.selectbox[0].select(E.L("21-30 岁"))
    at.selectbox[1].select(E.L("不愿透露"))
    at.selectbox[2].select(E.L("偶尔（每月几次）"))
    at.selectbox[3].select(E.L("从未用过"))
    at.radio[0].set_value(E.L("从未发布过"))
    at.radio[1].set_value(E.L("否"))
    at.radio[2].set_value(E.L("否"))
    at.radio[3].set_value(E.L("我不知道"))
    at.radio[4].set_value(E.L("我不知道"))
    for key in ("_scr_self", "_scr_conf", "_scr_trust", "_scr_own"):
        E.set_sc(at, key, rng.randint(1, 7))
    E.btn_click(at, "提交并继续")
    E.btn_click(at, "开始")
    plan = [r["condition"] for r in at.session_state["round_plan"]]
    mode = ("mixed", "hint_pick", "hint_decline")[k % 3]
    for ridx, cond in enumerate(plan, start=1):
        if cond == "F" and mode != "mixed":
            _f_round(at, ridx, mode)
        else:
            E._ROUND_STEPS[cond](at, ridx)
    assert at.session_state["stage"] == "final_survey", at.session_state["stage"]
    _final_survey(at)
    return at.session_state["seq"], plan, mode


def _cli(fn, argv: list[str]) -> tuple[int, str]:
    """跑一个分析脚本的 main:argv 走 sys.argv(v3 / events / stats 不收参数)或直接传(其余)。"""
    buf, old = io.StringIO(), sys.argv
    sys.argv = ["x", *argv]
    try:
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            try:
                rc = fn(argv) if fn.__code__.co_argcount else fn()
            except SystemExit as e:
                rc = e.code
    finally:
        sys.argv = old
    return (rc or 0), buf.getvalue()


def _check_db(db_path: Path) -> None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        P = pd.read_sql("SELECT * FROM participants", con)
        T = pd.read_sql("SELECT * FROM trials", con)
        Q = pd.read_sql("SELECT * FROM questionnaires", con)
        EV = pd.read_sql("SELECT * FROM events", con)
    finally:
        con.close()
    assert len(P) == N and set(P["status"]) == {"done"}, P[["lang", "status"]]
    assert set(P["cohort"]) == {"ja"} and P.sort_values("id")["lang"].tolist() == LANGS, \
        f"选中文 / 英文的日本招募者也该是 cohort=ja(06 ㉞):{P[['lang', 'cohort']].values.tolist()}"
    assert sorted(P["seq"]) == [0, 1, 4, 5, 8, 9, 12, 13], f"交错分配不对:{sorted(P['seq'])}"
    sj = P["screening_json"].map(json.loads)
    for lg in set(LANGS):   # 同意书指纹按各自的界面语言
        c = json.load(open(ROOT / "i18n" / "locales" / f"{lg}.json", encoding="utf-8"))["consent"]
        fp = hashlib.sha1("\x1f".join(c[k] for k in ("body", "agree", "no_refresh")).encode()).hexdigest()[:16]
        got = set(sj[P["lang"] == lg].map(lambda d: d.get("consent_sha1")))
        assert got == {fp}, f"{lg} 同意书指纹没落库或不一致:{got} ≠ {fp}"
    assert sj.map(lambda d: bool(d.get("session_id"))).all(), "screening_json 没记 session_id(连点提交的防重靠它)"
    fs = P["final_survey_json"].map(json.loads)
    assert fs.map(lambda d: d.get("owned_round") is not None).all(), "总问卷 owned_round 没落库"
    assert len(T) == 4 * N and T["condition"].value_counts().to_dict() == {c: N for c in "CDEF"}, T["condition"].value_counts()
    assert len(Q) == 4 * N, f"问卷 {len(Q)} 份"
    for col in ("final_output", "intent_statement", "script_versions", "t_postgen", "t_total", "parse_ok"):
        assert T[col].notna().all(), f"trials.{col} 有空值"
    assert T.loc[T["condition"].isin(["E", "F"]), "guidance_json"].notna().all(), "E / F 没有 guidance_json"
    f_items = [it for g in T.loc[T["condition"] == "F", "guidance_json"]
               for rd in json.loads(g)["rounds"] for it in rd["items"]]
    e_items = [it for g in T.loc[T["condition"] == "E", "guidance_json"]
               for rd in json.loads(g)["rounds"] for it in rd["items"]]
    assert all(k in it for it in f_items
               for k in ("n_hint_clicks", "revealed", "n_reveal_clicks", "n_hint_declines", "fixed_q")), "F 的专有字段缺失"
    assert not any(it["ai_decided"] for it in f_items), "F 不该有「交给 AI」"
    assert all((it["chosen"] or "").strip() for it in f_items), "F 有题的答案是空的(翻页时没存下来?)"
    assert not any(k in it for it in e_items for k in ("n_hint_clicks", "revealed")), "E 的 item 混进了 F 的字段"
    bad = [it for it in f_items
           if it["revealed"] != (it["n_reveal_clicks"] > 0) or (it["picked_option"] and not it["revealed"])
           or (it["n_reveal_clicks"] + it["n_hint_declines"] > it["n_hint_clicks"])]
    assert not bad, f"F 提示记录不一致:{bad[:2]}"
    revealed = [it for it in f_items if it["revealed"]]
    assert all((not it["is_custom"]) == bool(it["picked_option"]) for it in revealed), "点开后点选的题没记 picked_option"
    n_ev = EV["type"].value_counts()
    for etype, field in (("f_hint_click", "n_hint_clicks"), ("f_reveal_click", "n_reveal_clicks"),
                         ("f_hint_decline", "n_hint_declines")):
        n_item = sum(int(it[field]) for it in f_items)
        assert n_ev.get(etype, 0) == n_item > 0, f"{etype} 事件 {n_ev.get(etype, 0)} ≠ 各题 {field} 之和 {n_item}"
    groups = EV.loc[EV["type"] == "llm_start", "payload_json"].map(lambda s: json.loads(s).get("group"))
    assert {"F-guidance-r1", "F-final"} <= set(groups), set(groups)
    print(f"  ✅ 库:8 人(界面 {P['lang'].value_counts().to_dict()},全部 cohort=ja)/ 32 trial / 32 份问卷;"
          f"F {len(f_items)} 题(点ヒント {sum(it['n_hint_clicks'] > 0 for it in f_items)}、"
          f"显示了选项 {len(revealed)}、点开后退回自己写 {sum(it['n_hint_declines'] > 0 for it in f_items)});"
          f"事件 f_hint_click {n_ev.get('f_hint_click', 0)} 次")


def main() -> int:
    db_path = E.db.DB_PATH
    tmp = Path(tempfile.mkdtemp())
    csv = tmp / "v3.csv"
    print(f"temp db: {db_path}")
    for k in range(N):
        seq, plan, mode = _participant(k)
        print(f"  被试 {k + 1}: seq={seq:>2} 顺序={''.join(plan)} F={mode}")
    _check_db(db_path)

    from analysis import events as A_ev, f_analysis as A_f, ja_predictions as A_jp  # noqa: PLC0415
    from analysis import question_value as A_qv, route as A_rt, stats as A_st, v3 as A_v3  # noqa: PLC0415
    import audit_db as A_audit  # noqa: PLC0415
    rc, _ = _cli(A_v3.main, ["--db", str(db_path), "--out", str(csv)])
    assert rc == 0 and csv.exists(), "v3 失败"
    rc, _ = _cli(A_ev.main, ["--db", str(db_path), "--csv", str(csv)])
    assert rc == 0, "events 失败"
    df = pd.read_csv(csv)
    assert len(df) == 4 * N and set(df["cohort"]) == {"ja"}, df["cohort"].value_counts()
    must = ["own_mean", "imagine", "violation", "not_against", "mine_ratio", "satisfaction", "tlx1", "attention_ok",
            "pre_investment", "post_investment", "total_investment", "n_ai_rounds", "hand_edit_chars", "t_total",
            "fs_pref_cond", "fs_owned_cond", "fs_effort_cond", "seq", "n_llm_unclosed", "parse_ok"]
    gaps = {c: int(df[c].isna().sum()) for c in must if c not in df or df[c].isna().any()}
    assert not gaps, f"v3 CSV 缺值:{gaps}"
    ef = df[df["condition"].isin(["E", "F"])]
    assert ef[["g_custom_rate", "g_n_questions", "ai_q_quality"]].notna().all().all(), "E / F 的引导与 AI 提问列缺值"
    print(f"  ✅ v3 + events:{len(df)} 行 × {len(df.columns)} 列,关键列无缺值")

    checks = [
        ("stats --lang ja", A_st.main, ["--lang", "ja", "--csv", str(csv)], "LMM 计划对比"),
        # 桩让每个 F 第一轮的 tone 题面夹带候补 → 8 人各丢 1 道 AI 补题
        ("f_analysis", A_f.main, ["--csv", str(csv), "--db", str(db_path)], "丢掉的 AI 补题 8("),
        ("ja_predictions", A_jp.main, ["--lang", "ja", "--csv", str(csv), "--db", str(db_path)], "[P3]"),
        ("question_value", A_qv.main, ["--db", str(db_path), "--lang", "ja"], "F(trial"),
        ("route", A_rt.main, ["--csv", str(csv), "--db", str(db_path)], "路线:**主推"),
        ("audit_db", A_audit.main, ["--db", str(db_path)], "提示记录不一致 0(应为 0)"),
    ]
    for name, fn, argv, marker in checks:
        rc, out = _cli(fn, argv)
        assert rc == 0 and marker in out, f"{name}:rc={rc},找不到「{marker}」\n{out[-800:]}"
        print(f"  ✅ {name}")
    _, out = _cli(A_f.main, ["--csv", str(csv), "--db", str(db_path)])
    assert "点开" in out and "[F−D 探索性比较]" in out, "F 分析没有点开率或 F−D"

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
    at.query_params["lang"] = "ja"
    at.run()
    at.session_state["researcher_ok"] = True
    at.session_state["researcher_mode"] = True
    at.run()
    assert at.exception == [], at.exception[:1]
    assert any(c.value.startswith("F ") and "選択肢" in c.value for c in at.caption), "后台没有 F 的统计"
    print("  ✅ 研究员后台渲染无异常,F 的统计出现")
    print("\nJA PIPELINE SMOKE PASSED —— 采集 → 入库 → 分析全链路可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
