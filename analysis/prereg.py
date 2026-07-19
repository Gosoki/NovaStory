"""冻结的预注册常量 —— pilot go/no-go 阈值、复合定义、终点层级、novice 定义、SESOI 的
**单一真源**。`pilot_check` / `stats` 从这里 import,避免 prose / code / prereg 三处静默分叉
(深度评审 2026-07-19 #33)。采数前把本模块 dump 成带 hash 的预注册产物(paper/8 §🎓 artifact 包)。

⚠️ 阈值为经验参考;正式 go/no-go 与 SESOI 在预注册前**在此文件锁定**,锁定后不再改。
"""
from __future__ import annotations

# ---- pilot 生死问题 go/no-go(paper/16) ----
D_FLOOR_ZERO_GREEN, D_FLOOR_ZERO_YELLOW = 0.30, 0.60   # D 零返工比例(越低越好)
C_CEIL_MEAN, C_CEIL_SD = 6.0, 1.0                       # imagine_match 天花板判定
C_CEIL_GAP = 0.30                                        # E−C 差低于此且 C 压顶 → 红
C_CEIL_YELLOW_MEAN = 5.5
NOVICE_SHARE_GREEN, NOVICE_SHARE_YELLOW = 0.60, 0.40
RELIABILITY_GREEN, RELIABILITY_YELLOW = 0.70, 0.60
OWN_ALPHA_FLOOR = 0.60                                   # own1-3 α 低于此 → 后手D 切 SoPA

# ---- novice 定义(5 项严格 AND,paper/16 §1③;录而不 gate,是预注册主分析人群) ----
NOVICE_DEF = ("published_idx==0 AND background=='no' AND written=='no' "
              "AND self_rating<=2 AND quiz_correct<=1")

# ---- SESOI(H4 TOST / 功效)—— TODO:预注册前用原始单位 a priori 设定(#12/#14, paper/8 A4) ----
SESOI: float | None = None

# ---- 终点层级(#13 族错误控制)——两个主复合上做 FWER,次要/探索门控其后;正式族在预注册 SAP 锁 ----
PRIMARY_ENDPOINTS = ("ownership_composite", "fidelity_composite")
SECONDARY_ENDPOINTS = ("satisfaction", "effort_composite",
                       "post_investment", "total_investment")

# ---- 复合公式(as-run,见 analysis/stats.build_composites) ----
COMPOSITES = {
    "fidelity_composite": "mean z(imagine, -violation, mine_ratio, embed_fidelity[若有])",
    "ownership_composite": "own_mean(own1-3);own3=self-investment facet 另行分报",
    "effort_composite": "mean z(log1p(n_ai_rounds, hand_edit_chars, t_postgen))  # H3a 事后返工",
}


def as_dict() -> dict:
    """机器可读的预注册快照(供 dump 成 hash 化产物)。"""
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
