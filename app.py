from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import datetime, timedelta

import streamlit as st

from core import db, state
from i18n import DEFAULT_LANG, get_lang, t
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

    researcher_view = bool(st.session_state.get("researcher_mode") and st.session_state.get("researcher_ok"))
    # 同意页是入口,给一块完整的标题区;其余每页只留一条细的品牌栏(v1 每页都是 44px 的 h1 + 副标题,
    # 手机首屏 844px 里任务本身要往下滑 1–2 屏才看得到)。
    _masthead(hero=st.session_state["stage"] == "consent" and not researcher_view,
              progress=None if researcher_view else _flow_progress(st.session_state["stage"]))

    if researcher_view:
        st.markdown(_WIDE_CSS, unsafe_allow_html=True)
        researcher.render()
        return

    # 上一次交互若请求了「回到顶部」,在这里统一兑现 —— 每个阶段都适用,
    # 而不是只有问卷页(rerun 不重置滚动位置,详见 views/_scroll.py)。
    _scroll.apply_pending()

    # 已入组的会话:把带续接 token 的 URL 记进浏览器,关掉标签页还能跳回来
    # (否则重新扫码 = 第二行被试 + 吃掉一个拉丁方 seq,见 core/state 的那段注释)。
    # 做完之后反过来清掉(2026-10-29 审计):否则同一台设备的下一个人在同意页会看到「前回の続きから」,
    # 点进去就是上一位的完成页,两小时内联系表单还开着,邮箱会写进上一位的行。
    if st.session_state.get("participant_id"):
        if st.session_state.get("stage") == "done":
            state.forget_resume_url()
        else:
            state.remember_resume_url()
    elif st.session_state.get("stage") != "consent":
        # 按了同意 = 选了「作为新的一位开始」:同意页的「继续上次」条子不能再跟到背景问卷页上
        # (它写着「この端末に、途中までの回答が残っています」,填问卷的人会当成自己的;一点就进了上一位的会话)
        state.drop_resume_bar()

    stage = st.session_state["stage"]
    if stage == "consent":
        consent.render()
    elif stage == "intro":
        intro.render()
    elif stage == "screening":
        screening.render()
    elif stage == "rounds":
        round_common.render()   # 轮次进度与步骤条在轮次头里(views/round_common._round_header)
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
    真正需要它的时刻是在第 2 轮卡住的那一刻。研究员模式不显示(那是他自己的邮箱)。
    仍走 st.caption(markdown 会把邮箱变成可点的 mailto 链接),样式由 .st-key-ns_footer 管。"""
    with st.container(key="ns_footer"):
        st.caption(t("app.contact"))


_CHECK_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>')

# 品牌标记:圆角方块里一颗四角星(nova)。纯 CSS 画(.ns-mark + ::after 的 mask),
# 因为 st.html 的清洗器会删掉 <svg>;颜色走令牌,深色主题自动翻转。
_MARK = '<span class="ns-mark" aria-hidden="true"></span>'
# <html lang> 的值。Streamlit 恒为 "en":日文汉字的字形回退因此不确定(Windows 上可能落到中文字体),
# 浏览器的日文禁则、按词组断行(word-break:auto-phrase)也都不生效。
_HTML_LANG = {"ja": "ja", "zh": "zh-CN", "en": "en"}



# 亮 / 暗切换(2026-11-06 用户:「默认跟系统走,但是点过了就按用户设置」「固定在左上角」「之后的页面也要」)。
# 每页左上角一颗圆钮,纯前端、不是 Streamlit 控件:点它不触发 rerun、不进任何数据,生成中也照常可点。
# 当场切换不重新加载(实验中途重新加载 = 当前轮从头再来):把 Streamlit 自己的主题换掉要走它的宿主消息
# SET_CUSTOM_THEME_CONFIG(只收 config.toml [client] allowedOrigins 里的来源 —— 那里列了我们自己的域名),
# 消息里带上与 config.toml 同一份配置生成的整套主题(用 Streamlit 自己生成 new_session 主题的函数,不手抄颜色;
# 2026-11-06 实测切换后与正常加载逐元素一致)。选择另写进 Streamlit 记主题的 localStorage 键,下次打开直接按它加载;
# 没点过 = 跟系统。拿不到主题信息(Streamlit 内部接口变了)就不显示按钮,其余照常。
_THEME_KEY_JS = "'stActiveTheme-'+location.pathname+'-v2'"


@st.cache_resource
def _theme_info_json() -> str | None:
    try:
        from google.protobuf.json_format import MessageToDict
        from streamlit.proto.NewSession_pb2 import CustomThemeConfig
        from streamlit.runtime.app_session import _populate_theme_msg
        out = {}
        for mode, base in (("light", CustomThemeConfig.BaseTheme.LIGHT), ("dark", CustomThemeConfig.BaseTheme.DARK)):
            m = CustomThemeConfig()
            _populate_theme_msg(m, "theme")
            _populate_theme_msg(m, f"theme.{mode}")
            m.ClearField("light")
            m.ClearField("dark")
            m.base = base
            out[mode] = MessageToDict(m, use_integers_for_enums=True)
        return json.dumps(out)
    except Exception:  # noqa: BLE001 —— 没有按钮也能做实验
        return None

# 页头底边那条细线 = 整个流程走到哪了(06 ⑮ (b) 的轻量版:不加任何字)。v1 只有轮内一根进度条,
# 同意 / 背景问卷 / 说明 / 总问卷全程没有进度,第 1 轮开局还显示 0%。权重大致按时长:入组约 12%、
# 各轮共 80%(轮内按步骤条细分)、总问卷之后到 100%。
_STAGE_PROGRESS = {"screening": .05, "intro": .10, "final_survey": .94, "done": 1.0}   # 同意页是标题区,不画线


def _flow_progress(stage: str) -> float | None:
    if stage == "rounds":
        return .12 + .80 * round_common.round_fraction()
    return _STAGE_PROGRESS.get(stage)


def _masthead(*, hero: bool, progress: float | None = None) -> None:
    """页头 + 把 <html lang> 设成会话语言。

    用 st.html 而不是 st.markdown:要顺带执行那一行设置 lang 的脚本(与 views/_scroll 同一做法,
    脚本跑在主页面里)。品牌名与副标题都从 app.title(「NovaStory · …」)拆出来,三语同一格式;
    哪天文案不带「 · 」也只是整句当副标题显示。"""
    lang = get_lang()
    brand, sep, product = t("app.title").partition(" · ")
    if not sep:
        brand, product = "NovaStory", t("app.title")
    b, p = html.escape(brand), html.escape(product)
    # 研究员入口(?admin=1)时左上角有展开侧栏的按钮,窄屏上页头要给它让位(样式见 .ns--shift)
    shift = " ns--shift" if (st.session_state.get("researcher_ok") or sidebar.admin_requested()) else ""
    # data-ns-theme:被试点过亮 / 暗(存在 Streamlit 自己的主题键里)就跟它,否则不设 = 跟系统(见 _DESIGN_CSS)
    info = _theme_info_json()
    labels = json.dumps({"light": t("common.theme_to_light"), "dark": t("common.theme_to_dark")}, ensure_ascii=False)
    btn = ('<button type="button" id="ns-theme-btn" class="ns-theme-btn">'
           '</button>') if info else ""
    if hero:
        head = (f'<header class="ns-hero{shift}">{btn}{_MARK}<div class="ns-hero-brand">{b}</div>'
                f'<h1 class="ns-hero-title">{p}</h1>'
                f'<p class="ns-hero-sub">{html.escape(t("app.subtitle"))}</p></header>')
    else:
        line = ("" if progress is None else
                f'<span class="ns-bar-progress" aria-hidden="true" style="width:{max(0.0, min(progress, 1.0)) * 100:.1f}%"></span>')
        head = (f'<header class="ns-bar{shift}">{_MARK}<span class="ns-bar-brand">{b}</span>{btn}'
                f'<span class="ns-bar-product">{p}</span>{line}</header>')
    js = (f"<script>document.documentElement.lang={json.dumps(_HTML_LANG.get(lang, lang))};"
          "(function(){var r=document.documentElement,K=" + _THEME_KEY_JS + ",v=null;"
          "try{v=JSON.parse(localStorage.getItem(K)||'null')}catch(e){}"
          "if(v==='Light')r.dataset.nsTheme='light';else if(v==='Dark')r.dataset.nsTheme='dark';"
          "else delete r.dataset.nsTheme;"
          "var b=document.getElementById('ns-theme-btn'),TI=" + (info or "null") + ",L=" + labels + ";"
          "if(!b||!TI)return;"
          "function cur(){return r.dataset.nsTheme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}"
          "function paint(){var to=cur()==='dark'?'light':'dark';b.textContent=to==='dark'?'🌙':'☀️';"
          "b.title=L[to];b.setAttribute('aria-label',L[to])}"
          "paint();"
          "b.onclick=function(){var to=cur()==='dark'?'light':'dark',sv=JSON.stringify(to==='dark'?'Dark':'Light');"
          "window.postMessage({stCommVersion:1,type:'SET_CUSTOM_THEME_CONFIG',themeName:'ns-'+to,themeInfo:TI[to]},location.origin);"
          "r.dataset.nsTheme=to;try{localStorage.setItem(K,sv)}catch(e){}"
          "setTimeout(function(){try{localStorage.setItem(K,sv)}catch(e){}},500);paint()};"
          "})();</script>")
    st.html(head + js, unsafe_allow_javascript=True)


# ─────────────────────── 设计语言(单一真源) ───────────────────────
# v2设计(2026-10-31):和纸 / 墨 / 青(+ 决定按钮的绿)。全站唯一的设计令牌来源 —— 颜色、圆角、阴影、字阶都从这里出;
# .streamlit/config.toml 把同一组颜色交给 Streamlit 自己的组件(两边改色要一起改)。
# v1(tag `v1设计`)之前它们散在 5 个文件里,光颜色字面量就有 24 个互不相干的值。
#
# 颜色语义(改之前先想清楚它属于哪一档):
#   --ns-go     绿   = 决定 / 推进 / 定稿   → 所有 type="primary" 按钮
#   --ns-ai     青   = 继续和 AI 折腾       → btn_more_ai(一个明确的非终局动作)
#   --ns-accent 青   = 强调 / 当前所在       → 步骤条当前格、选中的量表格、眉标
#   --ns-dim    弱化 = 注解 / 锚点 / 提示    → 所有 caption 级文字
# 绿 / 青必须泾渭分明,而且**一样醒目**:D/E 里「再让 AI 改」与「就这版提交」是被试的真实选择
# (n_ai_rounds 是因变量),哪个按钮更显眼本身就会推行为。所以两者取**相同的相对亮度**(都是 0.133,
# 白字 5.75:1,与任何底色的对比度都相同),两种主题用同一对 —— v1 的绿 / 蓝是 0.159 / 0.153。
# v2 初版用过墨色决定按钮(对比度 13.9:1 对 AI 的 5.75:1,深色下 15:1 对 3.4:1),审查指出后改回。
# Streamlit 默认的 primary 是珊瑚红,新手会读成「危险操作」,所以 primary 一律改走 --ns-go。
#
# 每个颜色分浅色 / 深色两档:主题跟被试的**操作系统**走(toolbarMode="minimal" 把切换菜单藏了),
# 浅色是「手机没开深色模式的人」的默认,不是边角情况。对比度见 config.toml 顶部(全部过 AA)。
#
# 第一行的 .ns-css 标记让这个只装 <style> 的元素不占版面(Streamlit 的纵向 gap 是 1rem,
# 页面顶部每个零高度元素都会白占一格 —— v1 首屏的空白有一部分就是这么来的)。
_DESIGN_CSS = """
<span class="ns-css"></span>
<style>
:root{
  --ns-surface:#ffffff; --ns-surface-2:#fbfaf7; --ns-sunken:#f1efe9;   /* 页面底色在 config.toml */
  --ns-text:#1f2328; --ns-text-2:#4b5059; --ns-dim:#676c75;
  --ns-line:#e5e1d8; --ns-line-strong:#d4cfc4;
  --ns-accent:#1565c0; --ns-on-accent:#ffffff;
  --ns-accent-soft:rgba(21,101,192,.08); --ns-accent-line:rgba(21,101,192,.32);
  --ns-go:#18753f; --ns-go-hover:#125c31;     /* 与 --ns-ai 同亮度(见上);两种主题同值 */
  --ns-ai:#1565c0; --ns-ai-hover:#0f4f99;
  --ns-ok:#2b6e47; --ns-ok-soft:rgba(43,110,71,.10);
  --ns-shadow:0 1px 2px rgba(31,35,40,.05),0 4px 14px -6px rgba(31,35,40,.10);
  --ns-raised:#ffffff;                 /* 浮在遮罩上的气泡(新手引导) */
  --ns-radius:14px; --ns-radius-sm:10px;
  --ns-fs-note:.82rem;
  --ns-chrome:rgba(255,255,255,.86);   /* 浮在正文上的控件底(展开侧栏箭头) */
  --ns-star:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 1.5l2.6 7.9L22.5 12l-7.9 2.6L12 22.5l-2.6-7.9L1.5 12l7.9-2.6z'/%3E%3C/svg%3E");
}
/* 深色:默认跟系统;被试在同意页点过「明るい／暗い表示」(views/consent._theme_toggle)就按他的选择 ——
   选择存在 Streamlit 自己的 localStorage 键里,页头脚本把它写成 <html data-ns-theme>,两处读的是同一个值。 */
