"""战国（zhanguo）共享规则层：数值表 + 地块生成。

多国引擎 `mp.py` 与 AI 层 `mp_ai.py` 都从这里取常量与生成函数；
这里不含任何世界状态（状态在 mp.World 里）。

地块生成：按地形权重随机出地形，再按该地形绑定的资源权重表随机出
矿石/黄金/耕地/石油/木头 五项资源。地形由 `seed:x:y` 决定（纯函数），
同一种子下重放同样的激活序列结果完全一致——对局可复现。
"""

from __future__ import annotations

import random

# 资源项与上限
RESOURCES = ["矿石", "黄金", "耕地", "石油", "木头"]
RESOURCE_MAX = {"矿石": 5, "黄金": 2, "耕地": 5, "石油": 3, "木头": 5}

# 五种地形。每种地形给全部五项资源一张权重表：
#   列表下标 = 资源数值（x0, x1, ...），元素 = 抽中该数值的权重。
# 下标越界即该项资源不可能出现的更高值（权重为 0）。
# 2026-09-09 重平衡：黄金富矿（x2）概率明显调低（期望 0.147→0.105 座/格，−29%），
# 石油略增（0.27→0.34，沙漠仍是唯一宝地且多出一档 x4），矿/耕/木总量基本不变、
# 地形专精更陡（山地矿石 2.53、森林木头 2.55、平原耕地 2.17）。
TERRAINS = {
    "平原": {  # 沃土：耕地/木头为主，几乎无矿无油
        "矿石": [80, 16, 3, 1, 0, 0],
        "黄金": [96, 4, 0],
        "耕地": [6, 26, 32, 21, 11, 4],
        "石油": [92, 7, 1, 0],
        "木头": [37, 30, 21, 9, 3, 0],
    },
    "森林": {  # 木头宝地，兼有耕地
        "矿石": [74, 19, 6, 1, 0, 0],
        "黄金": [95, 5, 0],
        "耕地": [28, 30, 26, 11, 4, 1],
        "石油": [95, 4, 1, 0],
        "木头": [5, 15, 29, 28, 17, 6],
    },
    "丘陵": {  # 全能有，样样不拔尖，略出黄金
        "矿石": [18, 28, 25, 17, 8, 4],
        "黄金": [88, 11, 1],
        "耕地": [32, 30, 24, 10, 3, 1],
        "石油": [80, 15, 4, 1],
        "木头": [24, 29, 25, 14, 6, 2],
    },
    "山地": {  # 矿石/黄金富集，粮木贫
        "矿石": [4, 18, 29, 26, 16, 7],
        "黄金": [76, 20, 4],
        "耕地": [72, 20, 6, 2, 0, 0],
        "石油": [74, 20, 5, 1],
        "木头": [60, 26, 11, 3, 0, 0],
    },
    "沙漠": {  # 石油宝地，其余皆贫
        "矿石": [68, 21, 8, 3, 0, 0],
        "黄金": [89, 10, 1],
        "耕地": [90, 7, 2, 1, 0, 0],
        "石油": [12, 26, 28, 26, 8],
        "木头": [95, 4, 1, 0, 0, 0],
    },
}

# 地形固定属性（与地块类型绑定，不随机）：
#   defense        防御加成 %（防守方优势，负值=无险可守）
#   build_penalty  建设惩罚 %（建筑费用/工时加成，越高越难建设）
TERRAIN_STATS = {
    "平原": {"defense": 0, "build_penalty": 0},       # 开阔易建，无防御加成
    "森林": {"defense": 10, "build_penalty": 15},     # 林荫遮蔽，稍难施工
    "丘陵": {"defense": 25, "build_penalty": 25},     # 攻守皆费劲
    "山地": {"defense": 50, "build_penalty": 50},     # 易守难攻，也难建设
    "沙漠": {"defense": -10, "build_penalty": 40},    # 无险可守，环境恶劣难施工
}

# 地形出现的先验权重
TERRAIN_WEIGHTS = {"平原": 30, "森林": 25, "丘陵": 20, "山地": 15, "沙漠": 10}

# 地图上每格显示的单字符
TERRAIN_CHARS = {"平原": "P", "森林": "F", "丘陵": "H", "山地": "M", "沙漠": "D"}

