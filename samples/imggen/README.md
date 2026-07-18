# 分镜配图 · 生图风格 & 可行性(2026-07-19)

用 OpenAI **gpt-image-1**(`quality="low"`,1536×1024)测试给分镜配手绘风插图。

## ✅ 规定风格(锁定,以后要生图都用这个前缀,保证风格一致)
```
Simple minimalist black-and-white line drawing, clean thin uniform black outlines
on a plain solid white background. Very few lines, flat, minimal detail.
NO shading, NO cross-hatching, NO hatching, NO texture, NO gradient.
Draw only the key subject with at most one simple prop; empty white background.
Do NOT draw any border, frame or panel; leave generous white margin around the subject.
No color, no text, no numbers. Scene: <这里接场景描述>
```
要点:①简笔清线、无阴影/排线/纹理 ②白底、无背景杂物 ③不画边框、留白 ④单一主体。
→ 这样多张之间**风格差距很小**,且够"简笔"。

## 当前用途
- **说明页样例**的 3 张(睡→惊醒→奔跑)就是用上面这个风格生成的,压缩后存 `assets/intro_sample/shot{1,2,3}.jpg`(各 ~12KB),base64 内嵌渲染(`views/intro.py` + `views/_storyboard.py`)。
- 本目录里 `gptimage1_*.png` 是**最早的测试**(细节偏多、还带手绘边框),留作对比;正式采用的是上面锁定的简笔风格。

## 结论(见 paper/11)
- 质量足够好,场景可辨,远胜手写 SVG。
- **但不进当前实验**:成本(~$0.015-0.02/张)、延迟(10-30s/张,实时生成会污染"等待时长"DV)、且**引入图像模态=改变研究**。
- 真要做"每镜配图"→ 作为独立视觉功能立项;当前实验保持文字分镜,样例配图仅示意。

测试累计花费约 $0.16。
