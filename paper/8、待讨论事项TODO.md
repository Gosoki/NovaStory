# 待讨论事项 TODO

> 建立 2026-06-12;2026-06-13 按「挡不挡重构」重新分层;2026-06-24 加入日语/跨语言效度项。结清的条目沉到文末留痕。
> 权威设计=paper/7 v4.1;论文骨架=paper/10;主张=paper/9;文献=paper/reference/。

---

## 🌏 日语/跨语言效度(2026-06-24 审计新增,pilot/预注册前)

- **JP1 量表跨语言效度**:所有权(Van Dyne&Pierce 英文母本)与 agency(只取 J-SoAS 2 题且自译非原句)的日译删减版效度未知。→ 用 J-SoAS 已发表日语原句子集;所有权做回译+认知访谈;pilot 报告日语样本 Cronbach α;论文区分"已验证日语量表"vs"自译删减版"。
- **JP2 日语 embedding 保真效度**:embedding 余弦测意图保真在日语短分镜文本上可比性存疑。→ pilot 验证 embedding 余弦与盲评保真/想象匹配的收敛效度;保真主锚用盲评+逐镜头标注,embedding 降为辅助。
- **JP3 LLM-judge 日语可靠性**:judge/盲评效度证据(TTCW/Zheng)多基于英文。→ 人评校验用**日语母语评审**(非研究者),单独报告日语 judge-人评相关,不沿用英文数字;Limitations 写明。
- **JP4 文化反应偏差**:日本被试中点/谦逊偏好压缩量表方差、可能与条件交互。→ 主分析用被试内差分;查条件×反应风格交互(个体量表 SD 作协变量);锚点本地化;Limitations 写明。
- **JP5 字符门槛跨语言**:`MIN_INTENT_CHARS=10` 在日语信息量更低(假名助词)。→ 改语言感知门槛(日语上调或用形态素/词数);自填字数等用 MeCab/SudachiPy 形态素计数或按语言标准化。
- **JP6 语言锁定**:语言选择器常驻可被中途切换污染数据。→ 正式收数 consent 后锁定被试语言(禁用切换或仅研究员可改),每轮 lang 写入 events 校验三轮一致。
- **JP7 日本招募**:日本 Prolific 池小 + novice 准入进一步缩小。→ pilot 实测可达量;备 CrowdWorks/Lancers/大学池;按日本市场定报酬;时长×延迟纳入监控。

## ⚙️ 延后的代码加固(2026-06-24 审计,非阻塞)

- **CG1 seq 并发竞态**:`db.insert_participant` 的 `COUNT(*)%9` 与 INSERT 非原子,WAL 下多人同时筛查通过可能撞 seq。→ 改 INSERT 后由自增 id 推导 seq,或 `BEGIN IMMEDIATE`。被试量小可暂留。
- **CG2 双击提交无防抖**:submit_trial / insert_questionnaire 可能被快速双击重复落库。→ 提交后禁用按钮或加 r_trial_id 幂等守卫。
- **CG3 `_normalize_topic` ValueError**:脏 legacy preset 的 `shot_seconds` 非数字会崩 load_topics。→ int() try 兜底。

## 🔴🔧 全项目审计高优先修复(2026-06-26,10 维并行+对抗验证;62 发现→60 确认。详见时间轴 06-26)

> 去重后约 5 个 high 根因(很多发现来自不同维度但指向同一处)。**建议本期修**=会污染数据或卡死被试且改动小;**可延后**=影响小或需设计决策。
>
> **✅ 状态(2026-06-26):AUD1-5 已修 + AUD6 已加 consent 强提示(完整会话恢复仍待决策)。E2E 通过。** 已修详情:
> - AUD1+2 `views/_postgen.py`:照搬 E 的"成功后才提交",判定改 `out and out.strip()`。
> - AUD3 `core/state.py`:新增 `r_llm_wait_pre`,E round-1 `t_pregen` 已扣除最终生成等待。
> - AUD4 `views/sidebar.py`(语言器移进 researcher 区,被试锁 ja)+ `views/guidance.py`(`ai_decided` 存为语言无关布尔,不再字符串比对)。
> - AUD5 `views/guidance.py`:追问界面 `r_versions` 非空时加"取消,返回草稿"出口(新键 `guidance.cancel`)。
> - AUD6(lite) `views/consent.py`:加"勿刷新/勿返回"强提示(新键 `consent.no_refresh`)。**完整恢复(URL pid 续接)仍未做**,见下。
> - 配套:`devtools.py`/`dev_smoke_e2e.py` 同步 `ai_decided` 字段;E2E `safe_run` 补 checkbox 回填。

