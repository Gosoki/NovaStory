# NovaStory 离线管线(M7)。用法: make baseline / ghosts / metrics / stats / power / analysis
PY := .venv/bin/python

.PHONY: baseline ghosts metrics stats power analysis

baseline:        ## 采样地板:每题 30 份纯机器 C 式输出
	$(PY) scripts/baseline_gen.py

ghosts:          ## ghost-run:每 trial K=20 份纯机器对照
	$(PY) scripts/ghost_run.py

metrics:         ## HLZ / 多样性 / 编辑距离 / MC1 / MC2 → tidy CSV
	$(PY) analysis/metrics.py --backend openai

stats:           ## LMM / Wilcoxon / 置换 / TOST / 剂量-反应 → stats_report.md
	$(PY) analysis/stats.py

power:           ## 模拟功效分析 → power_sim.md/csv
	$(PY) analysis/power_sim.py

analysis: metrics stats power
