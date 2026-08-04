"""冻结的分析计划常量 —— 试测 go/no-go 阈值、复合定义、终点层级、novice 定义、SESOI 的
**单一真源**。`pilot_check` / `stats` 从这里 import,避免 prose / code 两处静默分叉。

⚠️ **本研究不做第三方预注册(OSF/AsPredicted)** —— 2026-08-03 拍板 B3。
替代做法 = **内部冻结**:正式采数前把本模块 `as_dict()` dump 成带 hash 的文件、连同 git
commit 一起留档,作为「分析计划在见到数据之前就已确定」的时间戳证据。强度弱于第三方预
注册(自己的仓库自己能改历史),写作时按实情表述为「分析计划在采数前确定并纳入版本管理」,
**不得写 "preregistered"**。见 docs/paper/05_分析计划冻结与试测决策树.md。

⚠️ 所有阈值在采数开始前锁定,锁定后不再改。
"""
from __future__ import annotations

# ---- pilot 生死问题 go/no-go(docs/paper/05 §2) ----
D_FLOOR_ZERO_GREEN, D_FLOOR_ZERO_YELLOW = 0.30, 0.60   # D 零返工比例(越低越好)
C_CEIL_MEAN, C_CEIL_SD = 6.0, 1.0                       # imagine_match 天花板判定
C_CEIL_GAP = 0.30                                        # E−C 差低于此且 C 压顶 → 红
C_CEIL_YELLOW_MEAN = 5.5
NOVICE_SHARE_GREEN, NOVICE_SHARE_YELLOW = 0.60, 0.40
RELIABILITY_GREEN, RELIABILITY_YELLOW = 0.70, 0.60
OWN_ALPHA_FLOOR = 0.60                                   # own1-3 α 低于此 → 后手D 切 SoPA

# ---- novice 定义(5 项严格 AND;录而不 gate)----
# B1 拍板(2026-08-03):招募端**不设门槛**(随机找人),但 novice 子集 = **采数前冻结的主分析
# 人群**(B3:本研究不做第三方预注册,措辞一律用「冻结/事前确定」);全样本为稳健性分析,经验者另作「経験あり vs なし」对比/调节分析。
# → 功效必须按 novice 子集(更小 N)算,并超招募到子集也达标。
NOVICE_DEF = ("published_idx==0 AND background=='no' AND written=='no' "
              "AND self_rating<=2 AND quiz_correct<=1")

# ---- SESOI(H4 TOST 的等价界 / 功效)——【已锁定 2026-08-03,B2】----
# 单位 = **DV 原始单位**。H4 的主质量 DV = 结构完整度 structural_completeness
# = mean(parse_ok, field_completeness, spec_ok),0-1 比例,故 0.10 = **10 个百分点**。
# (spec_ok = 「15s 且 3 镜」达标;旧写法 shots_ok 只判镜数,已不是 H4 的成分。)
#
# 读法:「E 的结构完整度比 D 低 10 个百分点以内 → 判定为『质量无实质损失』」。
# 为什么是 0.10 而不是 0.05 / 0.15:
#   0.05 在 N≈36 下几乎不可能通过 → 等价界过窄 = 永远测不出等价,等于自断非劣性主张;
#   0.15 太宽,审稿人会问「差 15 个点也算不劣?」;
#   0.10 = 每 10 份稿子里多 1 份结构不全 —— 对「客观下界」型指标是可辩护的实质阈值。
# 这是 a priori 设定(设定时 N=0,未见任何真实数据),与观测数据无关。
# 仍为 None 时 stats.tost 拒绝执行,不退回任何默认界。
SESOI: float | None = 0.10

# 功效换算注记:power_sim 以配对 dz 工作,而 SESOI 是原始单位 → dz ≈ SESOI / SD(配对差)。
# SD 未知(无功效 pilot),故 power_sim 报**功效曲线**(dz 0.3-0.7)而非单点;真数据到手后用
# 实测 SD 回算本 SESOI 对应的 dz,写进结果节。
# ⚠️ 主分析人群 = novice 子集(B1) → 功效须按子集 N 另算一条曲线。

# ---- 终点层级(#13 族错误控制)——两个主复合上做 FWER,次要/探索门控其后;正式族在 SAP 锁 ----
PRIMARY_ENDPOINTS = ("ownership_composite", "fidelity_composite")
SECONDARY_ENDPOINTS = ("satisfaction", "effort_composite",
                       "post_investment", "total_investment")

# ---- 复合公式(as-run,见 analysis/stats.build_composites) ----
# z 一律按**全样本**算(跨全部 trial 的均值/标准差),被试间差异交给 LMM 的随机截距
# (1|被试) 吸收——不用被试内 z:每被试每条件仅 1 轮,被试内 SD 由 3 个点估计、噪声过大,
# 且与随机截距功能重叠(用户 2026-08-03 拍板)。⚠️ docs/paper/04 §2.1 仍写「被试内 z」,待回写。
COMPOSITES = {
    "fidelity_composite": ("0.5*mean z(imagine, -violation, mine_ratio) + 0.5*z(embed_fidelity)"
                           ";embed 缺席则退回主观三腿等权并告警(2026-08-03 拍板:各半)"),
    "structural_completeness": ("mean(parse_ok, field_completeness, spec_ok)  # H4 唯一 DV;"
                                "spec_ok=15s且3镜达标;0-1 比例,与 SESOI 同单位 → 只做一次 TOST"),
    "ownership_composite": "own_mean(own1-3);own3=self-investment facet 另行分报",
    "effort_composite": "mean z(log1p(n_ai_rounds, hand_edit_chars, t_postgen))  # H3a 事后返工",
    "dose_composite": "mean z(pre_investment, g_custom_rate, 1-g_ai_decided_rate)  # H5 E 内剂量",
}


def as_dict() -> dict:
    """机器可读的**冻结快照**(供 dump 成 hash 化产物;B3:不是第三方预注册)。"""
    return {
        "pilot_thresholds": {
            "d_floor_zero": [D_FLOOR_ZERO_GREEN, D_FLOOR_ZERO_YELLOW],
            "c_ceiling": {"mean": C_CEIL_MEAN, "sd": C_CEIL_SD, "gap": C_CEIL_GAP,
                          "yellow_mean": C_CEIL_YELLOW_MEAN},
            "novice_share": [NOVICE_SHARE_GREEN, NOVICE_SHARE_YELLOW],
            "reliability": [RELIABILITY_GREEN, RELIABILITY_YELLOW],
            "own_alpha_floor": OWN_ALPHA_FLOOR,
        },
        "novice_def": NOVICE_DEF,
        "sesoi": SESOI,
        "primary_endpoints": list(PRIMARY_ENDPOINTS),
        "secondary_endpoints": list(SECONDARY_ENDPOINTS),
        "composites": COMPOSITES,
    }
