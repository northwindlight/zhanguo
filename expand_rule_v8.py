# -*- coding: utf-8 -*-
"""扩张流规则 AI · **v8 —— 推倒重写**（用户 2026-09-11：「是的，重写，另起 v8」）

■ v7 为什么不能靠调参救（这是**逻辑**问题，不是数值问题）

  v7 在同一批资源上叠了**四套互不知道的账**：

    ① 0.5 清仓       卖到 `must_keep`
    ② 0.6 刚性支出   买到"缺口"，`reserve=0`（有多少钱花多少）
    ③ 1/4 市场调剂   买到 `must_keep`，`reserve=200`
    ④ 建造留钱       `RESERVE=350` / `barr_fund=350`

  ② 和 ④ **直接对立**：②用 `reserve=0` 把现金买光，④要攒 350 买兵营 —— 谁在代码里
  靠前谁赢，②在 0.6、④在第 3 节，所以**兵营永远攒不到**。这不是 seed 特例，是必然。
  而且"征兵数"在三处各算一遍（`cap_now` / `_army_cap` / 第 5 节的 `cap`），三个数在
  数值上互不相关 → 装备必然"这节卖光、那节买回"。**改一处就顶塌另一处。**

■ v8 的结构：一榜、一账、一次决策

    一榜：所有事情排成**一张榜** —— 采集/工厂按**回本**，兵营/电厂/征兵按**条件**
          （用户口径「走条件 ROI」：都在 ROI 里，但各自的条件不同）
    一账：`need(物资)` = 本回合刚需 + 榜上**本回合真能落地的那几座**的料
          **清仓卖到它、市场买到它 —— 同一个数**，所以"同回合先卖后买"结构上不可能
    一次：照榜花。钱不够就跳过，让后面的上 —— 没有 `reserve` 常量。

■ 复用的两条 v7 逻辑（用户点名要保留）

    ① **预留**（原 `RESERVE` / `barr_fund`）：榜上最好的那件事买不起时，把它的缺口
       **留出来**，只允许"产出可变现"的建筑动用那笔钱 —— 因为它们是**加快攒到它**
       的唯一途径。名单不是硬编码的：由 `outputs` 能不能卖钱推出来。
       没有这条，便宜的建筑会把钱一直吃光，贵的永远轮不到（v6 反复踩过）。
    ② **电厂替换**：要建的那座是**用电建筑**而账上没电 → 这一座**换成电厂**。
       条件挂在**落地那一刻**（不是挂在"目标"上）—— v7 挂在目标上时漏得很惨：
       补给厂靠回本在候选表里赢了就建，目标那天若是兵营/农场，电厂那条根本不触发，
       于是每建一座补给厂就缺电 3~4 回合，等目标轮到用电建筑才补电。

用法：
    w = World(size=16, seed=0, nations=["秦"])
    w.begin_turn()
    while ...:
        expand_rule_turn_v8(w, "秦")
        w.resolve_turn()
        w.begin_turn()
"""
from __future__ import annotations

import random

from game import BUILDINGS, TERRAIN_STATS, ARMY_MAX_HP, MAX_SLOTS
from mp import build_econ, good_value

# ---------------------------------------------------------------- 口径常量
HORIZON = 200          # 评估基准回合数（用户：以后都按 200 回合算，不做长期 ROI）
MIL_SHARE = 0.15       # 军费占收入的上限：出兵、涨兵**同一个条件**（用户 2026-09-11）
GOODS = ("粮食", "木头", "矿石", "石油", "装备", "补给")

TROOPS_FOR = {"沙漠": 2, "平原": 2, "森林": 2, "丘陵": 2, "山地": 3}
# ↑ 用户口径「两两成组、山地三三成组」。除山地外一律 2 支。

_RES_OF = {"农场": "耕地", "矿场": "矿石", "林场": "木头",
           "石油厂": "石油", "黄金矿场": "黄金"}
_EXTRACTORS = ("黄金矿场", "矿场", "林场", "农场", "石油厂")
# ROI 项的候选（兵营/电厂**不在这里** —— 它们走条件，见第 2 节）
_FACTORIES = ("补给厂",)


