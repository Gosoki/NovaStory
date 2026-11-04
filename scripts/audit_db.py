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
        parts = v3.read_participants(con, "id, lang, seq, status, created_at, screening_json")
        trials = pd.read_sql("SELECT participant_id, round_idx, condition, final_output, guidance_json, script_versions,"
                             " revision_requests, n_ai_rounds, app_rev, model FROM trials", con)
        ev = pd.read_sql("SELECT participant_id, round_idx, type, payload_json, ts, trial_id FROM events", con)
    finally:
        con.close()
    parts["dev"] = parts["screening_json"].map(lambda x: bool(_j(x, {}).get("dev")))
    parts["lang"] = parts["lang"].fillna("?")
    parts["cohort"] = parts["cohort"].replace("", "?")
    keep = v3.included_participants(db)
    # 下面各段的分组标签都是**队列**(06 ㉞:日本招募的人选了中文也是 ja),变量名沿用 lang
    lang_of = dict(zip(parts["id"], parts["cohort"]))
    real = parts[~parts["dev"]]

    print("=" * 78)
    print(f"数据层审计 · {db}  (只读;只输出汇总)  最新入组 {parts['created_at'].max()}")
    print("=" * 78)
    print("\n[入组](非测试行)")
    for lg, g in real.groupby("cohort"):
        n_keep = len(set(g["id"]) & keep)
        st = g["status"].value_counts().to_dict()
        print(f"  {lg}: {len(g)} 行 {st};纳入分析 {n_keep} 人(交满 {config.rounds_for_cohort(lg)} 份问卷)"
              f" · 界面语言 {g['lang'].value_counts().to_dict()}")
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
        items = [it for x in e["guidance_json"] for rd in _j(x, {}).get("rounds", [])
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
        if len(f):   # F(06 ㉜):与 E 同一出题,选项默认藏起来
            its = [it for x in f["guidance_json"] for rd in _j(x, {}).get("rounds", [])
                   for it in rd.get("items", []) if isinstance(it, dict) and it.get("options")]
            if its:
                n = len(its)
                rev = sum(bool(it.get("revealed")) for it in its)
                own = sum(bool(it.get("is_custom")) for it in its)
                # 一致性:显示了选项却没点过「表示する」、没显示却记了点选的选项、没点过「ヒント」却有后续 —— 都应为 0
                bad = sum((bool(it.get("revealed")) != (int(it.get("n_reveal_clicks") or 0) > 0))
                          or (bool(it.get("picked_option")) and not it.get("revealed"))
                          or (int(it.get("n_hint_clicks") or 0) == 0
                              and int(it.get("n_reveal_clicks") or 0) + int(it.get("n_hint_declines") or 0) > 0)
                          for it in its)
                print(f"  {lg} F: {n} 题 · 点开提示 {sum(int(it.get('n_hint_clicks') or 0) > 0 for it in its) / n:.0%}"
                      f" · 显示了选项 {rev / n:.0%} · 最终自己写 {own / n:.0%}"
                      f" · 题面换成固定题 {sum(bool(it.get('fixed_q')) for it in its)} · 提示记录不一致 {bad}(应为 0)")
                # 题面夹带候补 → AI 补的题不问:丢多了 F 的题就比 E 少(f_leak_probe 是开采前的同一口径)
                n1 = {c: [len(_j(x, {})["rounds"][0]["items"]) for x in g.loc[g["condition"] == c, "guidance_json"]
                          if _j(x, {}).get("rounds")] for c in "EF"}
                # 与 f_analysis 同口径:只数产生了已提交 trial 的 attempt(作废 attempt 的出题不算)、只数第一轮
                drop = sum(int(d.get("f_dropped") or 0) for d in map(lambda x: _j(x, {}), ev.loc[
                    (ev["lang"] == lg) & (ev["type"] == "guidance_shown") & ev["trial_id"].notna(), "payload_json"])
                    if int(d.get("round") or 1) == 1)
                avg = {c: f"{sum(v) / len(v):.1f}" if v else "—" for c, v in n1.items()}
                print(f"  {lg} F: 第一轮平均题数 F {avg['F']} / E {avg['E']} · 因题面夹带候补丢掉的 AI 补题 {drop}")

    try:
        pt = v3.per_trial(v3.load(db))
    except Exception as e:  # noqa: BLE001 —— 作答质量这一段坏了不影响上面
        print(f"\n[作答质量] 算不出来({type(e).__name__}: {e})")
        pt = None
    if pt is not None and len(pt):
        print("\n[作答质量](纳入被试)")
        for lg, g in pt.groupby("cohort"):
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
