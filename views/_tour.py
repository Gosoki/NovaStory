"""创意页的新手引导(用户 2026-10-31:「不是加文字引导,是要动效,像新手引导一样」「只有第一次有这个引导就行了」)。

只在第 1 轮、每位被试一次:页面稍暗,聚光圈依次落在 题目卡 → 本轮说明框 → 输入框 上(阅读顺序;
说明框里是各条件的操纵说明,引导不能跳过它),「書きはじめる」关掉引导并把光标放进输入框。
第 2 轮起什么都不出(此前曾让三处各闪一下,用户要求只留第一次)。

纯前端:st.html 注入主页面执行(与 views/_scroll、问卷的分镜观察器同一做法),不回传 Python、
不记事件、不写任何数据;四个条件同一套引导。⚠️ 但第 1 轮的 t_read_intent / t_total
(round_start → 提交)包含看引导的那几秒 —— 第 1 轮的条件随 Williams 序列轮换,期望上平衡,
见 docs/paper/20 §5。几条护栏:
  · 不锁页面:遮罩是聚光圈的 box-shadow(pointer-events:none),底下照样能点能输入;
  · 每位被试只演一次:记号存 localStorage(键里带被试编号 —— 关掉标签页再用续接链接回来也不重演,
    同一台设备上的下一位被试编号不同、照常会看到),另有页内记号兜底(存储被禁时也不重演);
    被试已经在写了就不演,也不从正在输入的框里抢焦点;
  · 输入框一消失(提交创意、续接跳页)就撤场,不会残留遮罩;气泡始终留在屏内、放不下时不压目标;
  · 用按钮关掉后 0.4 秒内的点击被吞掉(连点不会穿透到下面的页面);
  · 不依赖外部库(不发外部请求),尊重系统「减少动态效果」。
样式在 app.py 的 .ns-tour-*。
"""
from __future__ import annotations

import json

import streamlit as st

from i18n import t

