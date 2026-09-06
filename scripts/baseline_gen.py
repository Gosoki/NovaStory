#!/usr/bin/env python
"""T7.2 采样地板 — 每题 N 份纯机器 C 式输出 → data/baseline/topic{i}.jsonl。

每行: {topic_idx, sample_idx, seed, text, lang, model, temperature, base_url, ts}
断点续跑: 已有行数直接跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, prompts, shots, state  # noqa: E402
from core.llm_batch import BatchClient  # noqa: E402

OUT_DIR = ROOT / "data" / "baseline"

# 空稿连续这么多次就中止,不写半份基线。
_EMPTY_RETRIES = 3


def generate_nonempty(client, system: str, user: str, tag: str) -> str:
    """拿到一份非空稿,否则中止整个生成。

    空串绝不能写进基线:基线是 Δ = sim(创意,终稿) − sim(创意,基线质心) 的**零点**,
    一行空稿会把该题质心整体拉偏,而下游没有任何环节会告诉你 —— embed 只检查模型一致性,
    deploy_check 只看文件在不在。更要命的是基线**事后补不回来**(必须与采数同一个模型快照,
    采数结束时那个快照可能已经下线),所以宁可当场失败也不要留下一份被污染的零点。

    判空用 shots.strip_format:模型偶尔只回一串【时长】之类的空壳标签,字符数不为零但
    实质内容为空,和被试侧 views/_streaming.py 判空是同一口径。
    """
    for k in range(_EMPTY_RETRIES):
        text = client.generate(system, user)[0]
        if shots.strip_format(text).strip():
            return text
        print(f"  ⚠ {tag} 第 {k + 1} 次返回空稿,重试")
    raise SystemExit(
        f"✗ {tag} 连续 {_EMPTY_RETRIES} 次返回空稿 —— 基线里不能有空行,已中止。\n"
        "  已写入的行是好的,修好模型/提示词后重跑本命令会从断点续上。"
    )


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def assert_consistent(path: Path, client, lang: str) -> None:
    """续跑前校验已有行的 model/temperature/base_url/lang 与当前一致,
    防止换 --config-index/--temperature/--lang 续跑时同一题基线混用不同配置。

    lang 尤其要查:三种语言的输出文件同名(topic{i}.jsonl),拿 --lang zh 试跑过之后
    再补 ja,两种语言会混进同一题的质心,而 Δ 的零点就此错位。"""
    meta = {**client.meta(), "lang": lang}
    # 看**每一行**:只看首行的话,中途换配置续跑过的文件检不出来
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        prev = json.loads(line)
        for k in ("model", "temperature", "base_url", "lang"):
            if k in prev and k in meta and prev[k] != meta[k]:
                raise SystemExit(
                    f"[{path.name}] 续跑配置不一致:已有 {k}={prev[k]!r} vs 当前 {meta[k]!r}。"
                    " 同一题基线不能混用模型/温度/语言——删除该文件重跑,或换回原配置。")


def load_seeds(path: Path | None) -> list[str]:
    """意图列表(每行一条,空行忽略);为空时调用方退回题目情境。"""
    if path is None:
        return []
    lines = [s.strip() for s in path.read_text(encoding="utf-8").splitlines()]
    return [s for s in lines if s]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="生成机器基线(采样地板):对前 3 题各生成 N 份 C 式纯机器输出"
    )
    ap.add_argument("--n", type=int, default=30, help="每题样本数(默认 30)")
    ap.add_argument(
        "--seeds-file", type=Path, default=None,
        help="意图列表文件,每行一条,循环使用;缺省用题目 scenario 作为 seed",
    )
    ap.add_argument(
        "--config-index", type=int, default=0,
        help="secrets.toml 中 api_configs 序号(默认 0)",
    )
    ap.add_argument("--temperature", type=float, default=config.TEMPERATURE,
                    help="默认 = 被试用的 config.TEMPERATURE;基线与被试温度不同则 Δ 的零点不同源")
    ap.add_argument("--lang", default="ja", choices=("ja", "zh", "en"),
                    help="生成语言(正式=ja,与被试数据可比;embed 只对同语言被试算 Δ;zh/en 仅测试)")
    args = ap.parse_args()

    # 走 state.load_topics:与被试同一份归一化(shot_seconds→total_seconds、整数化),
    # 直接 json.loads 会绕过它,旧 schema 下基线的镜数/总秒数会与被试实际跑的题目不同。
    topics = state.load_topics()[: config.N_ROUNDS]
    seeds = load_seeds(args.seeds_file)
    client = BatchClient.from_secrets(args.config_index, temperature=args.temperature)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"模型: {client.model}  温度: {client.temperature}  每题 N={args.n}")

    for i, topic in enumerate(topics):
        out_path = OUT_DIR / f"topic{i}.jsonl"
        done = count_lines(out_path)
        if done >= args.n:
            print(f"[topic{i}] 已有 {done} 份,跳过")
            continue
        if done:
            assert_consistent(out_path, client, args.lang)  # 防续跑换模型/温度/语言混入同一题
            print(f"[topic{i}] 续跑:已有 {done} 份,补到 {args.n}")
        system = prompts.build_system_script(topic, args.lang)
        with out_path.open("a", encoding="utf-8") as f:
            for j in range(done, args.n):
                # scenario/choices are {ja, zh, en} dicts — localize and rejoin;
                # embed._baseline_texts reverse-looks-up topic identity by this
                # exact string, so both sides must build it the same way.
                seed = (seeds[j % len(seeds)] if seeds
                        else prompts.scenario_text(topic, args.lang, with_note=False))
                text = generate_nonempty(
                    client, system, prompts.build_user_script(topic, seed, args.lang),
                    tag=f"[topic{i}] {j + 1}/{args.n}",
                )
                rec = {
                    "topic_idx": i,
                    "sample_idx": j,
                    "seed": seed,
                    "text": text,
                    "lang": args.lang,
                    **client.meta(),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                print(f"[topic{i}] {j + 1}/{args.n} 完成 ({len(text)} 字)")
    print("基线生成完毕 →", OUT_DIR)


if __name__ == "__main__":
    main()
