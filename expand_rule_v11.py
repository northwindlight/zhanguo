# -*- coding: utf-8 -*-
"""扩张流规则 AI · **v11 = v10 的经济段（原样复用）+ 重写的军事四件套**（用户 2026-09-15）

■ v11 是什么

  用户口径：**"维护 v10，准备迭代 v11；我需要更好的自动编队逻辑和寻路逻辑，
  以及目标选择，最好和 v10 解耦合。"** 于是：

    · **经济段（第 0~7 节）逐行照抄 v10** —— 它是对的（一榜/一账/一次决策、
      预留、电厂替换，都是踩过坑才定下来的），抄过来就不动；
    · **军事段（第 8 节）整段重写**，四个子系统搬进四个**独立模块**：

    | 模块 | 管什么 | v10 对应物 |
    |---|---|---|
    | `pathfind.py` | 视野掩码、多回合代价场、本回合落点 | 单步切比雪夫贪心（不懂地形代价） |
    | `targeting.py` | 候选枚举、估值、排序 | 只从"军队旁边一圈"里挑、资源按 0 算 |
    | `combat.py` | 难度评估（几支能赢、几回合、掉多少血） | `_fight_cost` 线性估、用全军最大值 |
    | `formation.py` | 兵力分配（先目标→评难度→自动分兵） | 按列表顺序拽人、多目标抢同一支军 |

  **v10 一个字节没动**（它仍是缺省基线，也是 RL 线的 BC 老师——改它=作废全部历史标签）。
  `DEFAULT_RULE_AI` 也没动：要用 v11 得在配置里写 `"rule_ai": "v11"`。

■ 与 v10 的三处**有意分歧**（都是"修 bug / 补能力"，不是偷偷调参）

  1. **补给按真实兵种算**：v10 走 `spend_rules.army_upkeep_units`，而那里读的是
     `a.get("kind", "步")` —— 军队字典存的键是 **`type`**，于是**每支骑兵都被按 1 算**
     （真值 2）。v10 的军费闸门、补给备料、断粮判断因此全线偏低。
     用户 2026-09-15 定：**v10 不动，v11 算对** ⇒ 本文件用 `game.unit_supply`
     并镜像引擎 `_supply_need` 的"军屯覆盖本格民兵"豁免（`_supply_units`）。
  2. **看得见的空目标只要 1 支**（`balance.V11_MIN_SQUAD_EMPTY=1`）：野人开局铺满全图、
     死过不再补（`guard_once`）、占地时连守卫一起清 ⇒ **看得见又没有守军 = 真空**，
     走进去就占地。v10 一律要求 `MIN_SQUAD=2`，在那儿白白多派一支。
     有守军的目标仍是 `V11_MIN_SQUAD=2`（"从不单兵作战"的教条照旧）。
  3. **征召料按引擎同一口径现读**（`world.recruit_cost(name, "步")`，含政体特价）：
     v10 读的是 `UNIT_TYPES["步"]["recruit"]` 与 `BUILDINGS["兵营"]["army_cost"]`
     两份手抄表——今天数值恰好相同，一改平衡就漂。

■ 只进攻、不驻防（用户口径）：不产生"回防"目标、不调 `retreat`。老家被偷袭是 v12 的事。

■ RL 线提醒：v11 若要当 BC 老师，`rl/bc.py` 的 teacher 分派是**硬编码 if/elif**，
  得单独加一支；`HORIZON` 已按惯例暴露在模块级（`set_horizon` 改的就是它）。

用法：
    w = World(size=16, seed=0, nations=["秦"])
    w.begin_turn()
    expand_rule_turn_v11(w, "秦")
    w.resolve_turn()
"""
from __future__ import annotations

import random

import grouping
import pathfind
from balance import (V11_FIELD_MAX_COST, V11_MAX_ATTACKS, V11_NEED_CAP,
                     V11_ROUNDS_CAP, V11_SCAN_RADIUS)
from combat import assess
from game import (BUILDINGS, MAX_SLOTS, TRADEABLE, unit_kind, unit_max_hp,
                  unit_supply)
from mp import build_econ

# ---------------------------------------------------------------- 口径常量
HORIZON = 200          # 评估基准回合数（用户：以后都按 200 回合算，不做长期 ROI）
MIL_SHARE = 0.30       # 军费占收入的上限：出兵、涨兵**同一个条件**（用户 2026-09-11）
MIL_RESERVE = 6        # 军事段预留的动作数（经济段拿到 max_actions - 这个数）
# ★ 军事段的下限/上限等**策略旋钮**全在 `balance.py` 第十节（`V11_*`）——那里是唯一调参入口。


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


