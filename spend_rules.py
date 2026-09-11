# -*- coding: utf-8 -*-
"""三条判据（用户 2026-09-11 定死）。**先写函数，不接逻辑、不实测。**

    第一条  rigid_expenditure()  —— **本回合**的刚性支出是多少，必须算出来；
                                  然后必须能提供**刚好这个回合**的支出：
                                  **缺一个买一个，不买多，也不买少。**
    第二条  gate_ok()            —— 刚性支出 / 总收入 **≤ 60%**（每回合）
    第三条  supply_ok()          —— 军队**必须满补给**，这是每个回合**前**的判断

三者关系：
    ③ 是回合前的硬门槛 —— 兵没饭吃就谈不上"能出兵"
    ② 是养不养得起的闸门 —— 占比超 60% 就该砍军队或砍建筑
    ① 是执行 —— 按算出来的数**精确采购**：多买 = 囤积（钱不周转），
      少买 = 断粮（部队掉血），两种都错。

口径（用户原话）：
  * "维护费只指买的费用，而自己造则是 0，**也就是按缺口计算**"
        → 缺口 = 本回合需要 − 自产 − 库存（库存也算已经有的，不该重复买）
  * 收入**含自产货物按市价折价**（不含的话占比被高估，60% 闸门形同虚设）
  * 是**本回合**的量，不是缓冲区 —— 不预囤 N 回合
"""
from __future__ import annotations

from game import BUILDINGS, MARKET, building_effect

SUPPLY_PER_ARMY = {"步": 1, "骑": 2}      # 每支军队每回合吃掉的补给
GATE = 0.60                               # 第二条：刚性支出占收入的上限


def _price(good: str) -> float:
    """当回合市价。评估用中间价；买卖另有 10% 价差，不计入评估。"""
    return float(MARKET.get(good, 0))


def _cnt(w, name: str, bn: str) -> int:
    return sum(t["buildings"][bn] for t in w.tiles.values() if t["owner"] == name)


def _made(w, name: str, good: str) -> int:
    """该国本回合**自产**的 good 数量（所有建筑 outputs 之和）。"""
    total = 0
    for t in w.tiles.values():
        if t["owner"] != name:
            continue
        for bn, cnt in t["buildings"].items():
            if not cnt:
                continue
            amt = (BUILDINGS.get(bn) or {}).get("outputs", {}).get(good)
            if amt:
                total += amt * cnt
    return total


def army_upkeep_units(w, name: str) -> int:
    """军队本回合要吃的补给**单位数**（步 1 / 骑 2）。"""
    return sum(SUPPLY_PER_ARMY.get(a.get("kind", "步"), 1)
               for a in w.nation_armies(name))


# --------------------------------------------------------------------------
# 第三条：军队必须满补给（每个回合**前**的判断）
# --------------------------------------------------------------------------

def supply_ok(w, name: str) -> tuple[bool, int]:
    """回合前判断：补给仓够不够军队本回合吃。

    返回 (是否满补给, 缺口单位数)。缺口就是**必须买**的量 —— 缺一个买一个。
    """
    need = army_upkeep_units(w, name)
    have = int(w.res(name, "补给"))
    return have >= need, max(0, need - have)


# --------------------------------------------------------------------------
# 第一条：本回合的刚性支出（算出数来，然后精确采购）
# --------------------------------------------------------------------------

