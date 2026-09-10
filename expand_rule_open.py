# -*- coding: utf-8 -*-
"""开局老师：只教**前 50 回合**，里程碑是——「2 支步兵，且补给不断」。

## 这份老师按一张**每回合收支账**做决策

    income  = 黄金矿场×10 + 市政厅(5+每建筑位1) + Σ(采集建筑产出 × 当回合市价)
    upkeep  = 口粮缺口 + 电力木耗 + ……            ← **只算"要花钱买的"，自产的记 0**

    upkeep / income > 60%  → 不建军，继续复利（否则被军费拖死）
    upkeep / income ≤ 60%  → 攒钱修兵营

（用户 2026-09-11 定的分档：**60% 是可以接受的线**，70% 勉强，**超过 80% 就没救了**。
 还有第二条策略——开荒（金矿抽奖）之后**饿死军队**再复利建设；本文件不走那步，
 **只完成军队建设**。）

## 三条从真实对局日志里量出来的事实（本文件存在的理由）

拆 `mp_journal.md` 两局 LLM 对局的前 50 回合：

| | 第 1 支兵 | 第 2 支兵 |
|---|---|---|
| LLM 五国（40×40） | **第 6 回合** | 第 7 回合 |
| 规则 AI v6 | 第 40~60 回合 | 第 50+ 回合 |

1. **五格并行建**：引擎限制是「每**地块**每回合 1 座」，不是每国 1 座。LLM 第 1 回合
   就在 5 块地上各落一座。
2. **不等"养得起"再征兵**：v6 的 `supply_cap()` 要补给产能跟上才肯征，于是前 40 回合
   一支不出。LLM 先出兵占地，补给后面补。
3. **兵营专款**（v6 的 `barr_fund`）：第一座兵营落地前，别的消费不许碰那笔钱。

## 前几版的反面教材（别再犯）

* 把装备卖到只剩 2 再买回 20 → 每回合白烧一遍价差，4 回合金从 1500 掉到 649（**炒单**）。
* 建设顺序写死成 if/elif → 开局没有矿石格的 seed 冻在第 3 回合。
* 每格都先建采集 → 采集把额度吃光，链子（能源→凑位→兵营）永远轮不到执行。
* 24/30 的 seed 死在"金剩 44~330、建不起 350 的兵营"→ 这就是要兵营专款的原因。

用法（与其它规则 AI 同签名）：

    from expand_rule_open import opening_turn
    opening_turn(world, name, rng, max_actions=10**9)
"""
from __future__ import annotations

import random

from game import BUILDINGS, MARKET, TOWN_HALL_GOLD, TOWN_HALL_PER_SLOT

MILESTONE_ARMIES = 2                  # 里程碑：2 支步兵
ARMY_GATE = 0.60                      # 军费占收入的上限（>60% 就不建军）

# 建造惩罚（选址用）：平原 0 最优，山地 +50% 最差
_TERRAIN_PENALTY = {"平原": 0, "森林": 15, "丘陵": 25, "沙漠": 40, "山地": 50}

_RES_FOR = {"农场": "耕地", "矿场": "矿石", "林场": "木头",
            "石油厂": "石油", "黄金矿场": "黄金"}
# 建设优先级：**金矿权重最大**（现金是万事的门槛）→ 补给厂原料 → 建材
_EXTRACTORS = ("黄金矿场", "农场", "矿场", "林场", "石油厂")
_CHAIN = ("木材能源厂", "兵营", "补给厂")     # 必须同格（兵营要本地 3 个建筑位）
_FILLERS = ("瞭望塔",)                        # 凑位用（不挑资源、不烧燃料）

_MVAL_CACHE: dict = {}


def _price(good: str) -> float:
    return float(MARKET.get(good, 0))


def _cnt(w, name, bn) -> int:
    return sum(t["buildings"][bn] for t in w.tiles.values() if t["owner"] == name)


def _gold(w, name) -> int:
    return w.res(name, "黄金")


def _cost_of(bn: str) -> tuple[int, int]:
    """(金币, 木材)。城堡的 cost 是列表，取第一级。"""
    info = BUILDINGS[bn]
    c = info["cost"]
    return (max(c) if isinstance(c, list) else c), info.get("wood", 0)


# --------------------------------------------------------------------------
# 收支账
# --------------------------------------------------------------------------

def income_of(w, name: str) -> float:
    """每回合金等价收入：金矿/市政厅产金 + 采集建筑产出按市价折金。"""
    total = 0.0
    for p in w.own_tiles(name):
        for bn, cnt in w.tiles[p]["buildings"].items():
            if not cnt:
                continue
            info = BUILDINGS.get(bn)
            if info is None:
                continue
            for good, amt in (info.get("outputs") or {}).items():
                total += _price(good) * amt * cnt
            if info["kind"] == "townhall":
                total += (TOWN_HALL_GOLD + TOWN_HALL_PER_SLOT * sum(
                    w.tiles[p]["buildings"].values())) * cnt
    return total