# ---- 建设系统 ----
# 每地块建筑位总数；城堡级数也占位
MAX_SLOTS = 20
STARTING_GOLD = 2000
STARTING_WOOD = 50     # 初始木材储备（建任何建筑都要花木头）
STARTING_SUPPLY = 10   # 初始补给仓（军队每支每回合耗 1 补给）
CASTLE_DEFENSE_PER_LEVEL = 10  # 每级城堡 +10% 防御
TOWN_HALL_GOLD = 5     # 每座市政厅每回合基础产金；另按该地块已占建筑位每座 +1 金（电网不足停摆）
TOWN_HALL_PER_SLOT = 1  # 市政厅：每座额外 +该地块建筑数×此值 金/回合

# 物资（地块可储存；能源不可存储，不在其中）
# 全局战略储备：木头=建建筑；补给=军队口粮（补给厂产出入全局补给仓 world.supply）
GOODS = ["粮食", "矿石", "石油", "装备"]

# 世界市场基准价（金/单位）。黄金是货币（国库），不可交易；MARKET["黄金"] 不是价格，
# 而是黄金矿场每座每回合的产金量（矿场 outputs 记作 1 单位"黄金"，按此折算成金）。
# 可交易 = 全部可存储物资：粮食/木头/矿石/石油/装备 + 补给（军队口粮，买卖直接走全局补给仓）。
# 能源不可存储、不在市场内。
MARKET = {
    "粮食": 2, "木头": 2, "矿石": 4, "石油": 6, "装备": 8, "补给": 5,
    "黄金": 10,
}
TRADEABLE = ["粮食", "木头", "矿石", "石油", "装备", "补给"]
# ---- 价格机制（2026-09-09 改革）----
# prices[g] 是「中间价」(mid)。一笔 n 单位的单子沿价格曲线走：
#   每单位推动 tick = 基准价 × PRICE_IMPACT ÷ 该商品深度
#   深度 = MARKET_DEPTH[g] × max(1, 现存国家数) ÷ 4（国家越多市场越深）
# 成交按「沿曲线均价」(p0+p1)/2 结算——不再整笔按成交后的清仓价 p1 结算
# （后者等于把最后一单位的价格摊给整笔，惩罚是应然的 2 倍）。
# 买卖另有价差 MARKET_SPREAD：买 +5% / 卖 −5%，翻转套利必亏。
# 每回合末市价向「供需均衡价」回归：保留 PRICE_REVERT，即回归 1-PRICE_REVERT。
PRICE_IMPACT = 0.02       # 每单位推动 = 基准价 × 该值 ÷ 深度
MARKET_DEPTH = {"粮食": 24, "木头": 24, "矿石": 16, "石油": 12, "装备": 8, "补给": 12}
MARKET_SPREAD = 0.10      # 买卖价差（买 +一半 / 卖 −一半）；0 = 无摩擦
PRICE_REVERT = 0.75       # 每回合保留的偏离比例（向均衡价回归 25%）
PRICE_MIN_RATIO = 0.2     # 市价下限 = 基准价 × 该值
PRICE_MAX_RATIO = 3.0     # 市价上限 = 基准价 × 该值

# 供需均衡价：每回合末按全世界本回合流量算
#   净缺口比 gap = (耗 − 产) ÷ (耗 + 产) ∈[-1,1]（耗大于产 → 贵）；单边为 0 时取 ±MARKET_GAP_ONE_SIDE
#   均衡价 = 基准价 × (1 + MARKET_SENS × gap)，再夹到 [EQ_MIN, EQ_MAX]
# 流量含：采集产出、工厂投料与产出、征兵耗料、建造耗木、能源厂燃料、军队补给消耗。
MARKET_SENS = 0.5         # 供需敏感度（缺口比 ±1 时均衡价 = 基准 ×0.5 / ×1.5）
MARKET_EQ_MIN_RATIO = 0.5  # 均衡价下限 = 基准价 × 该值
MARKET_EQ_MAX_RATIO = 1.5  # 均衡价上限 = 基准价 × 该值
MARKET_GAP_ONE_SIDE = 0.4  # 只有产或只有耗时，缺口比取 ±该值（软化：无人消耗≠无限过剩）

