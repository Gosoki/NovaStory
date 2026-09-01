#!/usr/bin/env python
"""分析链路回归自测 —— 合成 N=36 的实验库,把 v3 → events → stats → figures 整条链跑一遍
并逐项断言。数据未采(N=0)时,这是唯一能证明"冻结分析计划里的终点真的算得出来"的东西。

盯死的是**静默失败**(算错不可怕,可怕的是看起来跑通了):
  · v3 少行 / 少列,或 dev 被试混进分析人群
  · 任一冻结终点悄悄退回 Wilcoxon(LMM 或计划对比或 Holm 缺失)
  · LMM"成功"但 se/p 全 NaN(零方差 DV),Holm 把 NaN 排成 p=0 的假显著
  · TOST 在没有(或非法的)SESOI 下照样出结论 / 锁定了 SESOI 却出不来结论 / 缺条件时崩掉
  · E 的引导剂量列没落盘,或漏进了 C/D;剂量复合退化成 pre_investment 的线性缩放(H5 只测了时间)
  · 半截行(final_output / script_versions 为 NULL)让整条链抛栈
  · 试测规模(N=4、E 臂整条缺失)不是"大声降级"而是崩
  · 面板重算 per-trial 时抹掉别的步骤合入的列(embed.py 的保真Δ / events.py 的事件层 /
    judge.py 的盲评保真)—— 保真复合悄悄少一条腿
  · 说好产出的图少了几张
  · 空库直接抛栈(试测第一位被试提交前的常态)

合成库形状与埋点见 scripts/gen_synthetic_db.py。全程用临时库/临时输出目录,不碰项目树。

用法: .venv/bin/python scripts/analysis_smoke.py   (= make smoke)
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
import traceback
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

N_SUBJ = 36
_CHECKS: list[str] = []


def ok(msg: str) -> None:
    _CHECKS.append(msg)
    print(f"   ✅ {msg}")


def quiet(fn):
    """跑一个话很多的函数,吞掉 stdout(断言失败时错误信息本身已足够定位)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn()
    return out, buf.getvalue()