def expand_rule_turn_v8(world, name: str, rng: random.Random | None = None,
                        max_actions: int = 40, on_action=None, on_result=None) -> list:
    if rng is None:
        rng = random.Random(0)
    acts: list[tuple[str, dict, bool, str]] = []

    # ================================================================ 0. 工具
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

    def terr(p):
        t = world.tiles.get(p)
        return t["terrain"] if t else world.tile_terrain(*p)

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

    def buy(good, qty):
        """**市场调剂**：买多少由 `need` 定，不由余额定（不设 `reserve` 门槛）。"""
        px = max(1, int(world.prices.get(good, 2)))
        q = min(int(qty), max(0, int(R()["黄金"]) // px))
        return q > 0 and do("buy", {"good": good, "qty": q}, world.buy, name, good, q)

    def sell(good, qty):
        # `>=` 而不是 `>`：要卖光时 qty 恰好等于 have，用严格大于会**一个都卖不掉**
        # （引擎都调不到，日志里看不见 —— v7 踩过：粮食堆到 201 而现金常年 2~5）。
        return (int(qty) > 0 and int(R().get(good, 0)) >= int(qty)
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
    short_pw = power < need_pw
    # 本回合已下单的电厂发电量 —— 在建建筑**一回合落地、落地当回合不产出**
    # （mp.py:1310），所以本回合下单的电厂和用电建筑**同时**在下回合生效，
    # 一笔一笔记就能保证下回合账平。
    gen_add = 0
    _dem_add = 0       # 本回合已下单的用电建筑耗电量（串行判定里跟 `gen_add` 配对）

    from spend_rules import income_of, army_upkeep_units
    supply_px = float(world.prices.get("补给", 5))

    # ================================================================ 2. 条件项
    # 用户口径「走条件 ROI」：兵营 / 电厂 / 征兵都**在榜上**，但**不按回本选** ——
    # 它们各有一条自己的条件。这里把条件算出来，第 3 节排榜、第 7 节照榜花。
    supply_cap = cnt("补给厂") * 2               # 补给产能 = 军队编制上限
    want_barr = max(2, supply_cap // 2 + 2)      # 还想要几座兵营（编制缺口）

    # 征兵条件：**军费占比 < 15%**（用户：出兵、涨兵都是 15%）。
    # ⚠️ 口径必须是**军费**（口粮折金），不是刚性支出 —— 刚性账单含**一次性征兵原料**
    #    （装备 8 金 × 5 = 40），一征就顶破上限，闸门恒假 → 军队卡死（v7 实测）。
    inc = income_of(world, name)
    upkeep = army_upkeep_units(world, name) * supply_px
    mil_ok = inc <= 0.5 or (upkeep / inc) < MIL_SHARE
    army_cap = max(army_n + 1, supply_cap) if mil_ok else army_n

    # ================================================================ 3. 本回合的计划
    # **一次决策**：条件项先定"做不做、做几件"（不是"每格一件"—— 那会一回合
    # 在 5 个格子上各建一座电厂/兵营），ROI 项再按回本占剩下的格子。
    left = max(1, HORIZON - world.turn)
    free_tiles = [p for p in own if free_at(p)]
    room = len(free_tiles)

    # ROI 项：采集类 + 工厂，按**回本**升序（回本 = 花钱 / 每回合赚）
    roi: list[tuple[float, str, tuple]] = []
    for p in free_tiles:
        t = world.tiles[p]
        res, built = t["resources"], t["buildings"]
        for bn in _EXTRACTORS:
            if res.get(_RES_OF[bn], 0) <= built.get(bn, 0) + (t.get("pending") or {}).get(bn, 0):
                continue
            e = build_econ(world, bn, p)           # 传地块：按该格实际造价算回本
            if e["payback"] and e["payback"] <= left:
                roi.append((e["payback"], bn, p))
        for bn in _FACTORIES:
            e = build_econ(world, bn, p)
            if e["payback"] and e["payback"] <= left:
                roi.append((e["payback"], bn, p))
    roi.sort(key=lambda x: x[0])

    # 条件项①：兵营 —— 编制缺口（`want_barr`）。位置要 min_slots=3。
    #   榜上但**不走回本**：build_econ 给 barracks per=0、payback=None，best_build
    #   明确跳过 payback=None，所以它只能靠条件入选。
    n_barr = min(max(0, want_barr - cnt("兵营")),
                 sum(1 for p in free_tiles if slots(p) >= 3)) if cnt("兵营") < want_barr else 0
    # 条件项②：电厂 —— **缺电才做**，做几座由缺口算（不缺 → 0 → 不做，
    #   这就是"缺才建、够就停"，不是手写常量）。要把它后面要上的用电建筑也算进去。
    n_roi = max(0, room - n_barr)
    n_pw_new = sum(BUILDINGS[bn].get("energy", 0)
                   for _pb, bn, _p in roi[:n_roi])
    e_out = BUILDINGS["木材能源厂"]["energy_out"]
    n_plant = 0
    if power + n_pw_new > need_pw:
        n_plant = min(max(0, room - n_barr),
                      -(-(need_pw + n_pw_new - power) // e_out))
    # 条件项③：征兵 —— 军费占比 < 15%（用户：出兵、涨兵**同一个条件**）。
    #   在榜上、不按回本选，和兵营一个逻辑；吞吐 = 每座兵营每回合 1 支。
    n_recruit = 0
    if mil_ok and army_n < army_cap:
        _slots_rec = sum(max(0, world.tiles[p]["buildings"].get("兵营", 0)
                             - world.tiles[p].get("recruited_this_turn", 0)) for p in own)
        n_recruit = min(army_cap - army_n, _slots_rec)
    n_roi = max(0, room - n_barr - n_plant)

    # ================================================================ 4. 记账（**唯一口径**）
    # `need(物资)` = 本回合刚需 + 榜上**本回合真能落地的那几座**的料。
    # 清仓卖到它、市场买到它 —— 同一个数，所以不可能"同回合先卖后买"。
    need: dict[str, int] = {g: 0 for g in GOODS}

    def want(g, v):
        if v > 0:
            need[g] = need.get(g, 0) + int(v)

    want("补给", army_upkeep_units(world, name))                 # 刚需①军队口粮
    for bn in ("补给厂", "装备厂"):                                # 刚需②在产工厂投料
        for g, per in (BUILDINGS[bn].get("inputs") or {}).items():
            want(g, cnt(bn) * per)
    for bn in ("木材能源厂", "石油能源厂"):                        # 刚需③在产电厂燃料
        for g, per in (BUILDINGS[bn].get("fuel") or {}).items():
            want(g, cnt(bn) * per)
    # 计划料：本回合打算落的那些（第 3 节算出的件数）——
    # 条件项（电厂/兵营）先占格子，所以它们的木料一定先被留出来。
    for _ in range(n_plant):
        want("木头", BUILDINGS["木材能源厂"]["wood"])
    for _ in range(n_barr):
        want("木头", BUILDINGS["兵营"]["wood"])
    for _pb, bn, _p in roi[:n_roi]:
        want("木头", BUILDINGS[bn].get("wood", 0))
    for _ in range(n_recruit):                                    # 计划征兵料
        for g, per in BUILDINGS["兵营"]["army_cost"].items():
            want(g, per)

    # ================================================================ 5. 清仓
    # 卖到 `need` —— 只卖真多余的。意义是**把钱周转起来**：库存是"未实现的消费"，
    # 囤着不计 build，卖成钱去建造才计入。
    for g in GOODS:
        surplus = int(R().get(g, 0)) - need.get(g, 0)
        if surplus > 0:
            sell(g, surplus)

    # ================================================================ 6. 备料
    # 买到 `need` —— 与清仓**同一个数**。这就是「锁定库存（目标 + 刚需）」：
    # 锁的是"这套计划真要花掉的量"，多一个不买、少一个不买。
    for g in GOODS:
        gap = need.get(g, 0) - int(R().get(g, 0))
        if gap > 0:
            buy(g, gap)

    # ================================================================ 7. 花钱（**串行判定**）
    # 用户口径：「**串行判定，先判定条件**」—— 不是"先把条件算好、再一次性铺开"，
    # 而是**逐格走一遍**，每格按**当下**的账重判：条件项（前提）先判，ROI 项垫后。
    # 这样"本回合做几座"不需要预算表：做完了条件自然不成立，就停了。
    def cash_crop(bn: str) -> bool:
        """它的产出能不能卖成钱？只有能变现的才被允许在攒钱期动那笔钱。"""
        if bn in _EXTRACTORS:
            return True
        return any(g in GOODS for g in (BUILDINGS[bn].get("outputs") or {}))

    # **预留**（复用 v7 逻辑）：本回合"最好的那件事"（条件项优先，否则 ROI 榜首）
    # 若买不起，把它的缺口留出来 —— 只有能变现的产出类建筑能动那笔钱，
    # 因为它们是**加快攒到它**的唯一途径。没有这条，便宜的建筑会把钱一直吃光。
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
        """下单一座楼，**带电厂替换**（复用的第二条 v7 逻辑）：
        这一座是**用电建筑**而账上没电 → 换成电厂（前提优先）。
        条件挂在**落地这一刻** —— 挂在"目标"上会漏：补给厂靠回本在候选里赢了就建，
        目标那天若是兵营/农场，电厂那条根本不触发，于是每建一座补给厂缺电 3~4 回合。"""
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
        if len(acts) >= max_actions - 6:
            break
        if not free_at(p):
            continue
        t = world.tiles[p]

        # ---- ① 征兵（条件项，和兵营一个逻辑）：军费占比 < 15% ----
        # 原料在 4/6 节已按 `need` 备好 —— 这里不再按 `reserve` 买（v7 的坑：
        # `buy(..., reserve=40)` 要手上 ≥80 金才买得动，买不动就 break，而 q ≤ 0 时
        # 连 `do()` 都不调，**日志里一条都不留**，军队永远停在 2 支）。
        if (army_n < army_cap
                and t["buildings"].get("兵营", 0) > t.get("recruited_this_turn", 0)
                and not world.grid_short.get(name)
                and R()["粮食"] >= 10 and R()["装备"] >= 5):
            if do("recruit", {"tile": f"{p[0]+1} {p[1]+1}", "n": 1, "unit": "步"},
                  world.recruit, name, p[0], p[1], 1, "步"):
                army_n += 1
                continue

        # ---- ② 兵营（条件项）：编制缺口。**不看回本**（build_econ 给 per=0）----
        if cnt("兵营") < want_barr and slots(p) >= 3 and afford(p, "兵营") \
                and spend_ok("兵营"):
            if place(p, "兵营"):
                continue

        # ---- ③ 电厂（条件项）：**缺电才做**（不缺就永远不成立 → "够就停"）----
        if (power + gen_add) < need_pw + _dem_add + n_pw_new and afford(p, "木材能源厂") \
                and spend_ok("木材能源厂"):
            if build(p, "木材能源厂"):
                gen_add += BUILDINGS["木材能源厂"]["energy_out"]
                continue

        # ---- ④ ROI 项（选项）：这一格回本最快的那些 ----
        cand = [(pb, bn) for pb, bn, q in roi if q == p]
        if cand:
            _pb, bn = cand[0]
            place(p, bn)

    # ================================================================ 8. 扩张
    # 教条（用户口径）：**从不单兵作战**、**只满血推进**、**绕山地**。
    armies = [a for a in world.armies if a["owner"] == name and a["hp"] > 0]
    if not armies:
        return acts

    def tile_info(p):
        try:
            t = world._new_tile(*p, name)
            return t["terrain"], sum(t["resources"].values())
        except Exception:                            # noqa: BLE001
            return "平原", 0

    def dist_to(p):
        return min(max(abs(a["x"] - p[0]), abs(a["y"] - p[1])) for a in armies)

    targets = []
    for a in armies:
        for nb in world.neighbors(a["x"], a["y"]):
            if world.owned_by(*nb) is None and nb not in targets:
                targets.append(nb)
    if not targets:
        gs = [(g["x"], g["y"]) for g in world.armies if g["owner"] == "野人" and g["hp"] > 0]
        targets = sorted(gs, key=dist_to)[:12]
    # 越便宜（每格所需兵力少、资源多）越先打
    targets.sort(key=lambda p: (TROOPS_FOR.get(tile_info(p)[0], 3) / max(tile_info(p)[1], 1),
                                -tile_info(p)[1], dist_to(p)))

    used_ids: set = set()
    for (tx, ty) in targets[:8]:
        need_n = TROOPS_FOR.get(tile_info((tx, ty))[0], 3)
        near = [a for a in armies if a["id"] not in used_ids
                and not a.get("engaged") and a["hp"] >= ARMY_MAX_HP
                and max(abs(a["x"] - tx), abs(a["y"] - ty)) <= 1]
        if len(near) < need_n:
            movers = [a for a in armies if a["id"] not in used_ids
                      and not a.get("engaged") and a["hp"] >= ARMY_MAX_HP
                      and a.get("moved_turn") != world.turn]
            for a in movers[:need_n - len(near)]:
                # 绕山地：邻格是山地就不选它（山地行军亏、战斗也亏：守方 +50% 减伤）
                cands = [q for q in world.neighbors(a["x"], a["y"])
                         if world.owned_by(*q) is None and world.tile_terrain(*q) != "山地"]
                if not cands:
                    continue
                cur = max(abs(a["x"] - tx), abs(a["y"] - ty))
                step = min(cands, key=lambda q: max(abs(q[0] - tx), abs(q[1] - ty)))
                if max(abs(step[0] - tx), abs(step[1] - ty)) < cur:
                    do("move", {"army_id": a["id"], "x": step[0] + 1, "y": step[1] + 1},
                       world.move, name, a["id"], step[0], step[1])
                    used_ids.add(a["id"])
            near = [a for a in armies if a["id"] not in used_ids
                    and not a.get("engaged") and a["hp"] >= ARMY_MAX_HP
                    and max(abs(a["x"] - tx), abs(a["y"] - ty)) <= 1]
        if len(near) >= need_n:
            near.sort(key=lambda a: -a["hp"])
            grp = near[:need_n]
            used_ids.update(a["id"] for a in grp)
            do("attack", {"army_ids": [a["id"] for a in grp], "x": tx + 1, "y": ty + 1},
               world.attack, name, [a["id"] for a in grp], tx, ty)
    return acts