# 建筑定义（kind 决定回合行为）：
#   cost           造价（金）；城堡为列表，第 i 级造价 = cost[i]，逐级递增
#   wood           建造另需木材数（从全局木材储备扣除）
#   cap_resource   建造上限来源（该地块此项资源量即上限）；None=仅受建筑位/城堡级数限制
#   kind:
#     castle    城堡：max_level 级，每级 +10% 防御
#     extract   基础采集：outputs 每回合产出物资；林场产出木头入全局储备
#     gold      黄金矿场（产出黄金=货币，直接入国库）
#     energy    能源厂：耗全国木头/石油 → 产出 energy_out 能源；不耗能源维持
#     factory   高级工厂：每座每回合维持 energy 能源，投 inputs 产 outputs；能源不足全部瘫痪
BUILDINGS = {
    "城堡": {
        "kind": "castle",
        "cost": [100, 200, 400, 800, 1600],
        "wood": 10,
        "max_level": 5,
        "cap_resource": None,
    },
    "林场": {"kind": "extract", "cost": 45, "wood": 5, "cap_resource": "木头", "outputs": {"木头": 1}},
    "农场": {"kind": "extract", "cost": 50, "wood": 5, "cap_resource": "耕地", "outputs": {"粮食": 1}},
    "矿场": {"kind": "extract", "cost": 70, "wood": 5, "cap_resource": "矿石", "outputs": {"矿石": 1}},
    "石油厂": {"kind": "extract", "cost": 135, "wood": 8, "cap_resource": "石油", "outputs": {"石油": 1}},
    "黄金矿场": {"kind": "gold", "cost": 200, "wood": 10, "cap_resource": "黄金", "outputs": {"黄金": 1}},
    "木材能源厂": {"kind": "energy", "cost": 120, "wood": 15, "cap_resource": None, "fuel": {"木头": 1}, "energy_out": 2},
    "石油能源厂": {"kind": "energy", "cost": 240, "wood": 15, "cap_resource": None, "fuel": {"石油": 1}, "energy_out": 8},
    "补给厂": {"kind": "factory", "cost": 175, "wood": 12, "cap_resource": None, "inputs": {"粮食": 1, "矿石": 1}, "outputs": {"补给": 2}, "energy": 1},
    "装备厂": {"kind": "factory", "cost": 210, "wood": 12, "cap_resource": None, "inputs": {"矿石": 1, "石油": 1}, "outputs": {"装备": 2}, "energy": 1},
    # 兵营不自动产兵：每兵营每回合可征 1 支军队（army_cost 每支耗资），军队 100HP，从本地块征集；需本地已用建筑位≥3（防裸地兵营）
    "兵营": {"kind": "barracks", "cost": 350, "wood": 20, "cap_resource": None, "min_slots": 3, "army_cost": {"粮食": 10, "装备": 5}, "energy": 1},
    # 市政厅：很贵、每地块限 1 座、需该地块已用建筑位≥6 才可建；维持 1 电（电网不足即停摆）；
    # 每座每回合 = TOWN_HALL_GOLD(基础) + 该地块已占建筑位(不含自身)×TOWN_HALL_PER_SLOT 金 入国库
    "市政厅": {"kind": "townhall", "cost": 500, "wood": 40, "cap_resource": None,
               "energy": 1, "limit": 1, "min_slots": 6},
    # ---- 特殊建筑（不产出、不耗电，改规则）----
    # 瞭望塔：己方/盟方任一瞭望塔半径 WATCHTOWER_RADIUS 圆内的事件都可见（事件视野，不改可拓地）
    "瞭望塔": {"kind": "tower", "cost": 120, "wood": 15, "cap_resource": None},
    # 外交中心：**自建限 1 座**（limit_nation），叠加的只能靠夺地抢别国的——
    # 每座（含抢来的）让自己的外交费再减半（10→5→2→1，下限1）、写信费每座 -5 金（下限 5）；
    # 他国向你提议结盟/联盟/议和免费
    "外交中心": {"kind": "diplomat", "cost": 400, "wood": 30, "cap_resource": None,
                 "limit": 1, "limit_nation": 1, "min_slots": 5},
    # 工程院：本地块一切建造金价 -25%（与地形惩罚乘算，只认已落成的），需本地已用建筑位≥4
    "工程院": {"kind": "academy", "cost": 300, "wood": 30, "cap_resource": None,
               "limit": 1, "min_slots": 4},
    # 军屯：屯田 + 民兵编制——每回合 +1 粮；可征民兵（50金+5粮/支，不耗电、不受电网停摆影响）；
    # **每地块限 1 座**，且**全国民兵总数 ≤ 全国军屯总数**（军屯即民兵编制上限，阵亡可补员）；
    # 民兵驻本格不耗补给（每座军屯覆盖本格 1 支），离格照常吃
    "军屯": {"kind": "militia_camp", "cost": 220, "wood": 15, "cap_resource": "耕地",
             "limit": 1, "outputs": {"粮食": 1}},
}

