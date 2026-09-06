"""按需给分镜配图(仅当正式模型 = OpenAI 时启用)。

流程:被试提交这一版后进入本轮问卷 → 页面立刻显示、分镜「画面」列先显示"生成中" →
后台线程用 gpt-image-1-mini 并行生成 3 张手绘风插图、逐张写盘 → 问卷里的分镜表用 fragment
每 2 秒轮询磁盘、好一张显示一张。**全程非阻塞、失败即静默降级**(某镜失败就停在"生成中",
绝不打断实验)。图落 `data/storyboard_images/{被试}_{轮次}/{attempt}/`(留档,已 gitignore)。

生图在**提交之后**发生,不进创作净时长(t_pregen/t_postgen)。风格锁定见 samples/imggen/README。
线程读不到 session_state,故 api_key/base_url 由调用方在主线程捕获后传入。
"""
from __future__ import annotations

import base64
import html
import io
import threading
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
_ARCHIVE = _ROOT / "data" / "storyboard_images"

# 全局在飞图片数上限(进程级,跨被试共享)。
# 每位被试提交后起 3 个 worker 并发出图;5 人同时提交就是 15 张齐发,30 人同步走到第 1 轮
# 结束时可冲到约 45 张/分钟 —— 这会撞穿 gpt-image-1-mini 的分钟限额,而 429 被下面的
# `except Exception: pass` 吞掉,表现为分镜表里几格永远空白、被试和研究员都收不到任何提示。
# 按每张约 12 秒算,8 个并发 ≈ 40 张/分钟,把爆发摊平到限额之下;排队只是让图晚几秒出现,
# 而配图是在提交之后生成的,不占创作计时,被试完全无感。
_INFLIGHT = threading.Semaphore(8)
# 2026-08-03 选型实测(4 候选 × 2 轮 × 3 镜,风格指标 + 延迟 + 成本):
# gpt-image-1-mini 在**每一项**上都优于原来的 gpt-image-1 —— 快 30%(11.7s vs 16.6s)、
# 便宜 5 倍($0.0022 vs $0.0109/张)、彩度 0.0(原 1.1,风格锁写明 "No color")、
# 线条最干净(连通成分 5 / 微小碎片 0,原 10 / 4)。
# gpt-image-1.5 与 gpt-image-2 的笔画严重碎裂(成分 86 / 264、碎片 80 / 238)并出现
# 阴影块,违反 _STYLE 的 "clean thin uniform black outlines" + "NO shading"。
# ⚠️ 图像模型**没有带日期的快照可钉**(唯一有的 gpt-image-2-2026-04-21 风格最差)——
# 这是 B7 钉快照规则的一个已知例外。缓解:配图不是 DV;被试内在 C/D/E 均等,漂移不偏
# E−D 主对比;每张图都归档在 data/storyboard_images/,事后可复算风格指标检测漂移。
_IMG_MODEL = "gpt-image-1-mini"

# 锁定的简笔清线风格(samples/imggen/README)
_STYLE = (
    "Simple minimalist black-and-white line drawing, clean thin uniform black outlines "
    "on a plain solid white background. Very few lines, flat, minimal detail. "
    "NO shading, NO cross-hatching, NO texture, NO gradient. Draw only the key subject; "
    "empty white background. Do NOT draw any border, frame or panel; leave generous white "
    "margin around the subject. No color, no text, no numbers. Scene: "
)


def endpoint_supports_images() -> bool:
    """仅当当前配置的接口是 OpenAI(图像模型只在 OpenAI 上有)时开启配图。
    判据是 base_url 含 openai.com:填了别的 OpenAI 兼容网关会**静默**不配图。

    `NOVASTORY_NO_IMAGES=1` 是给测试脚本的总闸。起因:`state._ensure_api_defaults` 会
    把 secrets 里 api_configs[0] 自动灌进 session_state,而正式配置的就是 OpenAI ——
    于是 dev_smoke_e2e 这类"以为自己不联网"的脚本每跑一次都会真的发起 6 次配图任务
    (每次最多 3 张图)。它们的 worker 是 daemon 线程,脚本一结束就被杀,所以磁盘上看不到
    任何痕迹,却可能已经把请求发出去了 —— 最坏的一种失败:烧钱且无声。
    """
    import os
    if os.environ.get("NOVASTORY_NO_IMAGES") == "1":
        return False
    if not _is_real_server():
        return False
    return "openai.com" in (st.session_state.get("base_url") or "")


