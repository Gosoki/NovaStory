"""End-to-end smoke test (task book v2.0, T6.2) — no network, temp DB.

Drives the full v3 participant flow with Streamlit's AppTest:
consent → screening → R1(C one-shot) → R2(D revise + hand-edit, attention check)
→ R3(E guidance round-1 → script → follow-up guidance → hand-edit) → done,
then asserts the database contents (guidance_json / revision_requests /
script_versions / behavioral counters / timing columns / intake event trail).

Run:  .venv/bin/python scripts/dev_smoke_e2e.py
      NOVASTORY_E2E_LANG=ja .venv/bin/python scripts/dev_smoke_e2e.py   # 日语全流程
ja 模式另查:每屏界面文字与发给 LLM 的提示词里有没有简体中文(CLAUDE.md §0.3)。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

from core import config, db, llm  # noqa: E402
from i18n import DEFAULT_LANG, LANG_LABELS  # noqa: E402
from i18n import translator as _T  # noqa: E402
from views import questionnaire as _q  # noqa: E402

# 跑哪种语言的全流程(make smoke-e2e 两种都跑)。下面的按钮 / 选项标签写的是中文,
# 非 zh 时由 L() 按 i18n 键换成目标语言 —— 同一套步骤,不复制一份测试。
E2E_LANG = os.environ.get("NOVASTORY_E2E_LANG", "zh")


def _walk(d: dict, path: str = ""):
    for k, v in d.items():
        p = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            yield from _walk(v, p)
        elif isinstance(v, list):
            yield from ((f"{p}[{i}]", x) for i, x in enumerate(v) if isinstance(x, str))
        elif isinstance(v, str):
            yield p, v


_ZH_TEXT = list(_walk(_T._load("zh")))
_TGT_TEXT = dict(_walk(_T._load(E2E_LANG)))


def L(zh: str) -> str:
    """中文 UI 标签 → E2E_LANG 下同一个 i18n 键的文本(zh 时原样返回)。"""
    if E2E_LANG == "zh":
        return zh
    hits = {_TGT_TEXT[k] for k, v in _ZH_TEXT if v == zh}
    assert len(hits) == 1, f"标签 {zh!r} 在 zh.json 里对应 {len(hits)} 种 {E2E_LANG} 译文:{hits}"
    return hits.pop()


# ---------- stub LLM ----------

# 两行排版(2026-09-01 §9)—— prompts 现在要的就是这个,E2E 必须跑它,否则
# 全链路只证明了「解析器还认得旧格式」。旧的一行排版仍由 robustness_check 的桩覆盖。
_SCRIPT = (
    "1.\n"
    "【画面描写】闹钟显示23:00，主角猛地抬头\n"
    "【时长】3 秒 【拍法】特写 【台词/音效】\"完了完了\"\n"
    "2.\n"
    "【画面描写】书页快速翻动，荧光笔乱涂\n"
    "【时长】8 秒 【拍法】中景 【台词/音效】纸张哗哗声\n"
    "3.\n"
    "【画面描写】天亮，主角趴在书堆里睡着\n"
    "【时长】4 秒 【拍法】远景 【台词/音效】鸟叫"
)
_SCRIPT_REV = _SCRIPT.replace("完了完了", "哈哈哈，有意思")

_R1_QUESTIONS = {
    "questions": [
        {"dimension": "psychology", "question": "主角此刻最强烈的感受是？",
         "options": ["恐慌", "自嘲", "冷静"], "why": "情绪锚点决定表演方向"},
        {"dimension": "turning_point", "question": "故事在哪里转折？",
         "options": ["开场", "中段", "结尾"], "why": "决定节奏"},
        {"dimension": "key_shot", "question": "最想让观众记住哪个画面？",
         "options": ["翻书", "睡着", "天亮"], "why": "视觉重点"},
        {"dimension": "tone", "question": "整体基调？",
         "options": ["喜剧", "悬疑", "写实"], "why": "统一气质"},
        {"dimension": "ending", "question": "结局走向？",
         "options": ["失败", "反转", "开放"], "why": "余味"},
        {"dimension": "sound", "question": "突出哪种声音？",
         "options": ["钟表声", "独白", "环境音"], "why": "氛围"},
    ]
}
_FU_QUESTIONS = {
    "questions": [
        {"dimension": "pacing", "question": "第二镜是否太长？",
         "options": ["缩短", "保持", "拆成两镜"], "why": "节奏"},
        {"dimension": "dialogue", "question": "要不要加一句结尾台词？",
         "options": ["要", "不要"], "why": "收束"},
    ]
}


# ja 版:prompts.py 的 ja 分支要的两行排版(【画面】/【秒数】【カメラ】【セリフ・音】)。
_JA = {
    "script": (
        "1.\n"
        "【画面】目覚ましが23:00を指し、主人公がはっと顔を上げる\n"
        "【秒数】3秒 【カメラ】クローズアップ 【セリフ・音】「やばい、やばい」\n"
        "2.\n"
        "【画面】教科書のページが高速でめくられ、蛍光ペンが走る\n"
        "【秒数】8秒 【カメラ】ミディアム 【セリフ・音】紙がめくれる音\n"
        "3.\n"
        "【画面】朝、主人公が本の山に突っ伏して眠っている\n"
        "【秒数】4秒 【カメラ】ロング 【セリフ・音】鳥の声"
    ),
    "r1": {"questions": [
        {"dimension": "psychology", "question": "主人公がいちばん強く感じているのは？",
         "options": ["焦り", "自虐", "冷静"], "why": "感情の軸が演技の方向を決める"},
        {"dimension": "turning_point", "question": "物語はどこで転換する？",
         "options": ["冒頭", "中盤", "ラスト"], "why": "テンポが決まる"},
        {"dimension": "key_shot", "question": "観客にいちばん覚えてほしい画面は？",
         "options": ["ページをめくる", "眠り込む", "夜明け"], "why": "視覚の要"},
        {"dimension": "tone", "question": "全体のトーンは？",
         "options": ["コメディ", "サスペンス", "リアル"], "why": "雰囲気をそろえる"},
        {"dimension": "ending", "question": "結末の方向は？",
         "options": ["失敗", "どんでん返し", "オープン"], "why": "余韻"},
        {"dimension": "sound", "question": "どの音を際立たせる？",
         "options": ["時計の音", "独白", "環境音"], "why": "雰囲気"},
    ]},
    "fu": {"questions": [
        {"dimension": "pacing", "question": "2カット目は長すぎない？",
         "options": ["短くする", "そのまま", "2カットに分ける"], "why": "テンポ"},
        {"dimension": "dialogue", "question": "ラストにセリフを1つ足す？",
         "options": ["足す", "足さない"], "why": "締め"},
    ]},
}
# 被试输入(创意 / 修改请求 / 手改 / 引导自填),按语言给,ja 界面里不混中文输入。
_INPUT = {
    "zh": {"intents": ("主角发现卷子上的题目昨晚全梦到过", "末班车开走后他跟着夜跑团回家",
                       "告白的话写在毕业帽内侧被风吹走", "躲雨时和陌生人分享了一把伞"),
           "request": "更搞笑一点", "edit": "\n（我手改的：结尾加一个彩蛋镜头）",
           "custom": "我自己写的：荒诞但温柔",
           "f_custom": "我自己写的：先不敢相信，然后笑出来"},
    "ja": {"intents": ("答案用紙の問題を、ゆうべ全部夢で見ていたと気づく",
                       "終電を逃した彼は、夜のランニング集団について帰る",
                       "卒業帽の内側に書いた告白が風で飛ばされる",
                       "雨やどり中、知らない人と一本の傘を分け合う"),
           "request": "もっと笑える感じにして", "edit": "\n（自分で手直し：最後におまけのカットを追加）",
           "custom": "自分で書いた：ばかばかしいけど優しい",
           "f_custom": "自分で書いた：あたたかくて、少し照れくさい気持ち"},
}[E2E_LANG if E2E_LANG == "ja" else "zh"]
if E2E_LANG == "ja":
    _SCRIPT = _JA["script"]
    _SCRIPT_REV = _SCRIPT.replace("やばい、やばい", "ははっ、面白い")
    _R1_QUESTIONS, _FU_QUESTIONS = _JA["r1"], _JA["fu"]

_PROMPTS: list[tuple[str, str, str]] = []   # (group, system, user) —— ja 模式查简体中文混入


def _stub_stream(system, user, *, group, user_id="", temperature=None):
    _PROMPTS.append((group, system, user))
    yield _SCRIPT_REV if "revise" in group else _SCRIPT


def _f_leaky(data: dict) -> dict:
    qs = json.loads(json.dumps(data))["questions"]
    for q in qs:
        if q["dimension"] in ("key_shot", "tone"):
            q["question"] += f"（{q['options'][0]}／{q['options'][1]}）"
    return {"questions": qs}


def _stub_json(system, user, *, group, user_id="", retries=None, temperature=None):
    _PROMPTS.append((group, system, user))
    if group == "F-guidance-r1":   # F(06 ㉜):同 E 的题,但 key_shot 与 tone 两题故意把 2 个选项写进题面
        return _f_leaky(_R1_QUESTIONS)    # → key_shot 换成固定开放题面(选项保留),tone 不问
    if group == "F-guidance-fu":
        return _FU_QUESTIONS
    return _R1_QUESTIONS if group == "E-guidance-r1" else _FU_QUESTIONS



# 配图默认关闭:state._ensure_api_defaults 会把 secrets 的 OpenAI 配置灌进
# session_state,不关的话这个"离线"脚本每跑一次都会真的发图片请求(见 imagegen 注释)。
os.environ["NOVASTORY_NO_IMAGES"] = "1"
llm.generate_stream = _stub_stream
llm.generate_json = _stub_json
llm._client = lambda: None

# ---------- temp DB ----------

db.DB_PATH = Path(tempfile.mkdtemp()) / "smoke.db"
print(f"temp db: {db.DB_PATH}")

_injected: dict[str, object] = {}

EDIT_MARK = _INPUT["edit"]

# 日语里不用的简体字(只收和日文新字体不同形的常用字;写、将、没、画、温之类两边同形的不收)。
# ja 模式下任何一屏或任何一条提示词出现它们,就是中文混进了日语会话。
_ZH_ONLY = set(
    "这们说让么为样进过还请选择务动导问题时间实验镜头轮创编辑继续确认满现页设计惯经历该给从发结个"
    "开关见长车门书买卖读讲话语记论谢订许调谈谁课诉词译试详误变爱华员团围图场坏报处备复够孙宁审宽"
    "尔层岁岛币师带帮库应废异张弹强归录忆态总惊戏战护挂损换显晓术杂权极构标树桥梦检欢毕汇测浓灭环"
    "电疗盖盘码确离种积稳穷笔签简类紧红约级纪纯纸线练组细织终绍绝统绩维综绿缓缘罗联肤脑脸节苏药获"
    "营蓝虑虽补观规视觉访证评识贝负财责货质购费资赛赞赢赶跃轻载较辆边达运远违连迟适递邮释钟钢钱铁"
    "银链销错闭闲闻阅队阳阴阶际陆险难项顺须顾预领频颜额风飞饭饮馆驾鸟齐龄龙"
)
_LEAKS: list[str] = []
_INVISIBLE = re.compile(r"<style.*?</style>|<script.*?</script>|<!--.*?-->", re.S | re.I)


def _scan_screen(at) -> None:
    """收集当前这一屏所有可见文字,记下含简体字的那几条(只在 ja 模式)。"""
    texts: list[str] = []
    # html:v2 的品牌栏、续接/引导等都画在 st.html 里(script / style 照样剥掉)
    for kind in ("markdown", "caption", "title", "header", "subheader", "text", "info",
                 "warning", "error", "success", "button", "radio", "selectbox", "checkbox",
                 "text_area", "text_input", "expander", "button_group", "metric", "tabs", "html"):
        try:
            els = at.get(kind)
        except Exception:  # noqa: BLE001 —— 该版本没有这种元素就跳过
            continue
        for el in els:
            for attr in ("value", "label", "body"):
                v = getattr(getattr(el, "proto", None), attr, None) or getattr(el, attr, None) \
                    if attr != "value" or kind in ("markdown", "caption", "title", "header",
                                                   "subheader", "text", "info", "warning",
                                                   "error", "success") else None
                if isinstance(v, str):
                    texts.append(v)
            opts = getattr(el, "options", None)
            if opts:
                texts.extend(str(o) for o in opts)
    for x in texts:
        x = _INVISIBLE.sub("", x)   # <style> 里的 CSS 注释等被试看不到,不算混入
        if any(ch in _ZH_ONLY for ch in x):
            _LEAKS.append(f"[{at.session_state['stage'] if 'stage' in at.session_state else '?'}] {x[:120]!r}")


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
    for c in list(at.checkbox):  # e.g. stale _consent_agree after a page transition
        if c.key and c.key not in at.session_state:
            at.session_state[c.key] = False
    # 无 key 的 widget(总问卷的 4 个跨轮单选、操纵察觉单选)换页后同样会残留,但它们
    # 没有 key、按位置 id 存状态,上面那圈补不到 —— 序列化时 `state[self.id]` 直接 KeyError。
    # set_value(None) 让它序列化成「未选择」:对已消失的旧节点无副作用,对当前页刚设过值的
    # widget 也不会动(它的 _value 已不是 InitialValue,读 value 不会去查 session_state)。
    for w in list(at.radio) + list(at.selectbox):
        if not w.key:
            try:
                _ = w.value
            except KeyError:
                w.set_value(None)
    for k, v in _injected.items():  # segmented_control & friends
        if k not in at.session_state:
            at.session_state[k] = v
    at.run()
    if E2E_LANG == "ja":
        _scan_screen(at)


def btn_click(at, label):
    label = L(label)
    hits = [b for b in at.button if b.label == label]
    assert hits, f"button not found: {label!r} (have: {[b.label for b in at.button]})"
    hits[0].click()
    safe_run(at)


def run() -> None:
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()
    # 在同意页的语言选择器上选 E2E_LANG(默认 zh = 研究员的工作语言),和被试一样操作。
    lang_radio = at.radio(key="_consent_lang")
    lang_radio.set_value(LANG_LABELS[E2E_LANG])   # 按标签选,与 AVAILABLE_LANGS 的顺序无关
    at.run()
    assert at.session_state["lang"] == E2E_LANG, at.session_state["lang"]
    if E2E_LANG == "ja":
        _scan_screen(at)

    # --- consent → screening → intro (how-it-works page) → rounds ---
    at.checkbox(key="_consent_agree").check().run()
    btn_click(at, "同意并开始")
    assert at.session_state["stage"] == "screening", at.session_state["stage"]
    at.selectbox[0].select(L("21-30 岁"))
    at.selectbox[1].select(L("不愿透露"))
    at.selectbox[2].select(L("偶尔（每月几次）"))
    at.selectbox[3].select(L("从未用过"))          # aiexp (AI creative experience)
    at.radio[0].set_value(L("从未发布过"))
    at.radio[1].set_value(L("否"))
    at.radio[2].set_value(L("否"))
    at.radio[3].set_value(L("我不知道"))
    at.radio[4].set_value(L("我不知道"))
    set_sc(at, "_scr_self", 1)
    set_sc(at, "_scr_conf", 2)                # 自我效能:能不能写出短视频脚本的把握
    set_sc(at, "_scr_trust", 4)               # baseline trait: AI trust
    set_sc(at, "_scr_own", 5)                 # baseline trait: ownership disposition
    btn_click(at, "提交并继续")
    # Screening lands on the briefing page; the round clock starts on its button.
    assert at.session_state["stage"] == "intro", at.session_state["stage"]
    assert at.session_state["participant_id"], "说明页必须已经有被试身份（刷新才能续接）"
    btn_click(at, "开始")                          # intro "start" button
    assert at.session_state["stage"] == "rounds" and at.session_state["seq"] == 0

    # seq 0:zh → C, D, E;ja(4 条件设计)→ C, D, F, E。按计划逐轮跑,每种条件一个步骤函数。
    plan = [r["condition"] for r in at.session_state["round_plan"]]
    assert plan == _EXPECTED_PLAN, f"seq 0 的轮次计划不对:{plan}"
    for ridx, cond in enumerate(plan, start=1):
        assert at.session_state["round_idx"] == ridx
        _ROUND_STEPS[cond](at, ridx)

    # --- whole-study final survey ---
    assert at.session_state["stage"] == "final_survey", at.session_state["stage"]
    _answer_final_survey(at)

    # --- done ---
    codes = [el.value for el in at.code]
    assert codes and len(codes[0]) == 8, f"completion code missing: {codes}"
    print("completion code:", codes[0])

    _assert_db()
    if E2E_LANG == "ja":
        _assert_no_zh_leak()
    print(f"E2E SMOKE PASSED（{E2E_LANG}）")


_EXPECTED_PLAN = ["C", "D", "F", "E"] if E2E_LANG in config.FOUR_COND_LANGS else ["C", "D", "E"]


def _round_c(at, ridx: int) -> None:
    """C — one-shot."""
    at.text_area(key="_intent_input").set_value(_INPUT["intents"][ridx - 1])
    btn_click(at, "确定，开始创作")
    btn_click(at, "提交这一版，进入问卷")      # C 只生成一次,提交文案是中性的
    _answer_questionnaire(at, round_idx=ridx, attention=ridx == _q._ATTENTION_ROUND)


def _round_d(at, ridx: int) -> None:
    """D — revise via chat + hand-edit."""
    at.text_area(key="_intent_input").set_value(_INPUT["intents"][ridx - 1])
    btn_click(at, "确定，开始创作")
    inject(at, "_revision_input", _INPUT["request"])
    btn_click(at, "告诉 AI")                # → v2 (ai)
    assert at.session_state["r_n_ai_rounds"] == 1
    inject(at, "_script_edit", _SCRIPT_REV + EDIT_MARK)
    btn_click(at, "满意了，提交这一版")       # persist → v3 (user_edit) → questionnaire
    _answer_questionnaire(at, round_idx=ridx, attention=ridx == _q._ATTENTION_ROUND)


def _round_e(at, ridx: int) -> None:
    """E — guidance round-1 → script → follow-up → hand-edit."""
    at.text_area(key="_intent_input").set_value(_INPUT["intents"][ridx - 1])
    btn_click(at, "确定，开始创作")
    _answer_guidance(at, rnd=1, n=6, custom_idx=1, ai_idx=2)
    assert at.session_state["r_phase"] == "postgen"
    btn_click(at, "让 AI 继续引导")          # → follow-up questions
    _answer_guidance(at, rnd=2, n=2)
    assert at.session_state["r_n_ai_rounds"] == 1
    inject(at, "_script_edit", _SCRIPT_REV + EDIT_MARK)
    btn_click(at, "满意了，提交这一版")
    _answer_questionnaire(at, round_idx=ridx, ai_q=True,
                          attention=ridx == _q._ATTENTION_ROUND)


def _round_f(at, ridx: int) -> None:
    """F(06 ㉜)— 与 E 同一出题,但选项默认藏起来:每题先问「要显示选项吗？［显示］［自己写］」,
    点「显示」等 F_REVEAL_DELAY_S 才出选项;没有「交给 AI」;生成后同样可「让 AI 继续引导」。"""
    at.text_area(key="_intent_input").set_value(_INPUT["intents"][ridx - 1])
    btn_click(at, "确定，开始创作")
    assert at.session_state["r_phase"] == "guidance", at.session_state["r_phase"]
    qs = at.session_state["r_g_questions"]
    # 桩让 key_shot / tone 的题面夹带 2 个选项:key_shot → 固定开放题面(选项保留),tone → 不问
    assert [q["dimension"] for q in qs] == ["psychology", "turning_point", "key_shot", "ending", "sound"], qs
    assert qs[2]["question"] == L("这个故事最关键的看点，你想用怎样的一格画面来呈现？") and qs[2]["fixed_q"], qs[2]
    labels = [b.label for b in at.button]
    assert L("显示") in labels and L("自己写") in labels, labels
    assert not at.segmented_control, "F 的选项一开始就显示出来了"
    assert L("交给 AI 决定") not in str([w.options for w in at.segmented_control]), "F 不该有「交给 AI」"
    btn_click(at, "下一问")                   # 两个都没选就想翻页 → 拦下
    assert at.session_state["r_g_idx"] == 0 and any(L("请先选「显示」或「自己写」，再作答。") in e.value
                                                     for e in at.error), [e.value for e in at.error]
    t0 = time.time()
    btn_click(at, "显示")                     # 等 F_REVEAL_DELAY_S 秒才出选项
    waited = time.time() - t0
    rv0 = at.session_state["r_g_reveal"][0]
    assert rv0["shown"] and rv0["first"] == "reveal" and rv0["n"] == 1, rv0
    assert waited >= config.F_REVEAL_DELAY_S - 0.05, f"点开后只等了 {waited:.2f}s"
    seg = at.segmented_control
    assert seg and list(seg[0].options) == qs[0]["options"], "点开后选项没出来,或混进了「交给 AI」"
    # 第 0 题点选、第 1 题点「自己写」,其余自己写(注入 + 跳到最后一题,同 _answer_guidance 的做法)
    at.session_state["r_g_answers"] = {0: {"opt": qs[0]["options"][0], "custom": "", "ai_decided": False}}
    at.session_state["r_g_idx"] = 1
    safe_run(at)
    btn_click(at, "自己写")
    assert at.session_state["r_g_reveal"][1]["first"] == "self" and not at.session_state["r_g_reveal"][1]["shown"]
    assert not at.segmented_control and at.text_input(key="_g_custom_1_1") is not None
    _answer_guidance_f(at, n=len(qs), skip=(0,))
    assert at.session_state["r_phase"] == "postgen", at.session_state["r_phase"]
    assert not [b for b in at.button if b.label == L("告诉 AI")], "F 不该出现 D 的自由描述修改入口"
    btn_click(at, "让 AI 继续引导")          # F 生成后同 E:继续引导(题同样默认藏选项)
    _answer_guidance_f(at, n=2)
    assert at.session_state["r_n_ai_rounds"] == 1
    inject(at, "_script_edit", _SCRIPT_REV + EDIT_MARK)
    btn_click(at, "满意了，提交这一版")
    _answer_questionnaire(at, round_idx=ridx, ai_q=True,
                          attention=ridx == _q._ATTENTION_ROUND)


def _answer_guidance_f(at, n: int, skip: tuple = ()) -> None:
    """F:没点开过的题都按「自己写」作答(注入答案 + 显示状态,跳到最后一题再交卷)。"""
    qs = at.session_state["r_g_questions"]
    assert len(qs) == n, f"expected {n} questions, got {len(qs)}"
    answers = dict(at.session_state["r_g_answers"])
    reveal = dict(at.session_state["r_g_reveal"])
    for i in range(n):
        if i in skip:
            continue
        answers[i] = {"opt": None, "custom": _INPUT["f_custom"], "ai_decided": False}
        reveal.setdefault(i, {"first": "self", "n": 0, "t0": None, "shown": False})
    at.session_state["r_g_answers"] = answers
    at.session_state["r_g_reveal"] = reveal
    at.session_state["r_g_idx"] = n - 1
    safe_run(at)
    btn_click(at, "完成作答，生成脚本")


_ROUND_STEPS = {"C": _round_c, "D": _round_d, "E": _round_e, "F": _round_f}


def _assert_no_zh_leak() -> None:
    """ja 会话:界面每一屏 + 每条发给 LLM 的提示词都不含简体字;分镜提示词要的是 ja 字段标记。
    另查整份 ja 文案:新手引导的气泡文字在 <script> 里,逐屏扫描看不到。"""
    for key, txt in _TGT_TEXT.items():
        bad = sorted({ch for ch in txt if ch in _ZH_ONLY})
        if bad:
            _LEAKS.append(f"[locale {key}] 含简体字 {''.join(bad)}")
    for group, system, user in _PROMPTS:
        for part, txt in (("system", system), ("user", user)):
            bad = sorted({ch for ch in txt if ch in _ZH_ONLY})
            if bad:
                _LEAKS.append(f"[prompt {group}/{part}] 含简体字 {''.join(bad)}")
    script_sys = [s for g, s, _ in _PROMPTS if "guidance" not in g]
    assert script_sys and all("【画面】" in s and "【秒数】" in s for s in script_sys), \
        "ja 会话的分镜提示词里没有 ja 字段标记(【画面】/【秒数】)"
    assert not _LEAKS, "ja 会话里混进了简体中文:\n  " + "\n  ".join(dict.fromkeys(_LEAKS))
    print(f"ja ok:{len(_PROMPTS)} 条提示词 + 每屏界面文字都没有简体中文;分镜提示词用 ja 字段标记")


def _answer_guidance(at, rnd: int, n: int, custom_idx: int = -1, ai_idx: int = -1) -> None:
    # Answer via direct session injection + jump to the last question, mirroring
    # the researcher devtools path (reliable: per-question widget navigation with
    # segmented_control set_value loses values across the advance-rerun).
    qs = at.session_state["r_g_questions"]
    assert len(qs) == n, f"expected {n} questions, got {len(qs)}"
    ai_decide = L("交给 AI 决定")  # == t("guidance.ai_decide") in E2E_LANG
    answers = {}
    for i in range(n):
        if i == custom_idx:
            answers[i] = {"opt": None, "custom": _INPUT["custom"], "ai_decided": False}
        elif i == ai_idx:
            answers[i] = {"opt": ai_decide, "custom": "", "ai_decided": True}
        elif qs[i]["options"]:
            answers[i] = {"opt": qs[i]["options"][0], "custom": "", "ai_decided": False}
    at.session_state["r_g_answers"] = answers
    at.session_state["r_g_idx"] = n - 1
    safe_run(at)  # render the last question so its finish button appears
    btn_click(at, "完成作答，生成脚本")


def _answer_final_survey(at) -> None:
    # 5 个跨轮单选:最喜欢 / 以后想用 / 最接近一开始想的 / 最费劲 / 最像自己的作品(2026-11-02 加)
    for i in (0, 1, 2, 3, 4):
        bg = at.radio[i]
        bg.set_value(bg.options[2])           # 都选第 3 轮,便于断言
    at.text_area(key="_fs_pref_reason").set_value("smoke-reason")   # 选填,填了要能落库
    set_sc(at, "_fs_sat", 6)
    at.radio[5].set_value(at.radio[5].options[2])   # 操纵察觉:第 3 档「注意到 AI 参与方式不同」
    btn_click(at, "提交并完成")


def _answer_questionnaire(at, round_idx: int, attention: bool = False,
                          ai_q: bool = False) -> None:
    for i in range(1, 4):
        set_sc(at, f"_q_own{i}_{round_idx}", 5)
    for i in range(1, 3):
        set_sc(at, f"_q_soa{i}_{round_idx}", 4)
    if attention:
        set_sc(at, f"_q_attention_{round_idx}", 2)
    set_sc(at, f"_q_tlx1_{round_idx}", 3)
    set_sc(at, f"_q_violation_{round_idx}", 2)
    set_sc(at, f"_q_imagine_{round_idx}", 6)
    set_sc(at, f"_q_sat_{round_idx}", 6)
    if ai_q:  # E-only items: quality + amount of the AI's guiding questions
        set_sc(at, f"_q_ai_q_quality_{round_idx}", 6)
        set_sc(at, f"_q_ai_q_amount_{round_idx}", 4)
        # ai_q_best checkboxes must render in E (this trial asked >=2 guidance
        # questions); optional, so we leave them unchecked but assert they appear.
        assert any((c.key or "").startswith(f"_q_aqbest_{round_idx}_") for c in at.checkbox), \
            "ai_q_best checkboxes missing in E questionnaire"
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
    fs = json.loads(p.iloc[0]["final_survey_json"])
    assert fs["pref_round"] == 3 and fs["reuse_round"] == 3 and fs["overall_sat"] == 6, fs
    # 2026-09-02 新增的三条(跨轮保真 / 跨轮努力 / 操纵察觉开放题)
    assert fs["closest_round"] == 3 and fs["effort_round"] == 3, fs
    assert fs["owned_round"] == 3 and fs["pref_reason"] == "smoke-reason", fs   # 2026-11-02 加(06 ㉙)
    assert fs["noticed_idx"] == 2, f"操纵察觉选项没落库：{fs.get('noticed_idx')}"
    scr = json.loads(p.iloc[0]["screening_json"])
    assert scr.get("script_confidence") == 2, scr.get("script_confidence")

    assert list(tr["condition"]) == _EXPECTED_PLAN
    assert tr["model"].notna().all() and tr["t_total"].notna().all()

    by = {r["condition"]: r for _, r in tr.iterrows()}
    c, d, e = by["C"], by["D"], by["E"]
    rnd = {k: int(v["round_idx"]) for k, v in by.items()}   # 条件 → 第几轮

    import pandas as pd

    cv = json.loads(c["script_versions"])
    assert [x["author"] for x in cv] == ["ai"] and c["n_hand_edits"] == 0
    assert pd.isna(c["guidance_json"]) and pd.isna(c["revision_requests"])

    dv = json.loads(d["script_versions"])
    assert [x["author"] for x in dv] == ["ai", "ai", "user_edit"], dv
    reqs = json.loads(d["revision_requests"])
    assert len(reqs) == 1 and reqs[0]["text"] == _INPUT["request"]
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

    if "F" in by:   # 4 条件设计(ja):F = 与 E 同一出题,选项默认藏起来(06 ㉜)
        f = by["F"]
        gf = json.loads(f["guidance_json"])["rounds"]
        assert [r["source"] for r in gf] == ["fixed3+ai_supplement", "ai_from_draft"], gf
        items = gf[0]["items"]
        assert [it["dimension"] for it in items] == ["psychology", "turning_point", "key_shot", "ending", "sound"]
        assert not any(it["ai_decided"] for r in gf for it in r["items"]), "F 不该有「交给 AI」"
        i0, i1 = items[0], items[1]
        assert i0["revealed"] and i0["first_choice"] == "reveal" and i0["n_reveal_clicks"] == 1, i0
        assert not i0["is_custom"] and i0["picked_option"] == i0["chosen"] == i0["options"][0], i0
        assert i1["first_choice"] == "self" and not i1["revealed"] and i1["is_custom"], i1
        assert i1["chosen"] == _INPUT["f_custom"], i1
        assert items[2]["fixed_q"] and items[2]["options"], items[2]
        assert [x["author"] for x in json.loads(f["script_versions"])] == ["ai", "ai", "user_edit"]
        assert f["n_ai_rounds"] == 1 and f["n_hand_edits"] == 1
        assert f["t_pregen"] is not None and f["t_postgen"] is not None
        fev = ev[(ev["participant_id"] == f["participant_id"]) & (ev["round_idx"] == f["round_idx"])]
        rc = fev[fev["type"] == "f_reveal_click"]
        assert len(rc) == 1 and json.loads(rc["payload_json"].iloc[0])["wait_s"] == config.F_REVEAL_DELAY_S, rc
        assert (fev["type"] == "f_self_click").sum() == 1, fev["type"].value_counts()

    assert (tr["parse_ok"] == 1).all(), "两行排版没能 100% 解析"
    assert len(q) == len(_EXPECTED_PLAN) and q["imagine_match"].notna().all()
    assert q["satisfaction"].notna().all()
    # AI 提问题:E 与 F 有(F 自 2026-10-27 起),C / D 是 NULL
    aqq = q.set_index("round_idx")["ai_q_quality"]
    aqa = q.set_index("round_idx")["ai_q_amount"]
    for cond, r_ in rnd.items():
        if cond in ("E", "F"):
            assert pd.notna(aqq[r_]) and aqa[r_] == 4, (cond, aqq.to_dict(), aqa.to_dict())
        else:
            assert pd.isna(aqq[r_]) and pd.isna(aqa[r_]), (cond, aqq.to_dict(), aqa.to_dict())

    # intake stage: logged before the participant row existed (round_idx 0,
    # keyed by session_id) and backfilled with the id at screening — that
    # backfill is what makes consent/intro/screening dwell times attributable.
    intake = ev[ev["round_idx"] == 0]
    # 顺序已是 同意 → 背景问卷 → 说明页(§4)。2026-09-02 起总问卷的两条也写在 round_idx=0
    # (它在所有轮次之外,以前带着第 3 轮的 round_idx/attempt 落进第 3 轮的事件窗口)。
    assert set(intake["type"]) == {
        "consent_shown", "consent_agree", "intro_shown", "intro_continue",
        "screening_shown", "screening_submit", "final_survey_shown", "final_survey_submit",
    }, f"intake 事件不全：{sorted(set(intake['type']))}"
    assert intake["participant_id"].notna().all(), "intake 事件没回填 participant_id"
    assert intake["attempt"].nunique() == 1, "intake 事件的 session_id 不唯一"
    assert list(intake.sort_values("id")["seq_in_round"]) == [1, 2, 3, 4, 5, 6, 7, 8], \
        "intake 事件序号不连续（去重逻辑漏了或重复落库）"
    assert pd.notna(p.iloc[0]["finished_at"]), "finished_at 没写入，整场时长算不出"

    # the events analyzer must read the trail without counting intake as a round
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis import events as A_ev  # noqa: PLC0415 — test-only import

    aev, aparts = A_ev.load_events(db.DB_PATH)
    assert (A_ev.per_trial_events(aev)["round_idx"] != 0).all(), "intake 段被当成了一轮 trial"
    pp = A_ev.per_participant(aev, aparts).set_index("participant_id")
    row = pp.loc[int(p.iloc[0]["id"])]
    for c in ("t_consent", "t_intro", "t_screening", "t_intake_total",
              "t_final_survey", "t_session_total"):
        assert pd.notna(row[c]) and row[c] >= 0, f"时长列 {c} 算不出：{row[c]}"
    print(f"intake ok：同意 {row['t_consent']:.1f}s · 说明 {row['t_intro']:.1f}s · "
          f"背景问卷 {row['t_screening']:.1f}s · 总问卷 {row['t_final_survey']:.1f}s")

    r_e = set(ev[ev["round_idx"] == rnd["E"]]["type"])
    for needed in ("round_start", "intent_submit", "guidance_shown", "guidance_answer",
                   "guidance_submit", "script_shown", "continue_guidance_click",
                   "hand_edit_saved", "trial_submit", "questionnaire_submit"):
        assert needed in r_e, f"missing event {needed} in E round: {r_e}"
    r_d = set(ev[ev["round_idx"] == rnd["D"]]["type"])
    assert "revision_request" in r_d and "hand_edit_saved" in r_d
    if "F" in rnd:
        r_f = set(ev[ev["round_idx"] == rnd["F"]]["type"])
        for needed in ("guidance_shown", "guidance_answer_saved", "guidance_answer",
                       "guidance_submit", "script_shown", "hand_edit_saved", "trial_submit"):
            assert needed in r_f, f"missing event {needed} in F round: {r_f}"
    print(f"db ok: trials={len(tr)} questionnaires={len(q)} events={len(ev)}")


def _url_lang_check() -> None:
    """`?lang=` preselects the session language so a test/recruitment link can
    pin it without clicking the consent-page picker. Must apply only before a
    participant exists, and must ignore junk values rather than break the app."""
    at = AppTest.from_file("app.py", default_timeout=60)
    at.query_params["lang"] = "en"
    at.run()
    assert at.session_state["lang"] == "en", at.session_state["lang"]
    # the consent picker reflects it (the subject can still change it there)
    assert at.radio(key="_consent_lang").value == "English", \
        at.radio(key="_consent_lang").value

    at = AppTest.from_file("app.py", default_timeout=60)
    at.query_params["lang"] = "klingon"
    at.run()
    # 回落到**当前的默认语言**,别硬编码 ja —— 默认语言是可以改的(2026-09-03 暂改 zh)
    assert at.session_state["lang"] == DEFAULT_LANG, \
        f"未知语言应被忽略，回落 {DEFAULT_LANG}(实际 {at.session_state['lang']})"

    # a resumed participant keeps THEIR language: ?lang= must not override it
    pid, _, token = db.insert_participant(
        "ja", {"age_idx": 0}, {"is_novice": True}, passed=True
    )
    at = AppTest.from_file("app.py", default_timeout=60)
    at.query_params["t"] = token
    at.query_params["lang"] = "en"
    at.run()
    assert at.session_state["participant_id"] == pid
    assert at.session_state["lang"] == "ja", \
        f"续接被试的语言被 URL 覆盖了：{at.session_state['lang']}"
    print("url lang ok：?lang=en 生效 · 未知值回落默认语言 · 续接被试不被覆盖")


def _resume_check() -> None:
    """AUD6: a refreshed/reconnected participant resumes via the URL token instead
    of re-screening (which would insert a 2nd passed row + consume a 2nd seq).
    Seeds an in-progress participant who finished round 1, then boots a fresh
    session with ?t=<token> and asserts it lands back at round 2 with no dup row."""
    pid, seq, token = db.insert_participant(
        "ja", {"age_idx": 0}, {"is_novice": True}, passed=True
    )
    db.insert_trial(participant_id=pid, round_idx=1, condition="C", t_total=12.0)
    db.insert_questionnaire(participant_id=pid, round_idx=1, satisfaction=6)
    before = len(db.load_table("participants"))

    at = AppTest.from_file("app.py", default_timeout=60)
    at.query_params["t"] = token          # fresh session + resume token in the URL
    at.run()
    ss = at.session_state
    assert ss["participant_id"] == pid, ss["participant_id"]
    assert ss["stage"] == "rounds" and ss["round_idx"] == 2, (ss["stage"], ss["round_idx"])
    assert ss["seq"] == seq and len(ss["round_plan"]) == config.rounds_for_lang("ja")
    assert len(db.load_table("participants")) == before, "resume inserted a duplicate row"
    print("resume ok: refresh restored round 2, no duplicate participant / seq")


def _redo_dangling_check() -> None:
    """回归 core/db.py:234 —— 轮内 trial 提交后重做(OR REPLACE 换新自增 id),
    旧 attempt 的 events 不得残留指向已删除 trial id 的悬挂引用。"""
    pid, _, _ = db.insert_participant("ja", {"age_idx": 0}, {"is_novice": True}, passed=True)
    db.insert_event(pid, 1, "round_start", attempt="A")          # attempt A
    tid_a = db.insert_trial(participant_id=pid, round_idx=1, condition="C", t_total=9.0)
    db.attach_trial_to_events(tid_a, pid, 1, "A")                # A 的 events 打 tid_a
    db.insert_event(pid, 1, "round_start", attempt="B")          # 中断后重做:attempt B
    tid_b = db.insert_trial(participant_id=pid, round_idx=1, condition="C", t_total=10.0)
    db.attach_trial_to_events(tid_b, pid, 1, "B")                # OR REPLACE 删 tid_a
    assert tid_b != tid_a, "OR REPLACE 应分配新 id"
    with db._conn() as c:
        dangling = c.execute(
            "SELECT COUNT(*) FROM events WHERE trial_id IS NOT NULL"
            " AND trial_id NOT IN (SELECT id FROM trials)").fetchone()[0]
        a_tid = c.execute(
            "SELECT trial_id FROM events WHERE participant_id=? AND round_idx=1 AND attempt='A'",
            (pid,)).fetchone()[0]
    assert dangling == 0, f"有 {dangling} 条 events 悬挂指向已删除 trial"
    assert a_tid is None, f"旧 attempt A 的 events.trial_id 应清回 NULL，实为 {a_tid}"
    print("redo ok: no dangling trial_id after round redo")


if __name__ == "__main__":
    run()
    _url_lang_check()
    _resume_check()
    _redo_dangling_check()
