"""本机预览被试界面 —— 不调任何 API(2026-10-31,v2设计 改版时加)。

真实流程每一步都调 LLM(要授权、要花钱),所以本机以前只能看研究员后台、走不了被试流程。
这里把 core.llm 的出口换成固定的日/中/英样稿(出题、采访、分镜都有),配图关掉,
数据库指到 gitignored 的 data/analysis/ui_preview/preview.db —— 分析用的 data/novastory.db
和 data/analysis/ 下的分析产物都碰不到(研究员面板的「出分析结果」也重定向到预览目录)。

启动:  make preview        → http://127.0.0.1:8510/?lang=ja   (研究员工具再加 &admin=1,口令走 secrets 或
                                                              NOVASTORY_RESEARCHER_PW 环境变量)
       NOVASTORY_PREVIEW_DELAY=3 make preview   # 把等待画面放慢,看进度条 / 流式输出
改 app.py / views/ / i18n/ / config.toml:刷新页面即见;改 core/ 或本文件:要重启服务。
⚠️ 只供本机看界面。样稿是写死的,与被试实际看到的 AI 输出无关;这里产生的行不是数据。
"""
from __future__ import annotations

import os
import re
import runpy
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREVIEW_DIR = ROOT / "data" / "analysis" / "ui_preview"
sys.path.insert(0, str(ROOT))

os.environ["NOVASTORY_NO_IMAGES"] = "1"   # 配图会真的发图片请求(见 core/imagegen 注释)

# 改了界面代码,刷新页面就要看到:views / i18n 的文件一变就重新载入(app.py 本来就每次重读)。
# Streamlit 自己的文件监视只在「改动那一刻有会话连着」时才清模块缓存,新开的页面会拿到旧模块。
# 每个会话的脚本跑在各自的线程里:清缓存与重新 import 必须在锁里一次做完,且只在文件真的变了时做 ——
# 否则一个会话正在 import 时被另一个会话清掉,导入机制会抛 KeyError(多开几个标签页就能撞上)。
import threading  # noqa: E402

_LOCK = sys.__dict__.setdefault("_ns_preview_lock", threading.Lock())
with _LOCK:
    _mtime = max(p.stat().st_mtime for d in ("views", "i18n") for p in (ROOT / d).rglob("*") if p.is_file())
    if sys.__dict__.get("_ns_preview_mtime") != _mtime:
        for _m in [m for m in sys.modules if m in ("views", "i18n") or m.startswith(("views.", "i18n."))]:
            del sys.modules[_m]
        sys._ns_preview_mtime = _mtime
    from core import db, llm  # noqa: E402
    from views import (  # noqa: E402,F401 —— 在锁里把 app.py 要用的模块先载好,run_path 里的 import 只读缓存
        _scroll, analysis_panel, consent, final_survey, intro, researcher, round_common, screening, sidebar,
    )

DELAY = float(os.environ.get("NOVASTORY_PREVIEW_DELAY", "1.2"))

# 与真实输出同一排版(prompts.build_system_script:镜号单独一行、镜与镜之间空一行)——
# 空行要留:2026-11-02 历史面板的 markdown 串版就是空行触发的,桩稿没有空行时预览里看不出来。
_SCRIPT = {
    "ja": ("1.\n【画面】駅のホームで、足もとに落ちた切符に気づく主人公\n"
           "【秒数】4秒 【カメラ】クローズアップ 【セリフ・音】発車メロディが遠くで鳴る\n\n"
           "2.\n【画面】切符を拾い、改札の向こうへ走り出す\n"
           "【秒数】6秒 【カメラ】引き 【セリフ・音】「すみません、落としましたよ！」\n\n"
           "3.\n【画面】息を切らして手渡すと、相手がほっと笑う\n"
           "【秒数】5秒 【カメラ】ミディアム 【セリフ・音】「ありがとう、助かりました」"),
    "zh": ("1.\n【画面描写】站台上，主角注意到脚边掉着一张车票\n"
           "【时长】4 秒 【拍法】特写 【台词/音效】远处响起发车铃声\n\n"
           "2.\n【画面描写】主角捡起车票，朝检票口外跑去\n"
           "【时长】6 秒 【拍法】远景 【台词/音效】“不好意思，您的票掉了！”\n\n"
           "3.\n【画面描写】气喘吁吁地递过去，对方松了口气笑了\n"
           "【时长】5 秒 【拍法】中景 【台词/音效】“谢谢，帮大忙了”"),
    "en": ("1.\n【Visual】On the platform, the hero notices a ticket on the ground\n"
           "【Duration】4 s 【Shot】close-up 【Audio】A departure chime in the distance\n\n"
           "2.\n【Visual】The hero grabs it and runs past the gate\n"
           "【Duration】6 s 【Shot】wide 【Audio】\"Excuse me, you dropped this!\"\n\n"
           "3.\n【Visual】Out of breath, the hero hands it over; the other smiles in relief\n"
           "【Duration】5 s 【Shot】medium 【Audio】\"Thank you, you saved me\""),
}
_REVISED = {"ja": ("「ありがとう、助かりました」", "「ありがとう……あなたも同じ電車？」"),
            "zh": ("“谢谢，帮大忙了”", "“谢谢……你也坐这趟车吗？”"),
            "en": ("\"Thank you, you saved me\"", "\"Thanks... are you on this train too?\"")}

