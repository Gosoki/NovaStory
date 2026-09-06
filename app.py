from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta

import streamlit as st

from core import config, db, state
from i18n import DEFAULT_LANG, t
from views import (
    _scroll, consent, final_survey, intro, researcher, round_common, screening,
    sidebar,
)


def main() -> None:
    st.set_page_config(
        page_title="NovaStory",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    state.init_state()
    sidebar.render()

    st.title(t("app.title"))
    st.caption(t("app.subtitle"))

    if st.session_state.get("researcher_mode") and st.session_state.get("researcher_ok"):
        researcher.render()
        return

    # 上一次交互若请求了「回到顶部」,在这里统一兑现 —— 每个阶段都适用,
    # 而不是只有问卷页(rerun 不重置滚动位置,详见 views/_scroll.py)。
    _scroll.apply_pending()

    # 已入组的会话:把带续接 token 的 URL 记进浏览器,关掉标签页还能跳回来
    # (否则重新扫码 = 第二行被试 + 吃掉一个拉丁方 seq,见 core/state 的那段注释)。
    if st.session_state.get("participant_id"):
        state.remember_resume_url()

    stage = st.session_state["stage"]
    if stage == "consent":
        consent.render()
    elif stage == "intro":
        intro.render()
    elif stage == "screening":
        screening.render()
    elif stage == "rounds":
        _progress_bar()
        st.divider()
        round_common.render()
    elif stage == "final_survey":
        final_survey.render()
    elif stage == "done":
        _render_done()
    elif stage == "blocked":
        # 续接时题库不可用(state._attempt_resume):身份已恢复、不再入组,等研究员修好题库
        st.error(t("errors.not_ready"))
    else:
        # 没接上的 stage 值(拼错 / 新增阶段忘了接)以前会渲染成一张只有标题的空白页,
        # 被试端表现为「点了没反应」。宁可报错也不要静默。
        st.error(t("errors.not_ready"))
    _footer()


def _footer() -> None:
    """每一页都留一行联络邮箱。

    放页脚而不是管理员栏:那栏是研究员的工具,**被试永远不会打开它**,而这个邮箱
    正是给被试用的 —— 卡住了、想中途退出问一句、事后想要自己那份稿子,都得有地方问。
    同意书里也有同一个地址(伦理上必须有),但被试读完同意书就翻过去了,不会记住;
    真正需要它的时刻是在第 2 轮卡住的那一刻。研究员模式不显示(那是他自己的邮箱)。"""
    st.divider()
    st.caption(t("app.contact"))


# ─────────────────────── 设计语言(单一真源) ───────────────────────
# 这一块是全站唯一的设计令牌来源:颜色、圆角、细线、弱化文字、窄屏字阶,全从这里出。
# 之前它们散在 app.py / intro.py / round_common.py / _scale.py / _storyboard.py 里,
# 光颜色字面量就有 24 个互不相干的值 —— 同一种「弱化的小字」在三处是三个灰度。
#
# 颜色语义(改之前先想清楚它属于哪一档):
#   --ns-go     绿 = 决定 / 推进 / 定稿   → 所有 type="primary" 按钮
#   --ns-ai     蓝 = 继续和 AI 折腾       → btn_more_ai(一个明确的非终局动作)
#   --ns-accent 蓝 = 强调「读这句」       → 说明页的两句引导语
#   --ns-dim    弱化 = 注解 / 锚点 / 提示 → 所有 caption 级文字
# 绿/蓝两档必须泾渭分明:Streamlit 默认的 primary 是珊瑚红,新手会读成「危险操作」。
#
# 每个颜色分浅色 / 深色两档:config.toml 同时定义了 [theme.light] 与 [theme.dark],
# 主题跟被试的**操作系统**走,而 toolbarMode="minimal" 又把切换菜单藏了 —— 浅色是
# 「手机没开深色模式的人」的默认,不是边角情况。一套值不可能在两个底色上都过 WCAG AA
# (#3b82f6 在白底只有 3.7:1;#2563eb 在深色底 #0E1117 只有 3.1:1)。
_DESIGN_CSS = """
<style>
:root{
  --ns-accent:#2563eb; --ns-dim:#6e6e6e;
  --ns-go:#15803d;     --ns-go-hover:#166534;   /* 白字 5.02:1 / 7.13:1,均过 AA */
  --ns-ai:#2563eb;     --ns-ai-hover:#1d4ed8;
  --ns-line:rgba(128,128,128,.45);   /* 细线:例子框、卡片描边 */
  --ns-soft:rgba(128,128,128,.12);   /* 弱填充:例子框底色 */
  --ns-chrome:rgba(255,255,255,.82); /* 浮在正文上的控件底(展开侧栏箭头) */
  --ns-radius:8px;
  --ns-fs-note:.82rem;               /* 注解 / 锚点 */
}
@media (prefers-color-scheme: dark){
  :root{
    --ns-accent:#3b82f6; --ns-dim:#a3a3a3;
    --ns-line:rgba(160,160,160,.38); --ns-soft:rgba(160,160,160,.10);
    --ns-chrome:rgba(20,24,33,.82);
  }
}

/* 手机端两条硬伤(2026-09-06 实测):
   ① 页顶下拉 = 刷新,而刷新会把当前轮从头再来(state._attempt_resume) —— 关掉滚动链的橡皮筋;
   ② iOS Safari 对 <16px 的输入框会自动放大整页且失焦不缩回 —— 只对触屏设备提到 16px,
      桌面(研究员后台)维持原字号。 */
html, body, [data-testid="stMain"], [data-testid="stAppViewContainer"]{
  overscroll-behavior-y: contain;
}
/* state._bridge 的 1px 桥接 iframe(st.iframe 的 height 最小是 1):marker 容器连同
   紧跟其后的 iframe 容器一起收掉。用 height:0+overflow 而非 display:none —— 后者
   可能让浏览器跳过不可见 iframe 里的脚本,而那段脚本正是这个 iframe 的全部用途。 */
.stElementContainer:has(> .stMarkdown .ns-bridge),
.stElementContainer:has(> .stMarkdown .ns-bridge) + .stElementContainer{
  height:0!important; min-height:0!important; margin:0!important; padding:0!important;
  overflow:hidden!important;
}
@media (hover: none) and (pointer: coarse){
  [data-testid="stTextArea"] textarea,
  [data-testid="stTextInput"] input,
  [data-testid="stNumberInput"] input{ font-size:16px !important; }
}

button[data-testid="stBaseButton-primary"],
button[data-testid="stBaseButton-primaryFormSubmit"],
.stButton button[kind="primary"],
.stFormSubmitButton button[kind="primary"] {
    background-color: var(--ns-go) !important;
    border-color: var(--ns-go) !important;
    color: #ffffff !important;
}
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
.stButton button[kind="primary"]:hover,
.stFormSubmitButton button[kind="primary"]:hover {
    background-color: var(--ns-go-hover) !important;
    border-color: var(--ns-go-hover) !important;
}
div.st-key-btn_more_ai button {
    background-color: var(--ns-ai) !important;
    border: 1px solid var(--ns-ai) !important;
    color: #ffffff !important;
}
div.st-key-btn_more_ai button:hover {
    background-color: var(--ns-ai-hover) !important;
    border-color: var(--ns-ai-hover) !important;
}

/* Streamlit 1.60 的顶栏(position:absolute、高 60px)只在「没东西可显示」时才自己
   透明且不挡点击。侧栏一收起,展开箭头就住进去,顶栏随之变成一条横贯整幅的实色
   bar,正文从它下面滚过去 —— 就是「一条黑 bar 遮住我的内容」。强制它透明 + 穿透。

   stToolbar 自带 pointer-events:auto 且横贯整幅,只对 header 设 none 会被它抵消,
   顶部 60px 依旧点不动、而且是**看不见地**点不动(比那条 bar 更糟)。所以 toolbar
   一并 none,再单独放行两个真控件。 */
header[data-testid="stHeader"],
header[data-testid="stHeader"] [data-testid="stToolbar"] {
    background: transparent !important;
    pointer-events: none !important;
}
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"],
header[data-testid="stHeader"] [data-testid="stStatusWidget"],
header[data-testid="stHeader"] [data-testid="stMainMenu"] {
    pointer-events: auto !important;
}
/* 顶栏透明之后,展开侧栏的 « 箭头就直接浮在正文上 —— 手机上滚动时它压在题干中间,
   读起来像正文里混进了一个乱码字符(实测截图里三屏都出现)。给它一个圆形底,
   让它明确是一个悬浮控件而不是一个字。 */
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"]{
    background: var(--ns-chrome) !important;
    border-radius: 999px !important;
    -webkit-backdrop-filter: blur(3px);   /* iOS Safari 只认前缀版,而被试多半就在 iOS 上 */
    backdrop-filter: blur(3px);
}
/* 给它回一点按下去的反馈 —— 加了底色之后,Streamlit 自己的 hover 态被盖住了 */
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"]:hover,
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"]:active{
    background: var(--ns-accent) !important;
    color: #fff !important;
}

/* 7 点量表下方的锚点行。原来用 st.columns(3) 拼,窄屏下 Streamlit 会把列**竖着堆**,
   于是「まったくそう思わない / どちらともいえない / 非常にそう思う」变成三行阶梯,
   而且看不出哪一句属于哪一端 —— 对一个靠锚点定义刻度含义的 7 点量表来说,这不只是
   难看,是量表本身失去了刻度说明。改成一行 flex,窄屏也不会拆行。 */
/* 三栏 grid 而不是 flex + space-between:后者让中点随左右两句的长度左右漂移,
   对不准量表的第 4 格。1fr auto 1fr 下,中间那栏的中心恒在 50% = 第 4 格中心。 */
.ns-anchor{display:grid;grid-template-columns:1fr auto 1fr;align-items:start;
  gap:.45rem;margin:-2px 0 2px;color:var(--ns-dim);
  font-size:var(--ns-fs-note);line-height:1.25}
.ns-anchor>span{min-width:0}
.ns-anchor .a-l{text-align:left}
.ns-anchor .a-m{text-align:center}
.ns-anchor .a-r{text-align:right}

/* PC 全屏:layout="wide" 是给絵コンテ表格留的宽度(5 列的表在 centered 的 730px 里会挤成
   一团),但在 24 吋以上的屏幕上正文会横跨整幅 —— 一行两百多个字,眼睛读到行尾找不回行首。
   给正文容器一个最大宽度并居中:窄屏完全不受影响(max-width 只在窗口够宽时才起作用),
   宽屏则留出左右留白。1100px ≈ 絵コンテ表的舒适宽度上限。 */
.stMainBlockContainer,
[data-testid="stAppViewBlockContainer"]{max-width:1100px}
@media (min-width:1400px){
  .stMainBlockContainer,
  [data-testid="stAppViewBlockContainer"]{padding-left:4rem;padding-right:4rem}
}

/* 关掉 Streamlit 的「运行中变暗」。

   Streamlit 会给每个 .stElementContainer 打上 data-stale="true" 并施加
   opacity:.33 + transition:opacity 1s ease-in .5s。它本意是「脚本在跑,内容可能过时」,
   但对本实验有害:

   ① 被试**每点一次量表选项**都是一次全量 rerun(widget 消息不带 fragmentId),于是整页
      而不只是分镜区被标 stale —— 实测点一次选项有 12/13 个元素同时变暗。
   ② 配图生成期间问卷页要重建整张分镜表,rerun 轻易超过 .5s 的延迟阈值,淡入就跑起来了,
      被试看到的是「答一题、整页暗一下」。
   ③ 更要紧的是它污染测量:本轮问卷里就有 NASA-TLX 的负荷题与挫折感题,界面反复闪烁
      本身会推高这些评分,而闪烁频率又与条件相关(E 的分镜生成更晚)。

   代价是失去加载反馈。这里可以接受:分镜画框在图片没好时显示「生成中」文字占位,
   进度信息已经由那句话给足了,不需要再靠整页变暗来表达。
   钩子是 data-stale 属性(1.60 的已构建产物里唯一稳定的选择器);Streamlit 升级后若失效,
   表现只是「变暗回来了」,不会白屏。 */
.stElementContainer[data-stale="true"]{opacity:1!important;transition:none!important}
.stExpander summary,
[data-baseweb="tab-list"],[data-baseweb="tab"]{opacity:1!important}

@media (max-width:600px){
  /* 窄屏字阶:实测 h1 在 390px 上是 44px,一个 st.subheader 的分节标题能占掉三行屏幕。 */
  [data-testid="stHeading"] h1{font-size:1.55rem!important;line-height:1.3}
  [data-testid="stHeading"] h2{font-size:1.22rem!important;line-height:1.35}
  [data-testid="stHeading"] h3{font-size:1.08rem!important;line-height:1.4}
  .ns-anchor{font-size:.7rem;gap:.3rem}
}
</style>
"""


def _inject_css() -> None:
    st.markdown(_DESIGN_CSS, unsafe_allow_html=True)


def _progress_bar() -> None:
    i = st.session_state["round_idx"]
    st.progress(
        (i - 1) / config.N_ROUNDS,
        text=t("round.progress", i=i, n=config.N_ROUNDS),
    )


# Deliberately permissive: one @, a dot in the domain, no whitespace. A stricter
# pattern rejects real addresses (new TLDs, +tags, unicode locals) and the only
# cost of a typo here is one undeliverable mail — this must never be the reason a
# finished participant can't leave their address.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")


def _render_done() -> None:
    if not st.session_state.get("completion_code"):
        st.session_state["completion_code"] = db.make_completion_code(
            st.session_state["participant_id"]
        )
        # 只有「在本次会话里真的做完了」才置位。续接一位已完成的被试时完成码是从库里
        # 读回来的,不会走这里 —— 这是联系方式表单的归属凭据之一(另一条见 _finished_recently)。
        st.session_state["_completed_in_this_session"] = True
    st.success(t("done.title"))
    st.write(t("done.message"))
    st.code(st.session_state["completion_code"])
    # 先说这串码怎么用(紧贴着码本身,普通字号),再说「到这儿就结束了」(绿标)。
    # 顺序反过来的话,被试读到「可以关页面了」就走了,根本不会往下看完成码有什么用。
    st.caption(t("done.code_use"))
    st.success(t("done.code_hint"))
    st.warning(t("done.no_repeat"))
    _contact_form()
    # 带 ?admin=1 就够,不必先解锁:重置只清当前浏览器会话(reset_for_next 不碰数据库),
    # 而被试的链接里没有这个参数。以前要求 researcher_ok,可解锁入口本身也在 ?admin=1 后面,
    # 结果每跑完一遍测试都要绕回侧边栏输一次密码。
    if st.session_state.get("researcher_ok") or sidebar.admin_requested():
        if st.button(t("done.reset"), type="primary"):
            state.reset_for_next()
            _scroll.request()
            st.rerun()
        st.caption(t("done.reset_hint"))


# 完成后多久之内仍算「本人刚做完」。只凭 session 标志的话,完成页一刷新(手机切回浏览器时
# Safari 常常整页重载)就再也交不了邮箱 —— 而那正是被试在敲邮箱的时刻。两小时:够覆盖
# 「刷新 / 断线重连 / 先关掉想了想再回来」,又不至于让几天后转发出去的网址还能写。
_CONTACT_GRACE = timedelta(hours=2)


def _finished_recently(row: dict) -> bool:
    try:
        finished = datetime.fromisoformat(row.get("finished_at") or "")
    except ValueError:
        return False
    return datetime.now() - finished <= _CONTACT_GRACE


def _contact_form() -> None:
    """Opt-in contact capture on the completion screen (2026-09-01 §13).

    Kept strictly AFTER every measurement is in the database: it writes to the
    participant row via `contact_json` and touches nothing the analysis reads, so
    a subject who leaves an address is not a different data point from one who
    doesn't. Opt-in and optional — the wording says the film is AI-generated and
    may take up to a year, so nobody leaves an address on a wrong expectation.

    ⚠️ 这是全库唯一一列直接个人数据,与同意书「匿名分析」的口径冲突尚未处理
    (docs/paper/13 §0.7)。"""
    pid = st.session_state.get("participant_id")
    if not pid:
        return
    # 默认折叠:这是完成页最后一块、完全自愿的东西,展开着会让「做完了」的画面又长出
    # 一整屏表单。想要的人点开,不想要的人一眼看到完成码就可以关页面。
    with st.expander(t("done.contact_title"), expanded=False):
        st.markdown(t("done.contact_body"))
        # 「存过没有」以数据库为准,不以 session_state 为准:重连会丢掉 session,
        # 于是被试会看到一张空表单、以为没存上,再填一次就把上一次连同备注一起顶掉。
        row = db.get_participant(pid) or {}
        if row.get("contact_json"):
            st.success(t("done.contact_saved"))
            return
        if not (st.session_state.get("_completed_in_this_session") or _finished_recently(row)):
            # 拿着别人转发 / 共用机器上残留的 ?t= 网址进来的人,不给写。
            st.caption(t("done.contact_closed"))
            return
        want = st.checkbox(t("done.contact_want"), key="_contact_want")
        email = st.text_input(t("done.contact_email"), key="_contact_email", max_chars=254)
        note = st.text_area(
            t("done.contact_note"), key="_contact_note",
            placeholder=t("done.contact_note_ph"), height=80, max_chars=300,
        )
        if st.button(t("done.contact_submit"), width="stretch"):
            # NFKC:日本語IMEが全角のままだと「ｔａｒｏ＠ｅｘａｍｐｌｅ．ｃｏｍ」になり、
            # 半角に直さないと弾かれる(しかも半端に変換された全角ドメインは通ってしまい、
            # 1年後に不達で気づく)。
            addr = unicodedata.normalize("NFKC", email or "").strip()
            if not _EMAIL_RE.match(addr):
                st.error(t("errors.email_bad"))
                return
            payload = {
                "email": addr,
                "want_video": bool(want),
                "note": unicodedata.normalize("NFKC", note or "").strip(),
                "at": datetime.now().isoformat(timespec="seconds"),
                # 与 screening 的 consent_sha1 同一套存证:被试交出邮箱时**看到的是哪一版
                # 说明**,事后必须能答得上来(说明文一改,旧版本就在世上不存在了)。
                "disclosure_sha1": hashlib.sha1(
                    "\x1f".join(t(k) for k in
                                ("done.contact_body", "done.contact_want",
                                 "done.no_repeat")
                                ).encode("utf-8")).hexdigest()[:16],
                "lang": st.session_state.get("lang", DEFAULT_LANG),
            }
            try:
                stored = db.set_contact(pid, json.dumps(payload, ensure_ascii=False))
            except Exception:  # noqa: BLE001 — 库锁/磁盘满都不该在完成页上抛栈
                st.error(t("errors.contact_failed"))
                return
            if stored:
                # round_idx=0(intake 段的约定):这件事发生在所有轮次之外,记进第 3 轮的
                # 事件流会让「本轮最后一个事件」延伸到被试敲完邮箱为止。
                try:
                    db.insert_event(pid, 0, "contact_submit", {"want_video": bool(want)},
                                    attempt=st.session_state.get("session_id") or None)
                except Exception:  # noqa: BLE001 — 埋点失败不该拦住被试
                    pass
            st.rerun()


if __name__ == "__main__":
    main()