_JS = r"""
(function(){
  var C = __CFG__;
  var W = window, D = document;
  var DONE = W.__nsTourDone || (W.__nsTourDone = {});   // 页内记号:存储被禁时也不重演
  if (DONE[C.key]) return;
  try { if (W.localStorage.getItem(C.key)) return; } catch (e) {}
  if (W.__nsTour && W.__nsTour.key === C.key) return;   // 本轮正在演:rerun 又注入了一次
  if (W.__nsTour) W.__nsTour.stop(false);
  var reduce = !!(W.matchMedia && W.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var T = {key: C.key, alive: true, nodes: [], timers: [], raf: 0, offs: []};
  W.__nsTour = T;

  function q(sel){ return D.querySelector(sel); }
  function later(fn, ms){ T.timers.push(setTimeout(fn, ms)); }
  function el(tag, cls, parent){ var e = D.createElement(tag); e.className = cls;
    (parent || D.body).appendChild(e); if (!parent) T.nodes.push(e); return e; }
  function on(target, ev, fn, opt){ target.addEventListener(ev, fn, opt); T.offs.push(function(){ target.removeEventListener(ev, fn, opt); }); }
  function editing(){ var a = D.activeElement; return !!a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT'); }
  T.stop = function(markDone, focusSel, fromTap){
    if (!T.alive) return;
    T.alive = false;
    if (fromTap) swallowNextTap();
    if (T.raf) cancelAnimationFrame(T.raf);
    T.timers.forEach(clearTimeout);
    T.offs.forEach(function(f){ f(); });
    // 焦点先交出去再拆节点:被拆掉的按钮带着焦点,读屏会从页首重读
    var f = focusSel && q(focusSel);
    if (f) { if (!f.hasAttribute('tabindex') && f.tagName !== 'TEXTAREA') f.setAttribute('tabindex', '-1');
             try { f.focus({preventScroll: true}); } catch (e) { f.focus(); } }
    T.nodes.forEach(function(n){ if (n.parentNode) n.parentNode.removeChild(n); });
    if (markDone) { DONE[C.key] = 1; try { W.localStorage.setItem(C.key, '1'); } catch (e) {} }
    if (W.__nsTour === T) W.__nsTour = null;
  };

  // 用按钮关掉引导后 0.4 秒内的点击 / 触摸一律吞掉:节点是在点击处理里同步拆掉的,连点的第二下会落到
  // 下面的页面上(360px 手机上恰好是页脚的 mailto 链接,第 1 轮中途弹出邮件 App;别处则让刚聚焦的输入框失焦)
  function swallowNextTap(){
    var evs = ['pointerdown', 'mousedown', 'touchstart', 'click'];
    function eat(e){ e.preventDefault(); e.stopPropagation(); }
    evs.forEach(function(ev){ D.addEventListener(ev, eat, {capture: true, passive: false}); });
    setTimeout(function(){ evs.forEach(function(ev){ D.removeEventListener(ev, eat, {capture: true}); }); }, 400);
  }

  // 等目标渲染出来(Streamlit 分批画元素),最多等 6 秒
  var tries = 0;
  (function wait(){
    if (!T.alive) return;
    var first = q(C.steps[0].sel), last = q(C.steps[C.steps.length - 1].sel);
    if (first && last && first.getBoundingClientRect().height > 0) {
      later(tour, reduce ? 0 : 450);
      return;
    }
    if (++tries > 60) { T.stop(false); return; }
    later(wait, 100);
  })();

  // 第 1 轮:聚光圈 + 气泡,逐步
  function tour(){
    var f0 = q(C.focus);   // 开演前被试已经点进输入框 / 写了字:不再打断
    if (f0 && (D.activeElement === f0 || f0.value)) { T.stop(true); return; }
    var spot = el('div', 'ns-tour-spot');
    var tip = el('div', 'ns-tour-tip');
    tip.setAttribute('role', 'dialog');
    tip.setAttribute('aria-labelledby', 'ns-tour-text');
    var idx = 0, PAD = 8, GAP = 14, M = 12;

    function target(){ return q(C.steps[idx].sel); }

    function render(){
      var last = idx === C.steps.length - 1;
      tip.textContent = '';
      el('div', 'ns-tour-count', tip).textContent = (idx + 1) + ' / ' + C.steps.length;
      var txt = el('div', 'ns-tour-text', tip);
      txt.id = 'ns-tour-text';
      txt.textContent = C.steps[idx].text;
      var row = el('div', 'ns-tour-row', tip);
      var skip = el('button', 'ns-tour-skip', row);
      skip.type = 'button'; skip.textContent = C.skip;
      skip.onclick = function(){ T.stop(true, C.home, true); };
      var nx = el('button', 'ns-tour-next', row);
      nx.type = 'button'; nx.textContent = last ? C.go : C.next;
      nx.onclick = function(){
        if (last) T.stop(true, C.focus, true);
        else { idx += 1; show(true); }
      };
      tip.classList.remove('is-in'); void tip.offsetWidth; tip.classList.add('is-in');
      if (!editing()) { try { nx.focus({preventScroll: true}); } catch (e) {} }   // 不从输入框里抢焦点
    }

    // 只有换步时才让聚光圈 / 气泡带过渡地移过去;平时每帧跟着目标走,不能带过渡(否则滚动时拖影)
    // 目标 + 气泡能同时放进屏幕就居中;放不下(手机横屏、320px 小屏)就把目标滚到屏幕顶部、气泡放在它下面 ——
    // 否则气泡只能贴在目标身上,盖住要读的题目 / 条件说明
    function scrollTo(n){
      var r = n.getBoundingClientRect(), vh = W.innerHeight;
      var fits = (vh - r.height) / 2 >= PAD + GAP + tip.offsetHeight + M;   // 居中后上 / 下单侧放得下气泡
      var main = q('[data-testid="stMain"]') || q('.stMain');
      try {
        if (fits || !main) n.scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'center'});
        else main.scrollBy({top: r.top - (M + PAD), behavior: reduce ? 'auto' : 'smooth'});
      } catch (e) { n.scrollIntoView(); }
    }

    function show(moving){
      var n = target();
      if (!n) { T.stop(true); return; }
      if (moving && !reduce) {
        spot.classList.add('is-moving'); tip.classList.add('is-moving');
        later(function(){ spot.classList.remove('is-moving'); tip.classList.remove('is-moving'); }, 650);
      }
      render();      // 先换好气泡内容(scrollTo 要用它的高度)
      scrollTo(n);
    }

    function place(){
      if (!T.alive) return;
      var n = target();
      // 输入框没了 = 已经离开创意页(题目卡在本轮各阶段都在,不能只看当前这一步的目标)
      if (!q(C.focus) || !n || !D.body.contains(n)) { T.stop(true); return; }
      var r = n.getBoundingClientRect();
      var vw = D.documentElement.clientWidth, vh = W.innerHeight;
      spot.style.top = (r.top - PAD) + 'px';
      spot.style.left = (r.left - PAD) + 'px';
      spot.style.width = (r.width + PAD * 2) + 'px';
      spot.style.height = (r.height + PAD * 2) + 'px';
      var tw = tip.offsetWidth, th = tip.offsetHeight;
      var below = r.bottom + PAD + GAP, above = r.top - PAD - GAP - th;
      var top, side;
      if (below >= M && below + th <= vh - M) { top = below; side = 'below'; }
      else if (above >= M && above + th <= vh - M) { top = above; side = 'above'; }
      else {   // 上下都放不下(短屏 / 目标滚出屏):贴在离目标中心较远的那条边上
        top = (r.top + r.height / 2 > vh / 2) ? M : vh - th - M; side = 'inside';
      }
      top = Math.min(Math.max(top, M), Math.max(M, vh - th - M));   // 无论如何留在屏内
      var left = Math.min(Math.max(M, r.left), Math.max(M, vw - tw - M));
      tip.style.top = top + 'px';
      tip.style.left = left + 'px';
      tip.setAttribute('data-side', side);
      var ax = Math.min(Math.max(18, r.left + Math.min(r.width, 160) / 2 - left), tw - 18);
      tip.style.setProperty('--ax', ax + 'px');
      T.raf = requestAnimationFrame(place);
    }

    on(D, 'keydown', function(e){ if (e.key === 'Escape') T.stop(true, C.home); });   // 键盘关掉:没有「第二下」要吞
    // 直接点进输入框(没走完引导)= 已经知道该在哪写了
    on(D, 'focusin', function(e){ var f = q(C.focus); if (f && e.target === f) T.stop(true); });
    render();
    place();                                  // 先在目标上就位,再淡入遮罩 —— 不从左上角飞进来
    void spot.offsetWidth;
    spot.classList.add('is-on');
    scrollTo(target());
  }
})();
"""


def intent_tour() -> None:
    """写创意这一步的新手引导:只在第 1 轮、每位被试一次(见模块说明)。"""
    if (st.session_state.get("round_idx") or 1) != 1:
        return
    pid = st.session_state.get("participant_id") or "anon"
    cfg = {
        "key": f"ns_tour_{pid}",
        "steps": [
            {"sel": ".ns-topic", "text": t("tour.read")},
            {"sel": ".st-key-ns_flow", "text": t("tour.flow")},          # 各条件的操纵说明(round_common._render_intent)
            {"sel": ".st-key-_intent_input", "text": t("tour.write")},   # 整个输入区(含标签),只框输入框会切到标签
        ],
        "focus": ".st-key-_intent_input textarea",
        "home": ".ns-topic",   # 跳过 / Esc 之后焦点落回题目卡(不弹手机键盘)
        "next": t("tour.next"), "skip": t("tour.skip"), "go": t("tour.go"),
    }
    st.html('<span class="ns-util"></span><script>' + _JS.replace("__CFG__", json.dumps(cfg)) + "</script>",
            unsafe_allow_javascript=True)
