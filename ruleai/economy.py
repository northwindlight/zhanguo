# -*- coding: utf-8 -*-
"""v11 的**经济层**：一榜、一账、一次决策（口径照抄 `expand_rule_v10.py` 的经济段）。

★ **它不该知道军事层的存在**（用户 2026-09-15：「v11 是个经济层和军事层分离的 ruleai」）：
  两层只共享**一本动作账**（`ruleai.Ledger`）与 `world`。经济层管钱、料、建造、征兵；
  军事层管编组与出手。谁也别 import 谁。

★ 与 v10 的三处**有意分歧**（都是"修 bug / 补能力"，不是偷偷调参）：

  1. **补给按真实兵种算**：v10 走 `spend_rules.army_upkeep_units`，而那里读的是
     `a.get("kind", "步")` —— 军队字典存的键是 **`type`**，于是**每支骑兵都被按 1 算**
     （真值 2）。用户 2026-09-15 定：**v10 不动，v11 算对** ⇒ 本层用 `game.unit_supply`
     并镜像引擎 `_supply_need` 的"军屯覆盖本格民兵"豁免（`_supply_units`）。
  2. **征召料按引擎同一口径现读**（`world.recruit_cost(name, "步")`，含政体特价）：
     v10 读的是 `UNIT_TYPES["步"]["recruit"]` 与 `BUILDINGS["兵营"]["army_cost"]`
     两份手抄表——今天数值恰好相同，一改平衡就漂。
  3. **给军事段留额度**（`V11_MIL_RESERVE`）：清仓/备料也要卡这个闸。v10 只在建造循环里卡，
     于是买卖能把 `max_actions` 吃干、军事段一个动作都发不出（看海配置默认只有 12 个）。

★ **产兵权在这里**（用户 2026-09-15：「产兵由经济引擎决定」）：产几支、何时产、
  出生在哪个兵营格，都由本层决定；军事层只负责**用**已经存在的兵，不碰产兵。
"""
from __future__ import annotations

from balance import V11_MIL_RESERVE
from game import BUILDINGS, MAX_SLOTS, TRADEABLE, unit_kind, unit_supply
from mp import build_econ

HORIZON = 200          # 评估基准回合数（用户：以后都按 200 回合算，不做长期 ROI）
MIL_SHARE = 0.30       # 军费占收入的上限：出兵、涨兵**同一个条件**（用户 2026-09-11）


def _supply_units(world, name: str) -> int:
    """本国军队本回合要吃的补给**单位数** —— 现读 `game.unit_supply`（步1/骑2/民1）。

    镜像引擎 `World._supply_need`（`mp.py:1770`）：民兵驻在**自家军屯格**免费，
    每座军屯覆盖本格 1 支；离格/军屯被夺/同格第 2 支起照常吃。

    ★ 刻意**不用** `spend_rules.army_upkeep_units`：那里读 `a.get("kind", "步")`，
      而军队字典存的键是 `type` ⇒ 每支骑兵被按 1 算（真值 2）。见文件头"分歧 1"。
    """
    free: dict = {}
    need = 0
    for a in world.nation_armies(name):
        if unit_kind(a) == "民":
            t = world.tiles.get((a["x"], a["y"]))
            if t is not None and t["owner"] == name:
                cap = t["buildings"].get("军屯", 0)
                used = free.get((a["x"], a["y"]), 0)
                if used < cap:
                    free[(a["x"], a["y"])] = used + 1
                    continue
        need += unit_supply(a)
    return need


def _can_afford(world, name: str, res: dict, unit: str) -> bool:
    """征一支 `unit` 的原料够不够 —— 现读 `world.recruit_cost`（**含政体特价**）。

    v10 读的是 `UNIT_TYPES[unit]["recruit"]`（手抄的第二份）；引擎 `recruit` 收费走的是
    `world.recruit_cost`，两边必须同一份口径，否则"备料备够了却征不出来"。
    """
    return all(res.get(g, 0) >= amt for g, amt in world.recruit_cost(name, unit).items())


