"""EU4-like 小游戏终端入口。

用法：
    python3 main.py            从 save.json 读档，无档则开新局
    python3 main.py --new      强制开新局（覆盖旧档）
    python3 main.py --seed 42  指定随机种子（可复现）
    python3 main.py --size 20  小网格测试
    python3 main.py --save x.json  指定存档路径
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from game import (
    BUILDINGS,
    CASTLE_DEFENSE_PER_LEVEL,
    GOODS,
    MARKET,
    MAX_SLOTS,
    PRICE_REVERT,
    RESOURCE_MAX,
    RESOURCES,
    SITE_BUILDING,
    TERRAIN_CHARS,
    TERRAIN_STATS,
    TERRAINS,
    TRADEABLE,
    World,
)

# 建筑中文/英文别名
BUILD_ALIAS = {
    "城堡": "城堡", "castle": "城堡",
    "林场": "林场", "lumber": "林场",
    "农场": "农场", "farm": "农场",
    "矿场": "矿场", "mine": "矿场",
    "黄金矿场": "黄金矿场", "goldmine": "黄金矿场",
    "石油厂": "石油厂", "oil": "石油厂",
    "木材能源厂": "木材能源厂", "woodplant": "木材能源厂", "woodenergy": "木材能源厂",
    "石油能源厂": "石油能源厂", "oilplant": "石油能源厂", "oilenergy": "石油能源厂",
    "补给厂": "补给厂", "supply": "补给厂",
    "装备厂": "装备厂", "equip": "装备厂", "armory": "装备厂",
    "兵营": "兵营", "barracks": "兵营",
}

# 可交易物资中英别名（buy/sell/market 用）
GOOD_ALIAS = {
    "粮食": "粮食", "粮": "粮食", "grain": "粮食", "food": "粮食",
    "木头": "木头", "木": "木头", "木材": "木头", "wood": "木头", "lumber": "木头",
    "矿石": "矿石", "矿": "矿石", "ore": "矿石",
    "石油": "石油", "油": "石油", "oil": "石油",
    "装备": "装备", "装": "装备", "equip": "装备", "weapon": "装备", "weapons": "装备",
    "补给": "补给", "补": "补给", "supply": "补给", "provision": "补给",
}

SAVE_PATH = Path(__file__).with_name("save.json")

# 终端颜色（管道/重定向时自动关闭）
USE_COLOR = sys.stdout.isatty()
ANSI = {"平原": "32", "森林": "92", "丘陵": "33", "山地": "37", "沙漠": "93"}
UNKNOWN_ANSI = "2"


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if USE_COLOR else s


HELP = f"""\
开局即占 5 地块：中心 + 上下左右（十字形，自动命名；没有首都概念）。
地块没有数字ID，名字就是ID（占领时自动命名，可用 name 改名，全局唯一）。
引用地块：地块名（如 b 林场 北川）或 x y 坐标（如 b 林场 5 6）。
视野 = 自己的地块 + 相邻一圈；只能占领视野内地块。
每个视野内未占领地块都驻一支野人军队（100HP，自给自足）——扩张要靠打赢自动占领。