@media (prefers-color-scheme: dark){
  :root:not([data-ns-theme="light"]){
    --ns-surface:#1b1d22; --ns-surface-2:#1f2127; --ns-sunken:#22252b;
    --ns-text:#e7e5df; --ns-text-2:#c4c2bc; --ns-dim:#9b9ea6;
    --ns-line:#2c2f36; --ns-line-strong:#3c4049;
    --ns-accent:#64b5f6; --ns-on-accent:#14161a;
    --ns-accent-soft:rgba(100,181,246,.12); --ns-accent-line:rgba(100,181,246,.42);
    --ns-ok:#7cc99a; --ns-ok-soft:rgba(124,201,154,.13);
    --ns-shadow:0 1px 2px rgba(0,0,0,.35),0 6px 18px -8px rgba(0,0,0,.5);
    --ns-raised:#2a2e36;               /* 深色下气泡要比卡片亮一档,否则和变暗的页面混在一起 */
    --ns-chrome:rgba(20,24,33,.82);
  }
}
:root[data-ns-theme="dark"]{
    --ns-surface:#1b1d22; --ns-surface-2:#1f2127; --ns-sunken:#22252b;
    --ns-text:#e7e5df; --ns-text-2:#c4c2bc; --ns-dim:#9b9ea6;
    --ns-line:#2c2f36; --ns-line-strong:#3c4049;
    --ns-accent:#64b5f6; --ns-on-accent:#14161a;
    --ns-accent-soft:rgba(100,181,246,.12); --ns-accent-line:rgba(100,181,246,.42);
    --ns-ok:#7cc99a; --ns-ok-soft:rgba(124,201,154,.13);
    --ns-shadow:0 1px 2px rgba(0,0,0,.35),0 6px 18px -8px rgba(0,0,0,.5);
    --ns-raised:#2a2e36;               /* 深色下气泡要比卡片亮一档,否则和变暗的页面混在一起 */
    --ns-chrome:rgba(20,24,33,.82);
}

