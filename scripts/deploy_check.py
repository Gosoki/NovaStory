#!/usr/bin/env python
"""部署就绪闸门 —— 公开采数前跑一遍,把「部署硬门禁」变成 🟢🟡🔴(不打印任何密钥值)。

检查(评估 §答辩火力点「部署硬门禁」+ docs/paper/07 §2):
  ① 研究员强密码(≠ 弱口令 nova、长度足够)
  ② 正式模型 = OpenAI 置顶(api_configs[0])且带日期快照(可复现)
  ③ 空库起跑(data/novastory.db 无真实被试)
  ④ config.toml runOnSave 关闭(dev 设置)
  ⑤ .gitignore 覆盖被试数据
  ⑥ 备份脚本就位
  ⑦ 分析依赖已装(研究员后台「数据分析」面板非惰性 import,缺一个就当场崩)
  ⑧ topics.json 可用(≥N_ROUNDS 题、三语齐、镜数/秒数合法)——写坏了闸门不拦的话,
     第一个被试**提交筛查之后**才撞 RuntimeError,那时被试行已入库、拉丁方 seq 已被消耗

用法: .venv/bin/python scripts/deploy_check.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # check_topics 要 import core.{config,state}
G, Y, R = "🟢", "🟡", "🔴"
rows: list[tuple[str, str, str]] = []


def add(flag: str, name: str, detail: str) -> None:
    rows.append((flag, name, detail))


def check_password(sec: dict) -> None:
    import os
    # 与 core/config.researcher_password 同一条优先级:secrets → 环境变量 → 'nova'
    pw = sec.get("researcher_password", "") or os.environ.get("NOVASTORY_RESEARCHER_PW", "")
    if not pw or pw == "nova":
        add(R, "研究员密码", "未设置或仍是弱口令 'nova' → 公开即数据泄露+去盲。secrets 设强口令。")
    elif len(pw) < 12:
        # 与 DEPLOY.md / docs/paper/07 的 ≥12 要求一致;以前 8 位就放行、6 位只给黄灯不阻断
        add(R, "研究员密码", f"只有 {len(pw)} 字符(<12)。登录框已藏到 ?admin=1 并按 IP 节流,但口令强度仍是最后一道门。")
    elif len(pw) < 16:
        add(Y, "研究员密码", f"{len(pw)} 字符,建议 ≥16。")
    else:
        add(G, "研究员密码", "已设置且足够长。")


# 正式采数用的模型(2026-08-03 选型实测:引导问题最具体、规格违反 0/20、延迟 p95 10.5s)。
# 开发/测试期用 gpt-4o-mini-2024-07-18 即可,公开前换成这个。
FORMAL_MODEL = "gpt-5.4-mini-2026-03-17"


def check_model(sec: dict) -> None:
    cfgs = sec.get("api_configs", [])
    if not cfgs:
        add(R, "正式模型置顶", "secrets 无 api_configs。"); return
    c0 = cfgs[0]
    is_openai = "openai.com" in c0.get("base_url", "") and bool(c0.get("api_key"))
    model = c0.get("model", "")
    pinned = bool(re.search(r"-20\d\d[-_]?\d\d[-_]?\d\d", model))  # 带日期快照
    if not is_openai:
        add(R, "正式模型置顶", f"api_configs[0] 不是 OpenAI(现 ={c0.get('name','?')})。"
                              "正式采数须把 OpenAI 置顶(B9 拍板)。")
    elif not pinned:
        add(Y, "模型快照钉死", f"OpenAI 已置顶但 model='{model}' 未钉日期快照 → 采数跨周可能撞模型漂移。"
                              "改用带日期的快照(如 gpt-4o-mini-YYYY-MM-DD)。")
    elif model != FORMAL_MODEL:
        add(Y, "正式模型选型", f"OpenAI 置顶且钉死快照 {model},但正式采数选定的是 "
                              f"{FORMAL_MODEL}(选型实测见 docs/paper/02 §8)。开发期用当前模型没问题;"
                              "公开采数前换过去,并复跑一次 B4 提示词校验。")
    else:
        add(G, "正式模型置顶", f"OpenAI 置顶且已是选定的正式模型快照 {model}。")


def check_clean_db() -> None:
    db = ROOT / "data" / "novastory.db"
    if not db.exists():
        add(G, "空库起跑", "无 data/novastory.db(将自动新建空库)。"); return
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        n = con.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        con.close()
    except Exception as e:  # noqa: BLE001
        add(Y, "空库起跑", f"data/novastory.db 存在但读取异常({e})。"); return
    if n > 0:
        add(R, "空库起跑", f"data/novastory.db 已有 {n} 条 participants(开发脏数据)→ "
                          "先归档到 data/archive/ 再从空库起跑,否则污染拉丁方计数/分析。")
    else:
        add(G, "空库起跑", "库存在但无 participants。")


def check_run_on_save() -> None:
    cfg = ROOT / ".streamlit" / "config.toml"
    if cfg.exists() and re.search(r"^\s*runOnSave\s*=\s*true", cfg.read_text(), re.M):
        add(Y, "config runOnSave", "runOnSave=true 是 dev 设置(任何文件落盘都会踢掉在答题的会话);"
                                   "若 systemd ExecStart 带 --server.runOnSave false 则命令行覆盖配置,可忽略。")
    else:
        add(G, "config runOnSave", "无 dev 自动重载设置。")


def check_perms() -> None:
    """secrets(OpenAI key)与被试库不该是 world-readable:共用主机上任何本地账号都能读。"""
    import stat
    bad = []
    for rel in (".streamlit/secrets.toml", "data/novastory.db", "data/novastory.db-wal", "data/llm.log"):
        p = ROOT / rel
        if p.exists() and (p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
            bad.append(rel)
    if bad:
        add(R if ".streamlit/secrets.toml" in bad else Y, "文件权限",
            f"{' · '.join(bad)} 对同组/其他用户可读 → chmod 600(目录 chmod 700 data)。")
    else:
        add(G, "文件权限", "secrets 与被试数据仅属主可读。")


def check_gitignore() -> None:
    gi = ROOT / ".gitignore"
    txt = gi.read_text() if gi.exists() else ""
    if "data/*.db" in txt:
        add(G, "数据 gitignore", "被试数据(data/*.db)已 gitignore。")
    else:
        add(R, "数据 gitignore", ".gitignore 未覆盖 data/*.db → 被试数据可能被提交进仓库。")


def check_analysis_deps() -> None:
    """views/analysis_panel.py 的 `from analysis import ...` 在任何 try 之外,figures 又在
    模块级 import matplotlib、stats 模块级 import scipy —— 缺一个,研究员一点开「数据分析」
    就是 ImportError 崩页(且此前 .venv 里从未装过这批依赖)。让闸门先说,而不是现场炸。"""
    missing = [m for m in ("numpy", "scipy", "pandas", "statsmodels", "matplotlib")
               if importlib.util.find_spec(m) is None]
    if missing:
        add(R, "分析依赖", f"缺 {', '.join(missing)} → 研究员后台「数据分析」面板一点即崩"
                          "(ImportError)。装:uv pip install -r analysis/requirements-analysis.txt(本 venv 无 pip)")
    else:
        add(G, "分析依赖", "numpy/scipy/pandas/statsmodels/matplotlib 齐备,分析面板可用。")


def check_baseline(sec: dict) -> None:
    """机器基线(Δ 的零点)必须由**正式采数的那个模型快照**生成。

    这是全流程里少数**事后补不回来**的一件:数据采完再想补基线,当时的模型可能已下线,
    而 Δ = sim(创意,终稿) − sim(创意,基线质心) 的零点就废了。embed.py 现在会硬失败,
    但那是分析阶段才发现——太晚。这里提前到部署闸门。"""
    base = ROOT / "data" / "baseline"
    files = sorted(base.glob("topic*.jsonl")) if base.exists() else []
    if not files:
        add(Y, "机器基线", "无 data/baseline/ → 保真复合的 embedding 腿(占一半权重)拿不到。"
                          "正式模型快照定下来后跑 `make baseline`;基线**必须与采数同模型**,事后补不回来。")
        return
    # 逐行读,不只看首行:中途换过配置续跑的文件,首行是干净的、后面才是脏的。
    # 顺便一趟数出每题份数与空稿数。
    from analysis import prereg
    from core import config as _cfg

    models, langs, counts, empties = set(), set(), {}, {}
    for f in files:
        n = n_empty = 0
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                add(R, "机器基线", f"{f.name} 有无法解析的行 → 删掉重跑 make baseline。")
                return
            n += 1
            models.add(rec.get("model") or "?")
            langs.add(rec.get("lang") or "?")     # 旧文件没有这个字段,会记成 "?"
            if not (rec.get("text") or "").strip():
                n_empty += 1
        counts[f.name], empties[f.name] = n, n_empty

    cfg0 = (sec.get("api_configs") or [{}])[0]
    want = cfg0.get("model", "")
    short = {k: v for k, v in counts.items() if v < prereg.MIN_BASELINE_PER_TOPIC}
    blank = {k: v for k, v in empties.items() if v}

    if len(files) < _cfg.N_ROUNDS:
        # 只生成了一部分题:embed 会对缺题的被试整轮拿不到 Δ,而绿灯会让人以为已经齐了。
        have = {f.name for f in files}
        add(R, "机器基线", f"只有 {len(files)}/{_cfg.N_ROUNDS} 题有基线(缺 "
                          f"{[f'topic{i}.jsonl' for i in range(_cfg.N_ROUNDS) if f'topic{i}.jsonl' not in have]})"
                          " → 补跑 make baseline。")
    elif blank:
        # 空稿会把该题质心整体拉偏,且下游不会报警。baseline_gen 现在拒绝写空行,
        # 这条是为更早生成的文件兜底。
        add(R, "机器基线", f"基线含空稿({blank}) → 这些行会污染 Δ 的零点,删掉对应文件重跑。")
    elif short:
        add(R, "机器基线", f"每题份数不足 {prereg.MIN_BASELINE_PER_TOPIC}(实际 {short}) → "
                          "质心样本太少,Δ 的零点不稳,补跑 make baseline。")
    elif len(models) > 1:
        add(R, "机器基线", f"data/baseline/ 混了多个模型 {sorted(models)} —— 删掉重跑 make baseline。")
    elif want and models != {want}:
        add(R, "机器基线", f"基线用 {sorted(models)} 生成,但正式模型是 '{want}' → Δ 的零点与产出不同源,"
                          "embed.py 会硬失败。用正式快照重跑 `make baseline`。")
    elif len(langs) > 1:
        # 三种语言的输出文件同名,拿 --lang zh 试跑过再补 ja 就会混进同一个质心。
        add(R, "机器基线", f"基线混了多种语言 {sorted(langs)} → 删掉重跑 make baseline。")
    elif langs and langs != {embed_lang()}:
        add(Y, "机器基线", f"基线语言 {sorted(langs)} != 分析基准 {embed_lang()}(旧文件无 lang 字段时显示 '?')。"
                          "被试是日本人,正式基线应当用 ja 生成。")
    else:
        add(G, "机器基线", f"{len(files)} 题基线就位,每题 {sorted(counts.values())} 份,"
                          f"模型 {sorted(models)} 与正式模型一致。")


def embed_lang() -> str:
    """analysis/embed.py 认定的基线语言 —— 只对同语言被试算 Δ。"""
    try:
        from analysis import embed
        return getattr(embed, "BASELINE_LANG", "ja")
    except Exception:  # noqa: BLE001 — 缺分析依赖时不该拖垮整张闸门
        return "ja"


def check_topics() -> None:
    """题库必须先于第一个被试就绪。

    `load_topics()` 对损坏文件是**静默回退种子主题**(不崩也不告警),所以研究员改坏了
    题目自己不会知道;而题数 < N_ROUNDS 时 `enter_intro` 抛 RuntimeError —— 那一刻
    被试行已经 INSERT、seq 已经消耗,留下一个永远走不完的孤儿被试。故在部署前拦。"""
    from core import config, state

    f = ROOT / "data" / "topics.json"
    if not f.exists():
        add(Y, "题库 topics.json", "文件不存在 → 首次启动会写入种子主题(3 题 ja/zh),"
                                   "确认那就是你要用的题目。")
        return
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:   # ValueError 含 JSON 错与 UnicodeDecodeError(非 UTF-8 存盘)
        add(R, "题库 topics.json", f"文件损坏({type(e).__name__}) → load_topics 会**静默**"
                                   f"回退种子主题,被试跑的不是你以为的题目。")
        return
    if not isinstance(raw, list):
        add(R, "题库 topics.json", "顶层不是数组 → 会静默回退种子主题。")
        return
    topics = state.load_topics()
    if len(topics) < config.N_ROUNDS:
        add(R, "题库 topics.json", f"只有 {len(topics)} 题 < N_ROUNDS={config.N_ROUNDS} →"
                                   f" 被试提交筛查后才崩,且已消耗一个 seq。")
        return
    used = topics[: config.N_ROUNDS]
    bad, gone = [], []
    for i, t in enumerate(used):
        # choices 只进**被试看到的题目卡**(§15 的浅色括号行);2026-09-06 起它不再进
        # 模型 prompt(见 prompts.scenario_text)。缺了不会报错,只会让这道题在题目卡上
        # **悄悄少半句** —— 而那半句正是「你可以这样,也可以不这样」的启发。
        for field in ("title", "scenario", "choices"):
            v = t.get(field)
            missing = [lg for lg in ("ja", "zh", "en")
                       if not (v.get(lg) if isinstance(v, dict) else (v if lg == "ja" else None))]
            if len(missing) == 3:
                # 整个字段缺失:没有任何语言可回退。scenario 缺了模型与题目卡都会少半句;
                # choices 缺了则题目卡的启发行整行消失(模型侧不受影响)。
                # 两种都让被试看到的题面与你以为的不是同一个。这是红,不是黄。
                gone.append(f"#{i + 1}.{field}")
            elif missing:
                bad.append(f"#{i + 1}.{field} 缺 {'/'.join(missing)}")
        if not (1 <= t.get("shot_count", 0) <= 12):
            bad.append(f"#{i + 1}.shot_count={t.get('shot_count')}")
        if not (3 <= t.get("total_seconds", 0) <= 300):
            bad.append(f"#{i + 1}.total_seconds={t.get('total_seconds')}")
    # 前 N_ROUNDS 题必须互不相同。三种题目轮转 (0,1,2)/(1,2,0)/(2,0,1) 只保证下标不重,
    # 内容重不重从来没人查:复制一条题目当模板改到一半就保存,每位被试都会在两个轮次拿到
    # 同一道题,而第二次写作带着巨大的结转优势。这一条在 plan_for_seq、应用界面、
    # 分析管线里全程沉默,只能收数后翻 trials 才发现 —— 那时样本已经用掉。
    # 查重放在闸门而不是运行路径:后者会炸在一个已经吃掉 seq 的被试脸上。
    dup, dup_title = [], []
    for a in range(len(used)):
        for b in range(a + 1, len(used)):
            for field in ("title", "scenario", "choices"):
                va, vb = used[a].get(field), used[b].get(field)
                for lg in ("ja", "zh", "en"):
                    xa = (va.get(lg) if isinstance(va, dict) else va) or ""
                    xb = (vb.get(lg) if isinstance(vb, dict) else vb) or ""
                    if xa.strip() and xa.strip() == xb.strip():
                        dup.append(f"#{a + 1}≡#{b + 1} 的 {field}.{lg}")
                        if field == "title":
                            dup_title.append(f"#{a + 1}≡#{b + 1}")

    if dup:
        # 标题重复格外要命:analysis/embed.py 的 title2idx 是以标题为键的字典,
        # 两题同名会静默塌成一个键,那一题的所有 trial 都被配到另一题的基线上,Δ 的零点直接错配。
        extra = ("；**标题重复**会让 analysis/embed.py 的 title2idx 静默塌键,"
                 "该题的 Δ 会拿另一题的基线当零点。" if dup_title else "")
        add(R, "题库 topics.json", f"前 {config.N_ROUNDS} 题里有内容重复:{' · '.join(dup[:4])}"
                                   f" → 每位被试会在两个轮次拿到同一道题,轮间不可比{extra}")
    elif gone:
        add(R, "题库 topics.json", f"字段整个缺失:{' · '.join(gone[:4])} —— 无语言可回退,"
                                   f"该题的情境在**模型 prompt 与题目卡上都会少半句**"
                                   f"(题目卡的浅色括号行直接消失),被试跑的不是你以为的题面。")
    elif bad:
        add(Y, "题库 topics.json", f"{len(topics)} 题可用,但:{' · '.join(bad[:4])}"
                                   f"(缺的语言会回退到 ja/zh,被试可能看到混语)。")
    else:
        add(G, "题库 topics.json", f"{len(topics)} 题、前 {config.N_ROUNDS} 题三语齐、"
                                   f"镜数/秒数合法。⚠️ 采数期间禁止再改(会静默重配在跑被试的题目)。")


def check_consent() -> None:
    """同意书里的〔…〕占位符必须在发链接之前填掉。

    2026-09-02 重写同意书时补齐了 13 §0.1 列的必备项,但有两处只有你能填:
    研究者姓名·所属·指导教员·联络先,以及数据保存年限。它们以〔…〕留在正文里 ——
    这是**被试会逐字读到**的文字,漏填就等于把「〔填写保存年限〕」印在同意书上,
    而同意书的指纹(screening_json.consent_sha1)还会把这一版记下来。所以是红灯。"""
    import json as _json
    bad = []
    for lg in ("ja", "zh", "en"):
        try:
            d = _json.loads((ROOT / "i18n" / "locales" / f"{lg}.json").read_text(encoding="utf-8"))
            body = d.get("consent", {}).get("body", "")
        except Exception as e:  # noqa: BLE001
            add(R, "同意书", f"{lg}.json 读不出来({type(e).__name__})。")
            continue          # 别因为一个 locale 坏了就漏检其余两个
        if "〔" in body or "〕" in body:
            n = body.count("〔")
            bad.append(f"{lg}({n} 处)")
    if bad:
        add(R, "同意书占位符", f"{' · '.join(bad)} 仍含〔…〕未填 —— 被试会逐字读到,"
                              f"且这一版会被写进 consent_sha1 存证。填掉研究者信息与保存年限再发链接。")
    else:
        add(G, "同意书", "三语正文无未填占位符;⚠️ 采数期间禁止再改(改一个字,"
                         "事后就无法证明每位被试同意的是哪一版)。")


def check_freeze() -> None:
    """冻结产物(docs/paper/prereg_frozen.json)是「分析计划在见数据之前就定了」的全部证据。
    没有 → **红灯**;有但与当前代码不一致 → 红灯(那是协议偏离)。

    为什么「不存在」也是红:这道闸门的用途就是「发链接之前跑一遍」,而冻结与机器基线同属
    **事后补不回来**的一类 —— 第一条真被试数据一旦入库,「计划是在见数据之前定的」这句话就
    永久失去证据(B3 拍板不做第三方预注册,内部冻结是唯一的防 HARKing 材料)。
    早先给黄灯的话,三个红灯清掉后闸门会 exit 0 放行,等于默许在没有冻结的情况下开始采数。"""
    import contextlib, io
    spec = importlib.util.spec_from_file_location("freeze_prereg", ROOT / "scripts" / "freeze_prereg.py")
    freeze_prereg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freeze_prereg)
    if not freeze_prereg.OUT.exists():
        add(R, "冻结产物", "docs/paper/prereg_frozen.json 不存在 → 采数前**必须** `make freeze`(先清干净工作树)。"
                          "第一条真数据入库后就再也补不出「计划早于数据」的证据。")
        return
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = freeze_prereg.check(freeze_prereg.OUT)
    if rc == 0:
        add(G, "冻结产物", "prereg_frozen.json 与当前代码一致(make freeze-check 通过)。")
    else:
        add(R, "冻结产物", "prereg_frozen.json 与当前代码**不一致** → 采数已开始的话这是协议偏离;"
                          "看 `make freeze-check` 的逐条差异。")


def check_default_lang() -> None:
    """正式研究の被験者は日本人。既定言語が ja でないまま公開すると、リンクを開いた被験者が
    いきなり中国語/英語の画面を見ることになる —— 採取が始まってからでは取り返せない類。"""
    from i18n import translator as T
    if T.DEFAULT_LANG == "ja" and T.AVAILABLE_LANGS[0] == "ja":
        add(G, "既定言語", "ja(正式研究の言語)。")
    else:
        add(Y, "既定言語", f"既定 ={T.DEFAULT_LANG} / 選択肢の先頭 ={T.AVAILABLE_LANGS[0]} —— "
                          "研究者テスト用の暫定設定。被験者は日本人なので、リンクを配る前に "
                          "i18n/translator.py を ja 先頭へ戻すこと。")


# check_public 的结论,供 check_static_gzip 判断严重性:压缩既可以来自 app(serve.py 的
# 预压缩),也可以来自反代(nginx gzip)。只要被试收到的是压缩过的字节,目的就已经达到,
# 后端那条只是「还能更省 CPU」的优化,不该再算红灯。
_PUBLIC_COMPRESSED: bool | None = None


def _public_url(sec: dict) -> str:
    """被试实际打开的地址。没配就返回空串。"""
    return str(sec.get("public_url", "")).rstrip("/")


def _fetch(url: str, gzip_ok: bool = True) -> tuple[int, dict, bytes]:
    """取一个 URL,返回 (状态码, 小写键的响应头, 正文)。
    不用 requests(未必装),urllib 也不会自作主张解压,所以 content-encoding 能原样看到。"""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "Accept-Encoding": "gzip" if gzip_ok else "identity",
        "User-Agent": "novastory-deploy-check",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        # 压缩后没有 content-length,得靠实际字节数报大小,所以要读得够多。
        return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(4_000_000)


def check_public(sec: dict) -> None:
    """从**外面**看这个站是什么样:HTTPS 通不通、静态资源压没压。

    这一项存在的理由:反代未必在本机(本项目就是 nginx 在另一台机器上),所以
    「本机有没有进程监听 443」既证明不了有 HTTPS,也证明不了没有 —— 只能真去访问一次。
    压缩同理:app 侧发不发预压缩产物,和被试最终收到什么,中间还隔着一层反代。"""
    url = _public_url(sec)
    if not url:
        add(Y, "对外地址", "secrets.toml 没配 public_url → 无法验证被试实际拿到的是什么。"
                          '填一行 public_url = "https://你的域名" 后,本闸门会实测 HTTPS 与压缩。')
        return
    if not url.startswith("https://"):
        add(R, "对外地址", f"{url} 不是 HTTPS → 被试的自由文本与同意记录明文过网。")
        return
    try:
        _, _, home = _fetch(url + "/", gzip_ok=False)
    except Exception as e:  # noqa: BLE001
        add(R, "对外地址", f"{url} 打不开({type(e).__name__}) → 被试也打不开。")
        return

    m = re.search(rb"/static/js/index\.[A-Za-z0-9_-]+\.js", home)
    if not m:
        add(Y, "对外地址", f"{url} 可访问,但没能从首页找到主 JS,压缩情况未验证。")
        return
    asset = url + m.group(0).decode()
    try:
        _, h, body = _fetch(asset, gzip_ok=True)
    except Exception as e:  # noqa: BLE001
        add(Y, "对外地址", f"{url} 首页可访问,但静态资源取不到({type(e).__name__})。")
        return

    global _PUBLIC_COMPRESSED
    enc = h.get("content-encoding", "")
    _PUBLIC_COMPRESSED = bool(enc)
    # 压缩后 nginx 用 chunked 传输,没有 content-length —— 退回按实际读到的字节数报。
    size = int(h.get("content-length", 0) or 0) or len(body)
    if enc:
        add(G, "对外地址", f"{url} HTTPS 正常,静态资源以 {enc} 传输({size // 1024} KB)。")
    else:
        # 首屏约 2 MB 的 JS 全以原文过网。实测这一条在高延迟链路上值 3 秒以上。
        add(R, "对外地址", f"{url} 静态资源未压缩(主 JS {size // 1024} KB 原文)→ "
                          "反代上开 gzip(注意 gzip_types 要含 application/javascript、"
                          "gzip_proxied 要设 any),见 DEPLOY.md §4.5。")


def _check_script_mode() -> None:
    """没有 systemd 单元时的运行形态:服务由 scripts/start.sh 起的普通后台进程。

    这条路是**明确选择**的(用户不装 systemd),所以不判红;但要替它盯住 systemd 本来
    白送的那两件事:① 进程真的在跑吗 ② 机器重启后能自己起来吗。后者靠 crontab 的
    @reboot 兜底 —— 没有它的话,一次重启就是整站消失且无人知晓。"""
    import shutil, subprocess
    pid_file = ROOT / "data" / "novastory.pid"
    alive = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            alive = (Path("/proc") / str(pid)).exists()
        except (ValueError, OSError):
            alive = False
    if not alive:
        # 端口上还有别的东西也算在跑(比如手工敲的 streamlit run)
        try:
            ss = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=10).stdout
            alive = bool(re.search(r":8501\s", ss))
        except Exception:  # noqa: BLE001
            pass
    if not alive:
        add(R, "运行时", "既没有 systemd 单元,8501 上也没有进程 → 站是停的。"
                        "起服务:`scripts/start.sh`。")
        return

    cron = ""
    if shutil.which("crontab"):
        try:
            cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
        except Exception:  # noqa: BLE001
            pass
    live = [l for l in cron.splitlines()
            if "start.sh" in l and not l.lstrip().startswith("#")]
    boot = [l for l in live if "@reboot" in l]
    # 看门狗:定时(*/N)跑一次 start.sh。不装 systemd 就没有 Restart=always,
    # 进程因 OOM/异常退出后没有任何东西会拉起它 —— 采数跨天时这是最现实的失效路径。
    watch = [l for l in live if l.lstrip().startswith("*/")]
    missing = []
    if not boot:
        missing.append(f"@reboot 自启(机器重启后站就没了):`@reboot sleep 20 && cd {ROOT} && scripts/start.sh >> data/serve.log 2>&1`")
    elif f"cd {ROOT}" not in boot[0]:
        missing.append(f"@reboot 那行缺 `cd {ROOT}`(cron 从 $HOME 起步,日志重定向会失败)")
    if not watch:
        missing.append(f"看门狗(进程死了没人拉):`*/5 * * * * cd {ROOT} && scripts/start.sh >> data/serve.log 2>&1`")
    elif f"cd {ROOT}" not in watch[0]:
        missing.append(f"看门狗那行缺 `cd {ROOT}`")
    if missing:
        add(R, "运行时", "以普通后台进程运行(非 systemd),但缺 " + " · ".join(missing))
    else:
        add(G, "运行时", "以普通后台进程运行(非 systemd):进程在、@reboot 自启与 */5 看门狗都已配置。"
                        "⚠️ 以 root 跑且绑 0.0.0.0 —— 已知并接受的取舍(见 DEPLOY.md §2)。")


def check_runtime(sec: dict) -> None:
    """运行时(不只是文件与配置):单元是否持久、是否 root、是否绑 0.0.0.0、有没有 HTTPS。
    以前闸门对这四件事一无所知,而它们恰是 DEPLOY.md §2/§5 的全部内容。非 Linux / 无 systemctl 时跳过。"""
    import shutil, subprocess
    if shutil.which("systemctl") is None:
        add(Y, "运行时", "无 systemctl,跳过运行时检查(部署机上再跑一次)。")
        return
    try:
        out = subprocess.run(["systemctl", "show", "novastory.service", "-p", "FragmentPath", "-p", "User",
                              "-p", "ExecStart", "-p", "ActiveState", "-p", "UnitFileState"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        out = ""
    props = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    if props.get("ActiveState") != "active":
        _check_script_mode()      # 不用 systemd 时,服务是 scripts/start.sh 起的
        return
    bad = []
    if props.get("FragmentPath", "").startswith("/run/systemd/transient"):
        bad.append("transient 单元(重启即消失)→ 用 deploy/novastory.service 安装持久单元")
    elif props.get("UnitFileState") != "enabled":
        # 装了持久单元却只 `start` 没 `enable`:现在跑得好好的,重启后一样起不来,
        # 而 FragmentPath 已经不在 /run 下,上面那条查不出来。
        bad.append(f"单元未 enable(UnitFileState={props.get('UnitFileState') or '?'})→ "
                   "重启后不会自启,`systemctl enable novastory`")
    if props.get("User", "") in ("", "root"):   # systemd 不设 User= 时回显为空 = root
        bad.append("以 root 运行 → 单元里设 User=<非 root 账号>(仓库须搬出 /root)")
    exec_ = props.get("ExecStart", "")
    if "0.0.0.0" in exec_ or "--server.address" not in exec_:
        bad.append("监听 0.0.0.0(或未指定地址)→ --server.address 127.0.0.1,外部走反代")
    # HTTPS 交给 check_public 从外面实测。反代常常不在本机(本项目就是),
    # 「本机没有 443 监听者」并不等于没有 HTTPS —— 以前这里会因此误报一个红灯。
    if not _public_url(sec):
        try:
            ss = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=10).stdout
            if not re.search(r":443\s", ss):
                bad.append("本机无 :443 监听者,且未配 public_url → 无法确认有 HTTPS 反代")
        except Exception:  # noqa: BLE001
            pass
    if bad:
        add(R, "运行时", " · ".join(bad))
    else:
        add(G, "运行时", "持久单元、非 root、只听 127.0.0.1。")


def check_static_gzip() -> None:
    """静态资源有没有真在压缩传输。两件事都要成立才算数:预压缩产物是最新的,而且
    服务确实是用 scripts/serve.py 起的 —— 只满足一个,被试拿到的还是 2.5MB 原文 JS。"""
    import shutil, subprocess
    try:
        rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "precompress_static.py"), "--check"],
                            capture_output=True, text=True, timeout=60).returncode
    except Exception:  # noqa: BLE001
        add(Y, "静态压缩", "无法执行 precompress_static.py --check。")
        return
    if rc != 0:
        add(R, "静态压缩", "预压缩产物缺失或落后于 Streamlit 静态文件 → 跑 `make precompress`。")
        return
    # 产物在也没用,得看启动入口。transient/未部署的机器上查不到就只给黄灯。
    if shutil.which("systemctl") is None:
        add(Y, "静态压缩", "预压缩产物是最新的;无 systemctl,没法确认服务是否用 serve.py 起。")
        return
    try:
        out = subprocess.run(["systemctl", "show", "novastory.service", "-p", "ExecStart", "-p", "ActiveState"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        out = ""
    props = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    if props.get("ActiveState") != "active":
        add(Y, "静态压缩", "预压缩产物是最新的;服务未运行,无法确认启动入口。")
    elif "serve.py" in props.get("ExecStart", ""):
        add(G, "静态压缩", "预压缩产物最新,且服务经 serve.py 启动(首屏 2.50MB → 0.77MB)。")
    elif _PUBLIC_COMPRESSED:
        # 压缩已由反代承担,被试收到的就是压缩过的字节 —— 目的达到了,这条降为优化建议。
        add(Y, "静态压缩", "线上压缩由反代承担(见「对外地址」),已达标。改用 serve.py 启动可再省下"
                           "反代每次现场压缩的 CPU,并换成 level 9 的预压缩产物 —— 属优化,不阻断采数。")
    else:
        add(R, "静态压缩", "服务不是用 scripts/serve.py 起的,反代也没压 → 静态资源以原文发送,"
                           "预压缩产物白生成了(见 deploy/novastory.service)。")


def check_backup() -> None:
    """脚本在 + 对一个临时库**真跑一次** + 进了 cron。「文件存在」不是备份能用的证据。"""
    import subprocess, tempfile
    script = ROOT / "scripts" / "backup_db.sh"
    if not script.exists():
        add(R, "备份脚本", "无备份脚本 → 一次磁盘故障=毕业数据灭失。")
        return
    tmp = Path(tempfile.mkdtemp())
    con = sqlite3.connect(tmp / "t.db"); con.execute("CREATE TABLE x(a)"); con.commit(); con.close()
    r = subprocess.run(["bash", str(script), str(tmp / "t.db"), str(tmp / "out")],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not list((tmp / "out").glob("novastory-*.db")):
        add(R, "备份脚本", f"scripts/backup_db.sh 对临时库实跑失败(exit {r.returncode}):{(r.stderr or r.stdout)[-200:]}")
        return
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        cron = ""
    # 光有 backup_db.sh 这几个字不算数:cron 的工作目录是 $HOME,写成相对路径又没 cd 的话,
    # 脚本根本找不到、每天静默失败,而这里照样会亮绿灯 —— 实测踩过。所以要求那一行要么
    # 先 cd 进项目、要么用绝对路径调脚本。
    lines = [l for l in cron.splitlines()
             if "backup_db.sh" in l and not l.lstrip().startswith("#")]
    if not lines:
        add(Y, "备份脚本", "实跑通过,但 crontab 里没有 backup_db.sh → 加每日一次(+ 每场后),并异地同步一份。")
    elif any(f"cd {ROOT}" in l or str(ROOT / "scripts" / "backup_db.sh") in l for l in lines):
        add(G, "备份脚本", "实跑通过、已进 cron 且路径可用;确认异地还有一份。")
    else:
        add(R, "备份脚本", f"cron 里有 backup_db.sh 但**路径跑不通**({lines[0].strip()[:60]}…)→ "
                          f"cron 从 $HOME 起步,相对路径找不到脚本且不会报错。"
                          f"改成 `cd {ROOT} && scripts/backup_db.sh …` 或用绝对路径。")


def main() -> None:
    try:
        sec = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text())
    except FileNotFoundError:
        sec = {}
        add(R, "secrets.toml", "缺 .streamlit/secrets.toml。")
    check_password(sec)
    check_model(sec)
    check_clean_db()
    check_run_on_save()
    check_perms()
    check_gitignore()
    check_baseline(sec)
    check_topics()
    check_consent()
    check_freeze()
    check_default_lang()
    check_runtime(sec)
    check_public(sec)
    check_static_gzip()
    check_backup()
    check_analysis_deps()

    print("=" * 60)
    print("部署就绪闸门(公开采数前)")
    print("=" * 60)
    for flag, name, detail in rows:
        print(f"{flag} {name:14s} {detail}")
    reds = sum(1 for f, *_ in rows if f == R)
    yels = sum(1 for f, *_ in rows if f == Y)
    print("-" * 60)
    if reds:
        print(f"{R} 未就绪:{reds} 个红灯必须先解决,再公开采数。")
        sys.exit(1)
    print(f"{G} 就绪(剩 {yels} 个黄灯留意)。" if yels else f"{G} 全绿,可部署。")


if __name__ == "__main__":
    main()
