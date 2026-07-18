#!/usr/bin/env python
"""部署就绪闸门 —— 公开采数前跑一遍,把「部署硬门禁」变成 🟢🟡🔴(不打印任何密钥值)。

检查(评估 §答辩火力点「部署硬门禁」+ paper/13):
  ① 研究员强密码(≠ 弱口令 nova、长度足够)
  ② 正式模型 = OpenAI 置顶(api_configs[0])且带日期快照(可复现)
  ③ 空库起跑(data/novastory.db 无真实被试)
  ④ config.toml runOnSave 关闭(dev 设置)
  ⑤ .gitignore 覆盖被试数据
  ⑥ 备份脚本就位

用法: .venv/bin/python scripts/deploy_check.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G, Y, R = "🟢", "🟡", "🔴"
rows: list[tuple[str, str, str]] = []


def add(flag: str, name: str, detail: str) -> None:
    rows.append((flag, name, detail))


def check_password(sec: dict) -> None:
    pw = sec.get("researcher_password", "")
    if not pw or pw == "nova":
        add(R, "研究员密码", "未设置或仍是弱口令 'nova' → 公开即数据泄露+去盲。secrets 设强口令。")
    elif len(pw) < 8:
        add(Y, "研究员密码", f"已设置但偏短({len(pw)} 字符),建议 ≥12。")
    else:
        add(G, "研究员密码", "已设置且非弱口令。")


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
    else:
        add(G, "正式模型置顶", f"OpenAI 置顶且钉死快照 {model}。")


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


def check_config() -> None:
    cfg = ROOT / ".streamlit" / "config.toml"
    if cfg.exists() and "runOnSave = true" in cfg.read_text():
        add(Y, "config runOnSave", "runOnSave=true 是 dev 设置,生产建议关。")
    else:
        add(G, "config runOnSave", "无 dev 自动重载设置。")


def check_gitignore() -> None:
    gi = ROOT / ".gitignore"
    txt = gi.read_text() if gi.exists() else ""
    if "data/*.db" in txt:
        add(G, "数据 gitignore", "被试数据(data/*.db)已 gitignore。")
    else:
        add(R, "数据 gitignore", ".gitignore 未覆盖 data/*.db → 被试数据可能被提交进仓库。")


def check_backup() -> None:
    if (ROOT / "scripts" / "backup_db.sh").exists():
        add(G, "备份脚本", "scripts/backup_db.sh 就位;确认已进 cron(每日 + 每场后)、异地一份。")
    else:
        add(R, "备份脚本", "无备份脚本 → 一次磁盘故障=毕业数据灭失。")


def main() -> None:
    try:
        sec = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text())
    except FileNotFoundError:
        sec = {}
        add(R, "secrets.toml", "缺 .streamlit/secrets.toml。")
    check_password(sec)
    check_model(sec)
    check_clean_db()
    check_config()
    check_gitignore()
    check_backup()

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