# 兵种：征召耗粮装 / 每回合补给维持 / 每回合移动格数 / 基础攻击 / 满血上限（每军每战斗回合）
UNIT_TYPES = {
    "步": {"label": "步兵", "hp": 100, "speed": 1, "supply": 1, "atk": 50, "recruit": {"粮食": 10, "装备": 5}},
    "骑": {"label": "骑兵", "hp": 100, "speed": 2, "supply": 2, "atk": 50, "recruit": {"粮食": 12, "装备": 12}},
    # 民兵=廉价驻守军队（80HP/攻20，攻击只有步骑的四成）：只能在军屯征召（50金+5粮/支；
    # 全国民兵总数 ≤ 全国军屯总数，每军屯每回合 1 支）；驻本格（自家军屯格）不耗补给
    "民": {"label": "民兵", "hp": 80, "speed": 1, "supply": 1, "atk": 20,
           "recruit": {"黄金": 50, "粮食": 5}},
}


def unit_kind(a: dict) -> str:
    return a.get("type", "步")


def unit_max_hp(a: dict) -> int:
    """该军满血上限（兵种自带；旧档无 type 的军队按步兵）。"""
    return UNIT_TYPES[unit_kind(a)].get("hp", ARMY_MAX_HP)


def unit_speed(a: dict) -> int:
    return UNIT_TYPES[unit_kind(a)]["speed"]


def unit_atk(a: dict) -> int:
    """该军的每战斗回合基础伤害（野人等无 type 的旧军队按步兵 ARMY_ATTACK_DAMAGE 算）。"""
    return UNIT_TYPES[unit_kind(a)].get("atk", ARMY_ATTACK_DAMAGE)


def unit_supply(a: dict) -> int:
    return UNIT_TYPES[unit_kind(a)]["supply"]

# 军队属性
ARMY_MAX_HP = 100
ARMY_STARVE_DAMAGE = 35   # 补给不足时按缺口比例扣血（完全断供=35），交战中也照扣，HP≤0 阵亡
ARMY_HEAL_PER_TURN = 25   # 非战斗（且非断供）军队每回合回复，占满血 25%
ARMY_ATTACK_DAMAGE = 50   # 默认基础伤害（步/骑；民兵 20 —— 兵种攻击表见 UNIT_TYPES["atk"]）
RETREAT_RANGE = 1         # 撤退固定只能退相邻 1 格（3×3，所有人）；正常移动按兵种速度（步1/骑2）
RETREAT_ATK_PENALTY = 80  # 撤退军本回合战斗输出 -80%（撤离途中无心恋战；防撤退白嫖输出）
# 战斗骰：每回合掷 1d6 → 本回合双方伤害修正%（战争打几回合很正常）
COMBAT_DIE_MOD = {1: -25, 2: -15, 3: -5, 4: 5, 5: 15, 6: 25}

# 特殊建筑参数
WATCHTOWER_RADIUS = 4    # 瞭望塔事件视野半径（欧氏圆：dx²+dy²≤r²）
ENGINEER_DISCOUNT = 25   # 工程院：本地块建造金价减免 %
DIPLO_CENTER_MIN_COST = 1  # 外交中心叠加减半后的外交费下限
LETTER_CENTER_DISCOUNT = 5  # 外交中心对写信的减免：每座固定 -5 金
LETTER_COST_MIN = 5         # 写信费用下限（防零费刷信）

