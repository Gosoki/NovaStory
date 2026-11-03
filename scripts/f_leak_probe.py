#!/usr/bin/env python
"""F 出题的「题面夹带候补」实测 —— 开采前的放行条件(06 ㉜,2026-11-03;make probe-f)。

⚠️ 会调 API(默认 4 题 × 3 个创意 × 2 次 × E/F 两个提示词 = 48 次),跑之前先问用户。

为什么要测:F 把 AI 的选项藏起来,所以题面里不能写候补。题面含 ≥ 2 个选项原文时,app 会把 3 个固定观点
换成固定的开放式题面、把 AI 补的题直接不问(views/guidance._f_strip_leaks)。中国 E 的提示词没有「题面不写
候补」这条,按同一规则算,40 个第一轮里有 17 个会只剩 3 道固定题(平均 6.2 → 4.85 题)。F 的提示词加了
这条,实际会好多少只有真实模型说了算;丢得多,F 的题就比 E 少,F−E 又多一个混杂。

做法:同一批创意,分别用 E 和 F 的第一轮提示词出题,用 app 里同一套规范化与丢题规则计数。
放行:F 丢题后的平均题数比 E 少不超过 MAX_COUNT_GAP 题,且 AI 补的题被丢 ≤ MAX_DROP_SHARE。
不过 → 先改 F 的提示词(prompts.build_system_guidance_round1_f)再测,通过后才开采。

用法(用采数时的模型快照;本机 secrets 第一个配置不是它,要显式指定):
  .venv/bin/python scripts/f_leak_probe.py --config-index <openai 那条> --model gpt-5.4-mini-2026-03-17
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as stats
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import prompts, state  # noqa: E402
from core.llm_batch import BatchClient  # noqa: E402
from views import guidance  # noqa: E402  (_normalize_questions / _f_leaks / _f_strip_leaks = app 的同一套规则)

MAX_COUNT_GAP = 0.5     # F 丢题后平均题数最多比 E 少这么多
MAX_DROP_SHARE = 0.10   # AI 补的题最多丢这么多

# 每题 3 个创意:笼统 / 中等 / 具体(被试写的创意长短不一,夹带候补多发生在创意笼统时)
INTENTS = {
    0: ("切符を拾って、ちょっといいことが起きる",
        "拾った切符の行き先に行ってみたら、落とし主の思い出の場所だった",
        "落とし主を探して駅を走り回ったのに、切符は自分の弟のものだった"),
    1: ("最後のひと口をめぐる話",
        "最後のひと口を譲ったら、相手が半分にして返してくれた",
        "兄弟が最後のひと口を取り合っているうちに、犬に食べられてしまう"),
    2: ("知らない街でわくわくする",
        "引っ越し初日に迷子になり、入ったパン屋の店主に道を教わる",
        "スマホの電池が切れて、看板だけを頼りに歩き出す"),
    3: ("待っている間に不安がふくらむ",
        "5分前に着いたら、相手はもう物陰からこっちを見ていた",
        "相手に送るメッセージを何度も書いては消す"),
}


def ask(client: BatchClient, system: str, user: str) -> list[dict] | None:
    raw = client.generate(system, user)[0]
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        return guidance._normalize_questions(json.loads(m.group(0) if m else raw))
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config-index", type=int, default=0, help="secrets.toml 的 [[api_configs]] 序号")
    ap.add_argument("--model", default="", help="覆盖该配置的模型名(钉采数快照用)")
    ap.add_argument("--reps", type=int, default=2, help="每个创意出几次题")
    ap.add_argument("--lang", default="ja")
    a = ap.parse_args()

    client = BatchClient.from_secrets(a.config_index)
    if a.model:
        client.model = a.model
    print(f"模型 {client.meta()} · 每个创意 {a.reps} 次 · lang={a.lang}")
    topics = state.load_topics()
    e_n, f_n, e_leak, f_leak, f_q, f_ai, f_drop, f_fixed, f_short, bad = [], [], 0, 0, 0, 0, 0, 0, 0, 0
    for ti, intents in INTENTS.items():
        for intent in intents:
            user = prompts.build_user_guidance_round1(topics[ti], intent, a.lang)
            for _ in range(a.reps):
                e = ask(client, prompts.build_system_guidance_round1(a.lang), user)
                f = ask(client, prompts.build_system_guidance_round1_f(a.lang), user)
                if not e or not f:
                    bad += 1
                    continue
                e_leak += sum(bool(q["options"]) and guidance._f_leaks(q) for q in e)
                f_leak += sum(bool(q["options"]) and guidance._f_leaks(q) for q in f)
                f_q += len(f)
                f_ai += sum(q["dimension"] not in guidance._FIXED_DIMS for q in f)
                kept, n_fixed, n_drop = guidance._f_strip_leaks([dict(q) for q in f])
                f_fixed += n_fixed
                f_drop += n_drop
                e_n.append(len(e))
                f_n.append(len(kept or []))
                f_short += len(kept or []) <= len(guidance._FIXED_DIMS)
                print(f"  题{ti + 1} E {len(e)} 题 · F {len(f)} → {len(kept or [])} 题(换固定题面 {n_fixed}、丢 {n_drop})")
    if not e_n:
        print("✗ 一组都没拿到可用的出题(模型 / 网关不通?)")
        return 1
    gap = stats.mean(e_n) - stats.mean(f_n)
    drop_share = f_drop / f_ai if f_ai else 0.0
    print(f"\n共 {len(e_n)} 组(解析失败 {bad})")
    print(f"  题面夹带候补:E {e_leak}/{sum(e_n)} 题(参考,E 不处理) · F {f_leak}/{f_q} 题")
    print(f"  F:固定观点换题面 {f_fixed} · AI 补的题被丢 {f_drop}/{f_ai}({drop_share:.0%})· 只剩 3 道固定题的组 {f_short}/{len(f_n)}")
    print(f"  平均题数:E {stats.mean(e_n):.2f} · F(丢题后){stats.mean(f_n):.2f} · 差 {gap:.2f}")
    ok = gap <= MAX_COUNT_GAP and drop_share <= MAX_DROP_SHARE
    print(("✅ 放行" if ok else "❌ 不放行 —— 先改 prompts.build_system_guidance_round1_f 再测")
          + f"(条件:题数差 ≤ {MAX_COUNT_GAP}、丢题 ≤ {MAX_DROP_SHARE:.0%})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
