# 换电脑继续工作（交接说明）

> 写于 2026-09-23。**项目状态以 `docs/paper/10_核心决策留痕.md`（最新的在最上面）为准**，本文只负责「怎么接上」。
> 本文不含任何被试数据，可以放在 git 里。

---

## 一、在新电脑上准备（用户做）

1. **拿代码**：`git clone https://github.com/Gosoki/NovaStory.git`
2. **拿数据**：在实验服务器的 code-server 里，按
   `local → Claude → NovaStory → data → analysis → transfer` 找到 `novastory_data_20260923.tar.gz`
   （灰色，因为在 gitignore 里），右键 → **Download**。
   下载后**解压到项目文件夹外面**（比如「下载」文件夹）。
3. **在项目文件夹里启动 Claude Code**，把下面第二部分那段话发给它。

---

## 二、发给新电脑上的 Claude 的话（整段复制）

```
我换了一台电脑继续这个项目。请先读 docs/HANDOFF_换电脑继续.md 的第三、四、五部分，然后：
1. 实验数据包我解压在了【这里填你解压的位置】，请按第三部分把数据放进 data/，并用 git status 确认 git 不会追踪它们。
2. 按第三部分装好 Python 环境，跑一下验证。
3. 读 docs/paper/10 最上面几条和 docs/paper/06 的 ㉑，用大白话告诉我我们停在哪了、下一步是什么。
```

---

## 三、给 Claude：数据与环境

### 数据放置

解压出来的文件夹 `novastory_data_20260923/` 里的东西，放到项目的 `data/` 下：

| 包里 | 放到 |
|---|---|
| `novastory.db` | `data/novastory.db` |
| `storyboard_images/` | `data/storyboard_images/` |
| `analysis/` | `data/analysis/` |
| `llm.log` | `data/llm.log` |
| `topics.json` | 不用放（git 里已有同一份） |
| `README.md` | 不用放（说明包内文件用的） |

放好后跑 `git status`：以上文件**都不能出现**（它们全在 `.gitignore` 里）。出现了就停下来告诉用户。
放好后可以删掉解压出来的那个文件夹和 `.tar.gz`（`.gitignore` 里已经兜底忽略了 `novastory_data_*`）。

### 环境

- Python **3.12**
- 建虚拟环境并装依赖：
  ```
  python -m venv .venv
  .venv/bin/pip install -r requirements.txt numpy scipy statsmodels matplotlib
  ```
  `requirements.txt` 只列了实验网站需要的包；**分析还需要后面四个**。
  Windows 上路径是 `.venv\Scripts\python.exe` / `.venv\Scripts\pip.exe`，Makefile 可能用不了，直接调 python。
- 验证：`.venv/bin/python scripts/analysis_smoke.py`，应输出 `ANALYSIS SMOKE PASSED`（它用合成数据，不碰真库）。
- 逐人对照页：`data/analysis/review/review.html` 双击用浏览器打开；数据有更新时用
  `.venv/bin/python scripts/review_viewer.py` 重新生成。

### 这台电脑的边界

- 数据是 **2026-09-23 的副本**：中国队列 36 人（`lang=zh`），**已去掉 #39 自愿留的联系邮箱**。
  **日本队列的新数据只会出现在实验服务器上**，采完后要重新从服务器拉一份。
- 实验网站跑在服务器上，**这台电脑不跑、不部署、不改线上服务**。
- **没有 API 密钥**（`.streamlit/secrets.toml` 不在 git 里），**也不要调 API**。
  `make baseline` / `make embed` / `make norming` / `scripts/judge.py` 都需要 API，不要跑。

---

## 四、工作规则

这些原本记在服务器上 Claude 的记忆里，新电脑的 Claude 看不到，所以抄在这里。

1. **不要替用户 `git commit`**，也不要 `git add` 了就当作完成。改完文件就停，说清楚改了哪些文件；用户在 VS Code 里自己看、自己提交。
2. **不要杀进程**（`pkill` / `killall` / `kill` 都不行）。
3. **研究设计 / 分析口径 / 合规类的改动，先记进 `docs/paper/06` 待拍板，等用户决定**；
   bug、健壮性、可读性这类可以直接修。
4. **删任何数据之前，先把要删的内容打出来看**，确认来源；清库用整库归档（`DEPLOY.md §3`），不用 `DELETE`。
5. **被试数据不进 git、不上传网盘或任何外部服务** —— 同意书里只写了数据会提供给 OpenAI。
6. 用子 agent 时，**子 agent 只读，改仓库文件只由主会话做**。
7. 重要决定记进 `docs/paper/10`：在最上面追加，写日期、类别标签、一句话、受影响的文档/代码（规则见 `CLAUDE.md §0.1`）。
8. **用中文，尽量大白话**。
9. 数据分析时，看了数据才想到的东西一律标「探索性」，不要写成事先就假设了（见 `docs/paper/10` 2026-09-23「方向」条目）。

---

## 五、当前进度（2026-09-23）

- **中国队列 36 人已采满**，18 个 seq 各 2 名完成者，条件顺序完全平衡。
- **日本队列计划约 18 人，尚未开始**。采不满就**停在 18**（18 恰好是一整轮 Williams）；问卷与同意书都不改，保持与中国队列一致。
- **主要结果**：所有权、保真都**没有检测到** E（先提问）比 D（先生成后修改）的提升。
  注意是「没检测到」，不是「证明了没有」，也不是「反过来了」。
  完整报告：`data/analysis/mining_20260923/data_mining_report.md`（13 个方向、全局多重比较校正）。
- **研究定位已改为结果驱动**（先有结果，再推导研究目的）。**现在正在选论文定位**：见 `docs/paper/06 ㉑`，
  候选 A「选择不等于创作」/ B「默认修改流程的真实使用」/ C「先提问值不值」，**推荐 A + C**。
- **等用户授权才能做**：机器对照（embedding 保真腿）需要调 API；另外代码目前只认日语（`analysis/embed.py` 写死 `BASELINE_LANG="ja"`）。
