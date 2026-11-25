"""终局国力分（plan.md §2.2）：固定参考价净资产 + 领土价值；已亡国为 0。

固定价 = 开局价，不随市场漂移，所以抬价/砸盘/囤货都刷不出分（实测 `building_assets` 按市价会被木价扭曲 4 倍）。
"""
from __future__ import annotations

from balance import BUILDINGS, MARKET, TRADEABLE, UNIT_TYPES

TILE_VALUE = 50.0          # 每块地的固定价值 ≈ 一座最便宜的采集建筑的重置价（20 金 + 15 木 × 2）
REF = {g: float(MARKET[g]) for g in TRADEABLE}          # 粮木矿油装补 的参考价
REF_WOOD = REF["木头"]


def building_value(name: str, count: int = 1) -> float:
    """基础造价（金 + 木×参考价，不含地形加价与折扣）；城堡按已升到的级数累计。"""
    info = BUILDINGS[name]
    cost = info["cost"]
    gold = sum(cost[:count]) if isinstance(cost, list) else cost * count
    return gold + info.get("wood", 0) * count * REF_WOOD


def army_value(a: dict) -> float:
    """军队按征召成本×剩余血量比例折算（买兵不亏不赚，打残了才亏）。"""
    spec = UNIT_TYPES[a.get("type", "步")]
    cost = spec["recruit"]
    base = sum((amt if g == "黄金" else amt * REF.get(g, 0.0)) for g, amt in cost.items())
    return base * max(0.0, a["hp"]) / spec["hp"]


def power_score(w, nation: str) -> float:
    if nation not in w.nations:
        return 0.0
    n = w.nations[nation]
    v = float(n.res.get("黄金", 0))
    for g in TRADEABLE:
        v += n.res.get(g, 0) * REF[g]
    for (x, y), t in w.tiles.items():
        if t["owner"] != nation:
            continue
        v += TILE_VALUE
        for b, c in t["buildings"].items():
            if c:
                v += building_value(b, c)
    for a in w.nation_armies(nation):
        v += army_value(a)
    loan = w.bank.get("loans", {}).get(nation)
    if loan:
        v -= float(loan.get("principal", 0))
    return v