def run(tmp: Path) -> None:
    import numpy as np
    import pandas as pd

    from analysis import events as A_ev
    from analysis import figures as A_fig
    from analysis import prereg
    from analysis import stats as A_stats
    from analysis import v3
    from scripts import gen_synthetic_db

    db_path, csv, figdir = tmp / "smoke.db", tmp / "v3_per_trial.csv", tmp / "figures"

    # ---------- ① 合成库 ----------
    print("\n[1/10] 合成实验库(N=36 + 1 dev)")
    info = gen_synthetic_db.generate(db_path, n=N_SUBJ, seed=7)
    assert info["events"] > 0, "fixture 没写出 events"
    assert info["parse_fail"] and info["redo_rounds"], "fixture 缺解析失败/重做轮埋点"
    ok(f"trials {info['trials_total']}(非 dev {info['trials_non_dev']}) · events {info['events']} · "
       f"解析失败 {len(info['parse_fail'])} 条 · 重做轮 {len(info['redo_rounds'])} 个")

    # ---------- ② v3 逐 trial ----------
    print("[2/10] v3 确定性指标")
    raw = v3.load(db_path)
    pt = v3.per_trial(raw)
    assert len(pt) == info["trials_non_dev"], f"逐 trial 行数 {len(pt)} ≠ {info['trials_non_dev']}"
    missing = [c for c in v3._SUMMARY_COLS if c not in pt.columns]
    assert not missing, f"v3 缺列: {missing}"
    for key in ("participant_id", "round_idx", "condition", "topic"):
        assert key in pt.columns and pt[key].notna().all(), f"v3 缺/空的键列: {key}"
    ok(f"{len(pt)} 行 × {len(v3._SUMMARY_COLS)} 个文档列齐全")

    dev = info["dev_participant_id"]
    n_dev_trials = _q1(db_path, "SELECT COUNT(*) FROM trials WHERE participant_id=?", (dev,))
    assert n_dev_trials == 3, f"fixture 的 dev 被试没有 trials(={n_dev_trials}),排除就无从测起"
    assert dev not in set(pt["participant_id"]), f"dev 被试 {dev} 混进了分析人群"
    ok(f"dev 被试 id={dev}(库里有 {n_dev_trials} 条 trials)被排除")

    # 解析失败的那条:不丢行,但结构指标必须诚实(parse_ok=0 / 整稿一个标签)
    fpid, fridx = info["parse_fail"][0]
    frow = pt[(pt.participant_id == fpid) & (pt.round_idx == fridx)].iloc[0]
    assert frow["parse_ok"] == 0 and pd.isna(frow["field_completeness"]), frow.to_dict()
    assert frow["whole_script_fallback"] == 1, "解析失败轮应标记 whole_script_fallback"
    ok("解析失败的终稿:行保留、parse_ok=0、field_completeness=NaN、整稿标注被标记")

    # 半截行(final_output / script_versions 为 NULL):NULL 读进 pandas 是 nan,
    # 而 `nan or ""` 仍是 nan → 下游 .strip() 会炸(scripts/dev_smoke_e2e.py:325 真会写出这种行)
    npid, nridx = info["null_final"][0]
    nrow = pt[(pt.participant_id == npid) & (pt.round_idx == nridx)].iloc[0]
    assert nrow["parse_ok"] == 0 and nrow["n_shots"] == 0, nrow.to_dict()
    vpid, vridx = info["null_versions"][0]
    vrow = pt[(pt.participant_id == vpid) & (pt.round_idx == vridx)].iloc[0]
    assert vrow["n_ai_versions"] == 0 and pd.isna(vrow["final_vs_firstai_sim"]), vrow.to_dict()
    div = v3.diversity_by_group(raw)   # 这里也吃 final_output,NULL 行同样不能让它抛栈
    assert len(div) > 0, "多样性分组全空(半截行把成稿都吃掉了?)"
    ok(f"半截行存活:缺终稿 (p{npid} r{nridx}) 记 0 镜、缺版本历史 (p{vpid} r{vridx}) 相似度 NaN;"
       f"多样性 {len(div)} 组照出")

    csv.parent.mkdir(parents=True, exist_ok=True)
    pt.to_csv(csv, index=False)

    # ---------- ③ events 事件层 ----------
    print("[3/10] events 事件层指标")
    _, _log = quiet(lambda: _cli(A_ev.main, ["--db", str(db_path), "--csv", str(csv)]))
    merged = pd.read_csv(csv)
    assert all(c in merged.columns for c in A_ev._COLS), f"events 列没合入: {A_ev._COLS}"
    for c in ("t_questionnaire", "n_llm_calls", "llm_total_tokens", "llm_wait_max"):
        assert merged[c].notna().all(), f"事件层指标 {c} 有空行"
    assert (merged["t_questionnaire"] > 0).all(), "问卷时长必须为正"
    ev_raw, parts_raw = A_ev.load_events(db_path)
    ev_dev = A_ev.per_trial(ev_raw)
    assert dev not in set(ev_dev["participant_id"]), "dev 被试混进了事件层"
    assert (ev_dev["round_idx"] != 0).all(), "intake 段(round_idx=0)不该当成一轮 trial"
    # 逐被试 intake/整场时长表:合成库没有 intake 埋点,列必须全 NaN 而不是抛栈
    pp = A_ev.per_participant(ev_raw, parts_raw)
    assert len(pp) and "t_intake_total" in pp.columns, "逐被试时长表没出来"
    assert dev not in set(pp["participant_id"].dropna()), "dev 被试混进了逐被试时长表"
    ok(f"{len(A_ev._COLS)} 个事件层指标全部算出、dev 已排除;逐被试时长表 {len(pp)} 行")

    rpid, rridx = info["redo_rounds"][0]
    n_all = _q1(db_path, "SELECT COUNT(*) FROM events WHERE participant_id=? AND round_idx=?"
                         " AND type='llm_start'", (rpid, rridx))
    n_won = _q1(db_path, "SELECT COUNT(*) FROM events WHERE participant_id=? AND round_idx=?"
                         " AND type='llm_start' AND attempt IN (SELECT attempt FROM events"
                         " WHERE participant_id=? AND round_idx=? AND trial_id IS NOT NULL)",
                (rpid, rridx, rpid, rridx))
    assert n_all > n_won, "fixture 的重做轮没有作废段,切段逻辑测不到"
    got = merged.loc[(merged.participant_id == rpid) & (merged.round_idx == rridx),
                     "n_llm_calls"].iloc[0]
    assert got == n_won, f"重做轮 LLM 调用数 {got} ≠ 进论文那段的 {n_won}(作废段被算进来了)"
    ok(f"重做轮 (p{rpid} r{rridx}) 只算进了论文的那段:{n_won} 次调用(全轮 {n_all} 次)")

    # ---------- ④ 冻结分析计划的终点:LMM + 3 计划对比 + Holm ----------
    print("[4/10] 冻结分析计划终点的确证分析(LMM + 计划对比 + Holm)")
    # 三层必须与 stats.main 同构:少一层 build_quality,H4 的质量 DV 就根本不在 df 里
    df = A_stats.build_quality(A_stats.build_dose(A_stats.build_composites(merged)))
    endpoints = prereg.PRIMARY_ENDPOINTS + prereg.SECONDARY_ENDPOINTS
    for dv in endpoints:
        assert dv in df.columns, f"终点 {dv} 根本没构建出来"
        fit = A_stats.fit_lmm(df, dv)
        con = A_stats.contrasts(fit)
        assert list(con["contrast"]) == ["E-D", "E-C", "D-C"], con.to_dict("records")
        vals = con[["estimate", "se", "p_raw", "p_holm"]].to_numpy(dtype=float)
        assert np.isfinite(vals).all(), f"{dv}: 计划对比出现非有限值\n{con}"
        assert ((con["p_holm"] >= con["p_raw"] - 1e-12).all()
                and (con["p_holm"] <= 1.0).all()), f"{dv}: Holm 校正值不合法\n{con}"
        err, _log = quiet(lambda dv=dv: A_stats.analyze_endpoint(df, dv))
        assert err is None, f"{dv}: 确证分析缺失(退回 Wilcoxon)← {err}"
    ok(f"{len(endpoints)} 个终点全部拿到 LMM + 3 个计划对比 + Holm:{', '.join(endpoints)}")

    # 上面测的是函数;这里测研究员真正会跑的 `make stats` 命令行路径
    _, out = quiet(lambda: _cli(A_stats.main, ["--csv", str(csv)]))
    n_tab = out.count("LMM 计划对比")
    assert n_tab == len(endpoints), f"stats CLI 只打印了 {n_tab}/{len(endpoints)} 张计划对比表"
    assert "⛔ 最终警告" not in out, out[-800:]
    # 2026-09-01 拍板 2.4:H4 降为描述性,标题与口径都改了。门禁盯的是**新口径**:
    # 段落要在、且必须带上「不作确证性非劣主张」的警告 —— 若有人把它改回「非劣」,
    # 这条断言要当场拦住(那是把一个贴天花板的 DV 重新包装成结论)。
    assert "H4 质量(描述性" in out and "H5 剂量-反应" in out, "H4/H5 段落没跑"
    assert "不得写成「不劣」" in out or "未测量审美" in out or "INCONCLUSIVE" in out, \
        "H4 段落缺少『不作非劣主张』的口径警告(prereg.H4_NOTE 没打出来?)"
    # H4 的小标题是无条件打印的:质量 DV 建不出来时它照样在,底下换成一行 ⛔——
    # 只查标题等于没查,非劣主张可以整个消失而门禁全绿
    assert f"⛔ {A_stats._H4_QUALITY_DV} 未构建" not in out, \
        f"H4 段落只剩「{A_stats._H4_QUALITY_DV} 未构建」,非劣主张无 DV 可测\n{out[-800:]}"
    ok(f"make stats 的 CLI 路径:{n_tab} 张计划对比表 + H4(有 DV)/H5 段落,无 ⛔ 终点缺失汇总")

    # 零方差 DV 必须大声失败,不能"拟合成功"后打印 p=0(A1 的洞)
    df_flat = df.copy()
    df_flat["flat_dv"] = 1.0
    err, _log = quiet(lambda: A_stats.analyze_endpoint(df_flat, "flat_dv"))
    assert err, "零方差 DV 竟然产出了确证结论(Holm 把 NaN 排成了 0)"
    assert A_stats._holm([float("nan")] * 3) == [1.0, 1.0, 1.0], "NaN 的 p 被 Holm 排成了 0"
    ok(f"零方差 DV 被拦下:{err[:60]}…")

    # ---------- ⑤ TOST(H4 非劣) ----------
    print("[5/10] TOST 非劣(H4)")
    # SESOI 已于 2026-08-03 锁定(B2,单一真源 analysis/prereg.py)。门禁盯的是**拒绝行为**
    # 而不是字面值:没有界/界非法就不许出结论,有界就必须真出结论。prereg.py 只读,
    # 只在本进程内 monkeypatch 并原样还原。
    # 检验对象必须是冻结的 H4 质量 DV(结构完整度复合);它的三个成分只作描述性输出
    # (analysis/stats.py:397 明写"勿单独下非劣结论"),拿成分代打测不到 build_quality 的死活。
    h4_dv = A_stats._H4_QUALITY_DV
    assert h4_dv in df.columns, f"H4 的质量 DV {h4_dv} 没构建出来,非劣检验根本没有对象"
    # 2026-09-01 拍板 2.3:界按**终点**从 SESOI_BY_ENDPOINT 取,不再是一个标量套所有人。
    # 门禁必须打在真正被读的那个字典上 —— 改成按终点取界之后,老测试还在 patch 标量
    # `prereg.SESOI`,注入**静默失效**、tost 照常出结论,而断言仍然"通过"过一阵子。
    locked = prereg.SESOI_BY_ENDPOINT.get(h4_dv)
    assert isinstance(locked, (int, float)) and locked > 0, \
        f"SESOI_BY_ENDPOINT[{h4_dv}] 应是采数前锁定的正数(DV 原始单位),现在是 {locked!r}"
    r2, _log = quiet(lambda: A_stats.tost(df, h4_dv))
    assert isinstance(r2.get("equivalent"), bool), f"SESOI 已锁定却没出结论: {r2}"
    assert r2["bound"] == locked and r2["n"] >= 5, r2
    assert r2["verdict"] in ("equivalent", "INCONCLUSIVE"), r2   # 三分支必须表态
    ok(f"锁定的 SESOI={locked} → {h4_dv} 出结论 equivalent={r2['equivalent']}"
       f"/verdict={r2['verdict']}(mean_diff={r2['mean_diff']:.3f}、n={r2['n']})")

    # 两个主终点也必须各自登记了界(此前它们的「≈(不劣)」格在统计上是空的)
    for dv in prereg.PRIMARY_ENDPOINTS:
        b = prereg.SESOI_BY_ENDPOINT.get(dv)
        assert isinstance(b, (int, float)) and b > 0, \
            f"主终点 {dv} 没有冻结的等价界 → 四象限的「≈」格无检验支撑"
    ok(f"两个主终点各自登记了等价界: "
       f"{ {k: prereg.SESOI_BY_ENDPOINT[k] for k in prereg.PRIMARY_ENDPOINTS} }")

    orig = dict(prereg.SESOI_BY_ENDPOINT)
    try:
        for bad in (None, 0.0, -0.10):  # 未锁定 / 研究员手滑填 0 或负数
            prereg.SESOI_BY_ENDPOINT[h4_dv] = bad
            rb, _log = quiet(lambda: A_stats.tost(df, h4_dv))
            assert rb["equivalent"] is None and rb.get("verdict") == "INCONCLUSIVE" \
                and "SESOI" in rb.get("note", ""), (bad, rb)
        # 未登记的终点也必须拒绝,而不是回退到别的终点的界
        prereg.SESOI_BY_ENDPOINT.pop(h4_dv, None)
        rb, _log = quiet(lambda: A_stats.tost(df, h4_dv))
        assert rb["equivalent"] is None and rb.get("verdict") == "INCONCLUSIVE", rb
        ok("SESOI = None / 0 / 负数 / 未登记 → TOST 一律拒绝执行,不回退任何默认界")
    finally:
        prereg.SESOI_BY_ENDPOINT.clear()
        prereg.SESOI_BY_ENDPOINT.update(orig)
    assert prereg.SESOI_BY_ENDPOINT == orig, "monkeypatch 之后没把 SESOI_BY_ENDPOINT 还原"

    r3, _log = quiet(lambda: A_stats.tost(df[df["condition"] != "E"], h4_dv))
    assert r3["equivalent"] is None and "缺条件" in r3["note"], r3
    ok("试测期缺 E 条件 → TOST 给出'缺配对数据',不抛 KeyError")

    # ---------- ⑥ E 专属的引导剂量列 ----------
    print("[6/10] E 引导剂量列(H5 的自变量)")
    is_e = merged["condition"] == "E"
    for c in ("g_custom_rate", "g_ai_decided_rate", "g_n_questions", "g_n_rounds"):
        assert merged.loc[is_e, c].notna().all(), f"E 行的 {c} 有空值"
        assert merged.loc[~is_e, c].isna().all(), f"{c} 漏进了 C/D 行"
    assert df.loc[is_e, "dose_composite"].notna().all(), "dose_composite 在 E 内有空值"
    dose, _log = quiet(lambda: A_stats.dose_response(df, "fidelity_composite"))
    assert "beta" in dose, f"H5 剂量-反应没跑出来: {dose}"
    ok(f"g_* 仅 E 非空;dose_composite 就绪,H5 OLS n={dose['n']} beta={dose['beta']:.3f}")

    # 三条腿必须各自有被试间方差:若 g_* 是常数,复合就退化成 pre_investment 的线性缩放,
    # H5 看着"跑通"其实只测了时间(fixture 缺陷 S2)
    e_rows = df[df["condition"] == "E"]
    for c in ("g_custom_rate", "g_ai_decided_rate"):
        assert e_rows[c].std() > 0, f"{c} 在 E 内零方差,这条腿是常数"
    r_legs = float(e_rows["g_custom_rate"].corr(e_rows["g_ai_decided_rate"]))
    assert abs(r_legs) < 0.99, f"g_custom_rate 与 g_ai_decided_rate 完全同步(r={r_legs:.6f}),两条腿=一条"
    r_dose = float(e_rows["dose_composite"].corr(e_rows["pre_investment"]))
    assert abs(r_dose) < 0.99, \
        f"dose_composite 只是 pre_investment 的线性缩放(r={r_dose:.6f}),H5 剂量模型没被测到"
    # 题数本身是随机的(n_items),所以上面两条挡不住 S2 那种退化:is_custom=(i==0) 时
    # g_custom_rate 恒等于 轮数/题数,列上照样有方差(实测 r_legs=0.409、r_dose=0.592,全绿)。
    # 真正要的是**答法本身**的被试间方差 —— 同题数同轮数的人之间也得不一样。
    for c in ("g_custom_rate", "g_ai_decided_rate"):
        cell = e_rows.groupby(["g_n_questions", "g_n_rounds"])[c]
        within = cell.std()[cell.size() >= 2]
        assert (within > 0).any(), \
            f"{c} 在「同题数同轮数」的被试之间零方差,这条腿只是题数的换算,H5 剂量是假的"
    ok(f"剂量三条腿各自有方差(自填/AI代答 r={r_legs:.3f});"
       f"dose_composite vs pre_investment r={r_dose:.3f},不是纯缩放;"
       "同题数同轮数内答法仍有被试间方差")

    # ---------- ⑦ 面板重算不得抹掉 embed_fidelity ----------
    print("[7/10] 面板重算 per-trial 时 embed_fidelity 必须活着")
    from views import analysis_panel  # noqa: PLC0415(要 streamlit,晚导入)

    base = pd.read_csv(csv)
    base["embed_fidelity"] = np.linspace(-0.2, 0.2, len(base))
    base.to_csv(csv, index=False)
    kept, restored = analysis_panel.preserve_merged(v3.per_trial(v3.load(db_path)), csv)
    assert "embed_fidelity" in kept.columns, "点一次「出分析结果」就抹掉了 embed_fidelity"
    assert "embed_fidelity" in restored, "接回来了但没报告接了哪些列"
    chk = kept.merge(base[["participant_id", "round_idx", "embed_fidelity"]],
                     on=["participant_id", "round_idx"], suffixes=("", "_old"))
    assert len(chk) == len(kept), "接回 embed_fidelity 时行数变了"
    assert np.allclose(chk["embed_fidelity"], chk["embed_fidelity_old"]), \
        "embed_fidelity 接回来了但和原值对不上(按行序而非 (被试,轮次) 接的?)"
    ok(f"embed_fidelity 非空 {int(kept['embed_fidelity'].notna().sum())}/{len(kept)},逐 (被试,轮次) 数值一致")

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        A_stats.build_composites(kept)
    assert not [w for w in ws if "fidelity_composite" in str(w.message)], \
        f"四条腿齐全却仍告警缺腿: {[str(w.message) for w in ws]}"
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        A_stats.build_composites(kept.drop(columns=["embed_fidelity"]))
    assert [w for w in ws if "fidelity_composite" in str(w.message)], \
        "少了 embed_fidelity 这条腿却不告警 —— 保真复合会静默降级"
    ok("缺 embedding 腿时 build_composites 告警,补齐后不告警")

    # 事件层列同理:也是别的步骤(events.py)合进同一个 CSV 的,面板重算必须一并接回
    lost = [c for c in A_ev._COLS if c not in kept.columns]
    assert not lost, f"面板重算丢掉了事件层列 {lost} —— 与 embed_fidelity 同一类静默抹除"
    ok(f"事件层 {len(A_ev._COLS)} 列同样接回(共接回 {len(restored)} 列)")
    kept.to_csv(csv, index=False)

    # ---------- ⑧ 图 ----------
    print("[8/10] 图表产出")
    A_fig.CSV, A_fig.FIGDIR = csv, figdir
    _, _log = quiet(lambda: _cli(A_fig.main, ["--db", str(db_path)]))
    promised = ["fig_effort.png", "fig_ownership.png", "fig_fidelity.png", "fig_diversity.png"]
    for name in promised:
        f = figdir / name
        assert f.exists() and f.stat().st_size > 1000, f"图没写出来/是空文件: {name}"
    fig4 = csv.parent / "fig4_coding_material.csv"
    assert fig4.exists() and len(pd.read_csv(fig4)) > 0, "fig4 编码原料为空"
    ok(f"{len(promised)} 张图 + fig4 编码原料({len(pd.read_csv(fig4))} 条)全部落盘")

    # ---------- ⑨ 空库 ----------
    print("[9/10] 空库(试测第一位被试提交前)")
    from core import db as core_db  # noqa: PLC0415

    empty_db, empty_csv = tmp / "empty.db", tmp / "empty.csv"
    core_db.DB_PATH = empty_db
    core_db.init_db()
    _, out = quiet(lambda: _cli(v3.main, ["--db", str(empty_db), "--out", str(empty_csv)]))
    assert "N=0" in out, f"空库的提示不对: {out!r}"
    assert not empty_csv.exists(), "空库竟然写出了 CSV"
    _, out = quiet(lambda: _cli(A_ev.main, ["--db", str(empty_db), "--csv", str(empty_csv)]))
    assert "N=0" in out, f"空库 events 的提示不对: {out!r}"
    ok("v3 / events 在空库上干净退出(不抛栈、不写空 CSV)")

    # ---------- ⑩ 试测规模(3-6 人;可能整条 E 臂还没跑) ----------
    print("[10/10] 试测规模(N=4;以及 E 臂整条缺失)")
    from analysis import pilot_check  # noqa: PLC0415

    p_db, p_csv = tmp / "pilot.db", tmp / "pilot.csv"
    p_info = gen_synthetic_db.generate(p_db, n=4, seed=11)
    assert p_info["null_final"] and p_info["null_versions"], "试测规模下半截行埋点丢了"
    _, out = quiet(lambda: _cli(v3.main, ["--db", str(p_db), "--out", str(p_csv)]))
    assert p_csv.exists() and len(pd.read_csv(p_csv)) == p_info["trials_non_dev"], out[-400:]
    _, _log = quiet(lambda: _cli(A_ev.main, ["--db", str(p_db), "--csv", str(p_csv)]))
    _, out = quiet(lambda: _cli(A_stats.main, ["--csv", str(p_csv)]))
    assert "端点: ownership_composite" in out and "H4 质量(描述性" in out, out[-800:]
    _, out_p = quiet(lambda: pilot_check.run(p_db))
    for sec in ("D 地板效应", "C 天花板", "novice 占比", "量表信度"):
        assert sec in out_p, f"pilot_check 少了「{sec}」段:\n{out_p[-600:]}"
    ok(f"N=4({p_info['trials_non_dev']} trials):v3 / events / stats / pilot_check 全链跑通,不抛栈")

    # 整条终点全 NaN(试测常见:某量表还没接上)→ 必须报"数据不足",不能硬拟合
    d_nan = A_stats.build_composites(pd.read_csv(p_csv))
    d_nan["satisfaction"] = np.nan
    err, _log = quiet(lambda: A_stats.analyze_endpoint(d_nan, "satisfaction"))
    assert err and "数据不足" in err, f"整列 NaN 的终点竟然出了结论: {err}"
    ok(f"整列 NaN 的终点被拦下:{err}")

    con = sqlite3.connect(p_db)   # E 臂整条缺失(试测排期里 E 还没轮到)
    try:
        con.execute("DELETE FROM trials WHERE condition='E'")
        con.commit()
    finally:
        con.close()
    e_csv = tmp / "pilot_noE.csv"
    _, _log = quiet(lambda: _cli(v3.main, ["--db", str(p_db), "--out", str(e_csv)]))
    _, _log = quiet(lambda: _cli(A_ev.main, ["--db", str(p_db), "--csv", str(e_csv)]))
    _, out = quiet(lambda: _cli(A_stats.main, ["--csv", str(e_csv)]))
    assert "缺条件 E/D 的配对数据" in out, f"缺 E 臂时 TOST 没报缺配对:\n{out[-800:]}"
    # "大声降级"只有两种合法形态:要么 ⛔ 汇总里逐条点名缺确证结果的终点,要么(若 stats
    # 日后学会缺臂降级)照常出 D−C,但绝不能凭空打印含 E 的计划对比
    if "⛔ 最终警告" in out:
        named = sum(f"   - {dv}" in out for dv in endpoints)
        assert named == len(endpoints), f"只点名了 {named}/{len(endpoints)} 个终点\n{out[-800:]}"
        how = f"{named} 个终点在 ⛔ 最终警告里逐条点名"
    else:
        assert "E-D" not in out and "E-C" not in out, \
            f"E 臂整条缺失却打印了含 E 的计划对比:\n{out[-800:]}"
        how = "stats 缺臂降级且没凭空造出含 E 的对比"
    _, out_p = quiet(lambda: pilot_check.run(p_db))
    assert "量表信度" in out_p and "trials 各条件" in out_p, out_p[-400:]
    ok(f"缺 E 臂:{how}、TOST 报缺配对、pilot_check 照常出 4 项读数")


def _q1(db_path: Path, sql: str, args: tuple):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchone()[0]
    finally:
        con.close()


def _cli(main_fn, argv: list[str]):
    """按命令行参数调一个 main()(argparse 读 sys.argv)。"""
    old = sys.argv
    sys.argv = [main_fn.__module__, *argv]
    try:
        return main_fn()
    finally:
        sys.argv = old


def main() -> None:
    had_analysis_dir = (ROOT / "data" / "analysis").exists()
    tmp = Path(tempfile.mkdtemp(prefix="novastory_smoke_"))
    print(f"临时工作目录: {tmp}")
    try:
        run(tmp)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("\n" + "!" * 78)
        print(f"⛔ ANALYSIS SMOKE FAILED(已通过 {len(_CHECKS)} 项,见上方栈)")
        print("!" * 78)
        sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert (ROOT / "data" / "analysis").exists() == had_analysis_dir, \
        "自测污染了项目树:data/analysis/ 被创建"
    print("\n" + "=" * 78)
    print(f"ANALYSIS SMOKE PASSED —— {len(_CHECKS)} 项断言全过,临时目录已清理")
    print("=" * 78)


if __name__ == "__main__":
    main()
