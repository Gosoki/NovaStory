#!/usr/bin/env python3
"""把 Streamlit 的静态资源预先 gzip 一份,供 scripts/serve.py 直接发送。

背景:Streamlit 1.60 在 `starlette_app.py` 里用 `SelectiveGZipMiddleware` 主动让
`/static/` 绕开 gzip,注释写的理由是「本地压测显示 bypass 能改善首屏和峰值 RSS」。
那个结论在局域网成立(压缩的 CPU 开销 > 省下的传输时间),但本项目的被试在日本、
服务器在境外,链路是高 RTT + 有限带宽,结论正好反过来:首屏 2.35 MB 的 JS 原文传输
才是瓶颈,gzip 后约 0.7 MB。

预压缩(而不是运行时压缩)同时保住了 Streamlit 那条理由 —— 运行期 CPU 与 RSS 开销为零,
只是多读一个文件;而且离线压可以用 level 9,比中间件为了延迟只能用的低 level 更小。

用法:
    python scripts/precompress_static.py          # 生成/更新
    python scripts/precompress_static.py --check   # 只报告是否最新(退出码 1 = 需重建)
    python scripts/precompress_static.py --clean   # 删除所有 .gz
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sys
from pathlib import Path

# 只压文本类资源。字体(woff2)、图片(png/webp)已经是压缩格式,再 gzip 只会变大。
COMPRESSIBLE_SUFFIXES = frozenset({".js", ".css", ".html", ".json", ".svg", ".txt", ".xml"})

# 小于这个大小的文件不值得压:一个 TCP 段就发完了,gzip 头反而可能让它变大。
MIN_SIZE_BYTES = 1024

# 压得再狠也省不下几个字节就放弃,避免生成一堆没用的 .gz。
MIN_RATIO_GAIN = 0.95

GZIP_LEVEL = 9


def static_dir() -> Path:
    """定位当前解释器所用 Streamlit 的 static 目录。"""
    import streamlit

    return Path(streamlit.__file__).parent / "static"


def targets(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in COMPRESSIBLE_SUFFIXES
        and p.stat().st_size >= MIN_SIZE_BYTES
    )


def is_fresh(src: Path, gz: Path) -> bool:
    """产物与源文件的 mtime 必须**严格相等**才算新鲜。

    compress_one 用 copystat 把源文件的 mtime 原样盖到 .gz 上,所以「相等」就是
    「这份 .gz 正是这个版本的源文件压出来的」。

    早先写的是 `gz >= src`,单向比较在**降级**时会失效:把 Streamlit 回退到旧版本后,
    源文件的 mtime 变早、而 .gz 还是上一次压的(更晚),`>=` 依然成立 —— 于是发出去的是
    新版本的 JS 配旧版本的 index.html,页面直接崩。任一方向不一致都必须判过期。

    比到**秒**而不是纳秒:装包、rsync、打包解包都可能让亚秒位漂移,纳秒相等会因此
    误判过期(后果只是回退发原文,安全但压缩白做);而升级/降级带来的 mtime 差异
    远大于一秒,秒级比较照样抓得住。

    serve.py 里有同样的判断作为第二道保险。
    """
    return gz.exists() and int(gz.stat().st_mtime) == int(src.stat().st_mtime)


def compress_one(src: Path) -> tuple[bool, int, int]:
    """返回 (是否写出, 原大小, 压后大小)。压缩率不划算时删掉 .gz 并返回 False。"""
    gz = src.with_suffix(src.suffix + ".gz")
    raw = src.read_bytes()
    # mtime=0 让输出字节稳定,便于 --check 与内容比对;时间戳本来也没人看。
    packed = gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)
    if len(packed) > len(raw) * MIN_RATIO_GAIN:
        gz.unlink(missing_ok=True)
        return False, len(raw), len(raw)
    tmp = gz.with_name(gz.name + ".tmp")
    tmp.write_bytes(packed)
    # 原子替换:半截的 .gz 会被浏览器当成损坏的响应,比没有 .gz 糟糕得多。
    tmp.replace(gz)
    # 让 .gz 的 mtime 不早于源文件,is_fresh 才判得对。
    shutil.copystat(src, gz)
    return True, len(raw), len(packed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只检查是否需要重建,不写文件")
    g.add_argument("--clean", action="store_true", help="删除所有预压缩产物")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = static_dir()
    if not root.is_dir():
        print(f"✗ 找不到 Streamlit 静态目录:{root}", file=sys.stderr)
        return 2

    if args.clean:
        removed = 0
        for gz in root.rglob("*.gz"):
            gz.unlink()
            removed += 1
        print(f"已删除 {removed} 个 .gz")
        return 0

    files = targets(root)
    stale = [p for p in files if not is_fresh(p, p.with_suffix(p.suffix + ".gz"))]

    if args.check:
        # 压不动的文件(如已高度压缩的 json)永远不会有 .gz,不能算作「过期」,
        # 否则 --check 会永远失败。真去压一遍才知道,所以这里只看大文件。
        really_stale = [p for p in stale if p.stat().st_size >= 8192]
        if really_stale:
            print(f"✗ {len(really_stale)} 个静态文件缺少或落后于预压缩产物,请运行:make precompress")
            for p in really_stale[:5]:
                print(f"    {p.relative_to(root)}")
            return 1
        print(f"✓ 预压缩产物是最新的({len(files) - len(stale)}/{len(files)})")
        return 0

    raw_total = gz_total = 0
    written = 0
    for p in files:
        ok, raw, packed = compress_one(p)
        raw_total += raw
        gz_total += packed
        written += ok

    if not args.quiet:
        mb = lambda n: f"{n / 1048576:.2f} MB"  # noqa: E731
        ratio = gz_total / raw_total if raw_total else 1.0
        print(f"预压缩完成:{written}/{len(files)} 个文件")
        print(f"  原文  {mb(raw_total)}")
        print(f"  gzip  {mb(gz_total)}  ({ratio:.0%})")
        print(f"  目录  {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
