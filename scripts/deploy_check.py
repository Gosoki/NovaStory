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
    models = set()
    for f in files:
        first = next((l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()), "")
        if first:
            models.add(json.loads(first).get("model") or "?")
    cfg0 = (sec.get("api_configs") or [{}])[0]
    want = cfg0.get("model", "")
    if len(models) > 1:
        add(R, "机器基线", f"data/baseline/ 混了多个模型 {sorted(models)} —— 删掉重跑 make baseline。")
    elif want and models != {want}:
        add(R, "机器基线", f"基线用 {sorted(models)} 生成,但正式模型是 '{want}' → Δ 的零点与产出不同源,"
                          "embed.py 会硬失败。用正式快照重跑 `make baseline`。")
    else:
        add(G, "机器基线", f"{len(files)} 题基线就位,模型 {sorted(models)} 与正式模型一致。")


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
        # choices 与 scenario 同等重要:它既进被试看到的题目卡(§15 的浅色括号行),
        # 又被 prompts.scenario_text 拼进模型 prompt。缺了不会报错,只会让这道题
        # 的情境**悄悄少半句** —— 而那半句正是「你可以这样,也可以不这样」。
        for field in ("title", "scenario", "choices"):
            v = t.get(field)
            missing = [lg for lg in ("ja", "zh", "en")
                       if not (v.get(lg) if isinstance(v, dict) else (v if lg == "ja" else None))]
            if len(missing) == 3:
                # 整个字段缺失:没有任何语言可回退。scenario/choices 缺一个,
                # 这道题的**模型 prompt 与题目卡都会少半句情境**(prompts.scenario_text
                # 直接短路),被试看到的题面与你以为的不是同一个。这是红,不是黄。
                gone.append(f"#{i + 1}.{field}")
            elif missing:
                bad.append(f"#{i + 1}.{field} 缺 {'/'.join(missing)}")
        if not (1 <= t.get("shot_count", 0) <= 12):
            bad.append(f"#{i + 1}.shot_count={t.get('shot_count')}")
        if not (3 <= t.get("total_seconds", 0) <= 300):
            bad.append(f"#{i + 1}.total_seconds={t.get('total_seconds')}")
    if gone:
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
    没有 → 黄灯(采数前必须 make freeze);有但与当前代码不一致 → 红灯(那是协议偏离)。"""
    import contextlib, io
    spec = importlib.util.spec_from_file_location("freeze_prereg", ROOT / "scripts" / "freeze_prereg.py")
    freeze_prereg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freeze_prereg)
    if not freeze_prereg.OUT.exists():
        add(Y, "冻结产物", "docs/paper/prereg_frozen.json 不存在 → 采数前必须 `make freeze`(先清干净工作树)。")
        return
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = freeze_prereg.check(freeze_prereg.OUT)
    if rc == 0:
        add(G, "冻结产物", "prereg_frozen.json 与当前代码一致(make freeze-check 通过)。")
    else:
        add(R, "冻结产物", "prereg_frozen.json 与当前代码**不一致** → 采数已开始的话这是协议偏离;"
                          "看 `make freeze-check` 的逐条差异。")


def check_runtime() -> None:
    """运行时(不只是文件与配置):单元是否持久、是否 root、是否绑 0.0.0.0、有没有 :443 监听者。
    以前闸门对这四件事一无所知,而它们恰是 DEPLOY.md §2/§5 的全部内容。非 Linux / 无 systemctl 时跳过。"""
    import shutil, subprocess
    if shutil.which("systemctl") is None:
        add(Y, "运行时", "无 systemctl,跳过运行时检查(部署机上再跑一次)。")
        return
    try:
        out = subprocess.run(["systemctl", "show", "novastory.service", "-p", "FragmentPath", "-p", "User",
                              "-p", "ExecStart", "-p", "ActiveState"], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        out = ""
    props = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    if props.get("ActiveState") != "active":
        add(Y, "运行时", "novastory.service 未运行(本机没部署?)。")
        return
    bad = []
    if props.get("FragmentPath", "").startswith("/run/systemd/transient"):
        bad.append("transient 单元(重启即消失)→ 用 deploy/novastory.service 安装持久单元")
    if props.get("User", "") in ("", "root"):   # systemd 不设 User= 时回显为空 = root
        bad.append("以 root 运行 → 单元里设 User=<非 root 账号>(仓库须搬出 /root)")
    exec_ = props.get("ExecStart", "")
    if "0.0.0.0" in exec_ or "--server.address" not in exec_:
        bad.append("监听 0.0.0.0(或未指定地址)→ --server.address 127.0.0.1,外部走反代")
    try:
        ss = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=10).stdout
        if not re.search(r":443\s", ss):
            bad.append("没有任何进程监听 :443 → 无 HTTPS 反代(Caddy/nginx)")
    except Exception:  # noqa: BLE001
        pass
    if bad:
        add(R, "运行时", " · ".join(bad))
    else:
        add(G, "运行时", "持久单元、非 root、只听 127.0.0.1、有 :443 监听者。")


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
    if "backup_db.sh" in cron:
        add(G, "备份脚本", "实跑通过且已进 cron;确认异地还有一份。")
    else:
        add(Y, "备份脚本", "实跑通过,但 crontab 里没有 backup_db.sh → 加每日一次(+ 每场后),并异地同步一份。")


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
    check_runtime()
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