def upkeep_of(w, name: str, extra_armies: int = 0) -> float:
    """每回合金等价维护费——**按缺口算：自产的不计，只有要花钱买的才算**。

    含三项：① 军队口粮缺口 ② 电力木耗（能源厂每回合烧 1 木换电）
    ③ （调用方按需加）为新增建筑预留的电耗。
    """
    # ① 口粮缺口：军队要吃补给，先扣自产
    arms = len(w.nation_armies(name)) + extra_armies
    need_sup = sum({"步": 1, "骑": 2}.get(a.get("kind", "步"), 1)
                   for a in w.nation_armies(name))
    if extra_armies:
        need_sup += extra_armies
    made_sup = _cnt(w, name, "补给厂") * BUILDINGS["补给厂"]["outputs"]["补给"]
    short_sup = max(0, need_sup - made_sup)
    total = short_sup * _price("补给")

    # ② 电力木耗：每座能源厂每回合烧 fuel 木；用电建筑要够电才算"在转"
    wood_burn = sum((BUILDINGS[bn]["fuel"]["木头"]) * cnt
                    for t in w.tiles.values() if t["owner"] == name
                    for bn, cnt in t["buildings"].items()
                    if cnt and BUILDINGS.get(bn, {}).get("fuel", {}).get("木头"))
    total += wood_burn * _price("木头")
    _ = arms
    return total


def _chain_tile(w, name):
    """链子（能源/兵营/补给厂）建在哪一格：**优先平原**，其次看地形惩罚。"""
    own = w.own_tiles(name)
    if not own:
        return None
    return min(own, key=lambda p: (_TERRAIN_PENALTY.get(w.tiles[p]["terrain"], 99), p))


# --------------------------------------------------------------------------

