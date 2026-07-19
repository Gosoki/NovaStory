
# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 0.时不时要更新项目对应的说明（paper）
 - 你可以适时增加新的md
 - 适时更新md
 - 适标记过时或废弃的文档md
 - 一些可能用得上的文献放到 paper/reference 里面

### 0.1 维护项目时间轴 `paper/11、项目时间轴.md`
 - 每当发生这四类事件之一，就在 paper/11 最上方追加一条（日期 + 类别标签 + 一句话 + 受影响文档/代码）：
   ① 研究方向/主线变更　② 实验设计重大调整　③ 应用版本性重构　④ 关键决策拍板
 - 日常小改不记。类别标签：`方向` / `设计` / `代码` / `决策` / `文档`。
 - 同时：方向/设计变更后，检查 paper/ 里被取代的文档是否需要加过时横幅（保留历史决策与原因，但不要让旧文档被误读为当前状态）。

### 0.2 当前权威文档（避免引用过时内容）
 - 研究方向/论文骨架+假设表：`paper/10`　实验交互规格：`paper/7 v4.1`　工程：`paper/5 v2.0`（留档性质，细节以代码为准）　运行流程：`paper/6`　主张/文献核查：`paper/9`　文献库：`paper/reference/`　待办/下一步：`paper/8`　数据字典/可计算指标：`paper/12`　数据管理与合规：`paper/13`　案头研究(维度/量表/主题/预注册/客观评价)：`paper/14`　日语研究背景与目的(草案)：`paper/15`　**试测决策树与后手**：`paper/16`
 - 分析管线：`analysis/{v3,stats,power_sim,embed,figures,norming,textstats}.py`（A6 v3，`make analysis` 串全链）；**试测健康检查 `analysis/pilot_check.py`（`make pilot`；4 生死问题→🟢🟡🔴 + 后手分支）**；旧 `analysis/metrics.py` 为 v2(HLZ) 遗留，收数验收后删。
 - 多专家充分性评估（2026-07-18）：裁决=**勉强够**（纸面超标、押在未采数据）；答辩火力点与「答辩前必修」清单见 `paper/8 §🎓`。
 - **全项目深度对抗评审（2026-07-19，34 确认/17 驳回）**：每条处置见 `paper/17`；首批已修（加载器排 dev/H3a effort 复合/监控数据健康/直线作答/prereg 单一真源/多处文档错），**待拍板**：拉丁方→Williams、seq gating、novice gating、反向所有权项。预注册冻结常量在 `analysis/prereg.py`（pilot 阈值/SESOI/终点层级单一真源）。
 - 已过时（仅留档，勿引用为当前状态）：`paper/1`（v1 四组/同质化×介入点）、`paper/2`/`paper/3`（v2 ModeMirror）、`paper/4`（v2 方案书，待重写）

### 0.3 语言（实验对象是日本人）
 - 被试默认日语（`ja`），研究员测试用 `zh`。**ja/zh/en 三语现在都是全链路可用**（UI + `prompts.build_*` 输出 + `data/topics.json` 的 `{ja,zh,en}` 情境 + `core/shots.py` 分镜解析），被试同意页三语可选（默认 ja）；研究员后台随语言选择器切换。
 - 硬约束:①UI 文案 `i18n/locales/{ja,zh,en}.json` 三语键树必须一致(占位符也要对齐);②**LLM 输出语言由 `prompts.build_*` 的 `lang` 参数控制**(调用点传 `i18n.get_lang()`),每个 `build_*` 都有 zh/en/ja 三分支;③改分镜字段标记(【时长】/【Duration】…)时,`prompts.py` 与 `core/shots.py` 解析器必须同步。**改一处务必三语同步,别让被试看到混杂语言。**
 
 

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