#!/usr/bin/env python
"""实测就绪性验收 —— 把「多用户 / 中途刷新 / 断网 / 脏数据」这些**实测现场才会遇到**
的场景在临时库上跑一遍,逐项 ✅/❌。

`dev_smoke_e2e` 测的是「一切正常时走得通」,本脚本测的是「出事时被试卡不卡死、
数据脏不脏」。两者互补,采数前都要绿。

覆盖:
  A. 多用户并发 —— 拉丁方 seq 竞态 · events 并发写 · 写入中读全表 · 同轮重复提交
  B. 断网 / 网关空返回 —— C/D/E 六条生成路径:有没有提示、有没有出路、留不留孤儿
  C. 中途刷新 / 多标签页 —— intake 各阶段 · 轮内各阶段 · 同 token 双开 · 伪造 token
  D. 脏数据 / 极端输入 —— 超长/注入/emoji · topics.json 损坏 · 题库不足时不消耗 seq
  E. 后台线程 —— 配图 API 全挂时必须 settle(否则问卷 2 秒轮询永不停)

用法: .venv/bin/python scripts/robustness_check.py
      失败项会打印 ❌ 并以 exit 1 结束。
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from core import db, imagegen, llm, shots, state  # noqa: E402

_FAILS: list[str] = []


def ck(cond: bool, label: str, detail: str = "") -> bool:
    mark = "✅" if cond else "❌"
    print(f"   {mark} {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        _FAILS.append(label)
    return cond


def head(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


# ---------------------------------------------------------------- LLM stubs

_SCRIPT = (
    "1. 【景别】特写 【画面描写】闹钟 【台词/音效】\"完了\" 【时长 5 秒】\n"
    "2. 【景别】中景 【画面描写】翻书 【台词/音效】哗哗 【时长 5 秒】\n"
    "3. 【景别】远景 【画面描写】天亮 【台词/音效】鸟叫 【时长 5 秒】"
)
_QS = {"questions": [
    {"dimension": "psychology", "question": "感受?", "options": ["A", "B"], "why": ""},
    {"dimension": "tone", "question": "基调?", "options": ["C", "D"], "why": ""},
]}
# "ok" | "raise"(断网) | "empty"(网关返回空串,无异常) | "badjson"(JSON 解析不了)
MODE = {"stream": "ok", "json": "ok"}


def _stub_stream(system, user, *, group, user_id="", temperature=None):
    m = MODE["stream"]
    if m == "raise":
        raise llm.LLMCallError("Connection error: [Errno 111] Connection refused")
    yield "" if m == "empty" else _SCRIPT


def _stub_json(system, user, *, group, user_id="", retries=None, temperature=None):
    m = MODE["json"]
    if m == "raise":
        raise llm.LLMCallError("Request timed out")
    if m == "badjson":
        raise llm.LLMJsonError("Expecting value: line 1 column 1")
    return _QS


# ---------------------------------------------------------------- AppTest helpers

def boot_app(**qp) -> AppTest:
    at = AppTest.from_file("app.py", default_timeout=60)
    for k, v in qp.items():
        at.query_params[k] = v
    at.run()
    return at


def pick_zh(at: AppTest) -> None:
    r = at.radio(key="_consent_lang")
    r.set_value(r.options[1])          # options: [日本語, 中文, English]
    at.run()


def safe_run(at: AppTest) -> None:
    """AppTest 变通:st.rerun 换页后,元素树里仍留着已被 Streamlit 丢弃 key 的旧 widget,
    下一次 run 序列化它们时会 KeyError(dev_smoke_e2e 里同款处理)。先补齐缺失的 key。"""
    for w in list(at.radio) + list(at.selectbox) + list(at.get("button_group")):
        if w.key and w.key not in at.session_state:
            at.session_state[w.key] = None
    for w in list(at.text_input) + list(at.text_area):
        if w.key and w.key not in at.session_state:
            at.session_state[w.key] = ""
    for c in list(at.checkbox):
        if c.key and c.key not in at.session_state:
            at.session_state[c.key] = False
    at.run()


def click(at: AppTest, label: str) -> None:
    hits = [b for b in at.button if b.label == label]
    assert hits, f"按钮不存在 {label!r},现有:{[b.label for b in at.button]}"
    hits[0].click()
    safe_run(at)


def has_button(at: AppTest, label: str) -> bool:
    return any(b.label == label for b in at.button)


def submit_version(at: AppTest) -> None:
    """点「提交这一版」。C 的文案与 D/E 不同(§8:C 只生成一次,不能说「满意了」),
    调用方多半不关心当前是哪个条件,所以在这里认两种。"""
    for label in ("满意了,提交这一版", "提交这一版,进入问卷"):
        if has_button(at, label):
            click(at, label)
            return
    raise AssertionError(f"提交按钮不存在,现有:{[b.label for b in at.button]}")


def errors_of(at: AppTest) -> list[str]:
    return [e.value for e in at.error]


def run_intake(at: AppTest) -> None:
    """同意 → 背景问卷 → 说明页(§4 之后的真实顺序);出来时已在第 1 轮。"""
    pick_zh(at)
    at.checkbox(key="_consent_agree").check().run()
    click(at, "同意并开始")
    at.selectbox[0].select("21-30 岁")
    at.selectbox[1].select("不愿透露")
    at.selectbox[2].select("偶尔(每月几次)")
    at.selectbox[3].select("从未用过")
    at.radio[0].set_value("从未发布过")
    at.radio[1].set_value("否")
    at.radio[2].set_value("否")
    at.radio[3].set_value("我不知道")
    at.radio[4].set_value("我不知道")
    for key, val in (("_scr_self", 1), ("_scr_trust", 4), ("_scr_own", 5)):
        [b for b in at.get("button_group") if b.key == key][0].set_value(val)
    safe_run(at)
    click(at, "提交并继续")     # → 说明页(身份/续接 token 此时已就位)
    click(at, "开始")           # → 第 1 轮,round_start 从这里计时


def seed_round(cond: str) -> AppTest:
    """跳过 intake,把指定条件的第 1 轮摆到「写创意」阶段(dev 被试,不占 seq 统计)。"""
    at = boot_app()
    pick_zh(at)
    pid, seq, _ = db.insert_participant(
        "zh", {"a": 0}, {"is_novice": True, "dev": True}, passed=True)
    ss = at.session_state
    ss["participant_id"], ss["seq"] = pid, seq
    ss["stage"], ss["round_idx"] = "rounds", 1
    topics = state.load_topics()
    ss["round_plan"] = [{"condition": cond, "topic": dict(topics[0])} for _ in range(3)]
    ss["r_events"], ss["r_attempt"] = [], "seg1"
    at.run()
    return at


def write_intent(at: AppTest) -> None:
    at.text_area(key="_intent_input").set_value("测试用的故事创意,长度肯定够")
    click(at, "确定,开始创作")


def answer_guidance(at: AppTest) -> None:
    """每问点第一个选项 → 下一问,停在最后一问(真实交互路径)。"""
    for _ in range(20):
        bgs = [b for b in at.get("button_group") if (b.key or "").startswith("_g_opt_")]
        if bgs:
            bgs[0].set_value(bgs[0].options[0])
            safe_run(at)
        if has_button(at, "完成作答,生成脚本"):
            return
        click(at, "下一问")
    raise AssertionError("引导问题翻不完")


# ---------------------------------------------------------------- A. 并发

def section_a() -> None:
    head("A. 多用户并发(实测现场:多人同时坐下开始)")
    db.DB_PATH = Path(tempfile.mkdtemp()) / "conc.db"
    db.init_db()

    n = 24
    got, errs = [], []
    barrier = threading.Barrier(n)

    def w() -> None:
        try:
            barrier.wait()      # 让 N 个 INSERT 尽量撞在同一瞬间
            got.append(db.insert_participant(
                "ja", {"a": 0}, {"is_novice": True}, passed=True))
        except Exception as e:  # noqa: BLE001
            errs.append(repr(e))

    ths = [threading.Thread(target=w) for _ in range(n)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    seqs = sorted(s for _, s, _ in got)
    expect = sorted(list(range(18)) + list(range(n - 18)))
    ck(not errs, "24 人同时通过筛查无异常", f"{time.time() - t0:.2f}s")
    ck(seqs == expect, "拉丁方 seq 无重复无跳号", f"得到 {seqs[:6]}…")
    ck(len({t for _, _, t in got}) == len(got), "续接 token 唯一")

    ev_err: list[str] = []

    def ew(pid: int) -> None:
        try:
            for k in range(30):
                db.insert_event(pid, 1, "llm_start", {"k": k}, seq_in_round=k,
                                attempt=f"a{pid}")
        except Exception as e:  # noqa: BLE001
            ev_err.append(repr(e))

    pids = [p for p, _, _ in got][:12]
    ths = [threading.Thread(target=ew, args=(p,)) for p in pids]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    ck(len(db.load_table("events")) == 360 and not ev_err,
       "12 会话并发写 events 无丢失", f"{len(db.load_table('events'))}/360")

    stop, read_err, reads = threading.Event(), [], [0]

    def reader() -> None:
        while not stop.is_set():
            try:
                for tb in db.TABLES:
                    db.load_table(tb)
                reads[0] += 1
            except Exception as e:  # noqa: BLE001
                read_err.append(repr(e))

    def writer(pid: int) -> None:
        try:
            for k in range(40):
                db.insert_event(pid, 2, "hand_edit_saved", {"k": k})
                db.insert_trial(participant_id=pid, round_idx=2, condition="D",
                                t_total=float(k))
        except Exception as e:  # noqa: BLE001
            ev_err.append(repr(e))

    r = threading.Thread(target=reader)
    r.start()
    ws = [threading.Thread(target=writer, args=(p,)) for p in pids[:6]]
    for t in ws:
        t.start()
    for t in ws:
        t.join()
    stop.set()
    r.join()
    ck(not read_err and not ev_err,
       "研究员后台读全表与被试写入并发不互斥(WAL)", f"读 {reads[0]} 轮")

    for _ in range(10):     # 双击 / 多标签页同时提交同一轮
        threading.Thread(target=lambda: db.insert_trial(
            participant_id=pids[0], round_idx=3, condition="E", t_total=9.9)).start()
    time.sleep(0.6)
    with db._conn() as c:
        dup = c.execute("SELECT COUNT(*) FROM trials WHERE participant_id=? AND round_idx=3",
                        (pids[0],)).fetchone()[0]
    ck(dup == 1, "同轮重复提交 ×10 只落 1 行(唯一索引)", f"{dup} 行")


# ---------------------------------------------------------------- B. 断网/空返回

def section_b() -> None:
    head("B. 断网 / 网关空返回(六条生成路径:有提示吗?有出路吗?留孤儿吗?)")
    db.DB_PATH = Path(tempfile.mkdtemp()) / "fail.db"
    db.init_db()

    # B1 C 首次生成断网
    MODE.update(stream="raise", json="ok")
    at = seed_round("C")
    write_intent(at)
    ck(bool(errors_of(at)) and has_button(at, "重试"),
       "C 首次生成断网:有错误提示 + 重试按钮")

    # B2 C 首次生成返回空串(无异常)——拥挤网关的常见形态
    MODE.update(stream="empty")
    at = seed_round("C")
    write_intent(at)
    ck(bool(errors_of(at)) and has_button(at, "重试"),
       "C 首次生成空返回:有错误提示 + 重试按钮",
       "空返回若静默,被试会盯着「生成完成」的空白页")

    # B3 D 修改请求空返回:不留孤儿、草稿还在
    MODE.update(stream="ok")
    at = seed_round("D")
    write_intent(at)
    MODE.update(stream="empty")
    at.session_state["_revision_input"] = "更搞笑一点"
    click(at, "告诉 AI")
    ck(bool(errors_of(at)), "D 修改空返回:有错误提示")
    ck(len(at.session_state["r_revision_requests"]) == 0,
       "D 修改失败不留孤儿请求", "失败的请求若落库,历史时间线会错位")
    ck(bool(at.session_state["r_versions"]), "D 修改失败后草稿仍在")

    # B4 E 引导问题生成断网:有重试
    MODE.update(stream="ok", json="raise")
    at = seed_round("E")
    write_intent(at)
    ck(bool(errors_of(at)) and has_button(at, "重试"),
       "E 引导问题断网:有错误提示 + 重试按钮")

    # B5 E 引导 JSON 连续解析失败 → 降级为 1 道开放题
    MODE.update(json="badjson")
    at = seed_round("E")
    write_intent(at)
    ck(at.session_state["r_g_fallback"] and len(at.session_state["r_g_questions"]) == 1,
       "E 引导 JSON 坏:降级为开放题而非卡死")

    # B6 E 首轮终稿空返回:提示 + 不留孤儿引导轮
    MODE.update(json="ok")
    at = seed_round("E")
    write_intent(at)
    answer_guidance(at)
    MODE.update(stream="empty")
    click(at, "完成作答,生成脚本")
    ck(bool(errors_of(at)), "E 首轮终稿空返回:有错误提示",
       "此前点了按钮毫无反应")
    ck(len(at.session_state["r_guidance_rounds"]) == 0,
       "E 终稿失败不留孤儿引导轮")

    # B7 E 追问失败时能退回草稿
    MODE.update(stream="ok", json="ok")
    at = seed_round("E")
    write_intent(at)
    answer_guidance(at)
    click(at, "完成作答,生成脚本")
    MODE.update(json="raise")
    click(at, "让 AI 继续引导")
    ck(has_button(at, "取消,返回草稿"),
       "E 追问断网:有「取消,返回草稿」出路", "已有草稿时绝不能被困在引导页")

    # B8 失败可事后统计
    MODE.update(json="ok")
    ev = db.load_table("events")
    kinds = ev[ev["type"] == "llm_error"]["payload_json"].fillna("").str.contains("empty")
    ck(bool(kinds.any()), "空返回记为 llm_error{kind:'empty'},事后可统计网关空返回率")


# ---------------------------------------------------------------- C. 刷新/多标签

def section_c() -> None:
    head("C. 中途刷新 / 多标签页 / 伪造 token")
    db.DB_PATH = Path(tempfile.mkdtemp()) / "refresh.db"
    db.init_db()
    MODE.update(stream="ok", json="ok")

    n0 = len(db.load_table("participants"))
    at = boot_app()
    pick_zh(at)
    at.checkbox(key="_consent_agree").check().run()
    click(at, "同意并开始")            # 停在背景问卷页(§4 之后说明页在其之后)
    fresh = boot_app()                 # 刷新 = 全新 session,URL 无 token
    ck(fresh.session_state["stage"] == "consent"
       and len(db.load_table("participants")) == n0,
       "intake 阶段刷新:回到同意页且不产生被试行")

    orphan = db.load_table("events")
    orphan = orphan[(orphan["round_idx"] == 0) & (orphan["participant_id"].isna())]
    ck(len(orphan) > 0, "未走完 intake 的会话留下可统计的孤儿事件",
       f"{len(orphan)} 条 = intake 流失率的唯一来源,分析侧已过滤")

    at = boot_app()
    run_intake(at)
    pid = at.session_state["participant_id"]
    token = db.load_table("participants").set_index("id").loc[pid, "token"]

    r = boot_app(t=token)
    ck(r.session_state["stage"] == "intro" and r.session_state["round_idx"] == 1
       and r.session_state["participant_id"] == at.session_state["participant_id"],
       "写创意阶段刷新:续接回同一被试的第 1 轮(落在说明页,重看一遍简介再开始)")

    write_intent(at)                                   # 生成出草稿
    r = boot_app(t=token)
    ck(r.session_state["round_idx"] == 1 and len(r.session_state["r_versions"]) == 0,
       "有草稿时刷新:本轮从头重做(草稿不保留,设计如此)")

    submit_version(at)                                 # trial 已落库,问卷未答
    r = boot_app(t=token)
    ck(r.session_state["round_idx"] == 1,
       "trial 已交/问卷未交时刷新:重做该轮(trial 被 OR REPLACE 覆盖)")
    ck(len(db.load_table("participants")) == n0 + 1,
       "反复刷新不产生第二个被试行 / 不消耗第二个 seq")

    a, b = boot_app(t=token), boot_app(t=token)
    ck(a.session_state["r_attempt"] != b.session_state["r_attempt"],
       "同 token 双开:两个 session 段 id 可区分",
       "⚠️ 并发推进仍会互相覆盖,见报告「已知残留风险」")

    bad = boot_app(t="deadbeefNOPE")
    ck(bad.session_state["stage"] == "consent"
       and bad.session_state["participant_id"] is None,
       "伪造 token:回到同意页,不崩也不泄露")

    u1, u2 = boot_app(), boot_app()
    run_intake(u1)
    run_intake(u2)
    write_intent(u1)
    ck(u1.session_state["participant_id"] != u2.session_state["participant_id"]
       and u1.session_state["seq"] != u2.session_state["seq"],
       "两名被试并行:id 与 seq 均不同")
    ck(u2.session_state["r_intent"] == "",
       "会话隔离:u1 写的创意没串到 u2")


# ---------------------------------------------------------------- D. 脏数据

def section_d() -> None:
    head("D. 极端输入 / 损坏题库 / 题库不足")
    db.DB_PATH = Path(tempfile.mkdtemp()) / "edge.db"
    db.init_db()
    pid, _, _ = db.insert_participant("ja", {"a": 0}, {"is_novice": True}, passed=True)

    cases = {
        "超长 10 万字": "あ" * 100000,
        "HTML 注入": "<script>alert(1)</script><img src=x onerror=alert(1)>",
        "SQL 注入样": "'; DROP TABLE trials; --",
        "emoji + 零宽": "🎬​‮主角\U0001F9E0",
        "纯换行": "\n\n\n\n",
        "空字符串": "",
    }
    all_ok = True
    for i, (name, val) in enumerate(cases.items(), start=1):
        try:
            db.insert_trial(participant_id=pid, round_idx=i, condition="C",
                            intent_statement=val[:200], final_output=val,
                            parse_ok=int(bool(shots.parse_shots(val))))
            back = db.load_table("trials").set_index("round_idx").loc[i, "final_output"]
            ok = back == val or (val == "" and back in ("", None))
        except Exception:  # noqa: BLE001
            ok = False
        all_ok &= ok
        if not ok:
            print(f"      ↳ {name} 落库不原样")
    ck(all_ok, "极端输入原样落库、parse_shots 不抛")
    with db._conn() as c:
        tbs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    ck("trials" in tbs, "SQL 注入样输入后表仍在(参数化查询)")

    # 分镜解析守卫(2026-09-01 §9 排版改动引入,当晚审计抓到)。共同点:兜底切分的
    # 锚点从「时长」换成了「画面描写」——后者是有内容的字段,模型会重复、被试在
    # 可编辑文本框里也会重复,于是「切得多的赢」这条启发式会拿错误的切法压过正确的。
    two_line = ("1.\n【画面描写】闹钟黑着\n【时长】3 秒 【拍法】特写 【台词/音效】滴答\n"
                "2.\n【画面描写】猛地睁眼\n【画面描写】他抓起书包\n"
                "【时长】5 秒 【拍法】中景 【台词/音效】完了\n"
                "3.\n【画面描写】冲出家门\n【时长】7 秒 【拍法】远景 【台词/音效】脚步")
    ck(len(shots.parse_shots(two_line)) == 3,
       "一个镜头里出现两个【画面描写】:不产生幻影镜头",
       "否则被试要给 3 镜的稿子标 4 次归属,结构完整度也被拉低")

    glued = ("【画面描写】闹钟黑着\n【时长】3 秒 【拍法】特写 【台词/音效】滴答\n"
             "猛地睁眼\n【时长】5 秒 【拍法】中景 【台词/音效】完了\n"
             "【画面描写】冲出家门\n【时长】7 秒 【拍法】远景 【台词/音效】脚步")
    ck(shots.parse_shots(glued) == [],
       "手改丢了编号又丢了中间的字段标记:诚实失败,不把两镜粘成一镜",
       "silently-wrong 的逐镜头数据比 parse_ok=0 更坏")

    tail_num = ("【画面描写】倒计时开始\n【时长】3 秒 【拍法】特写 【台词/音效】倒计时 10\n"
                "【画面描写】人群\n【时长】5 秒 【拍法】中景 【台词/音效】BGM 90")
    parsed_tail = shots.parse_shots(tail_num)
    ck(len(parsed_tail) == 2 and parsed_tail[0]["audio"].endswith("10")
       and parsed_tail[1]["audio"].endswith("90"),
       "音效字段以数字结尾时数字不被吞掉",
       "被吞掉的字会一路进入 strip_format → embedding 保真")

    legacy = ("【时长】3 秒 【拍法】特写 【画面描写】A 【台词/音效】a\n"
              "【时长】5 秒 【拍法】中景 【画面描写】B 【台词/音效】b\n"
              "【时长】7 秒 【拍法】远景 【画面描写】C 【台词/音效】c")
    ck(len(shots.parse_shots(legacy)) == 3, "旧的一行排版(时长在前)仍然解析得出 3 镜")

    tmp = Path(tempfile.mkdtemp())
    orig_dir, orig_file = state.DATA_DIR, state.TOPICS_FILE
    state.DATA_DIR, state.TOPICS_FILE = tmp, tmp / "topics.json"
    try:
        broken_ok = True
        for content in ("{{{not json", "[]", '{"a":1}',
                        '[{"title":"a","scenario":"b","shot_count":"x","total_seconds":null}]'):
            state.TOPICS_FILE.write_text(content, encoding="utf-8")
            try:
                tp = state.load_topics()
                broken_ok &= bool(tp) and isinstance(tp[0].get("shot_count"), int)
            except Exception:  # noqa: BLE001
                broken_ok = False
        ck(broken_ok, "topics.json 损坏/脏字段:回退种子主题不崩",
           "⚠️ 静默回退 → 由 deploy_check 的题库闸门在部署前拦")
    finally:
        state.DATA_DIR, state.TOPICS_FILE = orig_dir, orig_file

    # 题库不足时,screening 必须在 INSERT 之前拦下(否则消耗一个 seq 留孤儿)
    state.DATA_DIR, state.TOPICS_FILE = tmp, tmp / "topics.json"
    state.TOPICS_FILE.write_text(
        '[{"title":{"ja":"a","zh":"a"},"scenario":{"ja":"b","zh":"b"},'
        '"shot_count":3,"total_seconds":15}]', encoding="utf-8")
    try:
        db.DB_PATH = tmp / "short.db"
        db.init_db()
        before = len(db.load_table("participants"))
        at = boot_app()
        try:
            run_intake(at)
        except AssertionError:
            pass    # 提交按钮点了但没进 rounds,后面的 selectbox 取不到,属预期
        after = len(db.load_table("participants"))
        ck(after == before and at.session_state["stage"] == "screening",
           "题库不足:筛查提交被拦在 INSERT 之前,不消耗 seq",
           f"被试行 {before}→{after}")
    finally:
        state.DATA_DIR, state.TOPICS_FILE = orig_dir, orig_file


# ---------------------------------------------------------------- E. 后台线程

def section_e() -> None:
    head("E. 后台配图线程(API 全挂时必须 settle)")
    imagegen._ARCHIVE = Path(tempfile.mkdtemp()) / "imgs"

    class _SS(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    orig_st = imagegen.st
    imagegen.st = type("M", (), {"session_state": _SS()})()
    try:
        sh = [{"idx": i, "visual": v} for i, v in enumerate(["闹钟", "翻书", "天亮"], 1)]
        imagegen.ensure_started(999, 1, sh, "sk-invalid", "https://127.0.0.1:9/v1")
        t0 = time.time()
        while time.time() - t0 < 90 and not imagegen.all_done(999, 1, 3):
            time.sleep(0.5)
        ck(imagegen.all_done(999, 1, 3),
           "配图 API 全挂:all_done 会 settle", f"{time.time() - t0:.1f}s")
        ck(all(h == "" for h in imagegen.frame_htmls(999, 1, 3, "生成中…")),
           "失败的镜降级为空画框,不永远挂着「生成中」",
           "否则问卷页 2 秒轮询永不停")
    finally:
        imagegen.st = orig_st


# ---------------------------------------------------------------- main

def main() -> None:
    llm.generate_stream = _stub_stream
    llm.generate_json = _stub_json
    llm._client = lambda: None

    section_a()
    section_b()
    section_c()
    section_d()
    section_e()

    print(f"\n{'=' * 68}")
    if _FAILS:
        print(f"❌ 实测就绪性验收未通过 —— {len(_FAILS)} 项:")
        for f in _FAILS:
            print(f"   · {f}")
        print("=" * 68)
        sys.exit(1)
    print("ROBUSTNESS CHECK PASSED —— 并发 / 断网 / 刷新 / 脏数据 / 后台线程 全绿")
    print("=" * 68)


if __name__ == "__main__":
    main()
