# NovaStory 离线分析管线(A6 v3)。安装依赖: pip install -r analysis/requirements-analysis.txt
# 全流程(真数据到手后): make baseline → make analysis(= v3 → embed → stats → figures)
PY := .venv/bin/python

.PHONY: baseline norming v3 embed stats power figures analysis

baseline:   ## 机器基线(norming / embed 的输入):每题 N 份纯机器稿 → data/baseline/
	$(PY) scripts/baseline_gen.py --n 12 --lang ja

norming:    ## 主题开放度 norming(先 make baseline)→ 三题是否可比
	$(PY) analysis/norming.py

v3:         ## 确定性指标(结构/多样性/逐镜头保真/版本演化/努力再分配/主观复合)→ v3_per_trial.csv
	$(PY) analysis/v3.py

embed:      ## embedding 相对基线保真 Δ,合入 CSV(需 OpenAI + baseline)
	$(PY) analysis/embed.py

stats:      ## LMM / E−D 主对比(Holm)/ TOST 非劣 / Wilcoxon / 剂量-反应(无 CSV 则合成自测)
	$(PY) analysis/stats.py

power:      ## 模拟功效 + MDES(SESOI 先验,无 pilot;paper/14 §4)
	$(PY) analysis/power_sim.py

figures:    ## 招牌图(努力再分配)+ 主 DV 分条件 → data/analysis/figures/
	$(PY) analysis/figures.py

# 真数据到手后的完整链路
analysis: v3 embed stats figures

# 注:v2(HLZ)的 analysis/metrics.py 已被 v3.py 取代,收数验收后删除。
