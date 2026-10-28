"""第 4 条件 F 的日本队列内部分析(2026-10-27,docs/paper/06 ㉖)—— 只执行 prereg 里写死的东西。

  主要检验  prereg.F_PRIMARY    逐镜头「主に自分のアイデア」比例 mine_ratio(构念 = 想法来源归属,不是所有权),
                                F vs E,双侧配对 t(+ Wilcoxon、95% CI)→ 判定三分支(显著报方向 / 不显著走 TOST ±1/6)
  次要      prereg.F_SECONDARY  带事先写明的方向,只作描述,不进确证判定;F−D 不比较(prereg.F_NOT_COMPARED)
  操纵检查  prereg.F_MANIPULATION_CHECK  F 里全部回答的总字数与题数分布(3-5 题;从库里的 guidance_json 读)
  机制      prereg.F_MECHANISM  镜头层条件 logit:本人文字重合 / 所选选项重合 → 标「mine」(复现中国 SPA1 / SPA2)

纳入:CSV 是 v3 的逐 trial 表(已按 v3.included_participants 过滤:ja 要交满 4 份问卷);
这里再只取 lang ∈ 4 条件设计的语言、且 F 与对比条件都有的被试。
次要里的 fidelity_composite 用 stats.build_composites 在**日本 4 个条件的全部 trial** 上算 z
(与中国 / 合并分析的复合不在同一把尺上 —— 那两者不含 F,见 stats.drop_non_confirmatory)。

用法:  .venv/bin/python analysis/f_analysis.py [--csv data/analysis/v3_per_trial.csv] [--db data/novastory.db]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import re

import numpy as np
import pandas as pd
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import prereg  # noqa: E402
from core import config  # noqa: E402
from core.shots import parse_shots  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "analysis" / "v3_per_trial.csv"
DB = ROOT / "data" / "novastory.db"


def paired(df: pd.DataFrame, dv: str, hi: str, lo: str) -> dict | None:
    """每位被试一个 hi − lo 差值:配对 t(双侧)、Wilcoxon、均值差的 95% CI、dz。"""
    w = df.pivot_table(index="participant_id", columns="condition", values=dv, aggfunc="mean")
    if hi not in w or lo not in w:
        return None
    d = (w[hi] - w[lo]).dropna()
    n = len(d)
    if n < 2:
        return {"n": n}
    t = st.ttest_1samp(d, 0.0)
    se = d.std(ddof=1) / np.sqrt(n)
    half = st.t.ppf(0.975, n - 1) * se
    try:
        p_w = float(st.wilcoxon(d).pvalue) if (d != 0).any() else float("nan")
    except ValueError:
        p_w = float("nan")
    sd = d.std(ddof=1)
    return {"n": n, "mean": float(d.mean()), "ci_lo": float(d.mean() - half), "ci_hi": float(d.mean() + half),
            "dz": float(d.mean() / sd) if sd > 1e-9 else float("nan"), "p_t": float(t.pvalue), "p_wilcoxon": p_w}


def verdict(r: dict | None, sesoi: float, alpha: float) -> str:
    """prereg.F_PRIMARY["decision"] 的三分支:显著 → 报方向;不显著 → TOST(±sesoi)→ 等价 / INCONCLUSIVE。"""
    if not r or "p_t" not in r:
        return "INCONCLUSIVE(数据不足)"
    if r["p_t"] < alpha:
        return "显著:" + ("F 高于 E" if r["mean"] > 0 else "E 高于 F")
    n, m = r["n"], r["mean"]
    se = (r["ci_hi"] - r["ci_lo"]) / 2 / st.t.ppf(0.975, n - 1)
    if not se > 0:
        return "INCONCLUSIVE(配对差零方差)"
    p_lo, p_hi = st.t.sf((m + sesoi) / se, n - 1), st.t.cdf((m - sesoi) / se, n - 1)
    eq = p_lo < alpha and p_hi < alpha
    return (f"不显著 → TOST ±{sesoi:.3f}:p = {p_lo:.4f} / {p_hi:.4f} → "
            + ("等价" if eq else "INCONCLUSIVE(只写「未检测到差异」+ 95% CI,⛔ 不写「两者相同」)"))


def _fmt(r: dict | None) -> str:
    if not r:
        return "数据不足(缺这两个条件之一)"
    if "mean" not in r:
        return f"n={r['n']}(不足 2 人,不检验)"
    return (f"n={r['n']}  差={r['mean']:+.3f}  95%CI[{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]  "
            f"dz={r['dz']:+.2f}  配对t p={r['p_t']:.4f}  Wilcoxon p={r['p_wilcoxon']:.4f}")


def manipulation_check(db_path: Path, ids: set[int]) -> str:
    """F 里全部回答的总字数 + 题数分布(3-5 题)+ 退回固定题面的题数(题面由 AI 调整失败时)。"""
    if not db_path.exists():
        return f"(没有 {db_path},跳过)"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT participant_id, guidance_json FROM trials WHERE condition='F'").fetchall()
    finally:
        con.close()
    chars, n_q, n_fb, n_items = [], [], 0, 0
    for pid, g in rows:
        if int(pid) not in ids or not g:
            continue
        for rnd in json.loads(g).get("rounds", []):
            if rnd.get("source") != "interview":
                continue
            items = rnd.get("items", [])
            chars.append(sum(len(str(it.get("chosen") or "")) for it in items))
            n_q.append(len(items))
            n_fb += sum(bool(it.get("fallback")) for it in items)
            n_items += len(items)
    if not chars:
        return "(库里没有可用的 F 采访记录)"
    c = pd.Series(chars)
    dist = pd.Series(n_q).value_counts().sort_index().to_dict()
    return (f"n={len(c)}  回答总字数 中位 {c.median():.0f}(IQR {c.quantile(.25):.0f}–{c.quantile(.75):.0f},"
            f"最少 {c.min()}、最多 {c.max()});题数分布 {dist};退回固定题面 {n_fb}/{n_items} 题")


# ---------------- 机制:镜头层的想法来源(prereg.F_MECHANISM;定义同中国挖掘报告 SPA1 / SPA2) ----------------

_SEG = re.compile(r"[ぁ-ゖァ-ヺー㐀-䶿一-鿿A-Za-z0-9]+")
_FORMAT_WORDS = ("时长", "拍法", "画面", "描写", "台词", "音效", "镜头", "特写", "近景", "中景", "远景", "全景",
                 "推近", "拉远", "分镜", "脚本", "秒钟", "カット", "ショット", "アップ", "カメラ", "セリフ",
                 "効果音", "ナレーション", "ズーム", "クローズアップ", "引き", "寄り", "絵コンテ", "秒数")


def _bigrams(text: str) -> set[str]:
    out = set()
    for seg in _SEG.findall((text or "").lower()):
        out |= {seg[i:i + 2] for i in range(len(seg) - 1)}
    return out


_FORMAT_BG = set().union(*(_bigrams(w) for w in _FORMAT_WORDS))


def _s(x) -> str:
    return x if isinstance(x, str) else ""   # read_sql 把 NULL 读成 None / NaN


def shot_rows(db_path: Path, ids: set[int], conds=("E", "F")) -> pd.DataFrame:
    """每个镜头一行:mine(0/1)、本人文字重合 own_ov、所选 AI 选项重合 opt_ov、镜头位置。
    重合 = 该来源(去掉题面与格式词)的 bigram 里,有多少比例出现在这一镜的画面 + 台词里。
    本人文字 = 创意陈述 + 自己写的回答(F 的全部回答 / E 的自写回答);所选选项 = E 里点选的 AI 选项。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(conds))
        tr = pd.read_sql("SELECT t.participant_id, t.round_idx, t.condition, p.lang, t.topic_json,"
                         " t.intent_statement, t.final_output, t.guidance_json"
                         f" FROM trials t JOIN participants p ON p.id = t.participant_id WHERE t.condition IN ({q})",
                         con, params=conds)
        qs = pd.read_sql("SELECT participant_id, round_idx, shot_annotations_json FROM questionnaires", con)
    finally:
        con.close()
    m = tr[tr["participant_id"].isin(ids)].merge(qs, on=["participant_id", "round_idx"], how="inner")
    rows = []
    for r in m.itertuples():
        lang = _s(r.lang) or "ja"
        topic = json.loads(_s(r.topic_json) or "{}")
        tb = _FORMAT_BG.union(*(_bigrams((topic.get(k) or {}).get(lang, "")) for k in ("title", "scenario", "choices")))
        own, opt = [_s(r.intent_statement)], []
        for rnd in json.loads(_s(r.guidance_json) or "{}").get("rounds", []):
            for it in rnd.get("items", []):
                if it.get("is_custom"):
                    own.append(str(it.get("chosen") or ""))
                elif not it.get("ai_decided") and it.get("chosen"):
                    opt.append(str(it["chosen"]))
        u_own = _bigrams(" ".join(own)) - tb
        u_opt = _bigrams(" ".join(opt)) - tb - u_own
        tags = {a.get("shot"): a.get("tag") for a in json.loads(_s(r.shot_annotations_json) or "[]")
                if isinstance(a, dict)}
        for sh in parse_shots(_s(r.final_output)):
            if tags.get(sh["idx"]) not in prereg.SHOT_TAGS:
                continue
            sb = _bigrams(f"{sh.get('visual', '')}\n{sh.get('audio', '')}")
            rows.append({"trial": f"{r.participant_id}-{r.round_idx}", "condition": r.condition, "pos": sh["idx"],
                         "mine": int(tags[sh["idx"]] == "mine"),
                         "own_ov": len(sb & u_own) / len(u_own) if u_own else np.nan,
                         "opt_ov": len(sb & u_opt) / len(u_opt) if u_opt else 0.0})
    return pd.DataFrame(rows)