def rigid_expenditure(w, name: str, *, recruit: int = 0) -> dict:
    """**本回合**的刚性支出（金）——只算必须付的，且只算必须去市场买的缺口。

    固定三项（用户口径）：① 军队口粮 ② 补给厂投料 ③ 能源厂燃料
    另加：④ 征兵原料 —— **只有本回合真要征 `recruit` 支时**才计入那几支的量

    每一项：`缺口 = 本回合需要 − 自产 − 已有库存`，**负数取 0**。
    自产与库存都抵掉，剩下的才是必须掏钱的。

    返回：
        {
          "items": {项目: (缺口单位数, 折金)},   # 缺一个买一个：就买这么多
          "total": 本回合要付的金,
        }
    """
    items: dict[str, tuple[int, float]] = {}

    def add(good: str, units: int) -> None:
        if units <= 0:
            return
        n, g = items.get(good, (0, 0.0))
        items[good] = (n + units, g + units * _price(good))

    # ① 军队口粮
    add("补给", army_upkeep_units(w, name) - _made(w, name, "补给")
        - int(w.res(name, "补给")))

    # ② 工厂投料：**补给厂 + 装备厂都要算**（先前只算补给厂 —— 漏了装备厂的
    #    1 矿 + 1 石油/回合，账单少算 → 比例偏低 → 闸门假通过）。
    for factory in ("补给厂", "装备厂"):
        n_f = _cnt(w, name, factory)
        if not n_f:
            continue
        for good, per in (BUILDINGS[factory].get("inputs") or {}).items():
            add(good, n_f * per - _made(w, name, good) - int(w.res(name, good)))

    # ③ 能源厂燃料：每座 1 木
    n_plant = _cnt(w, name, "木材能源厂")
    if n_plant:
        add("木头", n_plant - _made(w, name, "木头") - int(w.res(name, "木头")))

    # ④ 征兵原料（本回合真要征才计）——**同样要扣自产**：
    #    装备市价 8 金/个，比早期收入还贵；不扣自产的话"自己造的装备"仍按市价算，
    #    于是一支兵的账单恒 = 粮10×2 + 装5×8 ≈ 60 金 ≫ 60%×收入 → **闸门永远不过**，
    #    永远征不出第 3 支兵（实测收入 22、账单 40、占比 182%）。
    #    自己造的记 0 之后，"建一座装备厂"就成了过关的唯一路径 —— 这正是它该有的作用。
    if recruit > 0:
        for good, per in BUILDINGS["兵营"]["army_cost"].items():
            add(good, per * recruit - _made(w, name, good) - int(w.res(name, good)))

    # 「该留多少」= 本回合需要 − 自产（**不是固定缓冲**）：
    #   清仓时留这么多，多出来的全卖成现金 —— 用户口径"不买多，也不买少"。
    keep: dict[str, int] = {}

    def want(good: str, units: int) -> None:
        if units > 0:
            keep[good] = keep.get(good, 0) + units

    want("补给", army_upkeep_units(w, name) - _made(w, name, "补给"))
    for factory in ("补给厂", "装备厂"):
        n_f = _cnt(w, name, factory)
        if not n_f:
            continue
        for good, per in (BUILDINGS[factory].get("inputs") or {}).items():
            want(good, n_f * per - _made(w, name, good))
    n_plant = _cnt(w, name, "木材能源厂")
    if n_plant:
        want("木头", n_plant - _made(w, name, "木头"))
    if recruit > 0:
        for good, per in BUILDINGS["兵营"]["army_cost"].items():
            want(good, per * recruit - _made(w, name, good))

    return {"items": items, "total": sum(g for _n, g in items.values()), "keep": keep}


def rigid_gold(w, name: str, *, recruit: int = 0) -> float:
    """只要金额时用它。"""
    return rigid_expenditure(w, name, recruit=recruit)["total"]


# --------------------------------------------------------------------------
# 总收入（第二条的分母）
# --------------------------------------------------------------------------

def income_of(w, name: str) -> float:
    """本回合**真正产得出来**的收入（金等价）—— **不是名义产出**。

    ★ 这是三条判据里最容易算错的一格，而且错法是**往宽里错**：
      先前按"建了就算产出"计，于是空转的工厂（没电 / 没原料）也被算成了收入
      → 分母虚高 → 刚性支出占比被压低 → **60% 闸门假通过**，
      账面"养得起"、实际没钱可用（v7 卡在 2 支兵、70 格地开发不动就是这个）。

    只计入：
      · **采集类**（农场/矿场/林场/石油厂/黄金矿场/军屯）：无投入，有资源就产
      · **工厂类**（补给厂/装备厂）：**要电、要投入** —— 缺电或缺原料即为 0
      · **市政厅**：产金
    """
    er = (w.energy_report or {}).get(name)
    power_ok = True if er is None else bool(er[-1])      # (产, 耗, 是否够)

    total = 0.0
    for p in w.own_tiles(name):
        t = w.tiles[p]
        for bn, cnt in t["buildings"].items():
            if not cnt:
                continue
            info = BUILDINGS.get(bn)
            if info is None:
                continue
            kind = info.get("kind")
            if kind == "factory":
                if not power_ok:
                    continue                             # 缺电 → 厂子停摆，产出为 0
                need = info.get("inputs") or {}
                if any(int(w.res(name, g)) < amt * cnt for g, amt in need.items()):
                    continue                             # 缺原料 → 这个回合不产出
            for good, amt in (info.get("outputs") or {}).items():
                total += _price(good) * amt * cnt
            if kind == "townhall":
                total += (building_effect("市政厅", "gold_base")
                          + building_effect("市政厅", "gold_per_slot")
                          * sum(t["buildings"].values())) * cnt
    return total