def opening_turn(world, name: str, rng: random.Random | None = None,
                 max_actions: int = 60, on_action=None, on_result=None) -> list:
    """行为克隆采样钩子（与 v6/rule_ai 同签名）。"""
    if rng is None:
        rng = random.Random(0)
    w, acts = world, []

    def do(tool, args, fn, *a, **k) -> bool:
        if len(acts) >= max_actions:
            return False
        if on_action is not None:
            on_action(tool, args)
        try:
            ok, msg = fn(*a, **k)
        except Exception as e:                     # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"
        if on_result is not None:
            on_result(tool, args, bool(ok))
        acts.append((tool, args, bool(ok), str(msg)))
        return bool(ok)

    def buy(good: str, want: int, budget_frac: float = 0.5) -> None:
        short = want - w.res(name, good)
        if short <= 0:
            return
        budget = _gold(w, name) * budget_frac
        n = 0
        for k in range(1, short + 1):
            if w.market_quote(good, k, "buy")[1] > budget:
                break
            n = k
        if n > 0:
            do("buy", {"good": good, "qty": n}, w.buy, name, good, n)

    main = _chain_tile(w, name)
    if main is None:
        return acts
    barracks_n = _cnt(w, name, "兵营")
    need_e = MILESTONE_ARMIES - len(w.nation_armies(name))

    # ---- 1. 收支账 → 闸门 ----
    inc = income_of(w, name)
    upk = upkeep_of(w, name, extra_armies=max(0, need_e))
    ratio = upk / inc if inc > 0.5 else 9.9        # 没有收入 = 养不起
    can_army = ratio <= ARMY_GATE

    # ---- 2. 清仓：只留三样，其余全卖成现金（**囤着不产生复利**）----
    keep_wood = sum(_cost_of(bn)[1] for bn in _CHAIN) + 10
    keep_food = 10 * max(need_e, 1) + 20            # 征兵口粮 + 缓冲
    keep_equip = 5 * max(need_e, 1) + 5
    keep_supply = len(w.nation_armies(name)) * 8 + 20
    for good, keep in (("木头", keep_wood), ("粮食", keep_food),
                       ("装备", keep_equip), ("矿石", 6), ("石油", 4),
                       ("补给", keep_supply)):
        excess = w.res(name, good) - keep
        if excess > 0:
            if good == "补给" and _cnt(w, name, "补给厂") == 0 and can_army:
                pass                               # 还没自产口粮时别把命卖掉
            else:
                do("sell", {"good": good, "qty": excess}, w.sell, name, good, excess)

    # ---- 3. 建设 ----
    # 3a. 链子（能源 → 凑位 → 兵营 → 补给厂），全在 main 格上
    t_main = w.tiles[main]
    slots = sum(t_main["buildings"].values()) + sum(t_main["pending"].values())
    recruit_ready = w.res(name, "粮食") >= 10 and w.res(name, "装备") >= 5
    if not t_main["built_this_turn"]:
        if not _cnt(w, name, "木材能源厂"):
            _try(w, name, main, "木材能源厂", do, buy)
        elif slots < 3 and (can_army or recruit_ready):
            # **兵营条件不满足但已达出兵条件 → 硬凑 3 格**
            _try(w, name, main, _cheapest_filler(w, name, main), do, buy)
        elif slots >= 3 and not barracks_n and can_army:
            _try(w, name, main, "兵营", do, buy)
        elif barracks_n and not _cnt(w, name, "补给厂") and _worth_supply_factory(w, name, ratio):
            _try(w, name, main, "补给厂", do, buy)

    # 3b. 其余格并行铺采集（金矿权重最大）；让开兵营专款
    barr_fund = 350 if not barracks_n else 0
    for p in sorted(w.own_tiles(name)):
        if w.tiles[p]["built_this_turn"] or p == main:
            continue
        t = w.tiles[p]
        for bn in _EXTRACTORS:
            key = _RES_FOR[bn]
            if t["resources"].get(key, 0) > t["buildings"][bn] + t["pending"][bn]:
                gold, _wood = _cost_of(bn)
                # **别在这里查木头**：木头不够时 `_try` 会自己去市场买，
                # 守卫里先拦一道就等于永远买不成（实测 seed 9/22 就死在这：
                # 木头剩 2~4、每回合只做卖出，链子从第 4 回合起一动不动）。
                if _gold(w, name) - barr_fund >= gold:
                    _try(w, name, p, bn, do, buy)
                break

    # ---- 4. 征兵：有兵营就征，**不等补给产能**（v6 前 40 回合不出兵就死在这）----
    if barracks_n and can_army:
        while len(w.nation_armies(name)) < MILESTONE_ARMIES and len(acts) < max_actions:
            if w.res(name, "粮食") < 12:
                buy("粮食", 30)
            if w.res(name, "装备") < 6:
                buy("装备", 12)
            if not any(do("recruit", {"tile": f"{p[0]+1} {p[1]+1}", "unit": "步", "n": 1},
                          w.recruit, name, p[0], p[1], 1, "步")
                       for p in sorted(w.own_tiles(name))
                       if w.tiles[p]["buildings"]["兵营"]):
                break

    # ---- 5. 养兵：没补给厂才买补给；有厂了就补原料 ----
    if w.nation_armies(name):
        if not _cnt(w, name, "补给厂"):
            n_arm = len(w.nation_armies(name))
            if w.res(name, "补给") < n_arm * 10:
                buy("补给", n_arm * 25)
        elif w.res(name, "补给") < len(w.nation_armies(name)) * 3:
            buy("粮食", 30)
            buy("矿石", 12)

    return acts


# --------------------------------------------------------------------------

def _cheapest_filler(w, name, main) -> str:
    """凑建筑位用：优先建本格资源能产的采集建筑（便宜），否则瞭望塔。"""
    for bn in _EXTRACTORS:
        key = _RES_FOR[bn]
        t = w.tiles[main]
        if t["resources"].get(key, 0) > t["buildings"][bn] + t["pending"][bn]:
            return bn
    return _FILLERS[0]


def _worth_supply_factory(w, name, ratio) -> bool:
    """补给厂评估：**省下的口粮维护**够不够回本（含 1 木电力）。

    只在"口粮要花钱买"（ratio 高）时才值得建；本来就不缺口粮就别花这 175 金。
    """
    if ratio <= 0.15:
        return False                               # 口粮几乎不花钱，别建
    need = sum({"步": 1, "骑": 2}.get(a.get("kind", "步"), 1) for a in w.nation_armies(name))
    saved = max(0, need - _cnt(w, name, "补给厂") * 2) * _price("补给")
    cost = 175 + 12 * _price("木头") + _price("木头")     # 建造 + 每回合 1 木电耗
    return saved > cost / 20                       # 20 回合内回本才建


def _try(w, name, p, bn, do, buy) -> bool:
    """在 p 格建 bn：木头不够先买，再下单。"""
    gold, wood = _cost_of(bn)
    if _gold(w, name) < gold:
        return False
    if w.res(name, "木头") < wood:
        buy("木头", wood + 4)
    if w.res(name, "木头") < wood:
        return False
    return do("build", {"tile": f"{p[0]+1} {p[1]+1}", "building": bn},
              w.build, name, p[0], p[1], bn)
