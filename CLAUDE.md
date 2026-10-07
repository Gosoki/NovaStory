
# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 0.时不时要更新项目对应的说明（docs/paper）
 - 你可以适时增加新的md
 - 适时更新md
 - 适标记过时或废弃的文档md
 - 一些可能用得上的文献放到 `docs/paper/reference/` 里面
 - ⚠️ **旧 `paper/` 目录已于 2026-08-03 整体归档**（每个文件顶部有归档横幅，迁移对照见 `paper/README.md`）。**只读、勿引用为当前状态、勿再往里写。**

### 0.1 维护决策留痕 `docs/paper/10_核心决策留痕.md`
 - 每当发生这四类事件之一，就在该文件最上方追加一条（日期 + 类别标签 + 一句话 + 受影响文档/代码）：
   ① 研究方向/主线变更　② 实验设计重大调整　③ 应用版本性重构　④ 关键决策拍板
 - 日常小改不记。类别标签：`方向` / `设计` / `代码` / `决策` / `文档`。
 - 同时：方向/设计变更后，检查 `docs/paper/` 里被取代的段落是否需要改写，**并检查 `docs/index.html`（对外发表用幻灯片）与 `docs/paper/09_日本語資料.md` 的発表原稿是否需要同步**。

### 0.2 当前权威文档（避免引用过时内容）
 - **索引：`docs/paper/README.md`**。权威顺位：**代码 > `docs/index.html`（发表用幻灯片，对外口径）> `docs/paper/`**。
 - 研究定位/贡献措辞/禁用措辞/文献切割：`docs/paper/01`　实验设计+交互规格+运行流程：`docs/paper/02`　测量/量表/数据字典：`docs/paper/03`　假设表+分析计划：`docs/paper/04`　分析计划冻结+试测决策树（**第三方预注册不做**）：`docs/paper/05`　**待拍板事项：`docs/paper/06`**　采数前执行清单(部署/合规)：`docs/paper/07`　论文写作与投稿：`docs/paper/08`　日语资料(タイトル/背景目的/発表原稿/用語ルール)：`docs/paper/09`　决策留痕：`docs/paper/10`　**中国队列首版结论：`docs/paper/14`**　文献库：`docs/paper/reference/`
 - 分析管线：`analysis/{v3,stats,power_sim,embed,figures,norming,textstats}.py`（A6 v3，`make analysis` 串全链）；**试测健康检查 `analysis/pilot_check.py`（`make pilot`；4 生死问题→🟢🟡🔴 + 后手分支）**；旧 `analysis/metrics.py` 为 v2(HLZ) 遗留，收数验收后删；`scripts/judge.py`（不在 `analysis/`）= LLM-judge **只做盲评保真**，四维审美 rubric 已弃（B5, 2026-08-03）。
 - **预注册冻结常量的单一真源：`analysis/prereg.py`**（pilot 阈值 / novice 定义 / SESOI / 终点层级 / 复合公式）——阈值只改这里，`pilot_check`/`stats` 从它 import。
 - 关键设计常量：`core/config.py: LATIN_SQUARE_N=18`（**Williams 6 排列 × 3 题目**，不是 3×3 拉丁方）、`N_ROUNDS=3`、`MIN_INTENT_CHARS=8`（B6, 2026-08-03 由 10 改为 8）。
 - 现状（2026-09-30）：**中国队列 36 人已采完；剔除研究员演示会话 pid 1 后分析 N=35**（`prereg.EXCLUDED_SESSIONS`，2026-09-30 确认；补 1 人待定，`06` ㉔-1），已出首版结论（`docs/paper/14`：所有权 / 保真 E−D 均未检出提升）；日本队列目标约 18、只在服务器采（规程 `07 §3.6`）。2026-09-23 起定位改为结果驱动，**卡点 = `06` ㉔ 数据清洗与同意书（开日本队列前）+ ㉑ 论文定位 + ㉒ 合并分析参数 + ㉓ 结论措辞**；冻结文件从未生成。答辩火力点须按结果重写（`08 §11`）。

### 0.3 语言（实验对象是日本人）
 - 被试：中国队列 `zh`（已采完）+ 日本队列 `ja`（采集中）。代码当前默认 `zh`（`i18n/translator.py`），日本队列不改代码、用 `?lang=ja` 链接（`07 §3.6`）。**ja/zh/en 三语现在都是全链路可用**（UI + `prompts.build_*` 输出 + `data/topics.json` 的 `{ja,zh,en}` 情境 + `core/shots.py` 分镜解析），被试同意页三语可选；研究员后台随语言选择器切换。
 - 硬约束:①UI 文案 `i18n/locales/{ja,zh,en}.json` 三语键树必须一致(占位符也要对齐);②**LLM 输出语言由 `prompts.build_*` 的 `lang` 参数控制**(调用点传 `i18n.get_lang()`),每个 `build_*` 都有 zh/en/ja 三分支;③改分镜字段标记(【时长】/【Duration】…)时,`prompts.py` 与 `core/shots.py` 解析器必须同步。**改一处务必三语同步,别让被试看到混杂语言。**
 
# 0.4 commit-msg 规则
永远不用加claude做代码贡献者，只按我一个人作为贡献者提交 commit
有全局配置优先全局，commit就提交本地的，由用户自己推送到远端，
commit-msg 规则：
1. 全程简体中文，技术术语保留英文。
2. 每行格式 <emoji><type>(scope): 描述。
3. emoji 对照：feat✨ fix🐛 docs📝 style🎨 refactor♻️ perf⚡ test🧪 chore🧹 build🔨 ci🔧 revert⏪
4. 第一行是主题行，概括这次改动整体做了什么，type 和 emoji 取本次最主要的那个，50 字以内。
5. 改动涉及多个方面时，主题行后空一行，再逐条列明细：同一 type 只写一行，多个 scope 用逗号合并，
   如 ✨feat(auth,api,db): 描述；不同 type 各占一行。改动单一时只写主题行，不要硬凑明细。
6. 描述每行 50 字以内，说清改了什么，不写「修复bug」这类模糊表述。

多文件改动示例：
♻️refactor(aut,xx,yy,zz): 重构登录链路并补齐测试

✨feat(auth,api): 新增短信验证码登录接口
🐛fix(session): 修复 token 过期后未清理缓存
🧪test(auth): 补充登录失败分支用例"""

 

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.


## 5.优先mvp，一些小功能可以后期统一加
 - 项目可能改动很大，一些可以后期统一优化的功能可以在前期省略。
 - 需要记录这些小功能是否要加，留档让我决定
 - 你认为是核心流程需要的功能可以直接加

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.