- **AUD1 D 修改通道失败不回滚【✅已修】**`views/_postgen.py:134-157`。`r_revision_requests.append` 与 `revision_request` 事件在 `stream_llm` **之前**执行,但 AI 版本只在成功时追加 → LLM 失败(`LLMCallError` 真实可发生)留下"孤儿请求":`render_history` 的 `requests[ai_idx-1]` 配对整体错位(被试看到的履历张冠李戴)、落库 `revision_requests` 条数与 `n_ai_rounds`/版本数不一致、重试产生重复 round 号。→ **照搬 E 的 `_finish_round` 模式**:先 `stream_llm`,`out and out.strip()` 成功后再 append/log/add_version/+1。
- **AUD2 D 修改通道空串判定【✅已修(与 AUD1 同处)】**`views/_postgen.py:152`。用 `if out is not None` 而非 `out and out.strip()`(C/D/E 其余 5 处都用后者)。模型只回 `<think>`/空白时 `stream_llm` 返回 `""`(非 None)→ `add_version("","ai")` 把脚本覆盖成空、清空编辑器、`n_ai_rounds` 虚增、空版本落库(原文仍在版本历史可找回)。→ 改判定为 `out and out.strip()`,空返回给提示不写版本。(与 AUD1 同一处,一并修。)
- **AUD3 E `t_pregen` 把最终生成等待算进创作时间【✅已修;影响主因变量】**`core/state.py:263-264`。`t_pregen=_delta("guidance_shown","guidance_submit")` **未扣 LLM 等待**,而 E-final 剧本生成(几十秒)恰在此区间内、又因发生在首个 `script_shown` 之前躲过 `r_llm_wait_post` 扣减 → E 引导阶段"创作投入"系统性高估,污染 C/D/E 时间对比。**仅影响 E round-1**(follow-up 已被 `t_postgen` 正确扣除)。→ 新增 `r_llm_wait_pre` 累加器(仅在「已有 guidance_shown 且尚无 guidance_submit」区间累加),`t_pregen=max(0, raw-r_llm_wait_pre)`;**只改 E 分支**。
- **AUD4 语言切换器对被试可见 + `ai_decided` 本地化串比对脆弱【✅已修;JP6 一并解决】**`views/sidebar.py:12-28` + `views/guidance.py:166-184`。语言器在 researcher 守卫**之外**,被试随时可切 →①违反"只见日语";②`ai_decided` 把本地化串 `_AI_DECIDE()` 当枚举存/比对,答题→提交间切语言会误判 `ai_decided=False` 并把"交给AI"当普通选项塞进 prompt,污染 E 引导数据;③翻页恢复静默丢弃 AI-decide 选择(与既知 widget-key 丢失同源)。→ **治标**:语言器移进 researcher 折叠区(JP6 的锁定也一并解决);**治本**:`ai_decided` 在 `_save_current` 时就存成语言无关布尔,不再字符串比对。
- **AUD5 E 追问生成失败被锁死、回不到草稿【✅已修】**`views/guidance.py:205-223`。follow-up 时已有可用草稿,但生成持续失败时 `_finish_round` 直接 return、`r_phase` 仍 `guidance`,无"取消/返回"出口,只能刷新(丢全会话)。→ `render()` 中当 `r_versions` 非空时加一个"取消/返回"按钮,置 `r_phase="postgen"` 并 rerun,不提交本轮。
- **AUD6 刷新/会话丢失即丢本轮数据且重复占 seq【🟡 lite 已修:consent 强提示;完整恢复待决策】**`core/state.py:64-103`。轮内进度全在 `session_state`,刷新→回 consent、`participant_id` 丢失→重新 screening 会**再 insert 一个 passed 行、占第二个 seq**,破坏拉丁方平衡 + 虚增样本(events 行因增量落库仍在,但 trial 级文本/会话续接丢失)。→ MVP 最低成本:URL query param 存 pid,init 时若该 pid 未 done 则从 DB 重建 `round_idx`(按已落 trials 数)/`seq`,避免重复占 seq;或至少 consent 页强提示"全程勿刷新/勿返回"并记为已知限制。**请拍板本期是否实现恢复。**