_QUESTIONS = {
    "ja": [("psychology", "切符を拾った瞬間、主人公がいちばん強く感じているのは？",
            ["焦り", "好奇心", "責任感"], "気持ちの軸が演技の方向を決めます"),
           ("turning_point", "物語はどこで動きますか？", ["拾った瞬間", "改札を越えるとき", "手渡すとき"],
            "テンポの山場が決まります"),
           ("key_shot", "観る人にいちばん覚えてほしい画面は？", ["足もとの切符", "走る背中", "相手の笑顔"],
            "視覚の要になります"),
           ("tone", "全体のトーンは？", ["あたたかい", "コミカル", "少し切ない"], "雰囲気をそろえます")],
    "zh": [("psychology", "捡到车票的那一刻，主角最强烈的感受是？", ["着急", "好奇", "责任感"], "情绪锚点决定表演方向"),
           ("turning_point", "故事在哪里转折？", ["捡到的瞬间", "冲过检票口", "递过去时"], "决定节奏的高点"),
           ("key_shot", "最想让观众记住哪个画面？", ["脚边的车票", "奔跑的背影", "对方的笑脸"], "视觉重点"),
           ("tone", "整体基调？", ["温暖", "搞笑", "略带伤感"], "统一气质")],
    "en": [("psychology", "When the hero picks up the ticket, what do they feel most?",
            ["Urgency", "Curiosity", "Responsibility"], "The emotional anchor sets the performance"),
           ("turning_point", "Where does the story turn?", ["The pickup", "Crossing the gate", "The handover"],
            "Sets the pacing peak"),
           ("key_shot", "Which image should the audience remember?", ["The ticket", "The running back", "The smile"],
            "The visual anchor"),
           ("tone", "Overall tone?", ["Warm", "Comic", "Bittersweet"], "Keeps the mood consistent")],
}
_FOLLOWUP = {
    "ja": [("pacing", "2カット目の6秒は長すぎませんか？", ["短くする", "このまま", "2カットに分ける"], "テンポ"),
           ("dialogue", "ラストのセリフを変えますか？", ["変える", "このまま"], "締めくくり")],
    "zh": [("pacing", "第二镜 6 秒会不会太长？", ["缩短", "保持", "拆成两镜"], "节奏"),
           ("dialogue", "要不要换掉结尾台词？", ["换", "不换"], "收束")],
    "en": [("pacing", "Is shot 2 (6 s) too long?", ["Shorten", "Keep", "Split in two"], "Pacing"),
           ("dialogue", "Change the closing line?", ["Change", "Keep"], "The ending")],
}
# F 采访的桩题面:第 2 问(观众感受)/ 第 3 问(哪一瞬间)/ 追问 各不相同 —— 以前第 2、3 问回同一句,
# 预览里看起来就是「同一个问题问了两遍」(2026-11-03 用户截图)
_INTERVIEW = {"ja": ("そのとき、観る人にはどんな気持ちになってほしいですか？",
                     "その気持ちがいちばん伝わるのは、15秒のうちどの瞬間だと思いますか？",
                     "手渡す瞬間で、いちばん大事にしたいのはどんなところですか？"),
              "zh": ("那一刻，你希望观众有什么感受？", "这种感受在 15 秒里的哪一瞬间最能传达？",
                     "递过去的那一瞬间，你最想守住的是什么？"),
              "en": ("At that moment, how do you want the audience to feel?",
                     "Which moment in the 15 seconds carries that feeling best?",
                     "In the handover moment, what matters most to you?")}

_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")


def _lang(*texts: str) -> str:
    """桩跑在后台线程里,读不到 session_state —— 按提示词本身的语言回样稿。"""
    s = "".join(texts)
    if _KANA.search(s):
        return "ja"
    return "zh" if re.search(r"[一-鿿]", s) else "en"


def _stub_stream(system, user, *, group, user_id="", temperature=None):
    lang = _lang(system, user)
    text = _SCRIPT[lang]
    if "revise" in group:
        text = text.replace(*_REVISED[lang])
    step = max(1, len(text) // 24)
    for i in range(0, len(text), step):
        time.sleep(DELAY / 24)
        yield text[i:i + step]


def _qs(rows):
    return {"questions": [{"dimension": d, "question": q, "options": o, "why": w} for d, q, o, w in rows]}


def _stub_json(system, user, *, group, user_id="", retries=None, temperature=None):
    lang = _lang(system, user)
    time.sleep(DELAY)
    if group.startswith("F-interview-more"):   # 第 3 题之后追问一次,第 5 题停
        return ({"more": True, "question": _INTERVIEW[lang][2]} if group.endswith("more4")
                else {"more": False, "question": ""})
    if group.startswith("F-interview"):
        return {"question": _INTERVIEW[lang][1 if group.endswith("q3") else 0]}
    return _qs(_QUESTIONS[lang] if group == "E-guidance-r1" else _FOLLOWUP[lang])


def _no_client():
    raise RuntimeError("ui_preview: 预览模式不调真实 API")


llm.generate_stream = _stub_stream
llm.generate_json = _stub_json
llm._client = _no_client
llm.ensure_configured = lambda: None
llm.ping = lambda max_tokens=5: (True, 0.1, "preview stub")
db.DB_PATH = PREVIEW_DIR / "preview.db"
analysis_panel.ANALYSIS_DIR = PREVIEW_DIR
analysis_panel.FIGDIR = PREVIEW_DIR / "figures"

runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
