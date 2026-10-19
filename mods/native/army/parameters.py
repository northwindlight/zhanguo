"""本领域的声明式数值；兼容汇总层不拥有这些值。"""

# 兵种：征召耗粮装 / 每回合补给维持 / 每回合移动格数 / 基础攻击 / 满血上限（每军每战斗回合）
UNIT_TYPES = {
    'native:infantry': {"name_key": "unit.native:infantry.name", "glyph": "I", "hp": 100, "speed": 1, "supply": 1, "atk": 50, "recruit": {'native:food': 10, 'native:equipment': 5}},
    'native:cavalry': {"name_key": "unit.native:cavalry.name", "glyph": "C", "hp": 100, "speed": 2, "supply": 2, "atk": 50, "recruit": {'native:food': 12, 'native:equipment': 12}},
    # 民兵=廉价驻守军队（80HP/攻25，攻击是步骑的**一半**）：只能在军屯征召（50金+5粮/支；
    # 全国民兵总数 ≤ 全国军屯总数×effects.militia_cap，每军屯每回合 militia_cap 支）；
    # 驻本格（自家军屯格）不耗补给。
    # ★ 2026-10-10 用户「军屯可以生产和驻守两队民兵，民兵轻微加强。攻击改成 25」：
    #   atk 20 → **25**（战力从步骑的四成抬到一半），军屯编制 1 → **2**（见 BUILDINGS["军屯"]）。
    #   两处一起动是**配套**的：编制翻倍而战力也抬 ⇒ 民兵从"纯钉子"变成"能顶一下的守备"，
    #   但仍打不过正规军（25 vs 50，且 80HP vs 100HP）。
    'native:militia': {"name_key": "unit.native:militia.name", "glyph": "M", "hp": 80, "speed": 1, "supply": 1, "atk": 25,
           "recruit": {'native:gold': 50, 'native:food': 5}},
}

# 军队属性
ARMY_MAX_HP = 100

ARMY_STARVE_DAMAGE = 35   # 补给不足时按缺口比例扣血（完全断供=35），交战中也照扣，HP≤0 阵亡

# ★ 2026-09-26 用户「回血改 30，25 太亏装备了」——回血从 25 抬到 **30**：
#   25HP 换 1 件装备（≈6~8 金）太亏，30 让"修兵"这笔账划得来。
ARMY_HEAL_PER_TURN = 30   # 非战斗（且非断供）军队每回合回复，占满血 30%

# 回血须本国补给充足且所在格无交战标记。每件装备提供 ARMY_HEAL_PER_TURN HP 总恢复量，
# 合格正规伤兵平均分配，单支每期最多恢复 30HP；全军实际恢复总量向上取整扣装备。
# 民兵独立恢复且不耗装备。保持整数系数，兼容既有资源目录与经济估值。
HEAL_EQUIP_COST = 1       # 每 30HP 正规军回血耗用的装备数

ARMY_ATTACK_DAMAGE = 50   # 默认基础伤害（步/骑；民兵 20 —— 兵种攻击表见 UNIT_TYPES["atk"]）

RETREAT_RANGE = 1         # 撤退固定只能退相邻 1 格（3×3，所有人）；正常移动按兵种速度（步1/骑2）

RETREAT_ATK_PENALTY = 80  # 撤退军本回合战斗输出 -80%（撤离途中无心恋战；防撤退白嫖输出）

# 战斗骰：每回合掷 1d6 → 本回合双方伤害修正%（战争打几回合很正常）
COMBAT_DIE_MOD = {1: -25, 2: -15, 3: -5, 4: 5, 5: 15, 6: 25}

# ---- 移动力与逐格代价（2026-09-15）----
# 兵种表里的 `speed` 从此是**每回合移动力**；一步的代价 = **max(出发格, 目标格)**
# （用户口径：对称——进森林/山地减速，待在里面的那一回合同样慢）：
#   步兵/民兵：任何地形都 1 ⇒ 仍是"每回合 1 格"（与旧行为一致）；
#   骑兵：平地/丘陵 1（移动力 2 ⇒ 平地 2 格），森林/山地 2（吃满 ⇒ 只能 1 格）。
# ★ 由此自然得到"**山地不可穿越**"：涉山地把移动力花光，就没得走了——
#   不是特判，是代价算出来的。多格移动一律**逐格**判定（路径化），
#   不再有"5×5 内任意格直接落子、中间格从不检查"的旧漏洞。
MOVE_COST = {
    'native:infantry': {"native:plain": 1, "native:desert": 1, "native:hill": 1, "native:forest": 1, "native:mountain": 1},
    'native:militia': {"native:plain": 1, "native:desert": 1, "native:hill": 1, "native:forest": 1, "native:mountain": 1},
    'native:cavalry': {"native:plain": 1, "native:desert": 1, "native:hill": 1, "native:forest": 2, "native:mountain": 2},
}

RETREAT_DEF_COVER = 50   # 防御方撤退：回合末战斗结算中只受 50% 伤害（进攻方撤退全额；★这个值是「承受比例」：50＝只承受一半、100＝全额承伤、0＝免伤）

ARMY_GID_BASE = 100_000_000  # 国家码与本国序列组成唯一编号

VALUES = {n: v for n, v in globals().copy().items() if n.isupper()}
