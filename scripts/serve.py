#!/usr/bin/env python3
"""生产环境启动入口:开启静态资源 gzip,然后把参数原样交给 streamlit CLI。

为什么需要这一层:
Streamlit 1.60 的 `SelectiveGZipMiddleware` 硬编码跳过 `/static/` 前缀,静态资源一律
以原文发送(实测线上 122 个请求、2.50 MB,Content-Encoding 全为空)。被试在日本、
服务器在境外,跨境链路上这 2.35 MB 原文 JS 是首屏的主要成本。

这里不改 site-packages(升级 Streamlit 就会丢,也污染依赖),而是在 Starlette app
构建之前替换 `StaticFiles.file_response`:客户端声明支持 gzip 且存在预压缩产物时,
直接发那个 .gz 文件。运行期不做压缩,所以 Streamlit 顾虑的 CPU/RSS 开销为零。

.gz 由 scripts/precompress_static.py 生成(make precompress)。产物不存在时这一层
什么都不做,服务照常启动 —— 只是回到未压缩的旧行为。

用法(参数与 `streamlit` 命令完全一致):
    python scripts/serve.py run app.py --server.port 8501
"""

from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 与 precompress_static.py 保持一致;两边都改才有意义,所以直接 import 而不是抄常量。
from scripts.precompress_static import COMPRESSIBLE_SUFFIXES  # noqa: E402


def _install_precompressed_static() -> bool:
    """让 StaticFiles 在条件满足时改发 .gz。返回是否成功挂上。"""
    try:
        from starlette.datastructures import Headers
        from starlette.responses import FileResponse
        from starlette.staticfiles import NotModifiedResponse, StaticFiles
    except ImportError:  # pragma: no cover — Streamlit 换掉 Starlette 时
        return False

    original = StaticFiles.file_response

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        gz = _pick_precompressed(full_path, stat_result, scope, status_code)
        if gz is None:
            return original(self, full_path, stat_result, scope, status_code)

        gz_path, gz_stat, media_type = gz
        # 显式给 media_type:让 FileResponse 自己猜的话,.gz 后缀会被猜成
        # application/gzip,浏览器会当成下载文件而不是执行脚本。
        response = FileResponse(
            gz_path, status_code=status_code, stat_result=gz_stat, media_type=media_type
        )
        response.headers["Content-Encoding"] = "gzip"
        # 同一 URL 对支持/不支持 gzip 的客户端返回不同字节,中间缓存必须按此分桶。
        response.headers["Vary"] = "Accept-Encoding"
        # ETag 由 gz 文件的 stat 生成,与上面发出的字节一致,304 才不会发错内容。
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response

    StaticFiles.file_response = file_response  # type: ignore[method-assign]
    return True


def _pick_precompressed(full_path, stat_result, scope, status_code: int):
    """能用预压缩产物就返回 (路径, stat, 原始 media_type),否则返回 None。"""
    if status_code != 200 or stat_result is None:
        return None

    path = Path(full_path)
    if path.suffix not in COMPRESSIBLE_SUFFIXES:
        return None

    from starlette.datastructures import Headers

    headers = Headers(scope=scope)
    if "gzip" not in headers.get("accept-encoding", ""):
        return None
    # Range 请求的字节区间是针对原文说的,换成 .gz 会返回错位的内容。
    # 这些资源不会被 range 请求,但发错内容的代价太高,直接让路。
    if "range" in headers:
        return None

    gz_path = path.with_suffix(path.suffix + ".gz")
    try:
        gz_stat = os.stat(gz_path)
    except OSError:
        return None
    # mtime 必须一致(precompress 用 copystat 对齐过),比到秒。只比「产物是否更旧」不够:
    # 把 Streamlit 降级到旧版本后,源文件 mtime 变早而 .gz 仍是上次压的,单向比较会放行,
    # 发出去就是新版本的 JS 配旧版本的 index.html。任一方向不一致都让路发原文。
    if int(gz_stat.st_mtime) != int(stat_result.st_mtime):
        return None

    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return gz_path, gz_stat, media_type


def main() -> int:
    enabled = _install_precompressed_static()
    if not enabled:
        print("⚠ 静态资源 gzip 未能挂载,以未压缩方式启动", file=sys.stderr)

    from streamlit.web.cli import main as streamlit_main

    # streamlit 的 click 入口从 sys.argv 取参数,把 argv[0] 换成它期待的名字即可透传。
    sys.argv = ["streamlit", *sys.argv[1:]]
    streamlit_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