def _is_real_server() -> bool:
    """在真正的 Streamlit 服务器里跑,还是在 AppTest / 裸脚本里?

    这是比环境变量更硬的一道防线:环境变量要靠每个测试脚本的作者记得设,
    而**实际发生过**的事是 —— 修好了仓库里的两个脚本,scratchpad 里另外几个
    测试脚本照样打了 36 次真实图像 API。判据:AppTest 会把 Runtime 换成
    MagicMock,裸脚本里 get_instance() 直接抛 RuntimeError,只有真服务器
    拿得到货真价实的 Runtime 实例。
    """
    try:
        from streamlit.runtime import get_instance
        rt = get_instance()
    except Exception:  # noqa: BLE001 — 裸脚本里 get_instance() 直接抛,没有 runtime
        return False
    # AppTest 塞的是 MagicMock(spec=Runtime),所以 isinstance 判不出来 —— 看真实类型。
    return not type(rt).__module__.startswith("unittest.mock")


def _attempt() -> str:
    return st.session_state.get("r_attempt") or "na"


def _dir(pid: int, ridx: int) -> Path:
    """归档目录按 (被试, 轮次, attempt) 分。同一轮重做(问卷页刷新 → 续接 → 从 intent 重跑,
    或 devtools 切换条件)会换一份新稿;只按 {pid}_{ridx} 命名的话,问卷页会把**上一次尝试**
    的旧图贴在新稿旁边 —— 插图是主观 DV 的测量情境,配错稿等于换了题。旧尝试的图仍留档。"""
    return _ARCHIVE / f"{pid}_{ridx}" / _attempt()


def _scene(shot: dict) -> str:
    return (shot.get("visual") or shot.get("raw") or "").strip()[:300]


def ensure_started(pid: int, ridx: int, shots: list[dict],
                   api_key: str, base_url: str) -> None:
    """本轮第一次进问卷时启动一次后台生成(session 标志防重复)。"""
    flag = f"_imggen_started_{pid}_{ridx}_{_attempt()}"
    if st.session_state.get(flag):
        return
    st.session_state[flag] = True
    d = _dir(pid, ridx)
    d.mkdir(parents=True, exist_ok=True)
    scenes = [_scene(s) for s in shots]

    def worker() -> None:
        try:
            import concurrent.futures as cf

            from openai import OpenAI
            from PIL import Image
            client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=120)

            def one(item: tuple[int, str]) -> None:
                i, scene = item
                out = d / f"shot{i + 1}.jpg"
                done = d / f"shot{i + 1}.done"  # attempt-finished marker (see all_done)
                if out.exists() or done.exists():
                    return
                try:
                    if scene:
                        # size/quality 已是**最省的档**,别再"优化":
                        #   · 256x256 / 512x512 → 400 Invalid size(不支持),1024x1024 是最小可选;
                        #   · size="auto" 会选 1536x1024 = 400 tok(贵 47%),所以必须写死 1024x1024;
                        #   · quality low=272 tok / medium=1056 / high=auto=4160 → low 便宜 3.9-15 倍。
                        # 生成 1024 再缩到 512 存盘不是浪费——1024 就是最小生成尺寸。
                        with _INFLIGHT:      # 全局限流,见 _INFLIGHT 处的说明
                            r = client.images.generate(
                                model=_IMG_MODEL, prompt=_STYLE + scene, n=1,
                                size="1024x1024", quality="low",
                            )
                        png = base64.b64decode(r.data[0].b64_json)
                        im = Image.open(io.BytesIO(png)).convert("RGB")
                        # keep the FULL 1:1 image (just shrink) — the 16:9 frame shows
                        # it whole via object-fit:contain (white side-bars blend into
                        # the white frame), so heads/feet aren't cropped.
                        im = im.resize((512, 512))
                        tmp = out.with_suffix(".tmp")
                        im.save(tmp, "JPEG", quality=82, optimize=True)
                        tmp.replace(out)  # atomic: reader never sees a half-written file
                except Exception:  # noqa: BLE001 — one image failing must never break the study
                    pass
                # Mark the attempt finished (success wrote out; empty scene / failure
                # writes only this marker) so all_done() can settle and the 2s poll
                # stops instead of spinning forever on a failed shot.
                try:
                    done.write_bytes(b"")
                except OSError:
                    pass

            with cf.ThreadPoolExecutor(max_workers=3) as ex:
                list(ex.map(one, list(enumerate(scenes))))
        except Exception:  # noqa: BLE001
            # 线程池起来之前就炸(缺 PIL / openai、key 为空)以前不写任何标记 → all_attempted
            # 永不为真,问卷页每 2 秒 fragment 重跑一整场。逐镜补上「尝试过了」的标记。
            for i in range(len(scenes)):
                try:
                    (d / f"shot{i + 1}.done").write_bytes(b"")
                except OSError:
                    pass

    threading.Thread(target=worker, daemon=True).start()