/* ── 不占版面的工具元素 ──────────────────────────────────────────────
   本块 CSS(.ns-css)、st.html 注入的脚本(.ns-util)、state._bridge 的 1px 桥接 iframe(.ns-bridge 标记
   连同紧跟其后的 iframe 容器)。position:absolute 让它们退出纵向 flex,不再各占一格 1rem 的 gap;
   仍是 height:0 + overflow:hidden 而**不是** display:none —— 后者可能让浏览器跳过不可见 iframe 里的
   脚本,而桥接 iframe 的全部用途就是那段脚本(续接网址的 localStorage 读写)。 */
.stElementContainer:has(.ns-css),
.stElementContainer:has(.ns-util),
.stElementContainer:has(> .stMarkdown .ns-bridge),
.stElementContainer:has(> .stMarkdown .ns-bridge) + .stElementContainer{
  position:absolute!important; width:1px!important; height:0!important; min-height:0!important;
  margin:0!important; padding:0!important; overflow:hidden!important;
}

/* 手机端两条硬伤(2026-09-06 实测):
   ① 页顶下拉 = 刷新,而刷新会把当前轮从头再来(state._attempt_resume) —— 关掉滚动链的橡皮筋;
   ② iOS Safari 对 <16px 的输入框会自动放大整页且失焦不缩回 —— 只对触屏设备提到 16px,
      桌面(研究员后台)维持原字号。 */
html, body, [data-testid="stMain"], [data-testid="stAppViewContainer"]{
  overscroll-behavior-y: contain;
}
@media (hover: none) and (pointer: coarse){
  [data-testid="stTextArea"] textarea,
  [data-testid="stTextInput"] input,
  [data-testid="stNumberInput"] input,
  [data-testid="stSelectbox"] input{ font-size:16px !important; }
}

/* ── 版面 ──────────────────────────────────────────────────────────
   layout="wide" 只为了让我们自己定宽度。880px:絵コンテ 5 列表的舒适宽度(内容区 ≈832px),
   同时把日文正文的行长压到 ~50 字(v1 的 1100px 是 ~58 字,眼睛读到行尾找不回行首)。
   顶部 v1 是 6rem 留白 + 44px 的 h1:手机首屏 844px 里任务要往下滑 1–2 屏才看得到。
   研究员后台另有 _WIDE_CSS(表格与图要宽)。 */
.stMainBlockContainer,
[data-testid="stMainBlockContainer"]{
  max-width:880px!important; padding:.75rem 1.5rem 4rem!important;
}
@media (max-width:640px){
  .stMainBlockContainer,
  [data-testid="stMainBlockContainer"]{ padding:.6rem 1rem 3rem!important; }
}

/* ── 文字 ──────────────────────────────────────────────────────────
   日文正文行距放到 1.75(拉丁字母的默认 1.6 对全角字太挤)。<html lang> 由 _masthead 设成会话语言,
   于是 :lang(ja) 下的禁则与按词组断行(word-break:auto-phrase,Chrome 119+;别的浏览器忽略)才生效 ——
   v1 手机上标题断成「絵コ / ンテ制作」就是没有这个。 */
.stMarkdown p, .stMarkdown li{ line-height:1.75; }
/* Streamlit 给 caption 再叠一层 opacity:.6 —— 叠在 --ns-dim 上只剩 2.3:1(深色 3.2:1),低于 AA。归 1,令牌自己管对比度 */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p{ color:var(--ns-dim)!important; opacity:1!important; line-height:1.6; }
[data-testid="stHeading"] :is(h1,h2,h3){ letter-spacing:.01em; text-wrap:balance; }
:lang(ja) :is([data-testid="stHeading"] h1,[data-testid="stHeading"] h2,[data-testid="stHeading"] h3,
  .ns-hero-title,.ns-topic-title,.ns-done-title){ word-break:auto-phrase; }
/* 标题右侧的锚点链接图标:被试用不上,手机上还会被当成正文里的一个怪字符 */
[data-testid="stHeaderActionElements"]{ display:none!important; }
hr{ border-color:var(--ns-line)!important; }

/* ── 页头 ──────────────────────────────────────────────────────── */
.ns-mark{ position:relative; display:block; flex:none; width:1.5rem; height:1.5rem; border-radius:28%;
  background:var(--ns-accent); }
.ns-mark::after{ content:""; position:absolute; inset:16%; background:var(--ns-on-accent);
  -webkit-mask:var(--ns-star) center/contain no-repeat; mask:var(--ns-star) center/contain no-repeat; }
.ns-bar{ position:relative; display:flex; align-items:center; gap:.55rem; min-height:2.75rem; padding:0 4.5rem .55rem 0;
  border-bottom:1px solid var(--ns-line); }