### 审计 medium / 可延后(择期或随手修)
- **AUD7**`views/screening.py:86-111` `published` 存本地化原文(日/中混入),与其它字段存 index 不一致,分析端难对齐。→ 统一存 index 或英文枚举。
- **AUD8**`core/db.py:31-83` `trials`/`questionnaires` 无 `UNIQUE(participant_id, round_idx)`,重入产生重复行(与 CG1/CG2 同族)。→ 加唯一约束 + 幂等守卫。
- **AUD9**`core/llm.py`+`views/_trial.py` E 用 `GUIDANCE_API_INDEX` 指定独立模型时,该 **model/base_url/温度从不落库**,trial 只记主会话参数,引导步无法复现/审计。→ 引导调用的 meta 也落库(events 或 trial 增列)。
- **AUD10**`views/_streaming.py:25` 脚本/修改类 LLM 调用(~75s)等待界面无时间预期,只显示「AIが生成しています…」。→ 加预期文案/进度提示(关联 D23 延迟决策)。
- **AUD-low/nit(约 40 条,择机)**:DB 无唯一约束/legacy 列易误导分析;`attention` 原始作答值不落库只存 0/1;`devtools` 切条件清空 `r_events` 致时长落 NULL;JSON 模式未用 `response_format=json_object`;per-shot 三标签语义重叠、错误提示不指明哪项、「15秒・3カット」硬/软约束没讲清、提交按钮非绿(与"绿=决定"约定相悖)、履历默认展开 480px 对首轮可能干扰;`scripts/ghost_run.py` 调用了不存在的 `prompts.build_user`(死脚本)、`baseline_gen.py`/`ghost_run.py` 调 `build_*` 不传 lang;`shot_type` 解析后无消费方(死字段)。**完整清单见审计原始结果**(`tasks/wxqj7vwwt.output`)。
- **误报(2,已剔除)**:devtools `_fill_guidance` 被覆盖(实际不会)、"无任何提示这轮 AI 帮法不同"(intent warning 已有说明)。

## 📋 逐操作详细日志 — 前瞻设计(规划;系统冻结后一次性加,现在不实现)

> 目标:每个被试的每个操作可逐条复盘——**时间 + 动作 + 具体选项/输入文本 + 脚本是否被改 + token**。("改了哪部分"暂不记,留全版本后验 diff。)源:审计 logging 维度 8 条 + 自查。

**地基(可直接复用,扩内容无需迁移)**:`events` 表(`participant_id, round_idx, ts, type, payload_json`)+ 统一 `log_event` 入口已存在,`payload_json` 是 schema-free 的;已覆盖阶段骨架事件。

