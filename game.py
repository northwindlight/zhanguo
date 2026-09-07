"""战国（zhanguo）地块生成核心（单机层）。。

100x100 网格，每个地块代表一块领土。地块初始为迷雾（未激活）；
每次「激活」一个地块：按地形权重随机出地形，再按该地形绑定的
资源权重表随机出 矿石/黄金/耕地/石油/木头 五项资源，然后存档。

存档只记录「已激活的地块 + 随机种子 + RNG 状态」，即文件系统即状态，
同一种子重放同样的激活序列可得到完全相同的结果（确定性）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

# 资源项与上限
RESOURCES = ["矿石", "黄金", "耕地", "石油", "木头"]
RESOURCE_MAX = {"矿石": 5, "黄金": 2, "耕地": 5, "石油": 3, "木头": 5}

# 五种地形。每种地形给全部五项资源一张权重表：
#   列表下标 = 资源数值（x0, x1, ...），元素 = 抽中该数值的权重。
# 下标越界即该项资源不可能出现的更高值（权重为 0）。
TERRAINS = {
    "平原": {  # 沃土：耕地/木头为主，几乎无矿无油
        "矿石": [75, 18, 5, 2, 0, 0],
        "黄金": [95, 4, 1],
        "耕地": [8, 28, 32, 19, 9, 4],
        "石油": [90, 8, 2, 0],
        "木头": [38, 30, 20, 9, 3, 0],
    },
    "森林": {  # 木头宝地，兼有耕地
        "矿石": [70, 20, 8, 2, 0, 0],
        "黄金": [93, 6, 1],
        "耕地": [30, 30, 25, 10, 4, 1],
        "石油": [96, 3, 1, 0],
        "木头": [5, 15, 30, 28, 16, 6],
    },
    "丘陵": {  # 全能有，样样不拔尖，略出黄金
        "矿石": [20, 30, 25, 15, 7, 3],
        "黄金": [85, 12, 3],
        "耕地": [30, 30, 25, 10, 4, 1],
        "石油": [86, 12, 2, 0],
        "木头": [25, 30, 25, 13, 5, 2],
    },
    "山地": {  # 矿石/黄金富集，粮木贫
        "矿石": [5, 20, 30, 25, 14, 6],
        "黄金": [70, 22, 8],
        "耕地": [70, 20, 7, 3, 0, 0],
        "石油": [82, 15, 3, 0],
        "木头": [62, 25, 10, 3, 0, 0],
    },
    "沙漠": {  # 石油宝地，其余皆贫
        "矿石": [70, 20, 8, 2, 0, 0],
        "黄金": [86, 12, 2],
        "耕地": [90, 7, 2, 1, 0, 0],
        "石油": [18, 30, 28, 24],
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

# 世界市场基准价（金/单位）。黄金是货币（国库），不可交易；MARKET["黄金"] 仅作
# 黄金矿场产出的兑换额（1 黄金 → 10 金）。可交易 = 全部可存储物资：
#   粮食/木头/矿石/石油/装备 + 补给（军队口粮，买卖直接走全局补给仓）。
# 能源不可存储、不在市场内。
MARKET = {
    "粮食": 2, "木头": 2, "矿石": 4, "石油": 6, "装备": 8, "补给": 5,
    "黄金": 10,
}
TRADEABLE = ["粮食", "木头", "矿石", "石油", "装备", "补给"]
# 价格受简单供需影响、随时间回归基准：
#   每买卖 1 单位 → 市价朝不利于你的方向移动 基准价×PRICE_TICK_RATIO÷现存国家数
#   （市场深度随玩家数量缩放：国家越多，单笔冲击越小）；
#   整笔按「成交后的清仓价」结算（大单自己把市价推走，越急买越贵、越急卖越贱）；
#   每过一回合，市价相对基准的偏离保留 PRICE_REVERT（→ 每回合回归 1-PRICE_REVERT）。
PRICE_TICK_RATIO = 0.02   # 每单位的市价推动 = 基准价 × 2%
PRICE_REVERT = 0.85       # 每回合保留偏离的比例（回归 15%）
PRICE_MAX_RATIO = 3.0     # 市价上限 = 基准价 × 3（市场再大也有限度）
PRICE_MIN = 1.0           # 市价下限 = 1 金/单位（不会跌到分文不值）

# 建筑定义（kind 决定回合行为）：
#   cost           造价（金）；城堡为列表，第 i 级造价 = cost[i]，逐级递增
#   wood           建造另需木材数（从全局木材储备扣除）
#   cap_resource   建造上限来源（该地块此项资源量即上限）；None=仅受建筑位/城堡级数限制
#   kind:
#     castle    城堡：max_level 级，每级 +10% 防御
#     extract   基础采集：outputs 每回合产出物资；林场产出木头入全局储备
#     gold      黄金矿场（产出黄金=货币，直接入国库）
#     energy    能源厂：木材厂耗全局木头 / 石油厂耗本地石油 → 产出 energy_out 能源；不耗能源维持
#     factory   高级工厂：每座每回合维持 energy 能源，投 inputs 产 outputs；能源不足全部瘫痪
BUILDINGS = {
    "城堡": {
        "kind": "castle",
        "cost": [100, 200, 400, 800, 1600],
        "wood": 10,
        "max_level": 5,
        "cap_resource": None,
    },
    "林场": {"kind": "extract", "cost": 50, "wood": 5, "cap_resource": "木头", "outputs": {"木头": 1}},
    "农场": {"kind": "extract", "cost": 60, "wood": 5, "cap_resource": "耕地", "outputs": {"粮食": 1}},
    "矿场": {"kind": "extract", "cost": 80, "wood": 5, "cap_resource": "矿石", "outputs": {"矿石": 1}},
    "石油厂": {"kind": "extract", "cost": 150, "wood": 8, "cap_resource": "石油", "outputs": {"石油": 1}},
    "黄金矿场": {"kind": "gold", "cost": 200, "wood": 10, "cap_resource": "黄金", "outputs": {"黄金": 1}},
    "木材能源厂": {"kind": "energy", "cost": 120, "wood": 15, "cap_resource": "木头", "fuel": {"木头": 1}, "energy_out": 2},
    "石油能源厂": {"kind": "energy", "cost": 300, "wood": 15, "cap_resource": "石油", "fuel": {"石油": 1}, "energy_out": 5},
    "补给厂": {"kind": "factory", "cost": 200, "wood": 12, "cap_resource": "耕地", "inputs": {"粮食": 1, "矿石": 1}, "outputs": {"补给": 2}, "energy": 1},
    "装备厂": {"kind": "factory", "cost": 240, "wood": 12, "cap_resource": "石油", "inputs": {"矿石": 1, "石油": 1}, "outputs": {"装备": 2}, "energy": 1},
    # 兵营不自动产兵：每兵营每回合可征 1 支军队（army_cost 每支耗资），军队 100HP，从本地块征集
    "兵营": {"kind": "barracks", "cost": 350, "wood": 20, "cap_resource": None, "army_cost": {"粮食": 10, "装备": 5}, "energy": 1},
    # 市政厅：很贵、每地块限 1 座、需该地块已用建筑位≥6 才可建；维持 1 电（电网不足即停摆）；
    # 每座每回合 = TOWN_HALL_GOLD(基础) + 该地块已占建筑位(不含自身)×TOWN_HALL_PER_SLOT 金 入国库
    "市政厅": {"kind": "townhall", "cost": 500, "wood": 40, "cap_resource": None,
               "energy": 1, "limit": 1, "min_slots": 6},
}

# 兵种：征召耗粮装 / 每回合补给维持 / 每回合移动格数
UNIT_TYPES = {
    "步": {"label": "步兵", "speed": 1, "supply": 1, "recruit": {"粮食": 10, "装备": 5}},
    "骑": {"label": "骑兵", "speed": 2, "supply": 2, "recruit": {"粮食": 12, "装备": 12}},
}


def unit_kind(a: dict) -> str:
    return a.get("type", "步")


def unit_speed(a: dict) -> int:
    return UNIT_TYPES[unit_kind(a)]["speed"]


def unit_supply(a: dict) -> int:
    return UNIT_TYPES[unit_kind(a)]["supply"]

# 军队属性
ARMY_MAX_HP = 100
ARMY_STARVE_DAMAGE = 10   # 补给不足时每回合扣血，HP≤0 阵亡
ARMY_HEAL_PER_TURN = 25   # 非战斗（且非断供）军队每回合回复，占满血 25%
ARMY_ATTACK_DAMAGE = 50   # 每支军队每战斗回合的基础伤害（受防守方地形+城堡防御修正）
RETREAT_RANGE = 1         # 撤退固定只能退相邻 1 格（3×3，所有人）；正常移动按兵种速度（步1/骑2）
# 战斗骰：每回合掷 1d6 → 本回合双方伤害修正%（战争打几回合很正常）
COMBAT_DIE_MOD = {1: -25, 2: -15, 3: -5, 4: 5, 5: 15, 6: 25}

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
    """按兵种编番号：骑一军、步二军…；多国加国名：秦·骑一军。野人不走这里。"""
    prefix = f"{owner}·" if owner not in ("player", "野人") else ""
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


class World:
    """size x size 地块世界。tiles 只存已激活地块：{(x, y): {"terrain", "resources"}}。"""

    def __init__(self, size: int = 100, seed: int | None = None, *, new_start: bool = False):
        self.size = size
        self.seed = seed if seed is not None else random.randrange(1 << 31)
        self.rng = random.Random(self.seed)
        self.tiles: dict[tuple[int, int], dict] = {}
        self.gold = STARTING_GOLD
        self.wood = STARTING_WOOD  # 全局木材储备（建任何建筑都要耗木）
        self.supply = STARTING_SUPPLY  # 全局补给仓（军队口粮，1 补给/支/回合）
        self.stock: dict[str, int] = {g: 0 for g in GOODS}  # 全局物资储备（粮/矿/油/装）
        self.prices: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}  # 世界市场现价
        self.grid_short = False  # 上一回合全国电网是否不足（高级建筑停摆）
        self.armies: list[dict] = []  # 军队：{id, name, hp, x, y, owner}
        self.next_army_id = 1
        self.cleared_tiles: set[tuple[int, int]] = set()  # 野人已被击败的地块（不再重生）
        self.turn = 0
        if new_start:
            self.place_start()  # 全新开局：中心+上下左右 5 地块（无首都概念）

    # ---- 视野与激活 ----
    def tile_terrain(self, x: int, y: int) -> str:
        """地块地形：纯函数，由种子+坐标决定（视野预览与占领结果一致）。"""
        return roll_terrain(random.Random(f"{self.seed}:{x}:{y}"))

    def frontier(self) -> set[tuple[int, int]]:
        """视野内可占领的地块：自己地块的相邻格（含对角线，未占领）。"""
        fr: set[tuple[int, int]] = set()
        for (x, y) in self.tiles:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.size and 0 <= ny < self.size and (nx, ny) not in self.tiles:
                        fr.add((nx, ny))
        return fr

    def _new_tile(self, x: int, y: int) -> dict:
        """生成一块新地皮（地形/资源/自动命名/建筑清零），不检查视野与野人。"""
        terrain = self.tile_terrain(x, y)
        used = {t["name"] for t in self.tiles.values() if t.get("name")}
        return {
            "terrain": terrain,
            "resources": roll_resources(self.rng, terrain),
            "buildings": {name: 0 for name in BUILDINGS},
            # 全部资源全局存储（world.stock），地块不再囤货
            "name": roll_tile_name(self.rng, used),  # 名字即唯一ID
            "recruited_this_turn": 0,   # 本回合已征召军队数（每兵营 1 支/回合）
            "built_this_turn": 0,       # 本回合已建建筑数（每地块每回合限 1 座）
        }

    def activate(self, x: int, y: int) -> dict:
        """占领一个地块：须先击败该地块野人（atk），且在视野内（首块任意）。"""
        self._check_bounds(x, y)
        key = (x, y)
        if key in self.tiles:
            return self.tiles[key]
        if self.tiles and key not in self.frontier():
            raise ValueError("该地块不在视野内（只能占领与现有地块相邻的地块）")
        if any(a["owner"] == "野人" and (a["x"], a["y"]) == key for a in self.armies):
            raise ValueError("该地块有野人驻军，先派军队击败（atk 军队id 地块）才能占领")
        tile = self._new_tile(x, y)
        self.tiles[key] = tile
        self.refresh_barbarians()  # 为新进入视野的地块驻扎野人
        return tile

    def place_start(self) -> None:
        """开局占据 5 地块：中心 + 上下左右（十字形）。仅在全新世界调用。"""
        if self.tiles:
            return
        c = self.size // 2
        cells = [(c, c), (c, c - 1), (c, c + 1), (c - 1, c), (c + 1, c)]  # 中 上 下 左 右
        for x, y in cells:
            if 0 <= x < self.size and 0 <= y < self.size and (x, y) not in self.tiles:
                self.tiles[(x, y)] = self._new_tile(x, y)
        self.refresh_barbarians()

    def tile_by_name(self, name: str) -> tuple[int, int] | None:
        """按地块名（即ID）查坐标，无则返回 None。"""
        for (x, y), t in self.tiles.items():
            if t.get("name") == name:
                return (x, y)
        return None

    def refresh_barbarians(self) -> None:
        """视野内（frontier）未占领地块各驻扎一支野人军队（正常军队，自给自足）。

        已被击败的地块（cleared_tiles）不再重生野人。
        """
        for (x, y) in self.frontier():
            if (x, y) in self.cleared_tiles:
                continue
            if not any(
                a["owner"] == "野人" and a["x"] == x and a["y"] == y for a in self.armies
            ):
                aid = self.next_army_id
                self.next_army_id += 1
                self.armies.append(
                    {
                        "id": aid,
                        "name": f"野人{aid}",
                        "hp": ARMY_MAX_HP,
                        "x": x,
                        "y": y,
                        "owner": "野人",
                        "moved_turn": -1,
                    }
                )

    # ---- 建设 ----
    def build(self, x: int, y: int, building: str) -> tuple[bool, str]:
        """在 (x, y) 建一座建筑。返回 (成功?, 消息)。"""
        tile = self.get(x, y)
        if tile is None:
            return False, "该地块尚未激活，先 a [x y]"
        if building not in BUILDINGS:
            return False, f"未知建筑：{building}"
        info = BUILDINGS[building]
        b = tile["buildings"]
        # 每地块每回合限建 1 座
        if tile["built_this_turn"] >= 1:
            return False, "该地块本回合已建过一座建筑（每地块每回合限 1 座，过一回合 n 后能再建）"
        # 20 格建筑位（城堡级数也占位）
        if sum(b.values()) >= MAX_SLOTS:
            return False, f"建筑位已满（{MAX_SLOTS} 格）"
        # 建造上限 = 当地资源量（城堡无资源上限，仅受级数限制）
        cr = info["cap_resource"]
        if cr is not None:
            limit = tile["resources"][cr]
            if b[building] >= limit:
                return False, f"{building} 已达上限：本地 {cr}={limit}，最多 {limit} 座"
        # 城堡 5 级上限
        if info["kind"] == "castle" and b[building] >= info["max_level"]:
            return False, f"{building} 已达 {info['max_level']} 级上限"
        # 特殊约束（市政厅等）：需本地块已用建筑位达标 / 每地块限座
        used = sum(b.values())
        if info.get("min_slots") and used < info["min_slots"]:
            return False, f"{building} 需该地块已用建筑位 ≥{info['min_slots']}（现 {used}），先在本地建满再盖"
        if info.get("limit") and b[building] >= info["limit"]:
            return False, f"{building} 已达上限（每地块 {info['limit']} 座）"
        # 造价（城堡逐级递增）+ 木材
        level = b[building]
        if info["kind"] == "castle":
            cost = info["cost"][level]
            label = f"城堡 L{level + 1}"
        else:
            cost = info["cost"]
            label = building
        wood = info["wood"]
        if self.gold < cost:
            return False, f"黄金不足：{label} 需 {cost} 金，国库 {self.gold}"
        if self.wood < wood:
            return False, f"木材不足：{label} 需 {wood} 木，储备 {self.wood}"
        self.gold -= cost
        self.wood -= wood
        b[building] += 1
        tile["built_this_turn"] = 1
        return True, f"建成 {label}（-{cost} 金 -{wood} 木），国库 {self.gold}，木材 {self.wood}"

    # ---- 世界市场 ----
    # 玩家与外部世界买卖物资换黄金（黄金是货币、不可交易）。价格受简单供需影响：
    #   买入=需求 → 推高市价；卖出=供给 → 压低市价；整笔按成交后的清仓价结算。
    #   每过一回合市价向基准价回归（advance_turn 里做）。无需任何建筑，随时可交易。
    def holding(self, good: str) -> int:
        """某项物资当前全国持有量（粮/矿/油/装在 stock，木头=wood，补给=supply）。"""
        if good == "木头":
            return self.wood
        if good == "补给":
            return self.supply
        return self.stock.get(good, 0)

    def _add_holding(self, good: str, amount: int) -> None:
        if good == "木头":
            self.wood += amount
        elif good == "补给":
            self.supply += amount
        else:
            self.stock[good] += amount

    def market_price(self, good: str) -> float:
        """某物当前市价（保留 1 位小数，喂 LLM 友好）。"""
        return round(self.prices[good], 1)

    def _clamp_price(self, good: str, p: float) -> float:
        base = MARKET[good]
        return min(max(p, PRICE_MIN), base * PRICE_MAX_RATIO)

    def buy(self, good: str, n: int) -> tuple[bool, str]:
        """从世界市场买 n 单位。整笔按推高后的清仓价成交，市价随后停在新价位。"""
        if good not in TRADEABLE:
            return False, f"「{good}」不在世界市场交易（可交易：{'、'.join(TRADEABLE)}；黄金是货币不是货物）"
        if n <= 0:
            return False, "购买数量需为正整数（buy 粮食 10）"
        base = MARKET[good]
        p0 = self.prices[good]
        p1 = self._clamp_price(good, p0 + base * PRICE_TICK_RATIO * n)  # 需求→推高
        cost = int(round(p1 * n))  # 整笔按清仓价 p1 结算
        if self.gold < cost:
            return False, (
                f"黄金不足：现价 {p0:.1f} 金，买 {good}×{n}（大单把市价推到 {p1:.1f}）"
                f"需 {cost} 金，国库仅 {self.gold}"
            )
        self.gold -= cost
        self._add_holding(good, n)
        self.prices[good] = p1
        return True, (
            f"购入 {good}×{n}：市价 {p0:.1f} → {p1:.1f}，按 {p1:.1f}/单位实付 {cost} 金"
            f"（国库 {self.gold}），持有 {self.holding(good)}"
        )

    def sell(self, good: str, n: int) -> tuple[bool, str]:
        """向世界市场卖 n 单位。整笔按压低后的清仓价成交，市价随后停在新价位。"""
        if good not in TRADEABLE:
            return False, f"「{good}」不在世界市场交易（可交易：{'、'.join(TRADEABLE)}；黄金是货币不是货物）"
        if n <= 0:
            return False, "卖出数量需为正整数（sell 矿石 5）"
        if self.holding(good) < n:
            return False, f"国家储备不足：{good} 现持有 {self.holding(good)}，卖不出 {n}"
        base = MARKET[good]
        p0 = self.prices[good]
        p1 = self._clamp_price(good, p0 - base * PRICE_TICK_RATIO * n)  # 供给→压低
        gold = int(round(p1 * n))  # 整笔按清仓价 p1 结算
        self.gold += gold
        self._add_holding(good, -n)
        self.prices[good] = p1
        return True, (
            f"售出 {good}×{n}：市价 {p0:.1f} → {p1:.1f}，按 {p1:.1f}/单位实收 {gold} 金"
            f"（国库 {self.gold}），余 {self.holding(good)}"
        )

    # ---- 征兵（手动，每兵营每回合 1 支军队） ----
    def recruit(self, x: int, y: int, n: int = 1, kind: str = "步") -> tuple[bool, str]:
        """在 (x, y) 征召 n 支 kind 兵种（步/骑），从本地块（兵营地）集结，100HP。"""
        if kind not in UNIT_TYPES:
            return False, f"未知兵种：{kind}（可选：{'、'.join(UNIT_TYPES)}）"
        tile = self.get(x, y)
        if tile is None:
            return False, "该地块尚未激活，先 a [x y]"
        barracks = tile["buildings"]["兵营"]
        if barracks <= 0:
            return False, "该地块没有兵营，先 b 兵营"
        if self.grid_short:
            return False, "全国电网不足，高级建筑（含兵营）停摆，无法征兵"
        cap = barracks - tile["recruited_this_turn"]
        if cap <= 0:
            return False, f"本回合征召产能已用完（每兵营 1 支/回合，共 {barracks} 支）"
        n = min(n, cap)
        cost = UNIT_TYPES[kind]["recruit"]  # 兵种各自征召耗资
        max_by_input = min(self.stock[f] // amt for f, amt in cost.items())
        n = min(n, max_by_input)
        if n <= 0:
            cost_desc = "、".join(f"{f}x{amt}" for f, amt in cost.items())
            return False, f"国家储备不足，无法征{UNIT_TYPES[kind]['label']}（每支耗 {cost_desc}）"
        for f, amt in cost.items():
            self.stock[f] -= amt * n
        seq = sum(1 for a in self.armies if a["owner"] == "player" and unit_kind(a) == kind) + 1
        for i in range(n):
            aid = self.next_army_id
            self.next_army_id += 1
            self.armies.append(
                {
                    "id": aid,
                    "name": army_name("player", seq + i, kind),  # 步一军、骑一军…
                    "type": kind,
                    "hp": ARMY_MAX_HP,
                    "x": x,
                    "y": y,
                    "owner": "player",
                    "moved_turn": -1,  # 最近一次移动所在回合（每回合按兵种速度走）
                    "engaged": False,  # 是否正在交战（atk 进入，retreat/撤离 解除）
                }
            )
        tile["recruited_this_turn"] += n
        cost_desc = "、".join(f"{f}-{amt * n}" for f, amt in cost.items())
        return True, (
            f"征召 {n} 支{UNIT_TYPES[kind]['label']}（{cost_desc}），从 ({x + 1},{y + 1}) 集结，"
            f"总兵力 {len(self.armies)}"
        )

    def move_army(self, army_id: int, x: int, y: int) -> tuple[bool, str]:
        """手动调遣军队：每回合限一次，且只能移动到相邻一格（含对角线）。"""
        self._check_bounds(x, y)
        for a in self.armies:
            if a["id"] == army_id:
                if a.get("engaged"):
                    return False, f"{a['name']} 正在交战中，不能直接移动；先 retreat 军队id 目标格 撤出"
                speed = unit_speed(a)
                if max(abs(a["x"] - x), abs(a["y"] - y)) > speed:
                    return False, (f"{UNIT_TYPES[unit_kind(a)]['label']} 每回合只能移动 {speed} 格"
                                   f"（当前 ({a['x']+1},{a['y']+1})，目标偏移过大）")
                if a.get("moved_turn") == self.turn:
                    return False, f"军队{army_id} 本回合已移动过（每回合 1 次移动）"
                frm = (a["x"] + 1, a["y"] + 1)
                a["x"], a["y"] = x, y
                a["moved_turn"] = self.turn
                return True, f"军队{army_id} {a['name']} 调遣 ({frm[0]},{frm[1]}) → ({x + 1},{y + 1})"
        return False, f"军队 {army_id} 不存在（army/armies 查看军队列表）"

    def name_tile(self, x: int, y: int, name: str) -> tuple[bool, str]:
        """给地皮改名。名字即地块唯一ID：非空、不得与其他地块重名。"""
        tile = self.get(x, y)
        if tile is None:
            return False, "该地块尚未占领，先 a x y"
        name = name.strip()
        if not name:
            return False, "名字不能为空（名字是地块的唯一ID，不能清除）"
        if " " in name:
            return False, "名字不能含空格（需为单个词）"
        holder = self.tile_by_name(name)
        if holder is not None and holder != (x, y):
            return False, f"名字「{name}」已被其他地块占用（({holder[0]+1},{holder[1]+1})）"
        old = tile.get("name")
        tile["name"] = name
        return True, f"({x + 1},{y + 1}) 改名：{old} → {name}"

    # ---- 战斗 ----
    # 战争按游戏回合推进：atk 冲入目标格并进入交战（atk 已含移动）；之后每过一回合
    # （n）掷骰结算一个战斗回合（战争打几回合很正常）；retreat 选一格撤出，代价是
    # 当回合挨敌方一击、不还手。野人无 AI：不主动攻击、不追击，平时可路过野人地块。
    def tile_defense(self, x: int, y: int) -> int:
        """地块总防御% = 地形与城堡**相乘**叠加（避免相加到 100% 无敌）。

        综合减伤 = 1 - (1-地形防御%) × (1-城堡防御%)。山地+城堡L5 为 75%。
        """
        tile = self.tiles.get((x, y))
        if tile is None:
            terrain = self.tile_terrain(x, y)
            castle = 0
        else:
            terrain = tile["terrain"]
            castle = tile["buildings"]["城堡"]
        t = TERRAIN_STATS[terrain]["defense"]
        c = castle * CASTLE_DEFENSE_PER_LEVEL
        return 100 - ((100 - t) * (100 - c)) // 100

    @staticmethod
    def _combat_power(n_units: int, def_pct: int) -> int:
        """n 支军队的总伤害：各 50 基础，按防守方总防御%修正。"""
        return max(1, n_units * ARMY_ATTACK_DAMAGE * (100 - def_pct) // 100)

    @staticmethod
    def _spread_damage(dmg: int, units: list[dict]) -> None:
        """伤害分摊：总伤害平均分给敌方各单位，余数给排前者。"""
        per, rem = divmod(dmg, len(units))
        for i, u in enumerate(units):
            u["hp"] -= per + (1 if i < rem else 0)

    def _roll_combat_die(self) -> tuple[int, int]:
        """掷战斗骰 1d6，返回 (点数, 伤害修正%)。"""
        d = self.rng.randint(1, 6)
        return d, COMBAT_DIE_MOD[d]

    @staticmethod
    def _round_damage(power: int, mod: int) -> int:
        """把某方基础总伤害按骰子修正换算成当回合实际伤害。"""
        return max(1, power * (100 + mod) // 100)

    def attack(self, army_ids: list[int], x: int, y: int) -> tuple[bool, str]:
        """atk：军队（在移动距离内者）冲入 (x, y) 并与该地野人交战。atk 已含移动。"""
        self._check_bounds(x, y)
        defenders = [d for d in self.armies if d["owner"] == "野人" and (d["x"], d["y"]) == (x, y)]
        if not defenders:
            return False, "该地块没有野人军队，无需战斗"
        targets = [a for a in self.armies if a["owner"] == "player" and a["id"] in army_ids]
        if not targets:
            return False, f"未找到我方军队（id: {army_ids}），army 面板查看"
        # 校验：距离 ≤ 兵种速度 且本回合未移动过（已在目标格则不需移动）；交战中不可改攻他处
        for a in targets:
            if a.get("engaged") and (a["x"], a["y"]) != (x, y):
                return False, (f"{a['name']} 正在交战中，不能离开战场改攻他处；"
                               f"想脱战先 retreat 军队id 目标格（会挨一击）")
            if max(abs(a["x"] - x), abs(a["y"] - y)) > unit_speed(a):
                return False, (f"{a['name']} 距 ({x + 1},{y + 1}) 超出"
                               f"{UNIT_TYPES[unit_kind(a)]['label']} 移动范围（{unit_speed(a)} 格），冲不进去")
            if (a["x"], a["y"]) != (x, y) and a.get("moved_turn") == self.turn:
                return False, f"{a['name']} 本回合已移动过"
        # 冲入 + 交战
        for a in targets:
            if (a["x"], a["y"]) != (x, y):
                a["x"], a["y"] = x, y
                a["moved_turn"] = self.turn
            a["engaged"] = True
        names = "、".join(f"{a['name']}({a['hp']}hp)" for a in targets)
        return True, (
            f"{names} 冲入 ({x + 1},{y + 1}) 与野人交战。"
            f"之后每过一回合（n）掷骰结算一轮；想走先 retreat 军队id 目标格（会挨一击）。"
        )

    def retreat(self, army_id: int, x: int, y: int) -> tuple[bool, str]:
        """retreat：撤出=一次移动（与 mv/atk 同额度）。只能在交战中用，**固定只能退相邻 1 格**
        （所有人，不按兵种速度），用掉本回合移动并脱离交战；下回合起可正常行动。"""
        self._check_bounds(x, y)
        a = next((m for m in self.armies if m["id"] == army_id and m["owner"] == "player"), None)
        if a is None:
            return False, f"军队 {army_id} 不存在（army/armies 查看军队列表）"
        if not a.get("engaged"):
            return False, f"{a['name']} 未在交战中，无需撤退（atk 参战后才能撤）"
        if (a["x"], a["y"]) == (x, y):
            return False, "撤出需选一个与当前不同的格"
        if max(abs(a["x"] - x), abs(a["y"] - y)) > RETREAT_RANGE:
            return False, "撤退固定只能退相邻 1 格（3×3），超出范围"
        if a.get("moved_turn") == self.turn:
            return False, f"{a['name']} 本回合已移动/进攻过，移动额度用尽，撤不出（下回合再撤）"
        defs = [d for d in self.armies if d["owner"] == "野人" and (d["x"], d["y"]) == (a["x"], a["y"]) and d["hp"] > 0]
        hurt = ""
        if defs:
            die, mod = self._roll_combat_die()
            dmg = self._round_damage(self._combat_power(len(defs), 0), mod)
            a["hp"] -= dmg
            hurt = f"，撤出时挨野人一击 {-dmg}HP（骰{die} 修正{mod:+d}%）"
            if a["hp"] <= 0:
                self.armies.remove(a)
                return False, f"{a['name']} 撤出时被野人击杀（HP≤0）"
        a["engaged"] = False
        a["x"], a["y"] = x, y
        a["moved_turn"] = self.turn
        return True, (
            f"{a['name']} 撤到 ({x + 1},{y + 1}){hurt}，脱离交战；下回合可正常行动"
        )

    def _advance_battles(self) -> tuple[list[str], set[int]]:
        """推进战争：每处交战地块结算一个战斗回合，本回合共用同一颗骰子。

        返回（战报行列表，本轮参战军队的 id() 集合——用于跳过回血/野外扣血）。
        """
        tiles: dict[tuple[int, int], list[dict]] = {}
        for a in self.armies:
            if a["owner"] == "player" and a.get("engaged") and a["hp"] > 0:
                tiles.setdefault((a["x"], a["y"]), []).append(a)
        if not tiles:
            return [], set()
        die, mod = self._roll_combat_die()
        lines = [f"⚔ 战斗骰 {die} → 本回合双方伤害修正 {mod:+d}%"]
        participants: set[int] = set()
        for (x, y), atks in sorted(tiles.items()):
            defs = [d for d in self.armies if d["owner"] == "野人" and (d["x"], d["y"]) == (x, y) and d["hp"] > 0]
            if not defs:
                for a in atks:
                    a["engaged"] = False
                continue
            for a in atks:
                participants.add(id(a))
            for d in defs:
                participants.add(id(d))
            tag = f"({x + 1},{y + 1}){self.tile_terrain(x, y)}"
            # 同时出手：双方按开战兵力全力互击，再一起结算阵亡（允许同归于尽）
            atk_dmg = self._round_damage(self._combat_power(len(atks), self.tile_defense(x, y)), mod)
            ret = self._round_damage(self._combat_power(len(defs), 0), mod)
            self._spread_damage(atk_dmg, defs)
            self._spread_damage(ret, atks)
            dead_def = [d for d in defs if d["hp"] <= 0]
            for d in dead_def:
                self.armies.remove(d)
            dead_atk = [a for a in atks if a["hp"] <= 0]
            for a in dead_atk:
                self.armies.remove(a)
            def_alive = [d for d in defs if d["hp"] > 0]
            atk_alive = [a for a in atks if a["hp"] > 0]
            if not def_alive and not atk_alive:
                # 野人死了就是无主空地，谁派军队来占就归谁，没有重生一说
                self.cleared_tiles.add((x, y))
                lines.append(
                    f"⚔ 同归于尽 @{tag}：我军 {len(dead_atk)} 支与野人 {len(dead_def)} 支同回合全灭——"
                    f"此地成无主空地，派军队来占即归你"
                )
            elif not def_alive:
                self.cleared_tiles.add((x, y))
                for a in atks:
                    a["engaged"] = False
                # 打赢即自动占领（atk 已含占领逻辑）
                try:
                    tile = self.activate(x, y)
                    occ = f"自动占领「{tile['name']}」"
                except ValueError as e:
                    occ = f"（占领失败：{e}）"
                lines.append(
                    f"⚔ 胜利 @{tag}：歼灭野人 {len(dead_def)} 支，我军余 {len(atk_alive)} 支，{occ}"
                )
            elif not atk_alive:
                def_hp = "、".join(f"{d['name']}[{d['hp']}hp]" for d in def_alive)
                lines.append(
                    f"⚔ 失败 @{tag}：我军 {len(dead_atk)} 支全灭，野人余 {def_hp}"
                )
            else:
                atk_hp = "、".join(f"{a['name']}[{a['hp']}hp]" for a in atk_alive)
                def_hp = "、".join(f"{d['name']}[{d['hp']}hp]" for d in def_alive)
                lines.append(
                    f"⚔ 交火 @{tag}：我军受 {ret} 伤（灭 {len(dead_atk)} 支），"
                    f"余 {atk_hp}；野人余 {def_hp}"
                )
        return lines, participants

    # ---- 回合 ----
    def advance_turn(self) -> dict:
        """过一回合。全部资源（含能源用法）均为全局制：

          1. 采集：全部地块的农场/林场/矿场/石油厂/黄金矿场 → 国家储备（粮矿油装备、木、金）
          2. 发电：全国能源厂（耗全国木头/石油）→ 全国电网（电力不存储）
          3. 电网校核：全国电网 vs 全部高级建筑（补给厂/装备厂/兵营）维持需求；
             不足则高级建筑全部停摆（能源厂除外）
          4. 工厂投料：能源充足时，各厂从国家储备按序投料生产
        战争：各交战地块掷骰结算一个战斗回合。
        军队：从全局补给仓按 1 补给/支扣维持（不分地块）；断供则全国军队扣血。
        """
        produced = {g: 0 for g in GOODS}
        income = 0          # 黄金矿收入
        hall_gold = 0       # 市政厅收入（每座基础+按本地建筑数，电网不足则停摆）
        wood_in = 0
        wood_fuel = 0
        oil_fuel = 0
        supply_in = 0
        energy_total = 0
        maintenance_total = 0
        factories: list[tuple[dict, str, int]] = []  # (tile, 名, 数)

        # 新回合：各地块 征召产能 / 建设次数 刷新
        for tile in self.tiles.values():
            tile["recruited_this_turn"] = 0
            tile["built_this_turn"] = 0

        # 1) 采集 → 国家储备
        for tile in self.tiles.values():
            b = tile["buildings"]
            for name, count in b.items():
                info = BUILDINGS[name]
                if count == 0 or info["kind"] not in ("extract", "gold"):
                    continue
                for g, amt in info["outputs"].items():
                    if g == "黄金":
                        income += amt * count * MARKET["黄金"]
                    elif g == "木头":
                        self.wood += amt * count
                        wood_in += amt * count
                    else:
                        self.stock[g] += amt * count
                        produced[g] += amt * count

        # 2) 发电（全国电网）：木材厂耗全国木头，石油厂耗全国石油
        for tile in self.tiles.values():
            for name, count in tile["buildings"].items():
                info = BUILDINGS[name]
                if count == 0 or info["kind"] != "energy":
                    continue
                if name == "木材能源厂":
                    batches = min(count, self.wood)
                    self.wood -= batches
                    wood_fuel += batches
                else:  # 石油能源厂
                    batches = min(count, self.stock["石油"])
                    self.stock["石油"] -= batches
                    oil_fuel += batches
                if batches:
                    energy_total += batches * info["energy_out"]

        # 收集高级建筑需求：工厂入列投料；兵营/市政厅只计维持电（征兵走 r 命令、市政厅产金见下）
        for tile in self.tiles.values():
            for name, count in tile["buildings"].items():
                if count == 0:
                    continue
                kind = BUILDINGS[name]["kind"]
                if kind == "factory":
                    factories.append((tile, name, count))
                    maintenance_total += count
                elif kind in ("barracks", "townhall"):
                    maintenance_total += count

        # 3) 电网校核（电力不存储、全局）：不足则高级建筑全部停摆
        self.grid_short = energy_total < maintenance_total
        if not self.grid_short:
            # 4) 工厂按序投料生产（从国家储备）
            for tile, name, count in factories:
                info = BUILDINGS[name]
                batches = count
                for f, amt in info["inputs"].items():
                    batches = min(batches, self.stock[f] // amt)
                if batches == 0:
                    continue
                for f, amt in info["inputs"].items():
                    self.stock[f] -= amt * batches
                for g, amt in info["outputs"].items():
                    if g == "补给":
                        self.supply += amt * batches
                        supply_in += amt * batches
                    else:
                        self.stock[g] += amt * batches
                        produced[g] += amt * batches
            # 市政厅：每座 = 基础 TOWN_HALL_GOLD + 该地块已占建筑位(不含自身)×PER_SLOT 金；电网不足即停摆
            for tile in self.tiles.values():
                h = tile["buildings"].get("市政厅", 0)
                if h:
                    others = sum(tile["buildings"].values()) - h
                    hall_gold += (TOWN_HALL_GOLD + others * TOWN_HALL_PER_SLOT) * h

        # 战争推进：每处交战地块结算一个战斗回合（本回合共用一颗骰子）
        wars, participants = self._advance_battles()

        # 军队全局补给维持 + 回复：补给仓全局扣（不分地块），每支军队 1 补给/回合；
        # 断供则全国军队扣血（HP≤0 阵亡），断供/交战的军队本回合不回复
        famine = None  # (缺口, 阵亡数)
        player_armies = [a for a in self.armies if a["owner"] == "player"]
        need = sum(unit_supply(a) for a in player_armies)  # 步1/骑2 补给每回合
        paid = min(need, self.supply)
        self.supply -= paid
        shortage = need - paid
        starved: set[int] = set()
        if shortage:
            dead = []
            for a in player_armies:
                a["hp"] -= ARMY_STARVE_DAMAGE
                if a["hp"] <= 0:
                    dead.append(a)
            for a in dead:
                self.armies.remove(a)
            famine = (shortage, len(dead))
            starved = {id(a) for a in self.armies if a["owner"] == "player"}
        for a in list(self.armies):
            if id(a) in starved or id(a) in participants:
                continue  # 断供/交战：不回复
            if a["owner"] == "player":
                a["hp"] = min(ARMY_MAX_HP, a["hp"] + ARMY_HEAL_PER_TURN)
            else:  # 野人自给自足
                a["hp"] = min(ARMY_MAX_HP, a["hp"] + ARMY_HEAL_PER_TURN)

        # 市场：现价随时间向基准价回归（每回合偏离收窄为原来的 PRICE_REVERT）
        for g in TRADEABLE:
            base = MARKET[g]
            p = self.prices[g]
            self.prices[g] = round(self._clamp_price(g, base + (p - base) * PRICE_REVERT), 2)

        self.turn += 1
        self.gold += income + hall_gold
        return {
            "turn": self.turn,
            "income": income,
            "hall_gold": hall_gold,   # 市政厅收入（电网不足停摆时为 0）
            "produced": produced,
            "supply_in": supply_in,
            "supply": self.supply,
            "wood_in": wood_in,
            "wood_fuel": wood_fuel,
            "oil_fuel": oil_fuel,
            "energy_total": energy_total,
            "maintenance_total": maintenance_total,
            "grid_short": self.grid_short,  # 全国电网不足（高级建筑停摆）
            "famine": famine,      # 全局断粮 (缺口, 阵亡数) 或 None
            "wars": wars,          # 战争战报行
            "total_armies": len(self.armies),
            "wood": self.wood,
            "stock": dict(self.stock),
        }

    def activate_random(self) -> tuple[int, int, dict]:
        """从视野内随机占领一块没有野人的地块（扩张基本要靠打赢自动占领）。"""
        free = [
            (x, y)
            for (x, y) in self.frontier()
            if not any(a["owner"] == "野人" and (a["x"], a["y"]) == (x, y) for a in self.armies)
        ]
        if not free:
            raise ValueError("视野内地块都被野人驻军把守，扩张需先 atk 打赢（自动占领）")
        x, y = self.rng.choice(sorted(free))
        return x, y, self.activate(x, y)

    def get(self, x: int, y: int) -> dict | None:
        self._check_bounds(x, y)
        return self.tiles.get((x, y))

    def _check_bounds(self, x: int, y: int) -> None:
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise IndexError(
                f"坐标越界：({x + 1},{y + 1}) 超出地图范围（1..{self.size}），不能越界"
            )

    # ---- 存取档 ----
    def save(self, path: str | Path) -> None:
        data = {
            "size": self.size,
            "seed": self.seed,
            "turn": self.turn,
            "gold": self.gold,
            "wood": self.wood,
            "supply": self.supply,
            "stock": self.stock,
            "prices": self.prices,
            "armies": self.armies,
            "next_army_id": self.next_army_id,
            "cleared_tiles": [list(k) for k in sorted(self.cleared_tiles)],
            "rng_state": list(self.rng.getstate()),
            "tiles": {
                f"{x},{y}": tile for (x, y), tile in sorted(self.tiles.items())
            },
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "World":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        world = cls(size=data["size"], seed=data["seed"])
        world.turn = data.get("turn", 0)
        world.gold = data.get("gold", STARTING_GOLD)
        world.wood = data.get("wood", STARTING_WOOD)
        world.supply = data.get("supply", STARTING_SUPPLY)
        stock = data.get("stock", {})
        for g in GOODS:
            world.stock[g] = stock.get(g, 0)
        # 世界市场现价：旧档无 prices → 回落到各物基准价
        for g in TRADEABLE:
            world.prices[g] = float(data.get("prices", {}).get(g, MARKET[g]))
        world.armies = data.get("armies", [])
        for a in world.armies:
            a.setdefault("moved_turn", -1)
            a.setdefault("owner", "player")
            a.setdefault("engaged", False)
        world.next_army_id = data.get("next_army_id", len(world.armies) + 1)
        if "rng_state" in data:
            # [版本, 内部向量, gauss_next]：内部向量必须还原成 tuple
            ver, internal, gauss_next = data["rng_state"]
            world.rng.setstate((ver, tuple(internal), gauss_next))
        for k, tile in data["tiles"].items():
            x, y = map(int, k.split(","))
            tile.setdefault("buildings", {})
            for name in BUILDINGS:  # 旧档迁移：补全新增建筑（如市政厅）的键
                tile["buildings"].setdefault(name, 0)
            tile.setdefault("name", None)
            tile.setdefault("recruited_this_turn", 0)
            tile.setdefault("built_this_turn", 0)
            # 旧模型残留迁移：地块库存并入国家储备（含曾入仓的补给）
            old_stock = tile.pop("stock", {})
            for g, v in old_stock.items():
                if g == "补给":
                    world.supply += v
                elif g in world.stock:
                    world.stock[g] += v
            tile.pop("soldiers", None)
            tile.pop("id", None)  # 名字即ID，数字ID已废除
            tile.pop("paralyzed", None)  # 瘫痪改为全国电网 grid_short
            world.tiles[(x, y)] = tile
        world.cleared_tiles = {tuple(k) for k in data.get("cleared_tiles", [])}
        world.refresh_barbarians()  # 补驻野人（旧档/视野变化后，已清剿的不重生）
        return world


if __name__ == "__main__":
    # 简易自检：随机激活 5 块并打印
    w = World(size=10, seed=1)
    for _ in range(5):
        x, y, tile = w.activate_random()
        print(f"({x}, {y}) {tile['terrain']} {tile['resources']}")
