# NovaStory 离线分析管线(A6 v3)。安装依赖: pip install -r analysis/requirements-analysis.txt
# 全流程(真数据到手后): make baseline → make analysis(= v3 → events → embed → stats → figures)
#                        次要证据要的话另跑 make judge(位置见文件末尾说明)
PY := .venv/bin/python
.DEFAULT_GOAL := help

.PHONY: help dev baseline norming v3 events pilot embed judge stats stats-novice power figures analysis smoke robust smoke-e2e i18n freeze freeze-check precompress serve

dev:        ## 本地开发起服务(热重载;生产 config.toml 默认关)
	.venv/bin/streamlit run app.py --server.runOnSave true --server.fileWatcherType auto

help:       ## 列出所有命令(直接敲 `make` 就看这个)
	@grep -hE '^[a-z][a-zA-Z0-9_-]*:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## / — /' | sort

baseline:   ## 机器基线(norming / embed 的输入):每题 N 份纯机器稿 → data/baseline/
	$(PY) scripts/baseline_gen.py --n 12 --lang ja

norming:    ## 主题开放度 norming(先 make baseline)→ 三题是否可比
	$(PY) analysis/norming.py

v3:         ## 确定性指标(结构/多样性/逐镜头保真/版本演化/努力再分配/主观复合)→ v3_per_trial.csv
	$(PY) analysis/v3.py

events:     ## 事件流指标(问卷时长/LLM 次数/token/最长等待/续接)合入 CSV(须先 make v3)
	$(PY) analysis/events.py

freeze:     ## 【采数前】生成分析计划冻结产物(prereg + 管线旋钮 + 源文件 sha256 + 依赖版本)
	$(PY) scripts/freeze_prereg.py --write

freeze-check: ## 校验「冻结值 == 当前代码实际值」;改过分析代码后必跑,不一致=协议偏离
	$(PY) scripts/freeze_prereg.py --check

pilot:      ## 试测健康检查(4 生死问题:D地板/C天花板/novice占比/量表信度)→ 🟢🟡🔴 + 后手(docs/paper/05)
	$(PY) analysis/pilot_check.py

embed:      ## embedding 相对基线保真 Δ,合入 CSV(需 OpenAI + baseline)
	$(PY) analysis/embed.py

judge:      ## 盲评保真 LLM-judge(OpenAI×3+ICC),judge_fidelity 合入 CSV(次要证据,不在 analysis 链)
	$(PY) scripts/judge.py

stats:      ## LMM / E−D 主对比(Holm)/ 三分支判定 / Wilcoxon / 剂量-反应【默认跑全样本 = 主分析人群】
	$(PY) analysis/stats.py

stats-novice: ## 同上但只跑 novice 子集(**事后探索性**,非确证;结论里必须标 exploratory)
	$(PY) analysis/stats.py --population novice

power:      ## 模拟功效 + MDES(SESOI 先验,无 pilot;docs/paper/05 §1.5)
	$(PY) analysis/power_sim.py

figures:    ## 招牌图(努力再分配)+ 主 DV 分条件 → data/analysis/figures/
	$(PY) analysis/figures.py

smoke:      ## 分析链路回归自测(合成 N=36 跑完整条链并断言;临时库,不碰 data/novastory.db)
	$(PY) scripts/analysis_smoke.py

smoke-e2e:  ## 被试全流程 E2E(正常路径:consent→3轮→完成码 + intake埋点/?lang=/续接/重做)
	NOVASTORY_NO_IMAGES=1 $(PY) scripts/dev_smoke_e2e.py

robust:     ## 实测就绪性验收(出事时扛不扛得住:并发/断网/刷新/脏数据/后台线程)→ docs/paper/12
	NOVASTORY_NO_IMAGES=1 $(PY) scripts/robustness_check.py

i18n:       ## 三语键树 + 占位符一致性(CLAUDE.md §0.3 硬约束①;改完文案必跑)
	$(PY) scripts/i18n_check.py

# ↓ 静态资源加速。Streamlit 1.60 主动让 /static/ 绕过 gzip(它按局域网压测调的),
#   跨境链路上首屏 2.5MB 原文 JS 因此成了主要成本。预压缩把线上字节降到 0.77MB。
#   产物在 .venv 里(不进 git),所以**装完依赖或升级 Streamlit 后要重跑**;
#   忘了也不会发错内容 —— serve.py 比对 mtime,产物过期就回退发原文。
precompress: ## 预压缩 Streamlit 静态资源(gzip);装/升级 streamlit 后必跑,make serve 会自动带上
	$(PY) scripts/precompress_static.py

serve:      ## 生产方式起服务(预压缩 + 静态资源 gzip;正式部署见 deploy/novastory.service)
	$(PY) scripts/precompress_static.py --quiet
	$(PY) scripts/serve.py run app.py --server.port 8501 --server.address 127.0.0.1 \
	  --server.headless true --server.runOnSave false --server.fileWatcherType none

# 真数据到手后的完整链路。events 必须排在 v3 之后(它把事件层列合入 v3 写的 CSV);
# events / embed / judge 互不依赖(三者都是「先删自己的列再 merge」,顺序无关),但都得在 v3
# 之后、stats+figures 之前。⚠️ 没有 data/baseline/ 时 embed 会 exit 1,整条链就停在那里
# —— 先 make baseline。
#
# judge 不进默认链,理由:① judge_fidelity 是**次要 / 收敛证据,不进保真主复合**(B5,
# docs/paper/03 §4),默认链的主终点结果不依赖它;② 它是唯一按「轮数 × reps 次」计费的
# LLM 调用(embed 有磁盘缓存、baseline 只跑一次),不该被 make analysis 顺手重复触发。
# 要它时的完整顺序:  make v3 events embed judge stats figures
analysis: v3 events embed stats figures  ## 【真数据后一条龙】v3 → events → embed → stats → figures(judge 另跑)

# 注:v2(HLZ)的 analysis/metrics.py 已被 v3.py 取代,收数验收后删除。
