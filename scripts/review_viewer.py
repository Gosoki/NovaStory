#!/usr/bin/env python3
"""研究员用：同屏对照每位被试每一轮「输入了什么 / AI 生成了什么 / 最后选了什么」。

只读主库，生成一个自包含 HTML（分镜图以 base64 内嵌）。不调 API、不写库、不碰被试端代码。
产物含被试原话，默认写到 data/analysis/review/（已 gitignore，与原始数据同隐私级别）——
**不要发布到任何外部服务**：同意书只写了数据会给 OpenAI。

用法:
    .venv/bin/python scripts/review_viewer.py
    .venv/bin/python scripts/review_viewer.py --db data/novastory.db --out data/analysis/review/review.html
然后在本机浏览器打开生成的 HTML（VS Code 里右键 → Download 到本地再打开）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import prereg, v3  # noqa: E402
from core import shots  # noqa: E402

DEFAULT_DB = ROOT / "data" / "novastory.db"
DEFAULT_OUT = ROOT / "data" / "analysis" / "review" / "review.html"
IMG_DIR = ROOT / "data" / "storyboard_images"

DIM_LABEL = {"psychology": "人物心理", "turning_point": "转折点", "key_shot": "关键镜头",
             "tone": "基调", "detail": "细节", "ending": "结局", "sound": "声音"}


def _dim(slug):
    """已知维度译成中文；其余是 AI 针对具体故事临时起的维度名，原样显示并注明。"""
    return DIM_LABEL.get(slug) or f"{slug}（AI 自拟维度）"


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default


def _labels() -> dict:
    """题目原文与选项标签都从 zh.json 读，和被试当时看到的保持一致。"""
    d = json.loads((ROOT / "i18n" / "locales" / "zh.json").read_text(encoding="utf-8"))
    return {"q": d.get("q", {}), "scr": d.get("screening", {}), "fs": d.get("final_survey", {})}


def _opt(lab: dict, sec: str, key: str, idx):
    if idx is None:
        return None
    try:
        return lab[sec].get(f"{key}_opt{int(idx) + 1}", str(idx))
    except (TypeError, ValueError):
        return str(idx)


def _img_b64(pid: int, rnd: int, attempt: str | None) -> dict[int, str]:
    """{镜头号: data URI}。优先用问卷记下的 attempt 目录（= 被试答问卷时看到的那组图）。"""
    base = IMG_DIR / f"{pid}_{rnd}"
    if not base.is_dir():
        return {}
    d = base / attempt if attempt and (base / attempt).is_dir() else None
    if d is None:  # 退路：取最新的 attempt 目录
        subs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        d = subs[-1] if subs else None
    out = {}
    for f in sorted(d.glob("shot*.jpg")) if d else []:
        try:
            k = int(f.stem.replace("shot", ""))
        except ValueError:
            continue
        out[k] = "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()
    return out


def _session_minutes(con, pid: int):
    t0 = con.execute("SELECT MIN(ts) FROM events WHERE participant_id=? AND type='consent_shown'",
                     (pid,)).fetchone()[0]
    t1 = con.execute("SELECT MAX(ts) FROM events WHERE participant_id=? AND type LIKE 'final_survey%'",
                     (pid,)).fetchone()[0]
    if not (t0 and t1):
        return None
    return round((dt.datetime.fromisoformat(t1) - dt.datetime.fromisoformat(t0)).total_seconds() / 60, 1)


def build(db: Path) -> dict:
    lab = _labels()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    included = set(v3.included_participants(db))

    people = []
    for (pid, seq, lang, status, created, scr_j, dem_j, fs_j, att_ok, att_raw) in con.execute(
            "SELECT id, seq, lang, status, created_at, screening_json, demographics_json,"
            " final_survey_json, attention_ok, attention_raw FROM participants WHERE passed=1 ORDER BY id"):
        scr, dem, fs = _loads(scr_j, {}), _loads(dem_j, {}), _loads(fs_j, {})
        released = bool(scr.get("released_at"))
        if scr.get("dev") and not released:
            continue  # 研究员自己的测试行，不是被试

        trials = {r[0]: r for r in con.execute(
            "SELECT round_idx, condition, topic_json, intent_statement, guidance_json, revision_requests,"
            " script_versions, final_output, t_read_intent, t_pregen, t_postgen, t_llm_wait, t_total"
            " FROM trials WHERE participant_id=?", (pid,))}
        qs = {r[0]: r for r in con.execute(
            "SELECT round_idx, ownership_json, soa_json, tlx_json, intent_violation, imagine_match,"
            " satisfaction, ai_q_quality, ai_q_amount, ai_q_best_json, shot_annotations_json, attempt"
            " FROM questionnaires WHERE participant_id=?", (pid,))}
        cond_of = {k: t[1] for k, t in trials.items()}

        rounds = []
        for rnd in (1, 2, 3):
            t, q = trials.get(rnd), qs.get(rnd)
            if t is None:
                rounds.append({"round": rnd, "missing": True})
                continue
            topic = _loads(t[2], {})
            loc = lambda k: (topic.get(k) or {}).get(lang or "zh") if isinstance(topic.get(k), dict) else topic.get(k)

            helpful = {h.get("idx") for h in _loads(q[9], []) if isinstance(h, dict)} if q else set()
            guidance = None
            g = _loads(t[4], None)
            if g:
                guidance, i = [], 0
                for gr in g.get("rounds", []):
                    for it in gr.get("items", []):
                        guidance.append({
                            "g_round": gr.get("round"), "dim": _dim(it.get("dimension")),
                            "question": it.get("question"), "options": it.get("options") or [],
                            "chosen": it.get("chosen"), "custom": bool(it.get("is_custom")),
                            "ai": bool(it.get("ai_decided")), "fallback": bool(it.get("fallback")),
                            "helpful": i in helpful})
                        i += 1

            versions = [{"v": v.get("v"), "author": v.get("author"), "text": v.get("text")}
                        for v in _loads(t[6], [])]
            reqs = [r.get("text") if isinstance(r, dict) else str(r) for r in _loads(t[5], [])]
            final = t[7] or ""
            tags = {a.get("shot"): a.get("tag") for a in _loads(q[10], []) if isinstance(a, dict)} if q else {}
            imgs = _img_b64(pid, rnd, q[11] if q else None)
            shot_list = [{"idx": s["idx"], "visual": s.get("visual"), "duration": s.get("duration"),
                          "shot_type": s.get("shot_type"), "audio": s.get("audio"),
                          "tag": tags.get(s["idx"]), "img": imgs.get(s["idx"])}
                         for s in shots.parse_shots(final)]

            qd = None
            if q:
                own, soa, tlx = _loads(q[1], {}), _loads(q[2], {}), _loads(q[3], {})
                qd = {"own1": own.get("own1"), "own2": own.get("own2"), "own3": own.get("own3"),
                      "soa1": soa.get("soa1"), "soa2": soa.get("soa2"), "tlx1": tlx.get("tlx1"),
                      "imagine": q[5], "violation": q[4], "satisfaction": q[6],
                      "ai_q_quality": q[7], "ai_q_amount": q[8]}
                block = [qd[k] for k in ("own1", "own2", "own3", "soa1", "soa2", "tlx1") if qd[k] is not None]
                straight = len(block) >= 4 and len(set(block)) == 1  # 与 analysis/v3.py 同一定义
            else:
                straight = False

            rounds.append({
                "round": rnd, "cond": t[1],
                "topic": {"title": loc("title"), "scenario": loc("scenario"), "choices": loc("choices")},
                "intent": t[3], "guidance": guidance, "revisions": reqs if t[1] == "D" else None,
                "versions": versions, "final": final,
                "final_is_v1": bool(versions) and (versions[0].get("text") or "").strip() == final.strip(),
                "shots": shot_list, "q": qd, "straightline": straight,
                "t": {"read_intent": t[8], "pregen": t[9], "postgen": t[10], "llm_wait": t[11], "total": t[12]},
            })

        def fs_round(key):
            r = fs.get(key)
            return {"round": r, "cond": cond_of.get(r)} if isinstance(r, int) else None

        crit = prereg.novice_criteria(scr)
        people.append({
            "id": pid, "seq": seq, "lang": lang, "status": status, "created": (created or "")[:16],
            "included": pid in included, "released": released,
            "attention_ok": att_ok, "attention_raw": att_raw,
            "demo": {"年龄": _opt(lab, "scr", "age", dem.get("age_idx")),
                     "性别": _opt(lab, "scr", "gender", dem.get("gender_idx")),
                     "AI 使用频率": _opt(lab, "scr", "ai_freq", dem.get("ai_freq_idx"))},
            "traits": {"发布过短视频": _opt(lab, "scr", "published", scr.get("published_idx")),
                       "AI 创作经验": _opt(lab, "scr", "aiexp", scr.get("aiexp_idx")),
                       "脚本熟练度自评": scr.get("self_rating"), "写脚本的把握": scr.get("script_confidence"),
                       "信任 AI": scr.get("trust"), "特质所有权": scr.get("own_trait"),
                       "术语小测答对": scr.get("quiz_correct")},
            "novice": {"n": sum(bool(v) for v in crit.values()), "is": prereg.is_novice(scr)},
            "fs": {"最喜欢": fs_round("pref_round"), "最接近心里想的": fs_round("closest_round"),
                   "以后还想用": fs_round("reuse_round"), "最费劲": fs_round("effort_round"),
                   "整体满意": fs.get("overall_sat"),
                   "察觉": _opt(lab, "fs", "q_noticed", fs.get("noticed_idx")),
                   "评论": (fs.get("comment") or "").strip() or None} if fs else None,
            "session_min": _session_minutes(con, pid),
            "rounds": rounds,
        })
    con.close()

    q = lab["q"]
    items = [("own1", "所有权①"), ("own2", "所有权②"), ("own3", "所有权③"), ("soa1", "主导感①"),
             ("soa2", "主导感②"), ("tlx1", "负荷"), ("imagine", "接近所想"), ("violation", "违背本意"),
             ("satisfaction", "满意"), ("ai_q_quality", "提问质量"), ("ai_q_amount", "提问数量")]
    return {
        "generated": dt.datetime.now().isoformat(timespec="minutes"),
        "items": [{"key": k, "short": s, "full": q.get(k, "")} for k, s in items],
        "tags": {"mine": q.get("tag_mine", "我的"), "ai_ok": q.get("tag_ai_ok", "AI·认可"),
                 "ai_against": q.get("tag_ai_against", "违背本意")},
        "people": people,
    }


HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NovaStory 逐轮对照</title>
<style>
:root{--bg:#f6f5f2;--panel:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e3e1dc;--soft:#f0eee9;
--C:#5b6b82;--D:#c2760c;--E:#0f8a7e;--mine:#1f7a4d;--ok:#6b6b70;--against:#b3261e;--warn:#9a5b00;
--mark:#fff3c4;font-family:-apple-system,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--panel:#1f1f22;--ink:#ececef;--mute:#9a9aa2;
--line:#34343a;--soft:#28282c;--mark:#4a3f10;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.6}
header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);
padding:10px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0}header .meta{color:var(--mute);font-size:12px}
header label{font-size:13px;cursor:pointer;user-select:none}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:0;background:transparent;color:var(--ink);padding:3px 10px;font:inherit;font-size:13px;cursor:pointer}
.seg button.on{background:var(--ink);color:var(--panel)}
input[type=search]{border:1px solid var(--line);border-radius:6px;padding:3px 8px;background:var(--panel);color:var(--ink);width:110px}
.wrap{display:flex;min-height:calc(100vh - 50px)}
nav{width:250px;flex:none;border-right:1px solid var(--line);background:var(--panel);overflow-y:auto;
position:sticky;top:50px;height:calc(100vh - 50px)}
nav .p{padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer}
nav .p:hover{background:var(--soft)}nav .p.on{background:var(--soft);box-shadow:inset 3px 0 0 var(--ink)}
nav .p .top{display:flex;justify-content:space-between;align-items:center}
nav .p .id{font-weight:600}nav .p.ex{opacity:.6}
.ord b{display:inline-block;width:18px;text-align:center;border-radius:3px;color:#fff;font-size:12px;margin-left:2px}
.bC{background:var(--C)}.bD{background:var(--D)}.bE{background:var(--E)}
.badges{margin-top:3px;display:flex;flex-wrap:wrap;gap:4px}
.bd{font-size:11px;padding:0 6px;border-radius:9px;border:1px solid var(--line);color:var(--mute)}
.bd.w{border-color:var(--warn);color:var(--warn)}.bd.x{border-color:var(--against);color:var(--against)}
main{flex:1;min-width:0;padding:14px 16px 60px}
.sum{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:14px}
.sum h2{margin:0 0 6px;font-size:17px}
.kv{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:13px}.kv span{color:var(--mute)}
.cols{display:grid;grid-template-columns:repeat(3,minmax(300px,1fr));gap:12px;overflow-x:auto}
.col{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;min-width:0}
.col>.hd{padding:9px 12px;color:#fff;font-weight:600;display:flex;justify-content:space-between;gap:8px}
.col>.hd small{font-weight:400;opacity:.9}
.sec{padding:9px 12px;border-top:1px solid var(--line)}
.sec h4{margin:0 0 5px;font-size:12px;color:var(--mute);font-weight:600;letter-spacing:.02em}
.intent{background:var(--soft);border-radius:6px;padding:7px 9px;white-space:pre-wrap}
.topic{font-size:12px;color:var(--mute)}
.gq{margin-bottom:9px}.gq .q{font-size:13px}.gq .dim{font-size:11px;color:var(--E);font-weight:600}
.gq ul{margin:2px 0 0;padding-left:18px;font-size:12.5px;line-height:1.45;color:var(--mute)}
.gq li.ch{color:var(--ink);font-weight:600;list-style:"✔ "}
.ans{font-size:13px;margin-top:3px;padding:3px 7px;border-radius:5px;display:inline-block}
.ans.cu{background:#e6f4ea;color:#1f5e37}.ans.ai{background:var(--soft);color:var(--mute)}
@media (prefers-color-scheme:dark){.ans.cu{background:#1e3a2a;color:#9fd9b3}}
.star{color:#c99400;font-size:12px}
.req{background:#fdf1e0;border-left:3px solid var(--D);padding:4px 8px;margin:4px 0;border-radius:3px;font-size:13px}
@media (prefers-color-scheme:dark){.req{background:#3a2c14}}
.none{color:var(--mute);font-size:13px;font-style:italic}
details{margin-top:4px}summary{cursor:pointer;font-size:12px;color:var(--mute)}
.ver{white-space:pre-wrap;font-size:12px;background:var(--soft);border-radius:5px;padding:6px 8px;margin:5px 0}
.ver .who{font-weight:600;font-size:11px;color:var(--mute)}
.shot{border:1px solid var(--line);border-radius:7px;margin-bottom:6px;overflow:hidden;
display:grid;grid-template-columns:96px 1fr;align-items:start}
.shot.noimg{grid-template-columns:1fr}
.shot img{width:96px;height:96px;object-fit:cover;display:block;cursor:zoom-in;background:var(--soft)}
.shot .b{padding:5px 8px;font-size:13px;min-width:0}.shot .m{font-size:11px;color:var(--mute)}
.shot .tag{display:inline-block;font-size:11px;font-weight:600;padding:0 7px;border-radius:9px;color:#fff;margin-bottom:2px}
.t-mine{background:var(--mine)}.t-ai_ok{background:var(--ok)}.t-ai_against{background:var(--against)}
.hl{background:var(--mark);border-radius:2px}
.qt{width:100%;border-collapse:collapse;font-size:12px}.qt td{padding:2px 0;vertical-align:middle}
.qt td:first-child{width:62px;color:var(--mute)}.qt td:last-child{width:22px;text-align:right;font-variant-numeric:tabular-nums}
.bar{height:8px;background:var(--soft);border-radius:4px;position:relative}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--ink);opacity:.55}
.bar::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;border-left:1px dashed var(--mute)}
.flag{font-size:12px;color:var(--warn);margin-top:4px}
.tm{font-size:12px;color:var(--mute)}
.miss{padding:30px 12px;text-align:center;color:var(--mute)}
dialog{border:0;padding:0;background:transparent;max-width:95vw}dialog img{max-width:95vw;max-height:90vh;border-radius:8px}
dialog::backdrop{background:rgba(0,0,0,.75)}
.help{font-size:12px;color:var(--mute)}
</style></head><body>
<header>
  <h1>NovaStory 逐轮对照</h1>
  <span class="meta" id="meta"></span>
  <span class="seg" id="ord"><button data-v="round" class="on">做题顺序</button><button data-v="cond">按条件 C·D·E</button></span>
  <label title="逐字面匹配被试意图里的两字片段；「主角」这类常见词也会被标出，只作阅读辅助，不是测量"><input type="checkbox" id="hl"> 高亮意图原词</label>
  <label><input type="checkbox" id="ex"> 显示未纳入（中途退出）</label>
  <input type="search" id="q" placeholder="被试 #">
  <span class="help">↑↓ 切换被试</span>
</header>
<div class="wrap"><nav id="list"></nav><main id="main"></main></div>
<dialog id="dlg"><img id="dlgimg" alt=""></dialog>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let state={order:'round',hl:false,ex:false,q:'',cur:null};
const $=id=>document.getElementById(id);
const COND={C:'C 一次成型',D:'D 生成后修改',E:'E 事前引导'};

// 意图原词高亮：取意图里的汉字/字母二元组，在文本里标出出现的片段
function grams(t){const s=(t||'').replace(/[^\p{L}\p{N}]/gu,'');const g=new Set();for(let i=0;i<s.length-1;i++)g.add(s.slice(i,i+2));return g}
function mark(text,g){
  text=text||'';if(!state.hl||!g||!g.size)return esc(text);
  const hit=new Array(text.length).fill(false);const idx=[];
  for(let i=0;i<text.length;i++)if(/[\p{L}\p{N}]/u.test(text[i]))idx.push(i);
  for(let k=0;k<idx.length-1;k++){const a=idx[k],b=idx[k+1];if(g.has(text[a]+text[b])){hit[a]=hit[b]=true}}
  let out='',on=false;for(let i=0;i<text.length;i++){if(hit[i]&&!on){out+='<span class="hl">';on=true}
    if(!hit[i]&&on&&/[\p{L}\p{N}]/u.test(text[i])){out+='</span>';on=false}out+=esc(text[i])}
  return out+(on?'</span>':'');
}
function visible(){return D.people.filter(p=>(state.ex||p.included)&&(!state.q||String(p.id).includes(state.q)))}
function ordStr(p){return p.rounds.map(r=>r.missing?'<b style="background:var(--line)">·</b>':`<b class="b${r.cond}">${r.cond}</b>`).join('')}
function nStraight(p){return p.rounds.filter(r=>r.straightline).length}

function renderList(){
  const ps=visible();
  $('meta').textContent=`纳入分析 ${D.people.filter(p=>p.included).length} 人 · 当前列出 ${ps.length} 人 · 生成于 ${D.generated}`;
  if(!ps.find(p=>p.id===state.cur))state.cur=ps.length?ps[0].id:null;
  $('list').innerHTML=ps.map(p=>{
    const b=[];
    if(!p.included)b.push(`<span class="bd">${p.released?'已释放':'未纳入'}</span>`);
    if(p.attention_ok===0)b.push(`<span class="bd x">注意力✗ 答${esc(p.attention_raw)}</span>`);
    const s=nStraight(p);if(s)b.push(`<span class="bd w">六题全同×${s}</span>`);
    if(p.novice.is)b.push('<span class="bd">新手5/5</span>');
    return `<div class="p ${p.id===state.cur?'on':''} ${p.included?'':'ex'}" data-id="${p.id}">
      <div class="top"><span class="id">#${p.id} <span class="tm">seq${p.seq}</span></span><span class="ord">${ordStr(p)}</span></div>
      <div class="badges">${b.join('')}</div></div>`}).join('')||'<div class="miss">没有匹配的被试</div>';
  document.querySelectorAll('nav .p').forEach(el=>el.onclick=()=>{state.cur=+el.dataset.id;renderList();renderMain()});
  const on=document.querySelector('nav .p.on');if(on)on.scrollIntoView({block:'nearest'});
}

function fsTxt(v){if(!v)return '—';if(typeof v==='object')return v.round?`第${v.round}轮（${v.cond||'?'}）`:'—';return esc(v)}

function renderSummary(p){
  const kv=o=>Object.entries(o).map(([k,v])=>`<div><span>${k}</span> ${v==null?'—':esc(v)}</div>`).join('');
  const fs=p.fs?Object.entries(p.fs).filter(([k])=>k!=='评论').map(([k,v])=>`<div><span>${k}</span> ${fsTxt(v)}</div>`).join(''):'<div><span>总问卷</span> 未填</div>';
  return `<div class="sum"><h2>#${p.id} <span class="ord">${ordStr(p)}</span>
    <span class="tm">seq ${p.seq} · 入组 ${esc(p.created)} · 全程 ${p.session_min??'—'} 分钟 ·
    ${p.included?'纳入分析':(p.released?'已释放（中途退出）':'未纳入')} ·
    注意力题 ${p.attention_ok===1?'✓':p.attention_ok===0?`✗（应选 2，答 ${esc(p.attention_raw)}）`:'—'}</span></h2>
    <div class="kv">${kv(p.demo)}<div><span>新手判定</span> ${p.novice.n}/5${p.novice.is?'（新手）':''}</div>${kv(p.traits)}</div>
    <div class="kv" style="margin-top:6px">${fs}</div>
    ${p.fs&&p.fs['评论']?`<div class="intent" style="margin-top:6px">💬 ${esc(p.fs['评论'])}</div>`:''}</div>`;
}

function renderGuidance(r,g){
  if(r.cond!=='E')return '';
  if(!r.guidance||!r.guidance.length)return `<div class="sec"><h4>② 引导问答</h4><div class="none">无记录</div></div>`;
  const nCu=r.guidance.filter(x=>x.custom).length,nAi=r.guidance.filter(x=>x.ai).length;
  return `<div class="sec"><h4>② 引导问答 · ${r.guidance.length} 题 · 自填 ${nCu} · 交给 AI ${nAi}</h4>`+
    r.guidance.map(x=>{
      const opts=x.options.map(o=>`<li class="${!x.custom&&!x.ai&&o===x.chosen?'ch':''}">${esc(o)}</li>`).join('');
      const ans=x.custom?`<div class="ans cu">✍️ 自填：${mark(x.chosen,g)}</div>`:x.ai?'<div class="ans ai">🤖 交给 AI 决定</div>':'';
      return `<div class="gq"><div class="dim">${esc(x.dim)}${x.helpful?' <span class="star">★ 被试勾选「有帮助」</span>':''}</div>
        <div class="q">${esc(x.question)}</div><ul>${opts}</ul>${ans}</div>`}).join('')+'</div>';
}

function renderVersions(r){
  const vs=r.versions||[];
  if(vs.length<=1)return '';
  let ai=0;const reqs=r.revisions||[];
  const body=vs.map((v,i)=>{
    let pre='';
    if(v.author==='ai'&&i>0){pre=reqs[ai]?`<div class="req">🗨 修改请求：${esc(reqs[ai])}</div>`:'';ai++}
    const who=v.author==='ai'?'AI 生成':v.author==='user_edit'?'被试手改':esc(v.author);
    return `${pre}<div class="ver"><div class="who">v${v.v} · ${who}${i===vs.length-1?' · 最终提交':''}</div>${esc(v.text)}</div>`}).join('');
  return `<details><summary>查看全部 ${vs.length} 个版本（含修改过程）</summary>${body}</details>`;
}

function renderRound(r,p){
  if(r.missing)return `<div class="col"><div class="hd" style="background:var(--line);color:var(--mute)">第${r.round}轮</div><div class="miss">未开始</div></div>`;
  const g=grams(r.intent);
  const revs=r.cond==='D'?`<div class="sec"><h4>② 修改请求</h4>${r.revisions&&r.revisions.length?r.revisions.map(x=>`<div class="req">🗨 ${esc(x)}</div>`).join(''):'<div class="none">没有提修改，直接提交了 AI 的第一版</div>'}</div>`:'';
  const nv=(r.versions||[]).length;
  const status=r.final_is_v1?'= AI 第 1 版原样提交，没有改动':`经过 ${nv} 个版本`;
  const sh=(r.shots||[]).map(s=>`<div class="shot ${s.img?'':'noimg'}">${s.img?`<img src="${s.img}" alt="镜头${s.idx}" title="点击看大图" loading="lazy">`:''}
    <div class="b">${s.tag?`<span class="tag t-${s.tag}">被试标注：${esc(D.tags[s.tag]||s.tag)}</span>`:'<span class="m">（未标注）</span>'}
    <div class="m">镜头 ${s.idx} · ${esc(s.duration)} · ${esc(s.shot_type)}</div>
    <div>${mark(s.visual,g)}</div>${s.audio?`<div class="m">台词/音效：${mark(s.audio,g)}</div>`:''}</div></div>`).join('')
    ||`<div class="ver">${mark(r.final,g)}</div>`;
  const q=r.q?`<table class="qt">${D.items.filter(it=>r.q[it.key]!=null).map(it=>{const v=r.q[it.key];
      return `<tr title="${esc(it.full)}"><td>${it.short}</td><td><div class="bar"><i style="width:${(v-1)/6*100}%"></i></div></td><td>${v}</td></tr>`}).join('')}</table>
      ${r.straightline?'<div class="flag">⚠ 本轮所有权/主导感/负荷六题全部同分</div>':''}`:'<div class="none">问卷未提交</div>';
  const t=r.t,f=x=>x==null?'—':Math.round(x)+'s';
  return `<div class="col"><div class="hd b${r.cond}"><span>第${r.round}轮 · ${COND[r.cond]}</span><small>${esc(r.topic.title)}</small></div>
    <div class="sec"><div class="topic">题面：${esc(r.topic.scenario)} ${esc(r.topic.choices)}</div></div>
    <div class="sec"><h4>① 被试写的意图</h4><div class="intent">${esc(r.intent)}</div></div>
    ${renderGuidance(r,g)}${revs}
    <div class="sec"><h4>③ 最终提交 · ${status}</h4>${sh}${renderVersions(r)}</div>
    <div class="sec"><h4>④ 本轮问卷（1–7，虚线 = 中点 4；悬停看原题）</h4>${q}</div>
    <div class="sec tm">⏱ 写意图 ${f(t.read_intent)}${r.cond==='E'?' · 引导 '+f(t.pregen):''} · 看稿到提交 ${f(t.postgen)} · 等 AI ${f(t.llm_wait)}</div></div>`;
}

function renderMain(){
  const p=D.people.find(x=>x.id===state.cur);
  if(!p){$('main').innerHTML='<div class="miss">没有选中的被试</div>';return}
  let rs=[...p.rounds];
  if(state.order==='cond')rs.sort((a,b)=>'CDE'.indexOf(a.cond||'Z')-'CDE'.indexOf(b.cond||'Z'));
  $('main').innerHTML=renderSummary(p)+`<div class="cols">${rs.map(r=>renderRound(r,p)).join('')}</div>`;
  document.querySelectorAll('.shot img').forEach(im=>im.onclick=()=>{$('dlgimg').src=im.src;$('dlg').showModal()});
}

$('dlg').onclick=()=>$('dlg').close();
document.querySelectorAll('#ord button').forEach(b=>b.onclick=()=>{state.order=b.dataset.v;
  document.querySelectorAll('#ord button').forEach(x=>x.classList.toggle('on',x===b));renderMain()});
$('hl').onchange=e=>{state.hl=e.target.checked;renderMain()};
$('ex').onchange=e=>{state.ex=e.target.checked;renderList();renderMain()};
$('q').oninput=e=>{state.q=e.target.value.trim();renderList();renderMain()};
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||$('dlg').open)return;
  if(e.key!=='ArrowDown'&&e.key!=='ArrowUp')return;
  const ps=visible(),i=ps.findIndex(p=>p.id===state.cur);
  const j=Math.max(0,Math.min(ps.length-1,i+(e.key==='ArrowDown'?1:-1)));
  if(ps[j]){state.cur=ps[j].id;renderList();renderMain();window.scrollTo(0,0);e.preventDefault()}
});
renderList();renderMain();
</script></body></html>"""


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="生成被试逐轮对照页（只读，不调 API）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    data = build(a.db)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    inc = sum(p["included"] for p in data["people"])
    print(f"✅ {a.out}  ({a.out.stat().st_size / 1e6:.1f} MB)")
    print(f"   纳入分析 {inc} 人 + 未纳入 {len(data['people']) - inc} 人（中途退出 / 已释放；研究员测试行已排除）")


if __name__ == "__main__":
    main()