# 已编码的图片:键是 (路径, mtime_ns, 大小),文件一变键就变,不会发到旧图。
# 问卷页每次 rerun 都要重建整张分镜表 —— 而被试**每点一次量表选项**就是一次全量 rerun。
# 不缓存的话,每次点选都要把三张图重新读盘 + base64,rerun 时间被推过 Streamlit 的 0.5s
# 阈值,整页就会淡成 opacity .33(那正是被试反馈的「问卷暗下来」)。
_B64_CACHE: dict[tuple[str, int, int], str] = {}


def _img_tag(f: Path) -> str:
    st_ = f.stat()
    key = (str(f), st_.st_mtime_ns, st_.st_size)
    tag = _B64_CACHE.get(key)
    if tag is None:
        tag = f'<img src="data:image/jpeg;base64,{base64.b64encode(f.read_bytes()).decode()}" alt=""/>'
        # 上限按**并发人数**定,不是按单人:它是进程级共享字典,每人每轮占镜数条。
        # 64 条只够 21 人同时在问卷页(3 镜),超了就整体 clear、所有人一起回到未命中。
        # 128 条约 3.8 MB,对 2.7 GB 可用内存可忽略。
        if len(_B64_CACHE) > 128:
            _B64_CACHE.clear()
        _B64_CACHE[key] = tag
    return tag


def frame_htmls(pid: int, ridx: int, n: int, generating_label: str) -> list[str]:
    """每镜的画框内容:已好→<img data-uri>,未好→"生成中"占位。供 _storyboard 的 sketches。"""
    d = _dir(pid, ridx)
    out = []
    for i in range(1, n + 1):
        f = d / f"shot{i}.jpg"
        if f.exists():
            out.append(_img_tag(f))
        elif (d / f"shot{i}.done").exists():
            out.append("")  # attempted but no image → fall back to the frame placeholder
        else:
            out.append(f'<span class="lbl">{html.escape(generating_label)}</span>')
    return out


def n_images(pid: int, ridx: int, n: int) -> int:
    """实际生成成功的张数(问卷页看到几张图 —— 这是主观 DV 的测量情境,要落库)。"""
    d = _dir(pid, ridx)
    return sum((d / f"shot{i}.jpg").exists() for i in range(1, n + 1))


def all_attempted(pid: int, ridx: int, n: int) -> bool:
    """True once every shot has either an image or a .done marker (failed/empty) —
    「都试过了」,不是「都生成成功了」,
    so the questionnaire's 2s poll settles instead of spinning on a failed shot."""
    d = _dir(pid, ridx)
    return all((d / f"shot{i}.jpg").exists() or (d / f"shot{i}.done").exists()
               for i in range(1, n + 1))
