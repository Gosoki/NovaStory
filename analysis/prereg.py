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

# ⚠️ 2026-09-01 拍板 2.1:上面的字符串**只是给人读的文档**。此前它是全库唯一的 novice
# 定义,而它**从不执行** —— screening 里另有一份内联的同义判断,v3 不导出 novice,
# stats 也没有人群参数,于是「主分析人群 = novice 子集」这条 B1 拍板在代码里根本不存在,
# 主分析实际跑的是全样本。下面这个函数是**唯一会被执行的定义**,
# screening / v3 / stats / pilot_check 四处共用。
#
# 每次分析都从 screening_json 的**原始 5 项重算**,而不是信任入库时写下的
# `is_novice` 布尔:定义一旦在冻结前微调(例如 B1 退路里的 4-of-5),旧行的布尔就
# 是按旧定义算的,重算才能保证全样本口径一致。
NOVICE_CRITERIA = ("published_idx", "background", "written", "self_rating", "quiz_correct")

# 满足几项才算 novice。5 = 全部满足(严格,现行);4 = B1 已声明的退路
# (若试测实测 novice 占比 <40% 即 pilot_check ③ 🔴 时启用)。
# ⚠️ 这个数字必须在**冻结时**定死,不能看了数据再改。
NOVICE_MIN_CRITERIA = 5


def novice_criteria(screening: dict) -> dict:
    """5 个操作化子项各自的真假(便于分报「哪一项把人筛掉了」)。

    容忍缺字段:缺的一项记 False(= 不能证明是新手就不算新手,与「客观下界」口径一致)。"""
    def _i(key, default=99):
        v = screening.get(key, default)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    return {
        "published_idx": _i("published_idx") == 0,
        "background": screening.get("background") == "no",
        "written": screening.get("written") == "no",
        "self_rating": _i("self_rating") <= 2,
        "quiz_correct": _i("quiz_correct") <= 1,
    }


def is_novice(screening: dict) -> bool:
    """主分析人群判定(B1)。`screening` = participants.screening_json 解析后的 dict。"""
    if not isinstance(screening, dict):
        return False
    return sum(novice_criteria(screening).values()) >= NOVICE_MIN_CRITERIA

# ---- SESOI(等价检验界)——【2026-08-03 锁 H4;2026-09-01 拍板 2.3 补齐两个主终点】----
# 单位一律 = **DV 原始单位**(不是 d/dz)。
#
# 为什么必须每个终点各锁一个:`05` 的四象限里有两个「≈(不劣)」格,它们只能由等价检验
# 定义。此前只有 H4 一个标量,于是 ownership / fidelity 两个**主**终点的「≈」格在统计上
# 是空的 —— 实际会发生「N 不足 → 不显著 → 写成不劣」,而且是**事先计划**的,
# 比事后 HARKing 更难辩解(11 致命级 #5)。
SESOI_BY_ENDPOINT: dict[str, float | None] = {
    # own1-3 的**原始均值**(stats: ownership_composite = own_mean,未 z 化),7 点量表分。
    # 0.5 分 = 半个刻度 —— 7 点量表上可辩护的最小实质差:小于半格的差异,
    # 被试自己都分辨不出选 5 还是 5.5。
    "ownership_composite": 0.5,
    # 保真的等价检验在**原始锚题 imagine(7 点量表)**上做,同样 0.5 分。
    # ⚠️ 不是在 fidelity_composite 上做 —— 那个复合是 z 合成的
    # (0.5*mean z(imagine, -violation, not_against) + 0.5*z(embed)),单位是**标准差**,
    # 在它上面写 0.5 就等于把等价界设成 0.5 SD(约中等效应),宽到几乎必然判"等价"。
    # 这正是 2026-09-01 自查抓到的单位错配:界的数值对,量纲错。
    "imagine": 0.5,
    # 结构完整度 0-1 比例 → 0.10 = 10 个百分点(B2 已锁,理由见下)。
    "structural_completeness": 0.10,
}

# 主终点 → 做等价检验时实际使用的 DV。z 合成的复合没有可解释的原始单位,
# 故保真的「≈」判在原始锚题上;所有权复合本身就是原始分,用自己。
# ⛔ 报告里必须写明:「保真的等价检验在 imagine 原始分上进行,复合分只用于主效应」。
EQUIV_DV = {
    "ownership_composite": "ownership_composite",
    "fidelity_composite": "imagine",
}

# 向后兼容:老代码/文档里的标量 SESOI 仍指 H4 的结构完整度界。
SESOI: float | None = SESOI_BY_ENDPOINT["structural_completeness"]

# H4 的 0.10 为什么不是 0.05 / 0.15:
#   0.05 在 N≈36 下几乎不可能通过 → 等价界过窄 = 永远测不出等价,等于自断非劣性主张;
#   0.15 太宽,审稿人会问「差 15 个点也算不劣?」;
#   0.10 = 每 10 份稿子里多 1 份结构不全 —— 对「客观下界」型指标是可辩护的实质阈值。
# 全部为 a priori 设定(设定时 N=0,未见任何真实数据),与观测数据无关。
# 某终点的界为 None 时 stats.tost 对它**拒绝执行**,不退回任何默认界。

