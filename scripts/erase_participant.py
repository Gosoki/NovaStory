#!/usr/bin/env python
"""按完成码 / 邮箱 / participant_id 删除一位被试的全部记录 —— 同意书承诺的「特定して削除」的执行工具。

删:participants / trials / trials_archive / questionnaires / events 的行、data/storyboard_images/{pid}_*/、
data/analysis/*.csv 与 judge.jsonl 里该被试的行、emb_cache.json 里由该被试文本算出的向量;
然后列出**仍含该被试的旧备份文件**——备份不会自动清,要么现场删、要么按 07 §2.5 的口径告知
「バックアップからは最長 KEEP 日で消えます」。

用法: .venv/bin/python scripts/erase_participant.py (--pid N | --code XXXXXXXX | --email a@b.jp) [--yes]
不带 --yes 只预览,不删。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "novastory.db"
IMG = ROOT / "data" / "storyboard_images"
ANALYSIS = ROOT / "data" / "analysis"
BACKUP = ROOT / "backup"


def find_pid(con: sqlite3.Connection, args) -> int | None:
    if args.pid:
        # 输错的 id 不能照样报告「已删除」(2026-10-29 审计)
        return args.pid if con.execute("SELECT 1 FROM participants WHERE id=?", (args.pid,)).fetchone() else None
    if args.code:
        r = con.execute("SELECT id FROM participants WHERE completion_code=?", (args.code.strip().upper(),)).fetchone()
        return r[0] if r else None
    if args.email:
        want = args.email.strip().lower()
        for pid, cj in con.execute("SELECT id, contact_json FROM participants WHERE contact_json IS NOT NULL"):
            try:
                if (json.loads(cj).get("email") or "").strip().lower() == want:
                    return pid
            except ValueError:
                continue
    return None


def participant_texts(con: sqlite3.Connection, pid: int) -> set[str]:
    """该被试写过 / 收到过的全部文本 —— 用来反查 emb_cache 里的向量(键 = sha1(model + 文本))。"""
    out: set[str] = set()
    from analysis.embed import guided_intent  # noqa: PLC0415 — 与 embed.py 同一个锚文本构造
    for intent, final, versions, guidance in con.execute(
            "SELECT intent_statement, final_output, script_versions, guidance_json "
            "FROM trials WHERE participant_id=?", (pid,)):
        for s in (intent, final):
            if s:
                out.add(s)
        try:
            out.update(v.get("text", "") for v in json.loads(versions or "[]") if v.get("text"))
        except ValueError:
            pass
        if intent:   # embed.py 意图定义②的锚文本(意图 + E 引导答案)也会进 emb_cache
            try:
                out.add(guided_intent(intent, guidance))
            except ValueError:
                pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="删除一位被试的全部记录")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pid", type=int)
    g.add_argument("--code")
    g.add_argument("--email")
    ap.add_argument("--yes", action="store_true", help="真的删(缺省只预览)")
    args = ap.parse_args()
    if not DB.exists():
        sys.exit(f"{DB} 不存在")
    con = sqlite3.connect(DB)
    pid = find_pid(con, args)
    if pid is None:
        sys.exit("没有匹配的被试(完成码 / 邮箱 / id 都不对应任何记录)")
    tables = ("trials", "trials_archive", "questionnaires", "events")
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t} WHERE participant_id=?", (pid,)).fetchone()[0] for t in tables}
    texts = participant_texts(con, pid)     # 先读出文本,删库之后就反查不到 emb_cache 的键了
    img_dirs = sorted(IMG.glob(f"{pid}_*")) if IMG.exists() else []
    # rglob:队列快照(zh_v1_*/)、数据挖掘目录(mining_*/)里的逐 trial CSV 也按 participant_id 存行
    csvs = sorted(ANALYSIS.rglob("*.csv")) if ANALYSIS.exists() else []
    print(f"participant_id={pid}: 行数 {counts},插图目录 {len(img_dirs)} 个,分析 CSV {len(csvs)} 份将剔除该 id 的行,"
          f"emb_cache 反查文本 {len(texts)} 条")
    if not args.yes:
        print("预览模式 —— 加 --yes 才会删除。")
        return
    con.execute("PRAGMA secure_delete=ON")   # 删掉的原文不留在库文件的空闲页里
    with con:
        for t in tables:
            con.execute(f"DELETE FROM {t} WHERE participant_id=?", (pid,))
        con.execute("DELETE FROM participants WHERE id=?", (pid,))
    con.close()
    for d in img_dirs:
        shutil.rmtree(d, ignore_errors=True)
    import pandas as pd
    changed = []
    for c in csvs:
        # 只重写**真的含该被试**的 CSV,并按 round_trip 精度读(2026-10-29 审计):以前只要有 participant_id 列
        # 就整份重写,浮点末位被改,冻结快照里登记了哈希的 CSV(zh_v1_*/MANIFEST)就对不上了
        df = pd.read_csv(c, float_precision="round_trip")
        if "participant_id" in df.columns and (df["participant_id"] == pid).any():
            df[df["participant_id"] != pid].to_csv(c, index=False)
            changed.append(c)
    if changed:
        print(f"分析 CSV:{len(changed)} 份删掉了该被试的行:{[str(c.relative_to(ANALYSIS)) for c in changed]}"
              + ("(⚠️ 其中有快照目录里的文件,快照的 MANIFEST 哈希要相应更新)"
                 if any("zh_v1_" in str(c) or "MANIFEST" in {p.name for p in c.parent.iterdir()} for c in changed) else ""))
    jl = ANALYSIS / "judge.jsonl"
    if jl.exists():
        keep = [l for l in jl.read_text(encoding="utf-8").splitlines()
                if l.strip() and json.loads(l).get("participant_id") != pid]
        jl.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    cache = ANALYSIS / "emb_cache.json"
    if cache.exists() and texts:
        from core.shots import strip_format
        from analysis.embed import Embedder  # 只为拿键的算法,不建客户端
        d = json.loads(cache.read_text())
        keys = set()
        for model in {"text-embedding-3-small", "text-embedding-3-large"}:
            for s in texts:
                s2 = strip_format(s or "").strip() or "(empty)"
                keys.add(hashlib.sha1(f"{model}\n{s2}".encode()).hexdigest())
        n0 = len(d)
        d = {k: v for k, v in d.items() if k not in keys}
        cache.write_text(json.dumps(d))
        print(f"emb_cache.json:删掉 {n0 - len(d)} 条向量")
    print(f"已删除 participant_id={pid} 的全部在线记录。")
    # 这些文件脚本改不了(整库副本 / 打包 / 自包含网页),但可能仍含该被试 —— 必须点名,不能说「全部」
    frozen = sorted([*ANALYSIS.rglob("*.tar.gz"), *ANALYSIS.rglob("*.db"), *ANALYSIS.rglob("*.html"),
                     *ANALYSIS.rglob("*.json"), *ANALYSIS.rglob("*.md"),
                     *ANALYSIS.rglob("*.log"), *ANALYSIS.rglob("*.txt")]) if ANALYSIS.exists() else []
    frozen = [p for p in frozen if p.name not in ("emb_cache.json",) and not p.name.startswith("norming")]
    if frozen:
        print(f"⚠️ data/analysis/ 下还有 {len(frozen)} 个文件可能仍含该被试,需人工处理(删掉或重新生成):"
              f"{[str(p.relative_to(ANALYSIS)) for p in frozen]}")
    old = (sorted([*BACKUP.glob("**/novastory-*.db"), *BACKUP.glob("**/storyboard_images-*.tar.gz"),
                   *BACKUP.glob("**/analysis-extras-*.tar.gz")]) if BACKUP.exists() else [])
    if old:
        print(f"⚠️ 备份目录里还有 {len(old)} 份历史快照可能仍含该被试(最早 {old[0].name}):"
              " 要么现场删掉这些快照,要么按 docs/paper/07 §2.5 告知「バックアップからは最長 30 日で消えます」。")


if __name__ == "__main__":
    main()
