#!/usr/bin/env python
"""三语键树一致性检查(CLAUDE.md §0.3 硬约束①)。

ja/zh/en 的键树必须完全一致,**占位符也要对齐** —— 一个 `{shot_count}` 漏在某一语言里,
被试看到的就是原样的花括号,而这类缺失在 UI 上不会报错、只会静默出现在某一种语言的
某一个页面上,只有正好用那门语言走到那一步才会发现。

用法:  .venv/bin/python scripts/i18n_check.py
退出码 0 = 一致;1 = 有差异(逐条打印)。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "i18n" / "locales"
LANGS = ("ja", "zh", "en")
_PH_RE = re.compile(r"\{(\w+)\}")


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, f"{key}."))
        else:
            out[key] = v
    return out


def main() -> int:
    flat = {L: _flatten(json.loads((LOCALES / f"{L}.json").read_text(encoding="utf-8")))
            for L in LANGS}
    base = LANGS[0]
    bad: list[str] = []
    for L in LANGS[1:]:
        for k in sorted(set(flat[base]) - set(flat[L])):
            bad.append(f"{L} 缺键: {k}")
        for k in sorted(set(flat[L]) - set(flat[base])):
            bad.append(f"{L} 多出键(而 {base} 没有): {k}")
    for k in sorted(set(flat[base])):
        want = set(_PH_RE.findall(flat[base][k]))
        for L in LANGS[1:]:
            if k not in flat[L]:
                continue
            got = set(_PH_RE.findall(flat[L][k]))
            if got != want:
                bad.append(f"占位符不一致 {k}: {base}={sorted(want)} {L}={sorted(got)}")

    if bad:
        print(f"⛔ i18n 三语不一致 —— {len(bad)} 处:")
        for b in bad:
            print(f"   · {b}")
        return 1
    print(f"✅ i18n 三语一致:{len(flat[base])} 键 × {len(LANGS)} 语言,占位符全部对齐。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