# ---- 判定三分支(2026-09-01 拍板 2.3)——写死,不允许临场解释 ----
# 每个主终点按顺序走:
#   ① 主对比显著(校正后) → **判优**(E 优于 D)
#   ② 不显著 → 走 TOST,界取 SESOI_BY_ENDPOINT[终点]
#   ③ TOST 通过 → **判等价/不劣**;TOST 不通过 → **inconclusive**
# ⛔ ③ 的最后一支**不得写成「不劣」「非劣」「no worse」**——数据不足以支持任何方向的结论。
# 这条规则的存在就是为了堵死「N 不足 → 不显著 → 说成不劣」这条路。
DECISION_BRANCHES = (
    "significant -> superior",
    "not significant -> TOST",
    "TOST pass -> equivalent | TOST fail -> INCONCLUSIVE (never 'non-inferior')",
)

# 功效换算注记:power_sim 以配对 dz 工作,而 SESOI 是原始单位 → dz ≈ SESOI / SD(配对差)。
# SD 未知(无功效 pilot),故 power_sim 报**功效曲线**(dz 0.3-0.7)而非单点;真数据到手后用
# 实测 SD 回算本 SESOI 对应的 dz,写进结果节。
# ⚠️ 主分析人群 = novice 子集(B1) → 功效须按子集 N 另算一条曲线。

# ---- H4(质量非劣)的地位 ——【2026-09-01 拍板 2.4】----
# **H4 降为描述性,不作确证性的「质量不劣」主张。**
#
# 为什么:H4 的 DV = 结构完整度,其三个成分里 spec_ok 是**二值**(3 镜 且 总时长达标),
# 而 02 §8.2 的选型实测里三条件的镜数合规、时长合规都是 **100%**。配对差恒为 0 →
# t 无定义 → TOST 要么无结论、要么给出一个「看着像结论」的无信息数字。
# 一个贴天花板的下界指标,测不出条件间差异是**必然**的,不是发现。
#
# 为什么不换成「有方差的客观代理」(MATTR/HD-D、终稿↔首版距离、同题内两两相似度):
# 那些量的是**词汇多样性/改动量**,不是质量。把它们改名叫质量 DV 是偷换概念,
# 比诚实地说「没测」更糟 —— 而且会正面撞上 01 的禁用措辞表。
#
# 落地口径:
#   ① `structural_completeness` 仍然算、仍然报,但只作**描述性**(三条件均接近 100% 这件事
#      本身就是结论:三种协作方式都能产出合规分镜);
#   ② TOST 仍然跑,零方差时如实返回 verdict="INCONCLUSIVE" + 「DV 触顶」注记
#      (stats.tost 已实装),⛔ **不得**解读为「等价/不劣」;
#   ③ `field_completeness`(0-1 连续,有方差)作描述性补充,比二值的 spec_ok 有信息量;
#   ④ 论文与幻灯片必须写「本研究は作品の質(美的・物語的)を測定していない」,
#      Limitations 里那条「质量的测量下界」保留并强化(11 §5.4 已列)。
H4_IS_CONFIRMATORY = False
H4_NOTE = ("H4=描述性:结构完整度是贴天花板的客观下界,零方差时 TOST 报 INCONCLUSIVE,"
           "不得写成「不劣」;本研究未测量审美/叙事质量。")

# ---- 终点层级(#13 族错误控制)——两个主复合上做 FWER,次要/探索门控其后;正式族在 SAP 锁 ----
PRIMARY_ENDPOINTS = ("ownership_composite", "fidelity_composite")
SECONDARY_ENDPOINTS = ("satisfaction", "effort_composite",
                       "post_investment", "total_investment")

# ---- 复合公式(as-run,见 analysis/stats.build_composites) ----
# z 一律按**全样本**算(跨全部 trial 的均值/标准差),被试间差异交给 LMM 的随机截距
# (1|被试) 吸收——不用被试内 z:每被试每条件仅 1 轮,被试内 SD 由 3 个点估计、噪声过大,
# 且与随机截距功能重叠(用户 2026-08-03 拍板)。⚠️ docs/paper/04 §2.1 仍写「被试内 z」,待回写。
COMPOSITES = {
    "fidelity_composite": ("0.5*mean z(imagine, -violation, not_against) + 0.5*z(embed_fidelity)"
                           ";embed 缺席则退回主观三腿等权并告警(2026-08-03 拍板:各半)。"
                           "⚠️ 2026-09-01 拍板 2.5:第三腿由 mine_ratio(=贡献量,与 own3 同构念,"
                           "使『E 保真最高』成为定义性结论)换为 not_against=1−ai_against_ratio"
                           "(=意图一致性,与贡献量正交);mine_ratio 降为描述性/机制变量"),
    "structural_completeness": ("mean(parse_ok, field_completeness, spec_ok)  # H4 DV(见下方触顶注记);"
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
        "novice_criteria": list(NOVICE_CRITERIA),
        "novice_min_criteria": NOVICE_MIN_CRITERIA,
        "sesoi": SESOI,
        "sesoi_by_endpoint": dict(SESOI_BY_ENDPOINT),
        "equiv_dv": dict(EQUIV_DV),
        "decision_branches": list(DECISION_BRANCHES),
        "h4_is_confirmatory": H4_IS_CONFIRMATORY,
        "h4_note": H4_NOTE,
        "primary_endpoints": list(PRIMARY_ENDPOINTS),
        "secondary_endpoints": list(SECONDARY_ENDPOINTS),
        "composites": COMPOSITES,
    }
