"""提问的价值 QV1「问题产出」(2026-11-03,docs/paper/06 ㉚-5;prereg.QUESTION_VALUE_EXPLORATORY)—— 全部探索性。

用户的原则(2026-11-02):再聪明的模型也猜不出用户没想好的东西 → 一个问题的价值,在于问出了模型自己写不出来、
又进了作品的内容。QV1 把它量成:回答的字符 bigram 里,
  · 同题同语言机器基线(全部稿子)里没有  —— 模型不问就写不出来
  · 创意陈述里没有                        —— 不是提问之前就已经说过的
  · 题面与分镜格式词里也没有
  · 而终稿里有                            —— 进了作品
的个数(每个 trial 一个值)。来源分两种:「自己写」(自填)与「点选 AI 选项」(E 默认显示选项;F 的选项要点开才看得到,06 ㉜);
「交给 AI」没有内容,不计。两种来源重合的 bigram 记在「自己写」名下。

中国 E 只作描述;日本的 F−E 配对比较在 analysis/f_analysis.py 里报(调用本模块的 trial_qv)。
只读库与基线文件,不调 API。

用法:  .venv/bin/python analysis/question_value.py [--db data/novastory.db] [--lang zh|ja]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import embed, prereg  # noqa: E402
from analysis.f_analysis import _FORMAT_BG, _bigrams, _s  # noqa: E402
from core.shots import strip_format  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "novastory.db"


def _answers(guidance_json: str) -> tuple[list[str], list[str]]:
    """(自己写的回答, 点选的 AI 选项)。E 与 F 同一套 item 形状(F 自 2026-11-03 起与 E 同一出题组件,06 ㉜);
    交给 AI 的题没有内容。"""
    own, opt = [], []
    for rnd in json.loads(_s(guidance_json) or "{}").get("rounds", []):
        for it in rnd.get("items", []):
            if not isinstance(it, dict) or it.get("ai_decided"):
                continue
            text = str(it.get("chosen") or "").strip()
            if text:
                (own if it.get("is_custom") else opt).append(text)
    return own, opt


def trial_qv(db_path: Path, ids: set[int], conds=("E", "F")) -> pd.DataFrame:
    """每个 E / F trial 一行:qv_own / qv_opt(两种来源各自进终稿的新内容 bigram 数)、qv_yield = 两者之和,
    以及分母 new_own / new_opt(回答里「模型写不出来」的 bigram 数)。"""
    from core import state  # noqa: PLC0415 —— 与 embed / ja_predictions 同一份题库归一化
    topics = state.load_topics()
    title2idx = {state.topic_text(t, "title", embed.BASELINE_LANG): i for i, t in enumerate(topics)}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(conds))
        tr = pd.read_sql("SELECT t.participant_id, t.round_idx, t.condition, p.lang, t.topic_json,"
                         " t.intent_statement, t.final_output, t.guidance_json"
                         f" FROM trials t JOIN participants p ON p.id = t.participant_id WHERE t.condition IN ({q})",
                         con, params=conds)
    finally:
        con.close()
    tr = tr[tr["participant_id"].isin(ids)]
    base_bg: dict[tuple[int, str], set[str]] = {}
    rows = []
    for r in tr.itertuples():
        lang = _s(r.lang) or "ja"
        topic = json.loads(_s(r.topic_json) or "{}")
        ti = title2idx.get(state.topic_text(topic, "title", embed.BASELINE_LANG))
        if ti is None:
            continue
        if (ti, lang) not in base_bg:
            base_bg[(ti, lang)] = set().union(*(_bigrams(strip_format(x)) for x in embed._baseline_texts(ti, topics, lang)))
        known = (base_bg[(ti, lang)] | _FORMAT_BG | _bigrams(_s(r.intent_statement))
                 | set().union(*(_bigrams((topic.get(k) or {}).get(lang, "")) for k in ("title", "scenario", "choices"))))
        own, opt = _answers(r.guidance_json)
        new_own = _bigrams(" ".join(own)) - known
        new_opt = _bigrams(" ".join(opt)) - known - new_own
        final = _bigrams(strip_format(_s(r.final_output)))
        rows.append({"participant_id": r.participant_id, "round_idx": r.round_idx, "condition": r.condition,
                     "lang": lang, "new_own": len(new_own), "new_opt": len(new_opt),
                     "qv_own": len(new_own & final), "qv_opt": len(new_opt & final),
                     "has_own": bool(own)})
    out = pd.DataFrame(rows, columns=["participant_id", "round_idx", "condition", "lang", "new_own", "new_opt",
                                      "qv_own", "qv_opt", "has_own"])
    out["qv_yield"] = out["qv_own"] + out["qv_opt"]
    return out


def describe(qv: pd.DataFrame) -> list[str]:
    """按条件的描述:每个 trial 平均有多少「模型写不出来、又进了终稿」的内容,分自己写 / 点选两种来源。"""
    def rate(num: int, den: int) -> str:
        return f"{num / den:.0%}" if den else "—"

    lines = []
    for c, g in qv.groupby("condition"):
        lines.append(
            f"{c}(trial {len(g)}):问题产出 qv_yield 均值 {g['qv_yield'].mean():.1f}(中位 {g['qv_yield'].median():.0f})"
            f" = 自己写 {g['qv_own'].mean():.1f} + 点选 {g['qv_opt'].mean():.1f};"
            f"回答里模型写不出来的内容进了终稿的比例:自己写 {rate(g['qv_own'].sum(), g['new_own'].sum())}"
            f" · 点选 {rate(g['qv_opt'].sum(), g['new_opt'].sum())}")
        if c == "E" and g["has_own"].any() and (~g["has_own"]).any():
            a, b = g[g["has_own"]], g[~g["has_own"]]
            lines.append(f"   E 里自己写过回答的 trial {len(a)} 个:qv_yield {a['qv_yield'].mean():.1f};"
                         f"只点选 / 交给 AI 的 {len(b)} 个:{b['qv_yield'].mean():.1f}(自选分组,只作描述)")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--lang", choices=prereg.COHORT_LANGS, default=None, help="只看一个队列(默认两个都看)")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"⛔ 没有 {args.db}")
        return 1
    from analysis import v3  # noqa: PLC0415
    keep = v3.included_participants(args.db)
    qv = trial_qv(args.db, keep)
    if args.lang:
        qv = qv[qv["lang"] == args.lang]
    spec = next(x for x in prereg.QUESTION_VALUE_EXPLORATORY if x["id"] == "QV1")
    print("=" * 78)
    print("提问的价值 QV1「问题产出」(prereg.QUESTION_VALUE_EXPLORATORY,2026-11-03;探索性,只作描述)")
    print(f"  定义:{spec['what']}")
    print("=" * 78)
    if qv.empty:
        print("⛔ 没有可算的 E / F trial")
        return 1
    for lg, g in qv.groupby("lang"):
        print(f"\n[{lg}] 被试 {g['participant_id'].nunique()} 人")
        for ln in describe(g):
            print("  " + ln)
    print("\n  读法:数值是 bigram 个数,不是字数;只说明「问出了模型默认写不出、又进了终稿的内容有多少」,"
          "不说明作品更好。日本的 F−E 比较见 make stats-f。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