def expand_rule_turn_v11(world, name: str, rng: random.Random | None = None,
                         max_actions: int = 40, on_action=None, on_result=None) -> list:
    if rng is None:
        rng = random.Random(0)
    acts: list[tuple[str, dict, bool, str]] = []

    # ================================================================ 0. 工具
    # （第 0~7 节 = `expand_rule_v10.py` 的经济段，照抄不动，只在上面那三处按 v11 口径改）
    def do(tool, args, fn, *a, **k) -> bool:
        if len(acts) >= max_actions:
            return False
        if on_action is not None:
            on_action(tool, args)
        try:
            ok, msg = fn(*a, **k)
        except Exception as e:                       # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"
        if on_result is not None:
            on_result(tool, args, bool(ok))
        acts.append((tool, args, bool(ok), str(msg)))
        return bool(ok)

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
        """经济段的额度用完了吗（给军事段留出 `MIL_RESERVE` 个动作）。

        ★ 这条闸**必须卡在买卖上**：清仓（第 5 节）与备料（第 6 节）在建造之前跑，
          一次可以花掉十几个动作（最多 6 项物资各一笔）。不拦它，经济段就会把
          `max_actions` 吃干，军事段一个动作都发不出去 —— 看海配置里默认只有 12 个动作，
          实测（40×40 seed 0）就是"200 回合 0 扩张、领土一直停在 5 格"。
          v10 也有这个结构，只是它的数字是那么量出来的；v11 要扩张，就得真留出额度。
        """
        return len(acts) >= max_actions - MIL_RESERVE

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
        if len(acts) >= max_actions - MIL_RESERVE:
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

    # ================================================================ 8. 扩张（★ v11：全局编组）
    # 模型见 `docs/v11编组模型.md`。三条口径（用户 2026-09-15）：
    #   · 编组是**全局**的：三个指标（组内距离最小 / 全体到目标最近 / 目标定人数）；
    #   · **只在目标消失时重编**，且只重编"无目标的那些军"（状态在 `grouping` 模块内存里）；
    #   · **任何军队一定有目标** ⇒ 没有"待命"这个状态。
    # 执行仍然守引擎的两条硬规矩：
    #   · 落点一律从 `world._reachable`（引擎合法集）里选 ⇒ 结构上不可能撞墙烧额度；
    #   · 引擎的 `attack` 是**原子**的（任一支到不了就整通全废）⇒ 出手前逐支复核。
    armies = [a for a in world.armies if a["owner"] == name and a["hp"] > 0]
    pool = sorted((a for a in armies
                   if not a.get("engaged") and a.get("moved_turn") != world.turn),
                  key=lambda a: a["id"])
    mask = pathfind.vision_mask(world, name)

    # ---- 8a. 状态机：目标消失 ⇒ 那一组解散；然后**只给无目标的军**做一次全局编组 ----
    grouping.refresh(world, name, mask, armies)
    field_cache: dict = {}
    if pool:
        grouping.regroup(world, name, mask, armies,
                         radius=V11_SCAN_RADIUS, need_cap=V11_NEED_CAP,
                         rounds_cap=V11_ROUNDS_CAP, cache=field_cache)
    state = grouping.targets_of(name)
    by_id = {a["id"]: a for a in pool}
    # 组 = target 的等价类（状态就是那张表，这里只是把它摊开成"按目标分组"）
    groups: dict = {}
    for aid, cell in sorted(state.items()):
        if aid in by_id:
            groups.setdefault(cell, []).append(aid)

    reach: dict = {}                                      # 每军每回合只问引擎一次（它无缓存）
    engaged_or_used: set = set()
    n_attacks = 0                                         # 本回合已开打的场数（上限见 V11_MAX_ATTACKS）

    def taken(cell) -> bool:
        """出手前再查一次：这一格现在**已经不是能打的**了（变成自己的/中立/盟国的）。

        ★ 必须查：编组是回合开头定的，而打仗/结盟在回合内会改变归属；
          对中立地 `atk` 撞墙会**烧掉整队的移动额度**（`_blind_cost`），白亏一回合。
        """
        owner = world.owned_by(*cell)
        return owner is not None and (owner == name or world.allied_between(name, owner)
                                      or not world.war_between(name, owner))

    def reach_of(a, *, for_attack: bool):
        key = (a["id"], for_attack)
        if key not in reach:
            reach[key] = world._reachable(name, a, for_attack=for_attack)
        return reach[key]

    # ---- 8b. 出手：够得着就打（满血才上），够不着就按地形代价寻路推进 ----
    for cell in sorted(groups):
        ids = groups[cell]
        if len(acts) >= max_actions:
            break
        members = [by_id[i] for i in ids if i in by_id and i not in engaged_or_used]
        able = [a for a in members
                if a["hp"] >= unit_max_hp(a)                 # 满血才冲阵（带伤的跟着走、养好再上）
                and pathfind.marchable(world, name, a, cell,
                                       reach=reach_of(a, for_attack=True))]
        # ★ 打不打**不问常数、只问判定式**：把"够得着的满血那几支"喂给 `combat.assess`，
        #   它说赢得下来就打 —— 这就是"最小编组由军队需要算出来"，没有 `MIN_SQUAD`。
        fit = (able and len(acts) < max_actions - 2 and not taken(cell)
               and n_attacks < V11_MAX_ATTACKS
               and assess(world, name, cell, able, visible=cell in mask,
                          need_cap=V11_NEED_CAP, rounds_cap=V11_ROUNDS_CAP).winnable)
        if fit:
            if do("attack", {"army_ids": [a["id"] for a in able],
                             "x": cell[0] + 1, "y": cell[1] + 1},
                  world.attack, name, [a["id"] for a in able], cell[0], cell[1]):
                engaged_or_used.update(a["id"] for a in able)
                n_attacks += 1
                continue
        if taken(cell):
            continue
        for a in members:                                    # 打不了 ⇒ 朝目标推进（每人一格）
            if len(acts) >= max_actions:
                break
            fld = pathfind.cost_field(world, name, {cell}, unit_kind(a), mask,
                                      max_cost=V11_FIELD_MAX_COST, cache=field_cache)
            nxt = pathfind.best_step(world, name, a, fld,
                                     reach=reach_of(a, for_attack=False), goal=cell)
            if nxt is None:
                continue                                     # 没有严格更近的一步 ⇒ 本回合不动
            do("move", {"army_id": a["id"], "x": nxt[0] + 1, "y": nxt[1] + 1},
               world.move, name, a["id"], nxt[0], nxt[1])
    return acts