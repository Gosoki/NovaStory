# NovaStory 离线管线。用法: make baseline / metrics / stats / power / analysis
# 注意: metrics/stats/power 仍是 v2(HLZ)时代脚本,收数前需按 v3 schema 重写
# (paper/8 分析层);ghost-run 已废弃删除(paper/7 D10)。
PY := .venv/bin/python

.PHONY: baseline metrics stats power analysis

baseline:        ## 采样地板:每题 30 份纯机器 C 式输出(A3 机器地板)
	$(PY) scripts/baseline_gen.py

metrics:         ## [待 v3 重写] HLZ / 多样性 / 编辑距离 → tidy CSV
	$(PY) analysis/metrics.py --backend openai

stats:           ## [待 v3 重写] LMM / Wilcoxon / 置换 / TOST → stats_report.md
	$(PY) analysis/stats.py

power:           ## [待 v3 重写] 模拟功效分析 → power_sim.md/csv
	$(PY) analysis/power_sim.py

analysis: metrics stats power