# 基地资源（随机生成的 5 项）→ 对应采集建筑
SITE_BUILDING = {
    info["cap_resource"]: name
    for name, info in BUILDINGS.items()
    if info["kind"] in ("extract", "gold")
}


_CN_DIGIT = "零一二三四五六七八九"


def cn_num(n: int) -> str:
    """把 1..99 转中文数字（≥100 退回阿拉伯数字）。"""
    if n < 0:
        return str(n)
    if n < 10:
        return _CN_DIGIT[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + _CN_DIGIT[n - 10] if n % 10 else "十"
    if n < 100:
        t, r = divmod(n, 10)
        return _CN_DIGIT[t] + "十" + (_CN_DIGIT[r] if r else "")
    return str(n)


def army_name(owner: str, seq: int, kind: str = "步") -> str:
    """按兵种编番号：骑一军、步二军…；国家军队加国名前缀：秦·骑一军。野人不走这里。"""
    prefix = f"{owner}·" if owner != "野人" else ""
    return f"{prefix}{kind}{cn_num(seq)}军"


def roll_terrain(rng: random.Random) -> str:
    names = list(TERRAIN_WEIGHTS)
    return rng.choices(names, weights=[TERRAIN_WEIGHTS[n] for n in names])[0]


def roll_resources(rng: random.Random, terrain: str) -> dict[str, int]:
    """按地形绑定的权重表抽全部资源，返回 {资源名: 数值}。"""
    return {
        res: rng.choices(range(len(weights)), weights=weights)[0]
        for res, weights in TERRAINS[terrain].items()
    }


# 地块自动命名：前缀+后缀组合（激活即命名，可用 name 命令改）。
# 词库尽量大（≈98×82≈8000 组合），多国各占大片地也不早退化；
# 实在撞完才退化到「词+序号」（北川2…）。
NAME_PREFIX = [
    "北", "南", "东", "西", "新", "古", "上", "龙", "凤", "金",
    "银", "青", "赤", "白", "黑", "临", "望", "平", "安", "宁",
    "武", "文", "云", "星", "月", "霜", "岚", "落", "永", "长",
    "苍", "碧", "翠", "幽", "玄", "沧", "遥", "朔", "晨", "暮",
    "晓", "曦", "曜", "凝", "澄", "昭", "宣", "兴", "昌", "隆",
    "靖", "绥", "泰", "康", "丰", "盈", "沃", "润", "祥", "瑞",
    "吉", "灵", "宝", "珍", "玉", "瑶", "珠", "珀", "琉", "紫",
    "绛", "浩", "瀚", "广", "远", "静", "逸", "悠", "隐", "朝",
    "阳", "晴", "皎", "媚", "秀", "丽", "繁", "盛", "归", "居",
    "定", "启", "承", "延", "通", "达", "明", "光",
]
NAME_SUFFIX = [
    "原", "川", "山", "岭", "河", "湖", "港", "城", "关", "屯",
    "堡", "镇", "州", "泽", "溪", "峰", "谷", "林", "坡", "岛",
    "滩", "桥", "仓", "营", "坊", "郡", "庭", "丘",
    "京", "都", "塘", "渡", "浦", "津", "洲", "渚", "汀", "湾",
    "澳", "礁", "岬", "屿", "崖", "峡", "嶂", "麓", "台", "阁",
    "楼", "亭", "寺", "观", "塔", "垛", "寨", "栅", "垒", "驿",
    "馆", "集", "井", "乡", "里", "邑", "苑", "圃", "园", "田",
    "垄", "畦", "渠", "堰", "堤", "坝", "牧", "耕", "榭", "轩",
    "庐", "斋", "市", "野",
]


def roll_tile_name(rng: random.Random, used: set[str]) -> str:
    """随机生成一个不与现有地名重复的地块名。"""
    base = ""
    for _ in range(500):
        base = rng.choice(NAME_PREFIX) + rng.choice(NAME_SUFFIX)
        if base not in used:
            return base
    i = 2  # 词库撞名兜底：加序号
    while f"{base}{i}" in used:
        i += 1
    return f"{base}{i}"