.ns-bar-progress{ position:absolute; left:0; bottom:-1px; height:2px; border-radius:2px; background:var(--ns-accent); }
/* 让出左上角展开侧栏的按钮(只有研究员入口有;正文居中的宽屏上碰不到它) */
/* 让出左上角展开侧栏的按钮(只有研究员入口有;正文居中的宽屏上碰不到它) */
@media (max-width:1000px){ .ns-bar.ns--shift{ padding-left:2.75rem; } .ns-hero.ns--shift{ padding-top:3.4rem; } }
.ns-bar-brand{ font-weight:700; font-size:1rem; letter-spacing:.01em; color:var(--ns-text); white-space:nowrap; }
.ns-bar-product{ font-size:.85rem; color:var(--ns-dim); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
.ns-bar-product::before{ content:""; display:inline-block; width:1px; height:.95em; margin:0 .65rem 0 .15rem;
  background:var(--ns-line-strong); vertical-align:-.12em; }
.ns-hero{ padding:1.75rem 0 .35rem; }
.ns-hero .ns-mark{ width:2.6rem; height:2.6rem; margin:0 0 1rem; }
.ns-hero-brand{ font-size:.8rem; font-weight:700; letter-spacing:.16em; text-transform:uppercase;
  color:var(--ns-accent); margin:0 0 .4rem; }
.ns-hero-title{ font-size:2rem!important; line-height:1.3!important; font-weight:700!important;
  margin:0 0 .55rem!important; padding:0!important; color:var(--ns-text); }
.ns-hero-sub{ font-size:1rem; color:var(--ns-text-2); margin:0; }

/* ── 卡片 ──────────────────────────────────────────────────────────
   白卡浮在和纸底上:一张卡 = 一件事(题目 / 要读的说明 / 要答的一组题)。 */
[data-testid="stForm"],
[data-testid="stExpander"] details,
.ns-topic,
[class*="st-key-ns_card"]{
  background:var(--ns-surface); border:1px solid var(--ns-line)!important;
  border-radius:var(--ns-radius)!important; box-shadow:var(--ns-shadow);
}
[data-testid="stForm"]{ padding:1.5rem 1.5rem 1.25rem!important; }
[class*="st-key-ns_card"]{ padding:1.1rem 1.25rem!important; }
[data-testid="stExpander"] summary{ font-weight:600; }
[data-testid="stExpander"] summary:hover{ color:var(--ns-accent); }
.ns-eyebrow{ font-size:.75rem; font-weight:700; letter-spacing:.1em; color:var(--ns-accent); margin:0 0 .4rem; }
/* 表单里的分节标题(筛查页「基本情報 / 創作経験 / …」):v1 是 2.25rem 的 h2 级大字 */
[data-testid="stForm"] [data-testid="stHeading"] h3{
  font-size:.98rem!important; color:var(--ns-accent); padding:1.35rem 0 .3rem!important; margin:0;
}
[data-testid="stForm"] [data-testid="stVerticalBlock"] > div:first-child [data-testid="stHeading"] h3{ padding-top:0!important; }

/* ── 轮次头(views/round_common._round_header)──────────────────── */
/* Streamlit 给每个 markdown 块垫了 margin-bottom:-1rem(抵消末段 <p> 的下边距);自绘的块不以 <p> 收尾,
   要自己把这 1rem 还回来,否则与下一块之间的 gap 被吃光。下面的 .ns-topic / .ns-done / .ns-shot 同理。 */
.ns-round{ margin:.4rem 0 .8rem; }
.ns-round-top{ display:flex; align-items:center; gap:.8rem; margin:0 0 .75rem; }
.ns-round-no{ font-size:.82rem; font-weight:700; letter-spacing:.06em; color:var(--ns-accent); }
.ns-pips{ display:inline-flex; gap:4px; }
.ns-pips i{ display:block; width:18px; height:4px; border-radius:2px; background:var(--ns-line-strong); }
.ns-pips i.done{ background:var(--ns-text-2); }
.ns-pips i.cur{ background:var(--ns-accent); }
.ns-steps{ display:flex; align-items:center; list-style:none; margin:0!important; padding:0!important; }
.ns-steps li{ display:flex; align-items:center; gap:.45rem; flex:1 1 0; min-width:0; margin:0!important; padding:0;
  color:var(--ns-dim); font-size:.86rem; line-height:1.3; }
.ns-steps li:last-child{ flex:0 0 auto; }
.ns-steps li::after{ content:""; flex:1 1 auto; min-width:.75rem; height:1.5px; margin:0 .6rem; border-radius:1px;
  background:var(--ns-line-strong); }
.ns-steps li:last-child::after{ display:none; }
.ns-steps .dot{ flex:none; display:inline-flex; align-items:center; justify-content:center; width:1.55rem; height:1.55rem;
  border-radius:50%; border:1.5px solid var(--ns-line-strong); background:var(--ns-surface);
  font-size:.74rem; font-weight:700; color:var(--ns-dim); }
.ns-steps .dot svg{ width:.8rem; height:.8rem; }
.ns-steps .lbl{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ns-steps li.done{ color:var(--ns-text-2); }
.ns-steps li.done .dot{ background:var(--ns-text-2); border-color:var(--ns-text-2); color:var(--ns-surface); }
.ns-steps li.done::after{ background:var(--ns-text-2); }
.ns-steps li.cur{ color:var(--ns-text); font-weight:700; }
.ns-steps li.cur .dot{ background:var(--ns-accent); border-color:var(--ns-accent); color:var(--ns-on-accent);
  box-shadow:0 0 0 4px var(--ns-accent-soft); }

/* ── 题目卡(views/round_common._topic_card)─────────────────────── */
.ns-topic{ padding:1.05rem 1.25rem 1rem; margin:0 0 1rem; }
.ns-topic-title{ font-size:1.15rem; font-weight:700; line-height:1.45; margin:0 0 .3rem; color:var(--ns-text); }
.ns-topic-scn{ font-size:1rem; line-height:1.75; margin:0 0 .15rem; color:var(--ns-text); }
/* 选择示例刻意弱化(§15):与情境同字重时会被读成「只能三选一」 */
.ns-topic-choices{ font-size:var(--ns-fs-note); line-height:1.55; color:var(--ns-dim); margin:0 0 .75rem; }
.ns-topic-spec{ display:inline-flex; align-items:center; gap:.4rem; padding:.22rem .75rem .22rem .6rem; border-radius:999px;
  background:var(--ns-sunken); color:var(--ns-text-2); font-size:.8rem; font-weight:600; }
.ns-topic-spec svg{ width:.95rem; height:.95rem; }

/* ── 提示框:整块色块(颜色在 config.toml 的 blue/green/yellow/redBackgroundColor 与 *TextColor)──
   v2 初版做成「左竖线 + 淡底 + 正文色字」,读起来像引用块;用户要回 v1 那种整块色块(2026-10-31),
   这里只统一圆角与内边距、行距,颜色全交给主题。 */
[data-testid="stAlertContainer"]{ border-radius:var(--ns-radius-sm)!important; padding:.9rem 1.1rem!important; }
[data-testid="stAlertContainer"] [data-testid="stMarkdownContainer"] p{ line-height:1.75; }

/* ── 按钮 ──────────────────────────────────────────────────────── */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button{
  min-height:2.85rem; border-radius:var(--ns-radius-sm)!important;
  transition:background-color .15s ease, border-color .15s ease, box-shadow .15s ease, transform .06s ease;
}
.stButton > button p, .stFormSubmitButton > button p, .stDownloadButton > button p{ font-weight:600; }
.stButton > button:active, .stFormSubmitButton > button:active{ transform:translateY(1px); }
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible,
[data-testid="stButtonGroup"] button:focus-visible{ outline:2px solid var(--ns-accent)!important; outline-offset:2px; }
button[data-testid="stBaseButton-primary"],
button[data-testid="stBaseButton-primaryFormSubmit"]{
  background:var(--ns-go)!important; border-color:var(--ns-go)!important; color:#ffffff!important;
  box-shadow:0 1px 2px rgba(0,0,0,.10), 0 8px 18px -12px rgba(0,0,0,.6);
}
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover{
  background:var(--ns-go-hover)!important; border-color:var(--ns-go-hover)!important;
}
/* v1 的 !important 实色把「未勾同意时禁用的开始按钮」也涂成了可点的绿 —— 禁用必须看得出是禁用 */
button[data-testid="stBaseButton-primary"]:disabled,
button[data-testid="stBaseButton-primaryFormSubmit"]:disabled{
  background:var(--ns-sunken)!important; border-color:var(--ns-line-strong)!important;
  color:var(--ns-dim)!important; box-shadow:none; cursor:not-allowed;
}
button[data-testid="stBaseButton-secondary"],
button[data-testid="stBaseButton-secondaryFormSubmit"]{
  background:var(--ns-surface); border-color:var(--ns-line-strong); color:var(--ns-text);
}
button[data-testid="stBaseButton-secondary"]:hover,
button[data-testid="stBaseButton-secondaryFormSubmit"]:hover{
  background:var(--ns-surface-2); border-color:var(--ns-text-2); color:var(--ns-text);
}
div.st-key-btn_more_ai button{
  background:var(--ns-ai)!important; border:1px solid var(--ns-ai)!important; color:#ffffff!important;
  box-shadow:0 1px 2px rgba(0,0,0,.10), 0 8px 18px -12px rgba(21,101,192,.85);
}
div.st-key-btn_more_ai button:hover{ background:var(--ns-ai-hover)!important; border-color:var(--ns-ai-hover)!important; }
div.st-key-btn_more_ai button p{ color:#ffffff!important; }

/* ── 输入 ──────────────────────────────────────────────────────── */
[data-testid="stTextArea"] textarea{ line-height:1.7; }
[data-testid="stTextAreaRootElement"], [data-testid="stTextInputRootElement"]{ border-radius:var(--ns-radius-sm)!important; }
[data-testid="stTextAreaRootElement"]:focus-within, [data-testid="stTextInputRootElement"]:focus-within{
  box-shadow:0 0 0 3px var(--ns-accent-soft);
}

/* ── 选择:分段控件(7 点量表 / 引导选项 / 逐镜头归属)的选中态要一眼看得出 ──
   v1 选中只是浅蓝底 + 蓝字,答到第十几题时很难扫出哪一题漏了。 */
[data-testid="stButtonGroup"] button[role="radio"]{ min-height:2.5rem; background:var(--ns-surface); }
[data-testid="stButtonGroup"] button[role="radio"]:hover{ background:var(--ns-accent-soft); }
[data-testid="stButtonGroup"] button[role="radio"][aria-checked="true"]{
  background:var(--ns-accent)!important; border-color:var(--ns-accent)!important; color:var(--ns-on-accent)!important;
}
[data-testid="stButtonGroup"] button[role="radio"][aria-checked="true"] *{ color:var(--ns-on-accent)!important; font-weight:700; }
/* 引导选项(E)与逐镜头三选一是「几句话里挑一句」,不是刻度:做成彼此分开的胶囊,窄屏折行时不会出现
   连体按钮断成两截的边框(7 点量表保持连体,那是一把尺子)。 */
:is([class*="st-key-_g_opt_"],[class*="st-key-_q_shot"],[class*="st-key-_q_whole_"]) [role="radiogroup"]{
  display:flex; flex-wrap:wrap; gap:.45rem; }
:is([class*="st-key-_g_opt_"],[class*="st-key-_q_shot"],[class*="st-key-_q_whole_"]) button[role="radio"]{
  border:1px solid var(--ns-line-strong)!important; border-radius:999px!important; margin:0!important;
  padding:.45rem 1rem!important; }
:is([class*="st-key-_g_opt_"],[class*="st-key-_q_shot"],[class*="st-key-_q_whole_"]) button[role="radio"][aria-checked="true"]{
  border-color:var(--ns-accent)!important; }
:is([class*="st-key-_g_opt_"],[class*="st-key-_q_shot"],[class*="st-key-_q_whole_"]) button[role="radio"] *{
  white-space:normal!important; overflow:visible!important; text-overflow:clip!important; text-align:left; }
:is([class*="st-key-_g_opt_"],[class*="st-key-_q_shot"],[class*="st-key-_q_whole_"]) button[role="radio"]{ height:auto!important; }
/* 1–7 的刻度在 ~350px 以下会折成「1–6」+ 一整行「7」,锚点也对不上:不许折行,格子按比例缩 */
[class*="st-key-ns_lk_"] [role="radiogroup"]{ flex-wrap:nowrap!important; }
[class*="st-key-ns_lk_"] button[role="radio"]{ flex:1 1 0!important; min-width:0!important; padding-left:.2rem!important; padding-right:.2rem!important; }
/* 单选:竖排的多选项 → 整行可点的选项卡(手机上 v1 每行只有 ~24px 高的点按区域);横排(是 / 否、语言)→ 胶囊 */
/* 1.60 的 st.radio 默认按内容定宽:每组选项卡宽窄不一(随该组最长的那句)。只让竖排的撑满 */
.stElementContainer:has(> [data-testid="stRadio"] [aria-orientation="vertical"]){ width:100%!important; }
[data-testid="stRadio"]:has([aria-orientation="vertical"]), [data-testid="stRadioGroup"][aria-orientation="vertical"]{ width:100%; }
[data-testid="stRadioGroup"]{ gap:.45rem; }
[data-testid="stRadioGroup"] [data-testid="stRadioOption"]{
  margin:0!important; border:1px solid var(--ns-line-strong); border-radius:var(--ns-radius-sm);
  background:var(--ns-surface); transition:border-color .15s ease, background-color .15s ease;
}
[data-testid="stRadioGroup"][aria-orientation="vertical"] [data-testid="stRadioOption"]{ width:100%; padding:.6rem .85rem; }
[data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]{ padding:.4rem .95rem .4rem .75rem; border-radius:999px; }
[data-testid="stRadioGroup"] [data-testid="stRadioOption"]:hover{ border-color:var(--ns-accent-line); }
[data-testid="stRadioGroup"] [data-testid="stRadioOption"]:has(input:checked){
  border-color:var(--ns-accent); background:var(--ns-accent-soft);
}
/* 同意的那一下:勾选框单独成框,与上面的长文分开 */
.st-key-_consent_agree{ background:var(--ns-surface); border:1px solid var(--ns-line-strong); border-radius:var(--ns-radius-sm); }
.st-key-_consent_agree label{ display:flex; width:100%; box-sizing:border-box; padding:.8rem 1rem; cursor:pointer; }   /* 整张卡都能点 */
.st-key-_consent_agree:has(input:checked){ border-color:var(--ns-accent); background:var(--ns-accent-soft); }
/* E / F 问卷「哪些问题帮到了你」(选填、可多选;views/questionnaire._ai_q_best_pick):不进「下一题」实线框的顺序,
   用总问卷选填题同一个静态虚线框;选项是整行卡片(同单选 / 同意页),勾上的亮起来,方形勾选框留着 = 可以多选 */
[class*="st-key-ns_aqbest_"]{ border:1.5px dashed var(--ns-accent-line); border-radius:12px; padding:.6rem .8rem .8rem; }
[class*="st-key-ns_aqopts_"]{ gap:.45rem!important; }
[class*="st-key-ns_aqopts_"] [data-testid="stCheckbox"] label{ display:flex; width:100%; box-sizing:border-box; padding:.6rem .85rem;
  cursor:pointer; background:var(--ns-surface); border:1px solid var(--ns-line-strong); border-radius:var(--ns-radius-sm);
  transition:border-color .15s ease, background-color .15s ease; }
[class*="st-key-ns_aqopts_"] [data-testid="stCheckbox"] label:hover{ border-color:var(--ns-accent-line); }
[class*="st-key-ns_aqopts_"] [data-testid="stCheckbox"] label:has(input:checked){ border-color:var(--ns-accent); background:var(--ns-accent-soft); }

/* ── 下一步引导(用户 2026-10-31:「不是加文字引导,是要动效,像新手引导一样」)────────
   聚光圈与气泡由 views/_tour.py 的脚本挂到 <body> 上(position:fixed,每帧跟着目标走)。
   遮罩就是聚光圈的 box-shadow(pointer-events:none);引导期间页面的点击由 _tour.py 在捕获阶段拦掉
   (只有气泡里的两个按钮能点),滚动照常。
   当前唯一要填的框(创意 / F 的回答)常亮一圈光晕;D 的「请 AI 改」框刻意不加:那是被试在
   「再改 / 提交」之间的自由选择,高亮它就是在推一边。 */
.ns-tour-spot{ position:fixed; z-index:1000001; top:0; left:0; width:0; height:0; border-radius:14px; pointer-events:none;
  box-shadow:0 0 0 200vmax rgba(12,14,18,0); transition:box-shadow .4s ease; }
/* 只有换步(.is-moving)时位置才带过渡:平时每帧跟着目标走,带过渡会在滚动时拖影 */
.ns-tour-spot.is-moving{ transition:top .55s cubic-bezier(.22,.7,.2,1), left .55s cubic-bezier(.22,.7,.2,1),
    width .55s cubic-bezier(.22,.7,.2,1), height .55s cubic-bezier(.22,.7,.2,1), box-shadow .4s ease; }
.ns-tour-spot.is-on{ box-shadow:0 0 0 200vmax rgba(12,14,18,.52); }
.ns-tour-spot::after{ content:""; position:absolute; inset:-2px; border-radius:inherit; border:2px solid var(--ns-accent);
  animation:ns-ring 1.8s ease-out infinite; }
.ns-tour-tip{ position:fixed; z-index:1000002; top:0; left:0; width:min(340px, calc(100vw - 24px)); box-sizing:border-box;
  padding:14px 16px 12px; border-radius:14px; background:var(--ns-raised); color:var(--ns-text);
  border:1px solid var(--ns-line); box-shadow:0 14px 36px -10px rgba(0,0,0,.45); }
.ns-tour-tip.is-moving{ transition:top .55s cubic-bezier(.22,.7,.2,1), left .55s cubic-bezier(.22,.7,.2,1); }
/* 气泡只有两个按钮接事件:点到气泡的字 = 点在页面上(被拦、晃一下,焦点不丢),在气泡的字上滑动 = 滑页面。
   长按按钮也不选字、不弹 iOS 的长按菜单 */
.ns-tour-tip{ pointer-events:none; -webkit-user-select:none; user-select:none; -webkit-touch-callout:none; }
.ns-tour-tip button{ pointer-events:auto; }
/* 引导期间(_tour.py 给 <html> 加 .ns-tour-lock):页面不能选字、长按不弹菜单(iOS 长按不发 contextmenu,
   只能靠 CSS)、光标不变手形 —— 看着就不像能点 */
.ns-tour-lock .stApp{ -webkit-user-select:none; user-select:none; -webkit-touch-callout:none; }
.ns-tour-lock .stApp *{ cursor:default!important; }
/* 强制配色(Windows 高对比度):遮罩的 box-shadow 会被去掉、按钮底色被覆盖成纯文字 —— 给气泡和两个按钮描边。
   按钮前面多一层 .ns-tour-tip:这一段在 .ns-tour-next / .ns-tour-skip 的 border:0 之前,同等权重会被它盖掉 */
@media (forced-colors: active){
  .ns-tour-tip{ border:2px solid CanvasText; }
  .ns-tour-tip .ns-tour-next, .ns-tour-tip .ns-tour-skip{ border:1px solid ButtonText; }
}
.ns-tour-tip.is-in{ animation:ns-tip-in .38s cubic-bezier(.22,.7,.2,1); }
/* 引导期间点到气泡以外:气泡左右晃一下 —— 「点这里的按钮」。用 translate 不用 transform,不与 ns-tip-in 打架 */
.ns-tour-tip.is-nudge{ animation:ns-nudge .42s cubic-bezier(.36,.07,.19,.97); }
.ns-tour-tip::before{ content:""; position:absolute; left:calc(var(--ax, 24px) - 7px); width:14px; height:14px;
  background:inherit; transform:rotate(45deg); border-radius:3px; }
.ns-tour-tip[data-side="below"]::before{ top:-6px; }
.ns-tour-tip[data-side="above"]::before{ bottom:-6px; }
.ns-tour-tip[data-side="inside"]::before{ display:none; }
.ns-tour-count{ font-size:.72rem; font-weight:700; letter-spacing:.08em; color:var(--ns-accent); margin:0 0 4px; }
.ns-tour-text{ font-size:1rem; font-weight:700; line-height:1.55; margin:0 0 12px; text-wrap:pretty; }
:lang(ja) .ns-tour-text{ word-break:auto-phrase; }
.ns-tour-row{ display:flex; align-items:center; justify-content:space-between; gap:8px; }
.ns-tour-skip{ background:none; border:0; padding:8px 4px; font:inherit; font-size:.85rem; color:var(--ns-dim); cursor:pointer; }
.ns-tour-next{ border:0; border-radius:10px; padding:9px 18px; font:inherit; font-size:.92rem; font-weight:700;
  background:var(--ns-go); color:#ffffff; cursor:pointer; }
.ns-tour-next:focus-visible, .ns-tour-skip:focus-visible{ outline:2px solid var(--ns-accent); outline-offset:2px; }
@keyframes ns-ring{ 0%{ box-shadow:0 0 0 0 var(--ns-accent-line); } 100%{ box-shadow:0 0 0 14px rgba(0,0,0,0); } }
@keyframes ns-tip-in{ from{ opacity:0; transform:translateY(8px) scale(.98); } to{ opacity:1; transform:none; } }
@keyframes ns-nudge{ 20%{ translate:-6px 0; } 40%{ translate:5px 0; } 60%{ translate:-3px 0; } 80%{ translate:2px 0; } }
:is(.st-key-_intent_input,[class*="st-key-_iv_answer_"]) [data-testid="stTextAreaRootElement"]{
  border-color:var(--ns-accent)!important; box-shadow:0 0 0 4px var(--ns-accent-soft); }
/* F 的回答框:每出一道新题(新 key = 新节点)呼吸两下 */
[class*="st-key-_iv_answer_"] [data-testid="stTextAreaRootElement"]{ animation:ns-ring 1.6s ease-out .3s 2; }
/* 第 2 轮起的写创意页:题目卡同样呼吸两下、之后常亮一圈(views/round_common._topic_card) */
.ns-topic.ns-topic--cue{ border-color:var(--ns-accent)!important; box-shadow:0 0 0 4px var(--ns-accent-soft);
  animation:ns-ring 1.6s ease-out .3s 2; }
/* 同意页:勾上同意、开始按钮亮起的那一下 */
.stElementContainer.st-key-_consent_agree:has(input:checked) + .stElementContainer button{ animation:ns-ring 1.6s ease-out 2; }
@media (prefers-reduced-motion: reduce){
  .ns-tour-spot, .ns-tour-tip, .ns-tour-spot.is-moving, .ns-tour-tip.is-moving{ transition:none; }
  .ns-tour-spot::after, .ns-tour-tip.is-in, .ns-tour-tip.is-nudge, .ns-topic.ns-topic--cue,
  [class*="st-key-_iv_answer_"] [data-testid="stTextAreaRootElement"],
  .stElementContainer.st-key-_consent_agree:has(input:checked) + .stElementContainer button{ animation:none!important; }
}

/* ── 7 点量表 ──────────────────────────────────────────────────────
   一题一块:题干 → 按钮行 → 锚点,块与块之间一条细线。v1 的锚点行紧贴着下一题的题干,
   看起来像是下一题的(筛查页尤其明显)。 */
[class*="st-key-ns_lk_"]{ gap:.5rem!important; padding:.2rem 0 .9rem; border-bottom:1px solid var(--ns-line); }
[class*="st-key-ns_lk_"] .stMarkdown p{ font-weight:500; }
/* 7 点量表下方的锚点行。原来用 st.columns(3) 拼,窄屏下 Streamlit 会把列**竖着堆**,
   于是「まったくそう思わない / どちらともいえない / 非常にそう思う」变成三行阶梯,
   而且看不出哪一句属于哪一端 —— 对一个靠锚点定义刻度含义的 7 点量表来说,这不只是
   难看,是量表本身失去了刻度说明。改成一行,窄屏也不会拆行。
   三栏 grid 而不是 flex + space-between:后者让中点随左右两句的长度左右漂移,
   对不准量表的第 4 格。1fr auto 1fr 下,中间那栏的中心恒在 50% = 第 4 格中心。 */
.ns-anchor{display:grid;grid-template-columns:1fr auto 1fr;align-items:start;
  gap:.45rem;margin:-2px 0 1rem;color:var(--ns-dim);
  font-size:var(--ns-fs-note);line-height:1.25}
.ns-anchor>span{min-width:0}
.ns-anchor .a-l{text-align:left}
.ns-anchor .a-m{text-align:center}
.ns-anchor .a-r{text-align:right}

/* 逐镜头归属:一镜一张卡(views/questionnaire._shot_preview);镜号圆圈与絵コンテ纸上的同形 */
[class*="st-key-ns_card_shot"]{ gap:.75rem!important; }
/* 镜号圆圈浮在行首:文字绕着它排,第二行起与下方的选项控件左对齐 */
.ns-shot{ margin-bottom:1rem; }
.ns-shot::after{ content:""; display:block; clear:both; }
/* 镜号圆圈用正文字体:Georgia 的数字是旧式数字(3、4、5 下沉),在圆圈里看着不居中 */
.ns-shot .cut{ float:left; display:inline-flex; align-items:center; justify-content:center; width:1.6rem; height:1.6rem;
  margin:.05rem .6rem .2rem 0; border:1.5px solid var(--ns-text); border-radius:50%;
  font-size:.82rem; font-weight:700; line-height:1; font-variant-numeric:tabular-nums; color:var(--ns-text); }
.ns-shot-txt{ line-height:1.7; color:var(--ns-text); }

/* 分镜稿的看稿排版(views/_streaming.script_view:C 的只读稿 / 历史各版 / 生成中):镜号圆圈同上,
   【画面】这类标签是小标签。<i> 是藏起来的【】与镜号的「.」—— 0 号字,看不见、复制时还在。 */
.ns-sb{ line-height:1.7; color:var(--ns-text); margin-bottom:1rem; }   /* 1rem:抵消 markdown 块的 -1rem */
.ns-sb i{ font-size:0; }
.ns-sb > * + *{ margin-top:.9rem; }
.ns-sb-cut{ display:grid; grid-template-columns:1.6rem minmax(0, 1fr); gap:0 .7rem; }
.ns-sb-no{ display:inline-flex; align-items:center; justify-content:center; width:1.6rem; height:1.6rem; margin-top:.1rem;
  border:1.5px solid var(--ns-text); border-radius:50%; font-size:.82rem; font-weight:700; line-height:1;
  font-variant-numeric:tabular-nums; color:var(--ns-text); }
.ns-sb-ln{ white-space:pre-wrap; overflow-wrap:anywhere; }
.ns-sb-gap{ height:.55em; }
/* 左边距按前面是什么分三档:行首(.ns-fl0)顶格;前面是空格的只补一点 —— 折行时那个空格挂在上一行
   行尾,折到行首的标签只缩进这一点;紧贴前文的(.ns-flg)补足 */
.ns-fl{ display:inline-block; margin:0 .4rem 0 .15rem; padding:0 .42rem; border-radius:5px; vertical-align:.08em;
  font-size:.74rem; font-weight:700; line-height:1.6; letter-spacing:.03em; color:var(--ns-accent); background:var(--ns-accent-soft); }
.ns-fl0{ margin-left:0; }
.ns-flg{ margin-left:.3rem; }
.ns-flq{ margin-right:0; }   /* 后面紧跟「『(:全角括号自带半格空白,再加外边距就成了两格 */

/* E 的引导题卡(views/guidance)与 F 的采访题:题面比正文大一档 */
.ns-qhead{ display:flex; align-items:center; gap:.7rem; margin:0 0 .45rem; }
.ns-qhead .ns-eyebrow{ margin:0; }
.ns-dots{ display:inline-flex; gap:5px; }
.ns-dots i{ display:block; width:7px; height:7px; border-radius:50%; background:var(--ns-line-strong); }
.ns-dots i.done{ background:var(--ns-text-2); }
.ns-dots i.cur{ background:var(--ns-accent); box-shadow:0 0 0 3px var(--ns-accent-soft); }
.ns-q{ font-size:1.08rem; font-weight:700; line-height:1.6; color:var(--ns-text); margin-bottom:1rem; }   /* 1rem:抵消 markdown 块的 -1rem */
.ns-echo{ font-size:.9rem; line-height:1.6; color:var(--ns-dim); margin:0 0 .25rem; }

/* 生成后的稿子:C 的只读稿与「你写的创意」回显 */
.st-key-ns_card_script{ background:var(--ns-surface-2)!important; }
.st-key-ns_card_intent{ gap:.35rem!important; box-shadow:none!important; background:var(--ns-sunken)!important; }
/* 模型写的题面(历史面板)与「为什么问」(E 的引导卡):逐字转义后的样式。markdown 块自带 -1rem 下边距:
   .ns-why 垫回 1rem;.ns-hq 只垫 .3rem,紧贴它下面的「→ 回答」,一问一答成组 */
.ns-hq{ font-weight:700; line-height:1.6; margin-bottom:.3rem; }
.ns-why{ font-size:.9rem; line-height:1.6; color:var(--ns-dim); margin-bottom:1rem; }

/* ── 对话(历史 / F 的采访)──────────────────────────────────────── */
/* 自定义图标头像在 1.60 里统一是 stChatMessageAvatarCustom,谁说的话只能从内容区的 aria-label 分
   (Streamlit 内部固定的英文串,不随界面语言变)。 */
[data-testid="stChatMessage"]{ border-radius:var(--ns-radius); padding:.75rem .9rem; gap:.75rem; background:transparent; }
[data-testid="stChatMessage"]:has([aria-label="Chat message from user"]){ background:var(--ns-sunken); }
[data-testid="stChatMessageAvatarCustom"]{ border:0!important; border-radius:9px!important; }
[data-testid="stChatMessage"]:has([aria-label="Chat message from assistant"]) [data-testid="stChatMessageAvatarCustom"]{
  background:var(--ns-accent)!important; }
[data-testid="stChatMessage"]:has([aria-label="Chat message from assistant"]) [data-testid="stChatMessageAvatarCustom"] *{
  color:var(--ns-on-accent)!important; }
[data-testid="stChatMessage"]:has([aria-label="Chat message from user"]) [data-testid="stChatMessageAvatarCustom"]{
  background:var(--ns-text-2)!important; }
[data-testid="stChatMessage"]:has([aria-label="Chat message from user"]) [data-testid="stChatMessageAvatarCustom"] *{
  color:var(--ns-surface)!important; }

/* ── 页脚 ──────────────────────────────────────────────────────── */
.ns-has-resume [data-testid="stMainBlockContainer"]{ padding-bottom:8rem!important; }   /* 续接提示条(core/state)的位置 */
.st-key-ns_footer{ margin-top:2rem; padding-top:1.1rem; border-top:1px solid var(--ns-line); }
.st-key-ns_footer [data-testid="stCaptionContainer"]{ text-align:center; font-size:.8rem; }

/* ── 完成页 ──────────────────────────────────────────────────────── */
.ns-done{ text-align:center; padding:1.5rem 0 .25rem; margin-bottom:1rem; }
.ns-done-mark{ width:3.6rem; height:3.6rem; margin:0 auto 1rem; border-radius:50%; display:flex;
  align-items:center; justify-content:center; background:var(--ns-ok-soft); color:var(--ns-ok); }
.ns-done-mark svg{ width:1.9rem; height:1.9rem; }
.ns-done-title{ font-size:1.45rem!important; line-height:1.45!important; font-weight:700!important;
  margin:0 0 .55rem!important; padding:0!important; color:var(--ns-text); }
.ns-done-msg{ color:var(--ns-text-2); }
.st-key-ns_done_code [data-testid="stCode"] pre{ padding:1.15rem 1rem!important; border:1px dashed var(--ns-line-strong);
  border-radius:var(--ns-radius-sm); }
.st-key-ns_done_code [data-testid="stCode"] code{ display:block; text-align:center; font-size:1.7rem!important;
  font-weight:700; letter-spacing:.22em; color:var(--ns-text)!important; }

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
    color: var(--ns-on-accent) !important;
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
/* 例外:AI 调用等待中(views/_streaming 在本次运行里画了 .ns-busy 标记)时,上一次运行残留、
   还没被重画的旧元素藏起来且不可点(2026-10-29 审计)。中国队列 E 出题时,进度条下面还完整显示着
   旧的创意框和「制作を開始」按钮,被试以为没反应、连点 —— 每点一次就是一次 rerun,调用被打断
   (现已能接着等,但旧按钮仍会改动状态)。只在有 AI 调用的运行里生效;不支持 :has() 的浏览器
   退回原样(旧元素照常显示,不影响功能)。 */
body:has(.stElementContainer:not([data-stale="true"]) .ns-busy) .stElementContainer[data-stale="true"]{
  visibility:hidden!important; pointer-events:none!important;
}
.stElementContainer:has(.ns-busy){height:0!important;min-height:0!important;margin:0!important;
  padding:0!important;overflow:hidden!important}
/* AI 调用等待中(.ns-busy-llm,views/_streaming 调用一结束就撤掉):本次运行里**已画出**的引导卡片 / 脚本编辑框 /
   修改框 / 「AIに伝える」不可点(2026-11-05 审计)。E/F 点「回答を終えて生成」、D 点「AIに伝える」之后,流式输出时
   改一个选项、点「前の質問」或点编辑框都会 rerun → 在途的生成没人接回、要再点一次生成、等待也没记进 AI 等待。
   只挡指针(滚动照常);不支持 :has() 的浏览器退回原样。 */
body:has(.stElementContainer:not([data-stale="true"]) .ns-busy-llm)
  :is(.st-key-ns_card_gq, .st-key-_script_edit, .st-key-_revision_input, .st-key-btn_more_ai){
  pointer-events:none!important;
}
/* 输入框获得焦点时 Streamlit 1.60 自带的英文提示「Press ⌘+Enter to apply」与字数「12/1000」—— 不随界面语言变,
   ja / zh 被试会看到英文 → 藏掉;max_chars 的上限照常生效(2026-11-05 审计)。 */
[data-testid="InputInstructions"]{display:none!important}
.stExpander summary,
[data-baseweb="tab-list"],[data-baseweb="tab"]{opacity:1!important}

/* 亮 / 暗切换(_masthead):每页页头右上角的圆钮(2026-11-06 用户:「做在右上角,画面滚动跟着走,固定在网页上」→
   放在页头元素里、随页面滚走,不是悬浮)。图标 = 点了会变成的样子(暗色时 ☀️、亮色时 🌙),完整说明在 title / aria-label。
   手机上页头右端让给 Streamlit 自己固定在屏幕右上角的「运行中」图标(.ns-bar 的右内边距原本就是为它留的),圆钮挪到它左边。 */
.ns-theme-btn{ position:absolute; right:0; z-index:2; width:2.25rem; height:2.25rem; padding:0;
  display:flex; align-items:center; justify-content:center; font-size:1.05rem; line-height:1; cursor:pointer;
  border-radius:50%; border:1px solid var(--ns-line-strong); background:var(--ns-surface); color:var(--ns-text); }
.ns-theme-btn:hover{ border-color:var(--ns-accent-line); }
.ns-bar .ns-theme-btn{ top:calc(50% - .275rem); transform:translateY(-50%); }   /* .275rem = 底内边距的一半:与品牌那行居中 */
.ns-hero{ position:relative; }
.ns-hero .ns-theme-btn{ top:1.75rem; }   /* 与标志同一高度(.ns-hero 的上内边距) */
/* 电脑屏整体放大一档(2026-11-06 用户:「pc 版字体大一点」;日本队列推荐用电脑做):Streamlit 与本页样式的字号
   都以根字号为基准(rem),只改根字号 = 全站按同一比例放大(正文 16→18px、注释约 13→15px),版面比例不变。
   手机 / 平板(<1024px)不受影响。内容列宽是 px 固定的,不随之变宽。 */
@media (min-width:1024px){ html{font-size:18px} }

@media (max-width:640px){
  /* 窄屏字阶:实测 h1 在 390px 上是 44px,一个 st.subheader 的分节标题能占掉三行屏幕。 */
  [data-testid="stHeading"] h1{font-size:1.4rem!important;line-height:1.35}
  [data-testid="stHeading"] h2{font-size:1.2rem!important;line-height:1.4}
  [data-testid="stHeading"] h3{font-size:1.06rem!important;line-height:1.45}
  .ns-hero{ padding-top:1rem; }
  .ns-hero .ns-mark{ width:2.1rem; height:2.1rem; margin-bottom:.8rem; }
  .ns-hero-title{ font-size:1.5rem!important; }
  .ns-bar{ padding-right:6rem; }   /* 右端:「运行中」图标(3.25rem)+ 亮暗圆钮 */
  .ns-bar .ns-theme-btn, .ns-hero .ns-theme-btn{ right:3.25rem; }
  .ns-hero .ns-theme-btn{ top:1rem; }
  .ns-bar-product{ font-size:.8rem; }
  [data-testid="stForm"]{ padding:1.1rem .95rem 1rem!important; }
  [class*="st-key-ns_card"]{ padding:.95rem 1rem!important; }
  .ns-topic{ padding:.95rem 1rem .9rem; }
  /* 步骤条:只留当前一步的名字,其余是编号圆点(五个名字一行放不下) */
  .ns-steps li{ flex:0 0 auto; }
  .ns-steps li.cur{ flex:1 1 auto; }
  .ns-steps li:not(.cur) .lbl{ position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0);
    white-space:nowrap; }   /* 只是视觉上藏:读屏照样读得到每一步的名字 */
  .ns-steps li::after{ flex:0 0 .7rem; min-width:.7rem; margin:0 .3rem; }
  .ns-steps li.cur::after{ flex:1 1 .7rem; }
  .ns-anchor{font-size:.7rem;gap:.3rem}
  /* 历史面板去掉头像:390px 上头像那一列吃掉 ~45px,稿子一行只剩八九个字。谁说的看小标题与底色就知道。 */
  .st-key-ns_history [data-testid="stChatMessageAvatarCustom"]{ display:none; }
  .st-key-ns_history [data-testid="stChatMessage"]{ padding:.65rem .75rem; }
  .ns-sb-cut{ gap:0 .55rem; }
}
</style>
"""

# 研究员后台:表格、图、平衡格要宽,不受被试页 880px 的阅读宽度限制。
_WIDE_CSS = ('<span class="ns-css"></span><style>.stMainBlockContainer,[data-testid="stMainBlockContainer"]'
             '{max-width:1320px!important}</style>')


def _inject_css() -> None:
    st.markdown(_DESIGN_CSS, unsafe_allow_html=True)


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
    # 文字与顺序同 v1,只把「完成」做成一块居中的标题区、完成码放大(v1 是两块绿框 + 一块黄框压过了完成码)。
    st.markdown(
        f'<div class="ns-done"><div class="ns-done-mark">{_CHECK_SVG}</div>'
        f'<h2 class="ns-done-title">{html.escape(t("done.title"))}</h2>'
        f'<div class="ns-done-msg">{html.escape(t("done.message"))}</div></div>',
        unsafe_allow_html=True,
    )
    with st.container(key="ns_done_code"):
        st.code(st.session_state["completion_code"], language=None)   # 页上第一个 st.code(E2E 按它取码)
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
