#!/usr/bin/env python3
"""把 docs/deck_teens.html 打包成单文件离线版。

用途:教室里没网、或者只想拷一个文件走。视频与图片会内嵌成 data URI,
成品不依赖 deck_teens/ 目录,也不发任何网络请求。

    python3 scripts/pack_deck_offline.py

**内容改动一律改 docs/deck_teens.html,改完重跑这个脚本。**
离线版是产物,直接编辑它会在下次打包时被覆盖。
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "deck_teens.html"
OUT = ROOT / "docs" / "deck_teens_offline.html"

MIME = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
}

BANNER = (
    "<!-- 离线单文件版:视频与图片已内嵌为 data URI,不依赖 deck_teens/ 目录,\n"
    "     也不访问任何网络。由 docs/deck_teens.html 经 scripts/pack_deck_offline.py 生成 ——\n"
    "     内容修改请改那一份再重新打包,直接改这里会被下次打包覆盖。 -->\n"
)


def main() -> int:
    if not SRC.exists():
        print(f"找不到 {SRC}", file=sys.stderr)
        return 1
    html = SRC.read_text(encoding="utf-8")
    embedded, missing, total = [], [], 0

    def repl(m: re.Match) -> str:
        nonlocal total
        rel = m.group(2)
        path = SRC.parent / rel
        # 注释里写的示例路径(deck_teens/文件名.mp4)不存在,原样留着
        if not path.exists():
            missing.append(rel)
            return m.group(0)
        ext = path.suffix.lower()
        if ext not in MIME:
            missing.append(f"{rel}(未知类型)")
            return m.group(0)
        raw = path.read_bytes()
        total += len(raw)
        embedded.append((rel, len(raw)))
        return f'{m.group(1)}="data:{MIME[ext]};base64,{base64.b64encode(raw).decode()}"'

    # 属性列表要覆盖全:src / href / poster,漏一个就会在离线版里留下死链
    packed = re.sub(r'(src|href|poster)="(deck_teens/[^"]+)"', repl, html)
    packed = packed.replace("<title>", BANNER + "<title>", 1)
    OUT.write_text(packed, encoding="utf-8")

    for rel, n in embedded:
        print(f"  内嵌 {rel}  {n / 1048576:.2f} MB")
    for rel in missing:
        print(f"  跳过 {rel}（文件不存在，多半是注释里的示例）")
    print(f"\n  {SRC.name}  {SRC.stat().st_size / 1048576:.2f} MB"
          f"  + 资源 {total / 1048576:.2f} MB")
    print(f"→ {OUT.name}  {OUT.stat().st_size / 1048576:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
