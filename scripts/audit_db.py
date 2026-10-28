"""数据层审计(只读,2026-10-29,docs/paper/19):把系统审计里用到的数据检查收成一条命令,拿到服务器的
新副本后直接跑,按队列给出汇总数字(不输出任何被试原文)。

  · 入组:各队列的完成 / 进行中 / 测试行,纳入分析的人数(交满全部问卷)
  · AI 调用健康:按队列 × 条件的发起 / 完成 / 失败 / 未闭合 / 中断后接上(llm_reattach,2026-10-29 之后的代码)
  · 配图成功率、分镜结构(解析 / 3 镜 / 15 秒)
  · 引导与修改:E 的点选 / 自写 / 交给 AI 比例与 fallback,D 修改入口使用率,F 的题数分布 / 退回固定题面 / 回答字数
  · 作答质量:注意力题失败、各轮 straightline
  · 代码与模型版本:app_rev、served_model 是否在采集中途变过

用法:  .venv/bin/python scripts/audit_db.py [--db data/novastory.db]   (或 make audit-db DB=…)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analysis import v3  # noqa: E402
from core import config  # noqa: E402
from core.shots import parse_shots  # noqa: E402


def _j(x, default):
    try:
        v = json.loads(x) if isinstance(x, str) and x.strip() else default
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def _dur(raw) -> float:
    return v3._duration_sec(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "novastory.db")
    db = ap.parse_args(argv).db
    if not db.exists():
        print(f"⛔ 没有 {db}")
        return 1
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        parts = pd.read_sql("SELECT id, lang, seq, status, created_at, screening_json FROM participants", con)
        trials = pd.read_sql("SELECT participant_id, round_idx, condition, final_output, guidance_json, script_versions,"
                             " revision_requests, n_ai_rounds, app_rev, model FROM trials", con)
        ev = pd.read_sql("SELECT participant_id, round_idx, type, payload_json, ts FROM events", con)
    finally:
        con.close()
    parts["dev"] = parts["screening_json"].map(lambda x: bool(_j(x, {}).get("dev")))
    parts["lang"] = parts["lang"].fillna("?")
    keep = v3.included_participants(db)
    lang_of = dict(zip(parts["id"], parts["lang"]))
    real = parts[~parts["dev"]]

    print("=" * 78)
    print(f"数据层审计 · {db}  (只读;只输出汇总)  最新入组 {parts['created_at'].max()}")
    print("=" * 78)
    print("\n[入组](非测试行)")
    for lg, g in real.groupby("lang"):
        n_keep = len(set(g["id"]) & keep)
        st = g["status"].value_counts().to_dict()
        print(f"  {lg}: {len(g)} 行 {st};纳入分析 {n_keep} 人(交满 {config.rounds_for_lang(lg)} 份问卷)")
    print(f"  测试 / 已释放行:{int(parts['dev'].sum())}")

    ev = ev[ev["participant_id"].isin(set(real["id"]))].copy()
    ev["lang"] = ev["participant_id"].map(lang_of)
    llm = ev[ev["type"].isin(("llm_start", "llm_done", "llm_error", "llm_reattach"))].copy()
    llm["cond"] = llm["payload_json"].map(lambda x: str(_j(x, {}).get("group", "?")).split("-")[0])
    print("\n[AI 调用](全部非测试被试;未闭合 = 发起后没有完成 / 失败记录)")
    for (lg, c), g in llm.groupby(["lang", "cond"]):
        n = g["type"].value_counts()
        s, d, e, r = (int(n.get(k, 0)) for k in ("llm_start", "llm_done", "llm_error", "llm_reattach"))
        print(f"  {lg} {c}: 发起 {s} · 完成 {d} · 失败 {e} · 未闭合 {max(s - d - e, 0)}({max(s - d - e, 0) / max(s, 1):.0%})"
              f" · 中断后接上 {r}")

    img = ev[ev["type"] == "images_ready"]
    if len(img):
        p = img["payload_json"].map(lambda x: _j(x, {}))
        img = img.assign(ok=p.map(lambda d: int(d.get("n_ok") or 0)), tot=p.map(lambda d: int(d.get("n_shots") or 0)))
        print("\n[配图] " + " · ".join(f"{lg} {g['ok'].sum()}/{g['tot'].sum()}" for lg, g in img.groupby("lang")))

    tr = trials[trials["participant_id"].isin(keep)].copy()
    tr["lang"] = tr["participant_id"].map(lang_of)
    sh = tr["final_output"].map(lambda x: parse_shots(x or ""))
    tr["n_shots"] = sh.map(len)
    tr["dur"] = sh.map(lambda s: sum(_dur(x.get("duration")) for x in s) if s else float("nan"))
    print("\n[分镜结构](纳入被试的终稿)")
    for (lg, c), g in tr.groupby(["lang", "condition"]):
        print(f"  {lg} {c}: n={len(g)} · 解析失败 {int((g['n_shots'] == 0).sum())} · 镜数≠3 {int((g['n_shots'] != 3).sum())}"
              f" · 总秒数≠15 {int(((g['dur'] - 15).abs() > v3.SPEC_TOL_SEC).sum())}")

    print("\n[引导 / 修改]")
    for lg, g in tr.groupby("lang"):
        e = g[g["condition"] == "E"]
        items = [it for x in e["guidance_json"] for rd in _j(x, {}).get("rounds", []) if rd.get("source") != "interview"
                 for it in rd.get("items", []) if isinstance(it, dict)]
        if items:
            n = len(items)
            cust = sum(bool(it.get("is_custom")) for it in items)
            dec = sum(bool(it.get("ai_decided")) for it in items)
            fb = sum(bool(it.get("fallback")) for it in items)
            silent = sum(1 for x in e["guidance_json"]
                         if not any(it.get("is_custom") for rd in _j(x, {}).get("rounds", []) for it in rd.get("items", [])))
            print(f"  {lg} E: {n} 题 · 点选 AI 选项 {(n - cust - dec) / n:.0%} · 自己写 {cust / n:.0%} · 交给 AI {dec / n:.0%}"
                  f" · fallback {fb} · 引导环节一字未写 {silent}/{len(e)} 人")
        d = g[g["condition"] == "D"]
        if len(d):
            used = int((pd.to_numeric(d["n_ai_rounds"], errors="coerce").fillna(0) > 0).sum())
            print(f"  {lg} D: 用过 AI 修改入口 {used}/{len(d)} 人")
        f = g[g["condition"] == "F"]
        if len(f):
            nq, fbq, chars = [], 0, []
            for x in f["guidance_json"]:
                for rd in _j(x, {}).get("rounds", []):
                    if rd.get("source") == "interview":
                        its = [it for it in rd.get("items", []) if isinstance(it, dict)]
                        nq.append(len(its))
                        fbq += sum(bool(it.get("fallback")) for it in its)
                        chars.append(sum(len(str(it.get("chosen") or "")) for it in its))
            if nq:
                print(f"  {lg} F: 题数 {pd.Series(nq).value_counts().sort_index().to_dict()} · 退回固定题面 {fbq}/{sum(nq)} 题"
                      f" · 回答总字数中位 {pd.Series(chars).median():.0f}")

    try:
        pt = v3.per_trial(v3.load(db))
    except Exception as e:  # noqa: BLE001 —— 作答质量这一段坏了不影响上面
        print(f"\n[作答质量] 算不出来({type(e).__name__}: {e})")
        pt = None
    if pt is not None and len(pt):
        print("\n[作答质量](纳入被试)")
        for lg, g in pt.groupby("lang"):
            att = g.drop_duplicates("participant_id")["attention_ok"]
            sl = g.groupby("round_idx")["straightline"].mean() if "straightline" in g else pd.Series(dtype=float)
            print(f"  {lg}: 注意力题失败 {int((att == 0).sum())}/{int(att.notna().sum())} 人 · straightline 按轮次 "
                  + " / ".join(f"{v:.0%}" for v in sl))

    print("\n[版本](纳入被试的 trial)")
    for lg, g in tr.groupby("lang"):
        print(f"  {lg}: app_rev {g['app_rev'].value_counts().to_dict()} · model {g['model'].value_counts().to_dict()}")
    done = ev[ev["type"] == "llm_done"]["payload_json"].map(lambda x: (_j(x, {}).get("repro") or {}).get("served_model"))
    print(f"  served_model(llm_done):{done.value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
