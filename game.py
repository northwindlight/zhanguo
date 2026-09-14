"""战国（zhanguo）共享规则层：**规则函数** + 地块生成。

数值表（建筑/地形/军队/价格）全部住在 `balance.py`——**要调平衡只改那个文件**；
本模块把它们原样转口（`from game import BUILDINGS` 拿到的就是 `balance.BUILDINGS`
同一个对象，不是副本），并提供规则函数（`unit_*`/`letter_cost`/`roll_*`/`building_effect`…）。
多国引擎 `mp.py` 与 AI 层 `mp_ai.py` 都从这里取常量与生成函数；
这里不含任何世界状态（状态在 mp.World 里）。

地块生成：按地形权重随机出地形，再按该地形绑定的资源权重表随机出
矿石/黄金/耕地/石油/木头 五项资源。地形由 `seed:x:y` 决定（纯函数），
同一种子下重放同样的激活序列结果完全一致——对局可复现。
"""

from __future__ import annotations

import random

# 数值表全部住在 balance.py（**唯一调参入口**）；这里原样转口，不是副本：
# `from game import BUILDINGS` 拿到的与 `balance.BUILDINGS` 是**同一个对象**，
# 就地改（如 `rl/jitter.py` 的域随机化）两边同时可见。
from balance import (
    ARMY_ATTACK_DAMAGE,
    ARMY_HEAL_PER_TURN,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    COMBAT_DIE_MOD,
    DIPLO_CENTER_MIN_COST,
    GOODS,
    LETTER_CENTER_DISCOUNT,
    LETTER_CHARS_PER_GOLD,
    LETTER_COST,
    LETTER_COST_ALLY,
    LETTER_COST_MIN,
    LETTER_FREE_CHARS,
    MARKET,
    MARKET_DEPTH,
    MARKET_EQ_MAX_RATIO,
    MARKET_EQ_MIN_RATIO,
    MARKET_GAP_ONE_SIDE,
    MARKET_SENS,
    MARKET_SPREAD,
    MAX_SLOTS,
    MOVE_COST,
    PRICE_IMPACT,
    PRICE_MAX_RATIO,
    PRICE_MIN_RATIO,
    PRICE_REVERT,
    RESOURCES,
    RESOURCE_MAX,
    RETREAT_ATK_PENALTY,
    RETREAT_RANGE,
    SITE_BUILDING,
    TERRAINS,
    TERRAIN_CHARS,
    TERRAIN_STATS,
    TERRAIN_WEIGHTS,
    TRADEABLE,
    UNIT_TYPES,
)


# ---- 建筑的「效果」= **数据**，不是散落的模块级常量（2026-09-12 改）----
# 为什么：效果写在常量里，就只有**读那份代码的人/规则 AI** 知道；模型（RL 与任何学习者）
# 看不见它，只能从"建了之后发生了什么"反推。搬进 `BUILDINGS[*]["effects"]` 之后，
# 「工程院 -25%」是一条可读、可对比、可整体抖动的**数据**。
# 约定：**键名是语义**（见下面 `building_effect` 的取值表），缺省 = 0（没这项效果）。
# ⚠ 只搬了六座复合建筑的效果；**外交线**的 LETTER_*/DIPLO_CENTER_* 仍是常量
#   （RL 不观测外交，见 rl/TOKEN_DESIGN.md §10.10）。
def building_effect(name: str, key: str, default: float = 0):
    """取某建筑的效果值。`effects` 里没有的键 → `default`（默认 0 = 无此效果）。"""
    return BUILDINGS.get(name, {}).get("effects", {}).get(key, default)


def unit_kind(a: dict) -> str:
    return a.get("type", "步")


def unit_max_hp(a: dict) -> int:
    """该军满血上限（兵种自带；旧档无 type 的军队按步兵）。"""
    return UNIT_TYPES[unit_kind(a)].get("hp", ARMY_MAX_HP)


def unit_speed(a: dict) -> int:
    """每回合**移动力**（不再是"格数"）：每走一步按目标地形扣 `unit_move_cost`。"""
    return UNIT_TYPES[unit_kind(a)]["speed"]


def unit_move_cost(a: dict, terrain: str) -> int:
    """走进 `terrain` 这一格要花几点移动力（表里没写的地形按 1 = 不受阻）。"""
    return MOVE_COST.get(unit_kind(a), {}).get(terrain, 1)


def unit_atk(a: dict) -> int:
    """该军的每战斗回合基础伤害（野人等无 type 的旧军队按步兵 ARMY_ATTACK_DAMAGE 算）。"""
    return UNIT_TYPES[unit_kind(a)].get("atk", ARMY_ATTACK_DAMAGE)


def unit_supply(a: dict) -> int:
    return UNIT_TYPES[unit_kind(a)]["supply"]


def letter_cost(text: str, allied: bool = False, diplo_centers: int = 0) -> int:
    """一封信的价钱 = 起步价（联盟 10 / 非联盟 20，吃外交中心减免、下限 5）
    + ceil(超出免费额的字数 ÷ 每金字数)（不吃任何减免）。"""
    base = LETTER_COST_ALLY if allied else LETTER_COST
    base = max(LETTER_COST_MIN, base - LETTER_CENTER_DISCOUNT * diplo_centers)
    over = max(0, len(text or "") - LETTER_FREE_CHARS)
    return base + -(-over // LETTER_CHARS_PER_GOLD)


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
