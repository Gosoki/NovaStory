#!/usr/bin/env python
"""冻结产物生成器 —— 把分析计划连同**能翻转结论的每一个旋钮**固化成带 hash 的单一文件。

背景:本研究**不做第三方预注册**(B3, 2026-08-03),替代做法是「内部冻结 + git 留档」。
那么这份产物就是全部防御力所在 —— 答辩/审稿被问 HARKing 时,能拿出来的只有它和 git 历史。

⚠️ 为什么不能只 dump `prereg.as_dict()`:能翻转 H4 结论的常量**不在 prereg 里**。
`SPEC_TOL_SEC`(总时长容差,直接决定 spec_ok)、`_SHOT_FIELDS`(字段齐全率的分母)、
各复合的腿集合、z 的作用域,全都住在 `analysis/v3.py` / `stats.py` 里。只冻 prereg
等于把锁挂在门框上、门还开着。本脚本因此同时记录:

  ① `prereg.as_dict()` —— 阈值 / novice 定义 / SESOI / 终点层级 / 复合公式 / 判定分支
  ② **散落在管线里的实际旋钮**(逐个取值,不是靠人抄)
  ③ 关键源文件的 **sha256**(改一个字节 hash 就变)
  ④ git HEAD + 工作树是否干净
  ⑤ `pip freeze`(依赖版本也能改变结果:statsmodels 换版本 LMM 数值会动)

产物 = canonical JSON(键排序、UTF-8),外加一个自身的 sha256。

用法:
  .venv/bin/python scripts/freeze_prereg.py                 # 打印到终端(预演)
  .venv/bin/python scripts/freeze_prereg.py --write         # 写 docs/paper/prereg_frozen.json
  .venv/bin/python scripts/freeze_prereg.py --check         # 校验:冻结值 == 当前代码实际值

冻结当天的顺序(docs/paper/05):
  1. 清干净工作树(git status 无输出)
  2. 删 analysis/metrics.py(v2/HLZ 遗留)
  3. make smoke && make smoke-e2e && make robust  全绿
  4. .venv/bin/python scripts/freeze_prereg.py --write
  5. **单独一个 commit 只放冻结产物**,message: `FREEZE: analysis plan, before any main-collection data`
  6. push 到 origin(本地 git 历史可改写,推上去才有第三方时间戳)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "paper" / "prereg_frozen.json"

# 改动会影响结果的源文件。核心判据:「改了它,同一份数据可能得出不同结论吗?」
HASHED_FILES = [
    "analysis/prereg.py",      # 阈值 / SESOI / 人群 / 终点
    "analysis/v3.py",          # 所有确定性指标的算法(结构完整度、逐镜头保真、剂量…)
    "analysis/stats.py",       # LMM / 计划对比 / Holm / TOST / 复合构建
    "analysis/events.py",      # 事件层指标(问卷时长、调用次数)
    "analysis/embed.py",       # embedding 保真 Δ(占保真复合一半权重)
    "analysis/textstats.py",   # 文本指标
    "analysis/pilot_check.py", # 试测 go/no-go 判读
    "core/shots.py",           # 分镜解析器 —— 它的宽严直接决定 parse_ok 与所有逐镜头指标
    "core/config.py",          # N_ROUNDS / LATIN_SQUARE_N / MIN_INTENT_CHARS / TEMPERATURE
]


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def collect_knobs() -> dict:
    """把散落在管线各处、能翻转结论的常量**逐个读出来**(不靠人抄)。"""
    from analysis import stats as A_stats
    from analysis import v3 as A_v3
    from core import config as C

    knobs = {
        "v3.SPEC_TOL_SEC": A_v3.SPEC_TOL_SEC,
        "v3._SHOT_FIELDS": list(A_v3._SHOT_FIELDS),
        "v3._TAGS": list(A_v3._TAGS),
        "config.N_ROUNDS": C.N_ROUNDS,
        "config.LATIN_SQUARE_N": C.LATIN_SQUARE_N,
        "config.MIN_INTENT_CHARS": C.MIN_INTENT_CHARS,
        "config.TEMPERATURE": C.TEMPERATURE,
        "config.SUPPLEMENT_RANGE": list(C.SUPPLEMENT_RANGE),
        "config.FOLLOWUP_RANGE": list(C.FOLLOWUP_RANGE),
    }
    # stats 侧的腿集合与 H4 DV 名 —— 命名随版本变过,缺哪个就记 None 而不是让脚本崩
    for name in ("_H4_QUALITY_DV", "_H4_LEGS", "_FIDELITY_LEGS", "_EFFORT_LEGS",
                 "_DOSE_LEGS", "_OWN_LEGS", "_Z_SCOPE"):
        v = getattr(A_stats, name, None)
        knobs[f"stats.{name}"] = list(v) if isinstance(v, (list, tuple, set)) else v
    return knobs


def build() -> dict:
    from analysis import prereg

    files = {}
    for rel in HASHED_FILES:
        p = ROOT / rel
        files[rel] = _sha256_file(p) if p.exists() else None

    # 已跟踪的改动 + 未跟踪的新文件。不解析 porcelain 的状态前缀 —— 前缀宽度随
    # 状态组合变化(重命名带 "->"、路径含空格会被引号包起来),切错一个字符就得到
    # 一个看着像路径的错路径。
    dirty = "\n".join(x for x in (
        _git("diff", "--name-only", "HEAD"),
        _git("ls-files", "--others", "--exclude-standard"),
    ) if x)
    payload = {
        "_what": "NovaStory 分析计划冻结产物(内部冻结,非第三方预注册 —— B3)",
        "_warning": ("⛔ 论文/发表不得写 'preregistered' / 「事前登録」。"
                     "可写:分析计划在采数前确定并纳入版本管理。"),
        "prereg": prereg.as_dict(),
        "pipeline_knobs": collect_knobs(),
        "file_sha256": files,
        "git": {
            "head": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "worktree_clean": dirty == "",
            "dirty_files": sorted(set(dirty.splitlines())) if dirty else [],
        },
        "python": sys.version.split()[0],
        "packages": _installed_packages(),
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    payload["_self_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return payload


def _installed_packages() -> list[str]:
    """依赖版本清单。用 importlib.metadata 而不是 `pip freeze` —— 本项目的 venv 里
    **没有装 pip**(`No module named pip`),那条路径会静默返回空列表,而依赖版本恰恰
    能改变结果(statsmodels 换个版本 LMM 的数值就会动)。"""
    try:
        import importlib.metadata as md
        out = []
        for dist in md.distributions():
            name = (dist.metadata or {}).get("Name")
            if name:
                out.append(f"{name}=={dist.version}")
        return sorted(set(out))
    except Exception:  # noqa: BLE001
        return []


def check(path: Path) -> int:
    """冻结之后的守门:产物里记的值 == 当前代码的实际值吗?"""
    if not path.exists():
        print(f"⛔ 冻结产物不存在:{path}\n   采数前必须先跑一次 --write。")
        return 1
    frozen = json.loads(path.read_text(encoding="utf-8"))
    cur = build()
    bad = []

    for k, v in frozen.get("prereg", {}).items():
        if cur["prereg"].get(k) != v:
            bad.append(f"prereg.{k}: 冻结={v!r} 现在={cur['prereg'].get(k)!r}")
    for k, v in frozen.get("pipeline_knobs", {}).items():
        if cur["pipeline_knobs"].get(k) != v:
            bad.append(f"旋钮 {k}: 冻结={v!r} 现在={cur['pipeline_knobs'].get(k)!r}")
    for rel, h in frozen.get("file_sha256", {}).items():
        if cur["file_sha256"].get(rel) != h:
            bad.append(f"文件 {rel} 已改动(sha256 不符)")

    if not bad:
        print(f"✅ 冻结校验通过 —— 分析计划与管线常量与 {path.name} 一致。")
        print(f"   冻结于 git {frozen.get('git', {}).get('head', '?')[:10]}"
              f" · 产物 sha256 {frozen.get('_self_sha256', '?')[:16]}")
        return 0
    print(f"⛔ 冻结校验失败 —— {len(bad)} 处与冻结产物不一致:")
    for b in bad:
        print(f"   · {b}")
    print("\n   采数已开始的话,这属于**协议偏离**:必须在 docs/paper/07 的偏差记录里写明")
    print("   改了什么、为什么、影响哪些 participant_id,并给冻结产物打新版本号。")
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description="生成/校验分析计划冻结产物")
    ap.add_argument("--write", action="store_true", help=f"写入 {OUT.relative_to(ROOT)}")
    ap.add_argument("--check", action="store_true", help="校验冻结值 == 当前实际值")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if args.check:
        sys.exit(check(args.out))

    payload = build()
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if not args.write:
        print(text)
        print(f"(预演 —— 加 --write 才会写入 {args.out.relative_to(ROOT)})", file=sys.stderr)
        return

    if not payload["git"]["worktree_clean"]:
        print("⚠️ 工作树不干净,冻结产物记录的 git HEAD 无法唯一还原代码状态:")
        for f in payload["git"]["dirty_files"][:10]:
            print(f"     {f}")
        print("   建议先提交或 stash 再冻结(仍会写入,但请自行确认这是你要的)。")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"✅ 已写入 {args.out}")
    print(f"   产物 sha256 = {payload['_self_sha256']}")
    print(f"   git HEAD    = {payload['git']['head']}")
    print("\n下一步(顺序不能乱):")
    print("   1. git add docs/paper/prereg_frozen.json")
    print("   2. git commit -m 'FREEZE: analysis plan, before any main-collection data'")
    print("      ——**这个 commit 只放冻结产物**,不要夹带别的改动")
    print("   3. git push origin  ——本地历史可改写,推上去才有第三方时间戳")


if __name__ == "__main__":
    main()