# --------------------------------------------------------------------------
# 第二条：刚性支出 / 总收入 ≤ 60%
# --------------------------------------------------------------------------

def gate_ok(w, name: str, *, recruit: int = 0) -> tuple[bool, float]:
    """第二条：`刚性支出 / 总收入 ≤ 60%`（每回合）。

    返回 (是否通过, 占比)。收入为 0 → 记 1.0（等于养不起）。
    """
    inc = income_of(w, name)
    if inc <= 0.5:
        return False, 1.0
    share = rigid_gold(w, name, recruit=recruit) / inc
    return share <= GATE, share


# --------------------------------------------------------------------------
# 第一条的执行：按账单**缺一个买一个**
# --------------------------------------------------------------------------

def buy_exact(bill: dict, buy) -> None:
    """照着 rigid_expenditure 的账单一笔一笔买 —— **不买多，也不买少**。

    `bill["items"]` 里的数量**就是本回合的缺口**（已扣过自产与库存），直接买这么多。
    多买 = 囤积（钱不周转），少买 = 断粮（部队掉血）。
    `buy(good, qty)` 由调用方提供（它自带日志与回调）。
    """
    for good, (gap, _gold) in bill["items"].items():
        if gap > 0:
            buy(good, gap)


# --------------------------------------------------------------------------
# 自由现金流（用户 2026-09-11 的口径：**别按必须支出算，按每回合积累的自由现金流算**）
# --------------------------------------------------------------------------

def free_cash_flow(w, name: str, *, recruit: int = 0) -> dict:
    """每回合的**自由现金流** = 可动用收入 − 刚性支出。

    用户的账：**刚性 ≤ 30% → 自由 ≥ 70%**，那 70% 就是能拿去复利（建造）的钱。
    所以"钱去哪了"要看**这个数**，不是看刚性支出占多少。

    "可动用收入"只算**能变成现金的**：金矿/市政厅产的现金 + 产出按**卖价**折金
    （产出得卖掉才是钱；库存不算 —— 囤着不是自由现金流）。

    返回 {可动用收入, 刚性支出, 自由现金流, 自由占比}。
    """
    cash_income = 0.0
    for p in w.own_tiles(name):
        t = w.tiles[p]
        for bn, cnt in t["buildings"].items():
            if not cnt:
                continue
            info = BUILDINGS.get(bn)
            if info is None:
                continue
            kind = info.get("kind")
            if kind == "townhall":
                cash_income += (building_effect("市政厅", "gold_base")
                                + building_effect("市政厅", "gold_per_slot")
                                * sum(t["buildings"].values())) * cnt
            for good, amt in (info.get("outputs") or {}).items():
                if good == "黄金":
                    cash_income += amt * cnt * MARKET["黄金"]      # 金矿出的就是现金
                else:
                    cash_income += good_value_sell(w, good, amt * cnt)
    rigid = rigid_gold(w, name, recruit=recruit)
    free = cash_income - rigid
    return {"可动用收入": cash_income, "刚性支出": rigid, "自由现金流": free,
            "自由占比": (free / cash_income) if cash_income > 0.5 else float("nan")}


def good_value_sell(w, good: str, amt: int) -> float:
    """按**卖价**折金（自由现金流口径：产出要卖掉才是钱）。"""
    if amt <= 0:
        return 0.0
    if good == "黄金":
        return amt * MARKET["黄金"]
    return amt * w.market_quote(good, 1, "sell")[0]