命令：
  a [x y] / 激活        占领地块（无坐标=视野内随机扩张）。带野人的地块打赢后自动占领
  b 建筑 [地块] / 建    建造建筑（默认最近地块；每地块每回合限建 1 座）
  r [N] [地块] [步|骑] / 征兵  征 N 支军队（默认最近地块，每兵营 1 支/回合）
                            步=步兵:10粮+5装·动1格·补1/回合；骑=骑兵:12粮+12装·动2格·补2/回合（如 r 2 北川 骑 / r 3 骑 5 6）
  mv 军队id 地块 / 移动  调遣军队（每回合 1 次，只能相邻一格含对角线，不能越界）
  atk 军队id[,id...] 地块 冲入交战（含移动）；每回合掷骰结算一轮，打赢自动占领
  retreat 军队id 目标格   撤出：当回合挨野人一击（不还手），移到目标格脱离交战
  name 老名字 新名字     给地块改名（名字唯一、不可空）
  army / 军队           军队面板：名字/阵营/血量/位置（纯文本）
  land / 地皮           地皮管理面板：全部地皮+可扩张地块（纯文本）
  res / 资源            资源管理面板：全部资源（纯文本，喂 LLM 用）
  n / 回合              过一回合：采集/发电/工厂投料/军队补给与回血，并自动存档
  m / 地图              打印地图（亮=己方 暗=视野内未占领 ?=视野外）
  d [地块] / 详情       查看地块详情（无参=最近地块）
  s / 统计              占领统计
  market / mkt          世界市场面板：各物基准价/现价/持有（喂 LLM 用）
  buy 物品 数量         从世界市场购入（花国库黄金，如 buy 木头 10）
  sell 物品 数量        向世界市场售出（赚国库黄金，如 sell 矿石 5）
  gold [N] / wood [N]   查看/设置 国库/木材储备（调试用）
  demo N / 演示         从视野随机扩张 N 块
  w / seed / h / q      存档 / 种子 / 帮助 / 退出（自动存档）

经济是全国制：全部资源（粮食/矿石/石油/装备/木头/补给/黄金）全局存储，res 面板看总量。
  建筑仍建在具体地块（建造数受该地块资源量上限限制），但产出/消耗都走国家储备。
世界市场（无建筑门槛，随时可交易）：黄金是货币。可交易=全部可存储物资：
  粮食/木头/矿石/石油/装备 + 补给（买卖直接进出补给仓）。价格受简单供需影响：
  买=需求推高市价、卖=供给压低市价，整笔按成交后的清仓价结算（大单自己把市价推走）；
  每过一回合市价向基准价回归。基准价：粮2 木2 矿4 油6 装8 补5 金。能源不存储、不在市场。
  产金路：黄金矿场 +10金/回合/座；市政厅（高密度城，需本地已用位>5）每座 +5金/回合；卖物资也能换黄金（分批卖更划算）。
建筑（造价金·耗木·上限）：
  城堡 100/200/400/800/1600 ·10木/级  5级，每级 +10% 防御
  林场 50·5木  上限=木头 → +1木头
  农场 60·5木  上限=耕地 → +1粮食
  矿场 80·5木  上限=矿石 → +1矿石
  石油厂 150·8木 上限=石油 → +1石油
  黄金矿场 200·10木 上限=黄金 → +1黄金(直接入国库)
  木材能源厂 120·15木 上限=木头 → 耗1木 发2能源
  石油能源厂 300·15木 上限=石油 → 耗1石油 发5能源
  补给厂 200·12木 上限=耕地 → 粮1+矿1=补给×2（入补给仓；维持1能源）
  装备厂 240·12木 上限=石油 → 矿1+油1=装备×2（维持1能源）
  兵营 350·20木 不限地 维持1能源；每兵营每回合可征 1 支军队（耗 10 粮食+5 装备）
  市政厅 500·40木 每地块限1座·需本地已用位≥6 → 每座每回合 +5金（维持1能源）
电力全国电网且不存储：全国能源厂发电，供全部高级建筑维持；发电 < 维持则高级建筑停摆
军队：100HP；从兵营地集结。每支每回合从补给仓扣 1 补给（全国）；仓空则全国军队扣 10HP；
  非战斗且未断供每回合回 25HP；交战中不回血
战争按回合推进：atk 冲入交战 → 每过一回合（n）掷战斗骰 1d6（修正双方 ±25%）结算一轮，
  战争打几回合很正常；打赢自动占领（atk 含占领逻辑），遇险可 retreat 选格撤出（只挨打不还手）。
战斗：每军 50 基础伤害，按野人地块地形+城堡总防御%修正，总伤害分摊到各野人；
  野人防守反击全额（攻方站敌方地界吃不到加成）分摊到我军；我方先手