def _res_of(building: str) -> str | None:
    """某采集建筑要的本地资源 —— 直接读 `cap_resource`（不再手抄一份）。"""
    return BUILDINGS.get(building, {}).get("cap_resource")


def _extractors() -> tuple[str, ...]:
    """当下所有「采集类」建筑（按 kind 现算，引擎加一种就自动带上）。"""
    return tuple(b for b, v in BUILDINGS.items()
                 if v.get("kind") in ("extract", "gold") and v.get("outputs"))


_FACTORIES = ("补给厂",)


def run(ledger, world, name: str) -> None:
    """把一个回合的经济动作追加进 `ledger`（额度与回调都由它统一管）。"""
    acts = ledger.acts
    do = ledger.do
    max_actions = ledger.max_actions

    # ================================================================ 0. 工具
    # （第 0~7 节 = `expand_rule_v10.py` 的经济段，照抄不动，只在上面那几处按 v11 口径改）
    def R():
        return world.nations[name].res

    def tiles():
        return sorted((x, y) for (x, y), t in world.tiles.items() if t["owner"] == name)

    def cnt(bn):
        return sum(t["buildings"].get(bn, 0)
                   for t in world.tiles.values() if t["owner"] == name)

    def slots(p):
        t = world.tiles[p]
        return sum(t["buildings"].values()) + sum((t.get("pending") or {}).values())

    def cost_of(bn):
        c = BUILDINGS[bn]["cost"]
        return sum(c) if isinstance(c, list) else c

    def free_at(p):
        """这一格本回合还能不能下单（引擎：每地块每回合限建 1 座）。"""
        return (not world.tiles[p].get("built_this_turn")) and slots(p) < MAX_SLOTS

    def afford(p, bn):
        cur = R()
        return (cur["黄金"] >= cost_of(bn) and cur["木头"] >= BUILDINGS[bn]["wood"]
                and free_at(p))

    def build(p, bn):
        return do("build", {"tile": f"{p[0]+1} {p[1]+1}", "building": bn},
                  world.build, name, p[0], p[1], bn)

    def econ_full() -> bool:
        """经济段的额度用完了吗（给军事段留出 `V11_MIL_RESERVE` 个动作）。

        ★ 这条闸**必须卡在买卖上**：清仓（第 5 节）与备料（第 6 节）在建造之前跑，
          一次可以花掉十几个动作（最多 6 项物资各一笔）。不拦它，经济段就会把
          `max_actions` 吃干，军事段一个动作都发不出去 —— 看海配置里默认只有 12 个动作，
          实测（40×40 seed 0）就是"200 回合 0 扩张、领土一直停在 5 格"。
          v10 也有这个结构，只是它的数字是那么量出来的；v11 要扩张，就得真留出额度。
        """
        return ledger.full(V11_MIL_RESERVE)

    def buy(good, qty):
        """**市场调剂**：买多少由 `need` 定，不由余额定（不设 `reserve` 门槛）。"""
        px = max(1, int(world.prices.get(good, 2)))
        q = min(int(qty), max(0, int(R()["黄金"]) // px))
        return (q > 0 and not econ_full()
                and do("buy", {"good": good, "qty": q}, world.buy, name, good, q))

    def sell(good, qty):
        # `>=` 而不是 `>`：要卖光时 qty 恰好等于 have，用严格大于会**一个都卖不掉**
        return (int(qty) > 0 and int(R().get(good, 0)) >= int(qty) and not econ_full()
                and do("sell", {"good": good, "qty": int(qty)},
                       world.sell, name, good, int(qty)))

    own = tiles()
    if not own:
        return acts
    armies = [a for a in world.armies if a["owner"] == name and a["hp"] > 0]
    army_n = len(armies)

    # ================================================================ 1. 账面盘点
    power = (cnt("木材能源厂") * BUILDINGS["木材能源厂"]["energy_out"]
             + cnt("石油能源厂") * BUILDINGS["石油能源厂"]["energy_out"])
    need_pw = sum(cnt(bn) * BUILDINGS[bn].get("energy", 0)
                  for bn in BUILDINGS if BUILDINGS[bn].get("energy"))
    gen_add = 0
    _dem_add = 0

    from spend_rules import income_of
    supply_px = float(world.prices.get("补给", 5))

    # ================================================================ 2. 条件项
    supply_cap = cnt("补给厂") * 2               # 补给产能 = 军队编制上限
    want_barr = max(2, supply_cap // 2 + 2)      # 还想要几座兵营（编制缺口）

    inc = income_of(world, name)
    upkeep = _supply_units(world, name) * supply_px      # ★ v11：真实兵种（骑算 2）
    mil_ok = inc <= 0.5 or (upkeep / inc) < MIL_SHARE
    army_cap = max(army_n + 1, supply_cap) if mil_ok else army_n

    # ================================================================ 3. 本回合的计划
    left = max(1, HORIZON - world.turn)
    free_tiles = [p for p in own if free_at(p)]
    room = len(free_tiles)

    roi: list[tuple[float, str, tuple]] = []
    for p in free_tiles:
        t = world.tiles[p]
        res, built = t["resources"], t["buildings"]
        for bn in _extractors():               # ★现算：引擎加一种采集建筑就自动带上
            _need = _res_of(bn)                # ★现读 cap_resource（不再手抄一份）
            if _need is None:
                continue
            if res.get(_need, 0) <= built.get(bn, 0) + (t.get("pending") or {}).get(bn, 0):
                continue
            e = build_econ(world, bn, p)           # 传地块：按该格实际造价算回本
            if e["payback"] and e["payback"] <= left:
                roi.append((e["payback"], bn, p))
        for bn in _FACTORIES:
            e = build_econ(world, bn, p)
            if e["payback"] and e["payback"] <= left:
                roi.append((e["payback"], bn, p))
    roi.sort(key=lambda x: x[0])

    n_barr = min(max(0, want_barr - cnt("兵营")),
                 sum(1 for p in free_tiles if slots(p) >= 3)) if cnt("兵营") < want_barr else 0
    e_out = BUILDINGS["木材能源厂"]["energy_out"]
    n_plant = 0
    if power < need_pw:
        n_plant = min(max(0, room - n_barr), -(-(need_pw - power) // e_out))
    n_recruit = 0
    if mil_ok and army_n < army_cap:
        _slots_rec = sum(max(0, world.tiles[p]["buildings"].get("兵营", 0)
                             - world.tiles[p].get("recruited_this_turn", 0)) for p in own)
        n_recruit = min(army_cap - army_n, _slots_rec)
    n_roi_slots = max(0, room - n_barr - n_plant)

    # ================================================================ 4. 记账（**唯一口径**）
    need: dict[str, int] = {g: 0 for g in TRADEABLE}

    def want(g, v):
        if v > 0:
            need[g] = need.get(g, 0) + int(v)

    want("补给", _supply_units(world, name))                     # 刚需①军队口粮 ★v11 口径
    for bn in ("补给厂", "装备厂"):                                # 刚需②在产工厂投料
        for g, per in (BUILDINGS[bn].get("inputs") or {}).items():
            want(g, cnt(bn) * per)
    for bn in ("木材能源厂", "石油能源厂"):                        # 刚需③在产电厂燃料
        for g, per in (BUILDINGS[bn].get("fuel") or {}).items():
            want(g, cnt(bn) * per)
    for _ in range(n_plant):
        want("木头", BUILDINGS["木材能源厂"]["wood"])
    for _ in range(n_barr):
        want("木头", BUILDINGS["兵营"]["wood"])

    # 计划料：**只留"金和木当下都付得起"的那些楼**
    #   木头留多了是致命的（钱全锁在木头里，而挡着建造的是金）；留少了只是这回合少建一座。
    _budget = int(R()["黄金"])
    for _g in TRADEABLE:
        if _g == "木头":
            continue
        _sur = int(R().get(_g, 0)) - need.get(_g, 0)
        if _sur > 0:
            _budget += int(_sur * 0.9 * max(1, int(world.prices.get(_g, 2))))
    _wx = max(1, int(world.prices.get("木头", 2)))
    planned: list[tuple[float, str, tuple]] = []
    for _pb, _bn, _p in roi:
        if len(planned) >= n_roi_slots:
            break
        _need_gold = int(cost_of(_bn)) + int(BUILDINGS[_bn].get("wood", 0)) * _wx
        if _need_gold > _budget:
            continue                     # 付不起就跳过（不中断：后面可能有更便宜的）
        _budget -= _need_gold
        planned.append((_pb, _bn, _p))
    for _pb, _bn, _p in planned:
        want("木头", BUILDINGS[_bn].get("wood", 0))
    for _ in range(n_recruit):                                    # 计划征兵料
        for g, per in world.recruit_cost(name, "步").items():     # ★ v11：与引擎同一份口径
            want(g, per)

    # ================================================================ 5. 清仓
    for g in TRADEABLE:
        surplus = int(R().get(g, 0)) - need.get(g, 0)
        if surplus > 0:
            sell(g, surplus)

    # ================================================================ 6. 备料
    for g in TRADEABLE:
        gap = need.get(g, 0) - int(R().get(g, 0))
        if gap > 0:
            buy(g, gap)

    # ================================================================ 7. 花钱（**串行判定**）
    def cash_crop(bn: str) -> bool:
        """它的产出能不能卖成钱？只有能变现的才被允许在攒钱期动那笔钱。"""
        if bn in _extractors():            # ★现算（v9 是一份写死的名单）
            return True
        return any(g in TRADEABLE for g in (BUILDINGS[bn].get("outputs") or {}))

    _top_bn = None
    if n_plant:
        _top_bn = "木材能源厂"
    elif n_barr:
        _top_bn = "兵营"
    elif roi:
        _top_bn = roi[0][1]
    save = max(0, cost_of(_top_bn) - int(R()["黄金"])) if _top_bn else 0

    def spend_ok(bn: str) -> bool:
        if save <= 0 or cash_crop(bn):
            return True
        return int(R()["黄金"]) - save >= cost_of(bn)

    def place(p, bn) -> bool:
        """下单一座楼，**带电厂替换**：这一座是**用电建筑**而账上没电 → 换成电厂。"""
        nonlocal gen_add, _dem_add
        if BUILDINGS[bn].get("energy", 0) and (power + gen_add) < need_pw + _dem_add + 1:
            if afford(p, "木材能源厂") and spend_ok("木材能源厂"):
                if build(p, "木材能源厂"):
                    gen_add += BUILDINGS["木材能源厂"]["energy_out"]
            return True                              # 本格已占用（建了，或电厂也建不起）
        if not afford(p, bn) or not spend_ok(bn):
            return False
        if build(p, bn):
            _dem_add += BUILDINGS[bn].get("energy", 0)
            return True
        return False

    for p in free_tiles:
        if ledger.full(V11_MIL_RESERVE):
            break
        if not free_at(p):
            continue
        t = world.tiles[p]

        # ---- ① 征兵（条件项）----
        if (army_n < army_cap
                and t["buildings"].get("兵营", 0) > t.get("recruited_this_turn", 0)
                and not world.grid_short.get(name)
                and _can_afford(world, name, R(), "步")):          # ★ v11：现读 recruit_cost
            if do("recruit", {"tile": f"{p[0]+1} {p[1]+1}", "n": 1, "unit": "步"},
                  world.recruit, name, p[0], p[1], 1, "步"):
                army_n += 1
                continue

        # ---- ② 兵营（条件项）----
        if cnt("兵营") < want_barr and slots(p) >= 3 and afford(p, "兵营") \
                and spend_ok("兵营"):
            if place(p, "兵营"):
                continue

        # ---- ③ 电厂（条件项）：补已成事实的缺口 ----
        if (power + gen_add) < need_pw + _dem_add and afford(p, "木材能源厂") \
                and spend_ok("木材能源厂"):
            if build(p, "木材能源厂"):
                gen_add += BUILDINGS["木材能源厂"]["energy_out"]
                continue

        # ---- ④ ROI 项（选项）：这一格回本最快的那些 ----
        cand = [(pb, bn) for pb, bn, q in roi if q == p]
        if cand:
            _pb, bn = cand[0]
            place(p, bn)
