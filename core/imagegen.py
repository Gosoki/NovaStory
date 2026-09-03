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
    判据是 base_url 含 openai.com:填了别的 OpenAI 兼容网关会**静默**不配图。"""
    return "openai.com" in (st.session_state.get("base_url") or "")


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


def frame_htmls(pid: int, ridx: int, n: int, generating_label: str) -> list[str]:
    """每镜的画框内容:已好→<img data-uri>,未好→"生成中"占位。供 _storyboard 的 sketches。"""
    d = _dir(pid, ridx)
    out = []
    for i in range(1, n + 1):
        f = d / f"shot{i}.jpg"
        if f.exists():
            b64 = base64.b64encode(f.read_bytes()).decode()
            out.append(f'<img src="data:image/jpeg;base64,{b64}" alt=""/>')
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