**缺口与预埋建议**:
1. **LOG1 内容缺失 → enrich payload**:多数事件只落计数/标志(`intent_submit` 只 `chars`、`guidance_answer` 只 `dimension/is_custom/ai_decided`、`revision_request` 只 `chars`、`hand_edit_saved` 只 `chars_delta`)。→ 约定每个动作事件 payload 带"具体内容"字段(选项原值 + 输入全文)。无需迁移,只是补 payload(创意/脚本文本已在 trials,重复存可接受)。
2. **LOG2 统一入口 `log_action`**:把所有"用户动作"收口到一个 `log_action(action, content=..., **meta)` 薄封装(底层仍 `log_event`),定一张 action 命名表 + payload 字段名 → 以后一次性接全 + 分析端字段稳定。
3. **LOG3 排序精度**:`ts` 是秒级 isoformat,同秒多操作丢先后。→ `events` 加一个**轮内自增 `seq` 列**(或存毫秒),保证逐操作可严格排序。
4. **LOG4 对齐 trial**:`events` 无 `trial_id` 外键,trial 轮末才落库。→ trial 落库后回填 `events.trial_id`,或按 `(participant_id, round_idx)+seq` 对齐。
5. **LOG5 脚本改动标记**:"是否改过"现靠 `chars_delta>0` 隐式;全版本已在 `script_versions(author=ai|user_edit)`,**diff 可后验重算**(故"改了哪部分"无需现在记)。→ 加显式 `script_modified` 布尔 + 保留全版本(已有)。
6. **LOG6 token(关联 C6)**:usage **当前完全没取**。→ 流式:`create(..., stream_options={"include_usage": True})` 读末 chunk usage;JSON 模式:`resp.usage` 已可得只是丢弃了。落点:`llm_done` 事件 payload 加 `{prompt_tokens, completion_tokens, total_tokens}`(`llm_done` 已带 `elapsed`,天然挂载点);可选 trials 加汇总列。**注意**:部分第三方 OpenAI 兼容网关流式不返回 usage,取不到记 null 别崩。

**实现时机**:系统冻结后一次迁移完成(`events` 加 `seq`/`trial_id` 列 + `llm.py` 取 usage + `log_event` enrich payload)。

---

## 🔴 重构源码前(门槛区)

### ~~C2 mimo-v2.5-pro 结构化输出稳定性测试~~ 【已完成,2026-06-13】
- **结果:质量 8/8 严格通过**(JSON 解析全成、3 固定维全齐、共 5-6 问、选项齐 4 个、问题紧扣创意、补充维合理[tone/ending/sound]);追加轮冒烟也通过(3 问,且准确点中稿子真实薄弱处)。**质量门槛通过,mimo 可用。**
- ⚠️ **遗留:延迟 62-84s/调用(均值 ~75s)**——开发/测试无所谓,正式实验体验差。处置:开发期用 mimo;**正式收数前重测延迟,仍 >30s 则引导步切 OpenAI**(gpt-4o-mini 单次引导调用 ≈$0.001-0.003,30 人全程 <$1,可忽略)。本测试的 prompt 即 C1 的 v0 草稿。

> **结论:重构前门槛全部清空。** 交互设计(paper/7 v4)、研究框架(路 B)、核心主张(paper/9)、模型可行性(C2)均已冻结/验证,等用户一声令下即可开工。

## 🟡 重构期间顺手定(实现层,不需要事前讨论)

### C1 引导/改稿 prompt 工程
- 第 1 轮 prompt(C2 测试版已是 v0 草稿)、追加轮"基于当前稿薄弱处"、D 条件改稿 prompt(吃自由反馈出修订稿)、解析失败重试 2-3 次+降级记 `fallback=true`;选项要具体互斥;警惕选项的 latent persuasion(Jakesch CHI 2023,见 reference/04)。
### C3 数据层迁移
- `dissent_json`→`guidance_json`(rounds 结构,v4 §6);新增 `script_versions[]`(author=ai|user_edit)、D 的 `revision_requests[]`、`n_ai_rounds`/`n_hand_edits`/`hand_edit_chars` 汇总列;**快照硬规则**(任何提交/交AI前先持久化用户手改);ModeMirror 字段停用不删;devtools/e2e/i18n 适配;步骤条文案改三条件新流程。
### C4 按钮文案
- E:「继续让 AI 引导」(候选:让 AI 再问问我);D:「告诉 AI 怎么改」;实现时定稿进 i18n。
### C5 旧资产清理
- 删除:ModeMirror 视图逻辑/dissent prompts/相关 i18n 键;`scripts/ghost_run.py` 与 analysis 的 HLZ 部分标记 deprecated(留 git 历史)。
- 保留:`baseline_gen.py`(A3 用)、judge/stats/power_sim(换 DV 后复用)、`dev_smoke_e2e.py`(改写)。

## 🟢 重写 paper/4 时(文档层)