每地块共 {MAX_SLOTS} 个建筑位（城堡级数也占位）"""

ALIAS = {
    "help": ("h", "帮助"),
    "map": ("m", "地图"),
    "activate": ("a", "激活", "act"),
    "build": ("b", "建", "build"),
    "recruit": ("r", "征兵", "recruit"),
    "move": ("move", "mv", "移动", "调遣"),
    "attack": ("atk", "attack", "攻打", "攻击"),
    "retreat": ("retreat", "撤", "撤出", "撤退"),
    "name": ("name", "命名", "rename"),
    "army": ("army", "armies", "军队", "军"),
    "land": ("land", "地皮", "地块", "地盘"),
    "resources": ("res", "资源", "resource"),
    "turn": ("n", "回合", "next"),
    "detail": ("d", "详情", "info"),
    "stats": ("s", "统计"),
    "gold": ("gold", "金"),
    "wood": ("wood", "木"),
    "market": ("market", "mkt", "市场", "市"),
    "buy": ("buy", "买", "购", "购入"),
    "sell": ("sell", "卖", "售", "售出"),
    "demo": ("demo", "演示"),
    "save": ("w", "存档"),
    "quit": ("q", "退出", "exit"),
    "seed": (),
}

LEGEND = (
    "  地形：" + "  ".join(f"{_c(ANSI[t], TERRAIN_CHARS[t])}={t}" for t in TERRAINS)
    + f"   {_c('2', '暗色')}=视野内未占领   ?=视野外"
)


def parse_point(toks: list[str]) -> tuple[int, int]:
    """'12 34'（1-based）-> (11, 33)（0-based）。"""
    if len(toks) < 2:
        raise ValueError("需要坐标，如：激活 12 34（1-based）")
    try:
        x, y = int(toks[0]) - 1, int(toks[1]) - 1
    except ValueError:
        raise ValueError("坐标需为数字") from None
    return x, y


def parse_tile_ref(world: World, toks: list[str]) -> tuple[int, int]:
    """解析地块引用（0-based 坐标）。地块没有数字ID，名字就是ID：

    - 地块名     → 如 b 林场 北川（land 面板查看名字）
    - 两个数字   → x y 坐标（如 b 林场 5 6）
    """
    if not toks:
        raise ValueError("需要地块引用：地块名字 或 x y 坐标（land 面板查看名字）")
    if len(toks) >= 2 and toks[0].isdigit() and toks[1].isdigit():
        return parse_point(toks[:2])
    if len(toks) == 1:
        name = toks[0]
        if name.isdigit():
            raise ValueError("地块没有数字ID，名字就是ID（land 面板查看名字）")
        pos = world.tile_by_name(name)
        if pos is None:
            raise ValueError(f"地块「{name}」不存在（land 面板查看名字）")
        return pos
    raise ValueError("地块引用需为：单个名字 或 x y 坐标")


def render_map(world: World) -> str:
    size = world.size
    frontier = world.frontier() if world.tiles else set()
    lines = [
        "    " + "".join(str((i // 10) % 10) if i % 10 == 0 else " " for i in range(size))
    ]
    for y in range(size):
        row = []
        for x in range(size):
            tile = world.get(x, y)
            if tile is not None:
                row.append(_c(ANSI[tile["terrain"]], TERRAIN_CHARS[tile["terrain"]]))
            elif (x, y) in frontier:
                # 视野内未占领：地形可见（暗色）
                t = world.tile_terrain(x, y)
                row.append(_c("2", TERRAIN_CHARS[t]))
            else:
                row.append(_c(UNKNOWN_ANSI, "?"))
        lines.append(f"{y:>3} " + "".join(row))
    return "\n".join(lines)


def _signed(v: int) -> str:
    return f"{v:+d}%"


def armies_at(world: World, x: int, y: int) -> list[dict]:
    return [a for a in world.armies if a["x"] == x and a["y"] == y]


def render_tile(world: World, x: int, y: int, tile: dict) -> str:
    terrain = tile["terrain"]
    res = tile["resources"]
    stats = TERRAIN_STATS[terrain]
    b = tile["buildings"]
    castle = b["城堡"]
    castle_def = castle * CASTLE_DEFENSE_PER_LEVEL
    name = tile.get("name") or "无名"
    title = f"地块 {name} ({x + 1}, {y + 1})"
    lines = [f"{title} —— {_c(ANSI[terrain], terrain)}"]
    lines.append(f"  地形防御 {_signed(stats['defense'])}   建设惩罚 {_signed(stats['build_penalty'])}")
    can_build = "可建" if tile.get("built_this_turn", 0) == 0 else "本回合已建"
    lines.append(
        f"  城堡 L{castle}（+{castle_def}%，合计 {_signed(stats['defense'] + castle_def)}）"
        f"   建筑位 {sum(b.values())}/{MAX_SLOTS}   本回合：{can_build}"
    )
    built = [f"{name}×{n}" for name, n in b.items() if n]
    if built:
        lines.append("  建筑：" + "  ".join(built))
    res_parts = []
    for name in RESOURCES:
        v = res[name]
        bl = SITE_BUILDING[name]
        bar = "█" * v + "·" * (RESOURCE_MAX[name] - v)
        res_parts.append(f"{name}{bar}x{v}({bl}{b[bl]}/{v})")
    lines.append("  资源上限：" + "  ".join(res_parts))
    if b["兵营"]:
        cap_avail = b["兵营"] - tile["recruited_this_turn"]
        cost = BUILDINGS["兵营"]["army_cost"]
        cost_desc = "+".join(f"{f}x{amt}" for f, amt in cost.items())
        lines.append(
            f"  兵营：征召产能 {b['兵营']}/回合（本回合余 {cap_avail}，每支耗 {cost_desc}，r 征兵）"
        )
    local = armies_at(world, x, y)
    if local:
        troop = "  ".join(f"{a['name']}({a['hp']}hp)" for a in local)
        lines.append(f"  驻军：{troop}（吃全局补给仓）")
    return "\n".join(lines)


def render_stats(world: World) -> str:
    n = len(world.tiles)
    total = world.size * world.size
    lines = [
        f"已占领 {n}/{total}（{100 * n // total}%）"
        f"  国库 {world.gold} 金  木材 {world.wood}  补给仓 {world.supply}"
        f"  军队 {len(world.armies)} 支  第 {world.turn} 回合"
    ]
    if not world.tiles:
        return "\n".join(lines)
    terrain_counts = Counter(tile["terrain"] for tile in world.tiles.values())
    lines.append(
        "  地形分布：" + "  ".join(f"{t}={terrain_counts[t]}" for t in TERRAINS if terrain_counts[t])
    )
    sums = {
        r: sum(tile["resources"][r] for tile in world.tiles.values()) for r in RESOURCES
    }
    lines.append("  资源总量：" + "  ".join(f"{r}={sums[r]}" for r in RESOURCES))
    bsum = Counter()
    for tile in world.tiles.values():
        for name, cnt in tile["buildings"].items():
            bsum[name] += cnt
    if any(bsum.values()):
        lines.append("  建筑：" + "  ".join(f"{name}×{bsum[name]}" for name in BUILDINGS if bsum[name]))
    stock = " ".join(f"{g}={world.stock[g]}" for g in GOODS) or "无"
    lines.append(f"  国家储备：{stock}")
    if world.grid_short:
        lines.append("  ⚠ 全国电网不足，高级建筑停摆中")
    return "\n".join(lines)


def render_armies(world: World) -> str:
    """军队面板：名字（默认自动命名 军N）、血量、位置。纯文本（喂 LLM 友好）。"""
    if not world.armies:
        return "# 军队面板\n（无军队，建兵营后用 r 征兵）"
    lines = [f"# 军队面板（共 {len(world.armies)} 支）"]
    for a in sorted(world.armies, key=lambda a: a["id"]):
        tile = world.get(a["x"], a["y"])
        where = tile.get("name") if tile and tile.get("name") else "野外"
        owner = "野人" if a.get("owner") == "野人" else "我方"
        moved = "已移动" if a.get("moved_turn") == world.turn else "可移动"
        status = "交战" if a.get("engaged") else moved
        lines.append(
            f"  {a['name']}({a['id']}) | {owner} | {a['hp']}HP | 位置 ({a['x'] + 1},{a['y'] + 1}) {where} | {status}"
        )
    return "\n".join(lines)


def render_land(world: World) -> str:
    """地皮管理面板：全部地皮信息，纯文本（喂 LLM 友好）。"""
    lines = ["# 地皮管理面板", f"共 {len(world.tiles)} 块已占领 / {world.size * world.size}"]
    for (x, y), tile in sorted(world.tiles.items()):
        b = tile["buildings"]
        stats = TERRAIN_STATS[tile["terrain"]]
        name = tile.get("name") or "无名"
        castle = b["城堡"]
        def_total = stats["defense"] + castle * CASTLE_DEFENSE_PER_LEVEL
        built = " ".join(f"{k}x{v}" for k, v in b.items() if v) or "无"
        res = " ".join(f"{r}x{tile['resources'][r]}" for r in RESOURCES)
        local = armies_at(world, x, y)
        troops = " ".join(f"军{a['id']}({a['hp']}hp)" for a in local) or "无"
        lines.append(
            f"{name} ({x + 1},{y + 1}) | {tile['terrain']}"
            f" | 地形防御{stats['defense']:+d}% 建设惩罚{stats['build_penalty']:+d}%"
            f" | 城堡L{castle} 总防御{def_total:+d}%"
            f" | 建筑位{sum(b.values())}/{MAX_SLOTS}"
        )
        lines.append(f"  资源上限: {res}")
        lines.append(f"  建筑: {built}")
        lines.append(f"  驻军: {troops}")
    fr = world.frontier()
    lines.append(f"可扩张地块（视野内，未占领，共 {len(fr)}）:")
    if fr:
        parts = []
        for x, y in sorted(fr):
            bar = [a for a in world.armies if a.get("owner") == "野人" and a["x"] == x and a["y"] == y]
            tag = f"[野人{bar[0]['id']}]" if bar else ""
            parts.append(f"({x + 1},{y + 1}) {world.tile_terrain(x, y)}{tag}")
        for i in range(0, len(parts), 8):
            lines.append("  " + "  ".join(parts[i : i + 8]))
    else:
        lines.append("  （无）")
    return "\n".join(lines)


def render_resources(world: World) -> str:
    """资源管理面板：国家全部资源，纯文本（喂 LLM 友好）。"""
    lines = [
        "# 资源管理面板（全部全局存储）",
        f"回合={world.turn} 国库={world.gold}金 木材={world.wood}"
        f" 补给仓={world.supply} 电网={'不足⚠' if world.grid_short else '正常'}"
        f" 军队数={len(world.armies)}",
    ]
    stk = " ".join(f"{g}={world.stock[g]}" for g in GOODS)
    lines.append(f"国家储备: {stk}")
    lines.append("军队:")
    if world.armies:
        for a in sorted(world.armies, key=lambda a: a["id"]):
            tile = world.get(a["x"], a["y"])
            where = tile.get("name") if tile and tile.get("name") else "野外"
            owner = "野人" if a.get("owner") == "野人" else "我方"
            lines.append(
                f"  军队{a['id']} {a['name']} {owner} {a['hp']}HP @({a['x'] + 1},{a['y'] + 1}) {where}"
            )
    else:
        lines.append("  （无军队）")
    lines.append("世界市场现价: " + " ".join(f"{g}={world.market_price(g)}" for g in TRADEABLE) + " （market 看详细/交易）")
    return "\n".join(lines)


def render_market(world: World) -> str:
    """世界市场面板：各物基准价/现价/相对基准/你的持有。纯文本（喂 LLM 用）。"""
    revert_pct = round((1 - PRICE_REVERT) * 100)
    lines = [
        "# 世界市场（黄金是货币，用 buy/sell 换物资 ↔ 黄金；无需建筑，随时可交易）",
        f"规则：买=需求→推高市价，卖=供给→压低市价；整笔按成交后的清仓价结算，"
        f"大单会自己把市价推走（越急越吃亏）。每过一回合市价向基准价回归 {revert_pct}%。",
        "  物品   基准   现价   相对基准   持有量",
    ]
    for g in TRADEABLE:
        base = MARKET[g]
        p = world.prices[g]
        rel = (p - base) / base
        if abs(rel) <= 0.02:
            tag = "≈基准"
        elif rel > 0:
            tag = f"贵 {rel * 100:+.0f}%"
        else:
            tag = f"贱 {rel * 100:+.0f}%"
        hold = world.holding(g)
        lines.append(f"  {g:<4}  {base:<4}  {world.market_price(g):<5}  {tag:<7}  {hold}")
    return "\n".join(lines)


def parse_trade(world: World, toks: list[str]) -> tuple[str, int]:
    """解析 buy/sell 参数：物品 [别名] + 数量。返回 (规范物名, 数量)。"""
    if len(toks) < 2:
        raise ValueError("用法：buy/sell 物品 数量（如 buy 木头 10 / sell 矿石 5）")
    good = GOOD_ALIAS.get(toks[0].lower())
    if good is None:
        raise ValueError(
            f"未知物品：{toks[0]}（可交易：{'/'.join(TRADEABLE)}，如 buy 粮食 10）"
        )
    try:
        n = int(toks[1])
    except ValueError:
        raise ValueError(f"数量需为整数：{toks[1]}") from None
    if n <= 0:
        raise ValueError("数量需为正整数")
    return good, n


def loop(world: World, save_path: Path) -> None:
    last: tuple[int, int] | None = None
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            world.save(save_path)
            print("已存档，再见 👋")
            return
        if not raw:
            continue
        toks = raw.split()
        cmd, rest = toks[0].lower(), toks[1:]
        try:
            if cmd in ALIAS["quit"]:
                world.save(save_path)
                print("已存档，再见 👋")
                return
            elif cmd in ALIAS["activate"]:
                if rest:
                    x, y = parse_tile_ref(world, rest)
                    was_known = world.get(x, y) is not None
                    tile = world.activate(x, y)
                else:
                    x, y, tile = world.activate_random()
                    was_known = False
                last = (x, y)
                if was_known:
                    print(f"{tile['name']} ({x + 1}, {y + 1}) 已占领过，当前状态：")
                else:
                    world.save(save_path)
                    print(f"已占领 {tile['name']} ({x + 1}, {y + 1})，自动存档 ✓")
                print(render_tile(world, x, y, tile))
            elif cmd in ALIAS["build"]:
                if not rest:
                    raise ValueError(f"需要建筑名：{'/'.join(BUILDINGS)}（如 b 林场 北川 / b 林场 5 6）")
                bname = BUILD_ALIAS.get(rest[0].lower())
                if bname is None:
                    raise ValueError(f"未知建筑：{rest[0]}（可选：{'/'.join(BUILDINGS)}）")
                ref = rest[1:]
                if ref:
                    x, y = parse_tile_ref(world, ref)
                elif last is not None:
                    x, y = last
                else:
                    raise ValueError("请先占领一块地，或给出地块名字/坐标：b 林场 北川 / b 林场 5 6")
                ok, msg = world.build(x, y, bname)
                print(msg)
                if ok:
                    world.save(save_path)
                    print(render_tile(world, x, y, world.get(x, y)))
            elif cmd in ALIAS["recruit"]:
                n = 1
                kind = "步"
                toks_rest = rest
                if rest and rest[0].isdigit():
                    n = int(rest[0])
                    toks_rest = rest[1:]
                for tok in list(toks_rest):
                    if tok in ("骑", "步"):
                        kind = tok
                        toks_rest.remove(tok)
                        break
                if toks_rest:
                    x, y = parse_tile_ref(world, toks_rest)
                elif last is not None:
                    x, y = last
                else:
                    raise ValueError("请先占领一块地，或给出地块名字/坐标：r 3 北川 / r 骑 2 5 6")
                ok, msg = world.recruit(x, y, n, kind)
                print(msg)
                if ok:
                    world.save(save_path)
                    print(render_tile(world, x, y, world.get(x, y)))
            elif cmd in ALIAS["attack"]:
                if len(rest) < 2:
                    raise ValueError("用法：atk 军队id[,军队id...] 地块（如 atk 1 北川 / atk 1,2 5 6）")
                try:
                    ids = [int(t) for t in rest[0].split(",") if t]
                except ValueError:
                    raise ValueError("军队 id 需为数字，多个用逗号分隔（如 atk 1,2 5 6）") from None
                x, y = parse_tile_ref(world, rest[1:])
                ok, msg = world.attack(ids, x, y)
                print(msg)
                if ok:
                    world.save(save_path)
            elif cmd in ALIAS["retreat"]:
                if len(rest) < 2:
                    raise ValueError("用法：retreat 军队id 目标格（如 retreat 1 5 6 / retreat 1 北川）")
                try:
                    aid = int(rest[0])
                except ValueError:
                    raise ValueError("军队 id 需为数字（如 retreat 1 5 6）") from None
                x, y = parse_tile_ref(world, rest[1:])
                ok, msg = world.retreat(aid, x, y)
                print(msg)
                if ok:
                    world.save(save_path)
            elif cmd in ALIAS["move"]:
                if len(rest) < 2:
                    raise ValueError("用法：mv 军队id 目标（如 mv 1 5 6 / mv 1 北川），army 查看军队")
                try:
                    aid = int(rest[0])
                except ValueError:
                    raise ValueError("军队 id 需为数字（如 mv 1 5 6）") from None
                x, y = parse_tile_ref(world, rest[1:])
                ok, msg = world.move_army(aid, x, y)
                print(msg)
                if ok:
                    world.save(save_path)
            elif cmd in ALIAS["name"]:
                if len(rest) < 2 or rest[0].isdigit():
                    raise ValueError("用法：name 现有地块名 新名字（如 name 北川 首都；名字唯一且不可空）")
                x, y = parse_tile_ref(world, rest[:1])
                new_name = rest[1].strip()
                ok, msg = world.name_tile(x, y, new_name)
                print(msg)
                if ok:
                    world.save(save_path)
            elif cmd in ALIAS["army"]:
                print(render_armies(world))
            elif cmd in ALIAS["land"]:
                print(render_land(world))
            elif cmd in ALIAS["resources"]:
                print(render_resources(world))
            elif cmd in ALIAS["turn"]:
                r = world.advance_turn()
                print(f"第 {r['turn']} 回合")
                if r["supply_in"]:
                    print(f"  补给仓 +{r['supply_in']}（仓内 {r['supply']}）")
                if r["wood_in"]:
                    print(f"  木材 +{r['wood_in']}（储备 {r['wood']}）")
                prod_parts = [f"{g}+{v}" for g, v in r["produced"].items() if v]
                if prod_parts:
                    print("  产出：" + "  ".join(prod_parts))
                if r["energy_total"] or r["maintenance_total"]:
                    fuel = r["wood_fuel"] + r["oil_fuel"]
                    print(
                        f"  电网：发电 {r['energy_total']} vs 维持 {r['maintenance_total']}"
                        f"（燃料 木{r['wood_fuel']}+油{r['oil_fuel']}）"
                    )
                if r["grid_short"]:
                    print(f"  ⚠ 全国电网不足，高级建筑全部停摆（需加能源厂）")
                if r["income"]:
                    print(f"  黄金矿收入 +{r['income']}，国库 {world.gold}")
                if r.get("hall_gold"):
                    print(f"  市政厅收入 +{r['hall_gold']}（每座 +5金；电网不足则停摆），国库 {world.gold}")
                if r["famine"]:
                    shortage, dead = r["famine"]
                    print(f"  ⚠ 补给仓断粮（缺 {shortage}）：全国军队扣血，{dead} 支饿毙")
                for line in r["wars"]:
                    print(" " + line)
                if r["total_armies"]:
                    print(f"  总兵力：{r['total_armies']} 支军队")
                world.save(save_path)
            elif cmd in ALIAS["gold"]:
                if rest:
                    try:
                        world.gold = int(rest[0])
                    except ValueError:
                        raise ValueError("金币数需为整数") from None
                    world.save(save_path)
                    print(f"国库已设为 {world.gold} 金")
                else:
                    print(f"国库：{world.gold} 金")
            elif cmd in ALIAS["wood"]:
                if rest:
                    try:
                        world.wood = int(rest[0])
                    except ValueError:
                        raise ValueError("木材数需为整数") from None
                    world.save(save_path)
                    print(f"木材储备已设为 {world.wood}")
                else:
                    print(f"木材储备：{world.wood}")
            elif cmd in ALIAS["market"]:
                print(render_market(world))
            elif cmd in ALIAS["buy"]:
                good, n = parse_trade(world, rest)
                ok, msg = world.buy(good, n)
                print(msg)
                if ok:
                    world.save(save_path)
            elif cmd in ALIAS["sell"]:
                good, n = parse_trade(world, rest)
                ok, msg = world.sell(good, n)
                print(msg)
                if ok:
                    world.save(save_path)
            elif cmd in ALIAS["map"]:
                print(render_map(world))
                print(LEGEND)
            elif cmd in ALIAS["detail"]:
                if rest:
                    x, y = parse_point(rest)
                elif last is not None:
                    x, y = last
                else:
                    raise ValueError("还没有激活过地块，先 a [x y]")
                tile = world.get(x, y)
                if tile is None:
                    print(f"({x + 1}, {y + 1}) 尚未激活")
                else:
                    print(render_tile(world, x, y, tile))
            elif cmd in ALIAS["stats"]:
                print(render_stats(world))
            elif cmd in ALIAS["demo"]:
                n = int(rest[0]) if rest else 5
                activated = 0
                for _ in range(n):
                    try:
                        x, y, tile = world.activate_random()
                    except ValueError:
                        break
                    last = (x, y)
                    activated += 1
                world.save(save_path)
                print(f"演示：从视野随机扩张 {activated} 块，共 {len(world.tiles)} 已占领，自动存档 ✓")
            elif cmd in ALIAS["save"]:
                world.save(save_path)
                print(f"已存档：{save_path}")
            elif cmd in ALIAS["seed"]:
                print(f"随机种子：{world.seed}")
            elif cmd in ALIAS["help"]:
                print(HELP)
            else:
                print(f"未知命令：{cmd}（输入 h 看帮助）")
        except (ValueError, IndexError) as e:
            print(f"⚠ {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="EU4-like 地块小游戏")
    ap.add_argument("--new", action="store_true", help="强制开新局（覆盖旧档）")
    ap.add_argument("--seed", type=int, default=None, help="指定随机种子")
    ap.add_argument("--size", type=int, default=100, help="网格大小（默认 100）")
    ap.add_argument("--save", default=str(SAVE_PATH), help="存档路径")
    args = ap.parse_args()

    save_path = Path(args.save)
    if not args.new and save_path.exists():
        world = World.load(save_path)
        print(f"读档成功：{save_path}（种子 {world.seed}，已占领 {len(world.tiles)} 地块）")
    else:
        world = World(size=args.size, seed=args.seed, new_start=True)
        world.save(save_path)
        names = "、".join(t["name"] for (x, y), t in sorted(world.tiles.items()))
        print(
            f"新开一局 {world.size}x{world.size}（种子 {world.seed}）："
            f"开局 5 地块（中心+上下左右）——{names}"
        )
    print(HELP)
    loop(world, save_path)


if __name__ == "__main__":
    main()
