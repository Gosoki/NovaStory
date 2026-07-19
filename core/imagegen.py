"""按需给分镜配图(仅当正式模型 = OpenAI 时启用)。

流程:被试提交这一版后进入本轮问卷 → 页面立刻显示、分镜「画面」列先显示"生成中" →
后台线程用 gpt-image-1 并行生成 3 张手绘风插图、逐张写盘 → 问卷里的分镜表用 fragment
每 2 秒轮询磁盘、好一张显示一张。**全程非阻塞、失败即静默降级**(某镜失败就停在"生成中",
绝不打断实验)。图落 `data/storyboard_images/{被试}_{轮次}/`(留档,已 gitignore)。

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
_IMG_MODEL = "gpt-image-1"

# 锁定的简笔清线风格(samples/imggen/README)
_STYLE = (
    "Simple minimalist black-and-white line drawing, clean thin uniform black outlines "
    "on a plain solid white background. Very few lines, flat, minimal detail. "
    "NO shading, NO cross-hatching, NO texture, NO gradient. Draw only the key subject; "
    "empty white background. Do NOT draw any border, frame or panel; leave generous white "
    "margin around the subject. No color, no text, no numbers. Scene: "
)


def enabled() -> bool:
    """仅当当前配置的接口是 OpenAI(gpt-image-1 需要)时开启配图。"""
    return "openai.com" in (st.session_state.get("base_url") or "")


def _dir(pid: int, ridx: int) -> Path:
    return _ARCHIVE / f"{pid}_{ridx}"


def _scene(shot: dict) -> str:
    return (shot.get("visual") or shot.get("raw") or "").strip()[:300]


def ensure_started(pid: int, ridx: int, shots: list[dict],
                   api_key: str, base_url: str) -> None:
    """本轮第一次进问卷时启动一次后台生成(session 标志防重复)。"""
    flag = f"_imggen_started_{pid}_{ridx}"
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
                if out.exists() or not scene:
                    return
                try:
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

            with cf.ThreadPoolExecutor(max_workers=3) as ex:
                list(ex.map(one, list(enumerate(scenes))))
        except Exception:  # noqa: BLE001
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
        else:
            out.append(f'<span class="lbl">{html.escape(generating_label)}</span>')
    return out


def all_done(pid: int, ridx: int, n: int) -> bool:
    d = _dir(pid, ridx)
    return all((d / f"shot{i}.jpg").exists() for i in range(1, n + 1))