- **B6 引导维度内容效度**:三维度(心理/转折/画面)需教材/从业者背书(候选引文见 reference/06);防"实验者自由度"。
- **B7 解释矩阵加反向分支**:Knijnenburg 2011(新手爱直接给结果)+ Slovic 1995(偏好被引出过程构建)→"新手嫌引导麻烦/满意度与所有权分离"分支(reference/05)。
- **效率口径三并报**:事前投入+事后修改量+总投入(paper/9 §4.1),轮数口径差异声明(E 一轮≠D 一轮)。
- **A3 同质化的叙事位置**:降为探索性;用户暂定方案=baseline_gen 机器多次采样 vs 用户 E 产出、或用户间 C vs E 统计对比(可再深入)。
- 贡献声明直接用 paper/9 §3 措辞;Related Work 按 reference/01 第一梯队逐一切割;防御段用 reference/05 两条反驳。

## 🔵 pilot 前/中(实验执行层)

- **B3 pilot 监控清单(更新)**:轮数/净用时分布;E 第 1 轮作答疲劳(AI代答率 >50% 即预警,补充维上限 4→2);D 的 AI 通道使用率(若多数人直提 → 强化页面文案);引导步实测延迟(>30s 切 OpenAI,D23)。
- **B8 被试语言/渠道(新增,从旧方案书继承,一直未定)**:Prolific 英文(需本地化 prompts/界面已有 en)vs 中文社群(现成)。影响:量表语言、judge 语言、文案。**pilot 前必须定。**
- **B9 正式实验模型锁定(新增)**:全程 OpenAI 快照(质量稳+延迟低,成本 30 人 <$5)vs 生成步免费+引导步 OpenAI。pilot 看延迟后定。
- 伦理审查流程确认(研究科要求);预注册时点=pilot 后、收数前(OSF)。

## ⚪ 分析层(拿到数据后;框架已入 paper/10 §6-7,以下为待敲定细节)

- **A1 「专业质量」rubric 终稿**:四维草案已定(景别运用/节奏转折/视听语言/情绪锚点,对应引导维度);待定:每维锚点描述、judge 模型选择、人评校验份数(建议 30)。素材 reference/06。
- **A4 主要终点的确切组合(新增)**:预注册前敲定"保真复合"(想象匹配+违背反向+逐镜头 mine 比例+盲评保真,如何加权/还是并列报告?)与"所有权复合"(own1-3 均分)。paper/4 重写时定。
- **A5 D 反馈内容编码 codebook(新增)**:revision_requests 映射到引导维度的编码方案(图4 的机制分析);两名编码者+一致性。pilot 数据上起草。
- **A3 执行**:探索性同质化对比(机器地板=baseline_gen)。
- **C6 token 用量统计**:API usage 字段落库即可;MVP 后统一加。

---

## 已结清(留痕)

- ~~A2 ghost-run 机器对照~~ → 整体废弃(D10):C/D 即对照;`baseline_gen.py` 保留给 A3。
- ~~B1 意图基准~~ → 创意 1-2 句即基准(D12);保真盲评以此为锚,E 问答轨迹单独分析(防循环测量)。
- ~~B2 loop 结构~~ → 看稿后[提交/继续引导/手改],无上限(D13/D20)。
- ~~B4 新颖性核查~~ → **完成(paper/9)**:宽泛主张被推翻(APE/Maier/Timing Matters),收窄合取成立;残余检索任务在 paper/9 §5,投稿前执行。
- ~~B5 D 的角色~~ → 升级为核心对仗:D=先生成再修改(常规 ChatGPT 工作流代理),E=先引导再生成(D15/D18)。
- ~~Q1-Q6 交互细节~~ → 全部拍板(D17-D22),见 paper/7 v4。
- ~~量表点数 7 vs 5~~ → **保留 7 点**(2026-06-25)：①采用的已验证量表原生 7 点(SoAS/Tapal 2017、心理所有权 Van Dyne&Pierce),改 5 点破坏验证格式与既往可比性;②7 点分辨率更高,利于检出被试内 C/D/E 微差;③缩问卷应靠减题(已 own→3、soa→2),非减点数。**写 methods 时以"源量表原生 7 点"为引用依据**(关联 JP1)。锚点(左/中/右)已上每条刻度,见 `views/questionnaire.py` `_scale_anchors`。