def mechanism(shots: pd.DataFrame, cond: str) -> str:
    """条件 logit(按 trial 分层,只用 mine 与非 mine 并存的 trial):mine ~ z(own_ov) [+ z(opt_ov)] + 位置。"""
    from statsmodels.discrete.conditional_models import ConditionalLogit  # noqa: PLC0415
    d = shots[shots["condition"] == cond].dropna(subset=["own_ov"])
    d = d[d.groupby("trial")["mine"].transform("nunique") > 1].copy()
    if d["trial"].nunique() < 5:
        return f"{cond}:mine 与非 mine 并存的 trial 只有 {d['trial'].nunique()} 个,信息量不足,不拟合"
    xs = ["own_ov"] + (["opt_ov"] if d["opt_ov"].std() > 0 else [])
    for x in xs:
        d[x + "_z"] = d[x] / d[x].std()
    d["pos2"], d["pos3"] = (d["pos"] == 2).astype(int), (d["pos"] == 3).astype(int)
    cols = [x + "_z" for x in xs] + ["pos2", "pos3"]
    try:
        fit = ConditionalLogit(d["mine"].to_numpy(), d[cols].to_numpy(float), groups=d["trial"].to_numpy()).fit(disp=0)
    except Exception as e:  # noqa: BLE001 —— 小样本下完全分离等;如实报告,不影响主检验
        return f"{cond}:拟合失败({type(e).__name__}: {e})"
    ci = fit.conf_int()
    out = [f"{x}: OR {np.exp(fit.params[i]):.2f} [{np.exp(ci[i, 0]):.2f}, {np.exp(ci[i, 1]):.2f}] p={fit.pvalues[i]:.3f}"
           for i, x in enumerate(xs)]
    return f"{cond}(信息 trial {d['trial'].nunique()} 个,镜头 {len(d)}):" + ";".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, default=CSV)
    ap.add_argument("--db", type=Path, default=DB)
    args = ap.parse_args(argv)

    raw = pd.read_csv(args.csv)
    langs = sorted(config.FOUR_COND_LANGS)
    df = raw[raw["lang"].isin(langs)] if "lang" in raw else raw.iloc[0:0]
    f = prereg.F_CONDITION
    have_f = set(df.loc[df["condition"] == f, "participant_id"]) if len(df) else set()
    df = df[df["participant_id"].isin(have_f)]
    print("=" * 78)
    print(f"第 4 条件 F 的日本内部分析(prereg.F_PRIMARY,2026-10-27)· 队列 {langs} · 被试 {len(have_f)} 人")
    print(f"  主要检验:{prereg.F_PRIMARY}")
    print("=" * 78)
    if not have_f:
        print("⛔ CSV 里还没有 F 的 trial(日本数据还没进库?)—— 无可分析。")
        return 1

    # prereg.JA_STOP_RULE:不满一整轮(16 序列各 ≥1 名完成者)→ F 的检验降为探索性
    n_round = prereg.WILLIAMS_ROUND_N_BY_LANG.get(langs[0], 16) if len(langs) == 1 else 16
    covered = (pd.to_numeric(df.drop_duplicates("participant_id")["seq"], errors="coerce").dropna()
               .astype(int).nunique() if "seq" in df else 0)
    explor = covered < n_round
    if explor:
        print(f"⛔ 只覆盖 {covered}/{n_round} 个序列(不满一整轮)→ 按 prereg.JA_STOP_RULE,下面 F 的判定**降为探索性**")
    hi, lo = prereg.F_PRIMARY["contrast"]
    print(f"\n[主要] {prereg.F_PRIMARY['dv']}  {hi} − {lo}(双侧,α={prereg.F_PRIMARY['alpha']};"
          f"构念 = {prereg.F_PRIMARY['construct']})")
    prim = paired(df, prereg.F_PRIMARY["dv"], hi, lo)
    print("  " + _fmt(prim))
    print("  → " + ("[探索性] " if explor else "") + "判定:"
          + verdict(prim, prereg.F_PRIMARY["sesoi"], prereg.F_PRIMARY["alpha"]))
    print("  ⚠️ F 的回答按生成 prompt 必须写进脚本 → F>E 有一部分按构造会出现;关于所有权只看下面的 own_mean")

    try:
        from analysis.stats import build_composites  # noqa: PLC0415
        comp = build_composites(df)
    except Exception as e:  # noqa: BLE001 —— 复合建不出来只影响一条次要
        print(f"  注:fidelity_composite 建不出来({type(e).__name__}: {e}),该次要项跳过")
        comp = df
    print("\n[次要](只作描述;方向是事先写明的预测;「差」一律 = 前者 − 后者,例如 F−E < 0 即 E 更高)")
    for name, direction in prereg.F_SECONDARY:
        dv, _, pair = name.partition(" · ")
        a, b = (pair.split("−") if pair else (hi, lo))
        src = comp if dv in comp.columns else df
        r = paired(src, dv, a, b) if dv in src.columns else None
        print(f"  {dv:<20} {a}−{b}  预测 {direction:<6}  " + _fmt(r))

    print(f"  (不比较 {'−'.join(prereg.F_NOT_COMPARED[0])}:{prereg.F_NOT_COMPARED[1]})")

    print(f"\n[操纵检查] {prereg.F_MANIPULATION_CHECK}")
    print("  " + manipulation_check(args.db, have_f))

    print(f"\n[机制(描述性)] {prereg.F_MECHANISM}")
    if args.db.exists():
        shots = shot_rows(args.db, have_f)
        for c in ("F", "E"):
            print("  " + (mechanism(shots, c) if len(shots) else f"{c}:库里没有可用的镜头标注"))
    else:
        print(f"  (没有 {args.db},跳过)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
