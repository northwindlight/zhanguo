# -*- coding: utf-8 -*-
"""v11 的**军事层**：编组 → 评估难度 → 寻路 → 出手。

★ **它不该知道经济层的存在**（用户 2026-09-15：经济层与军事层分离）：
  只共享**一本动作账**（`ruleai.Ledger`）与 `world`。产兵是经济层的事
  （用户：「产兵由经济引擎决定」），本层只管**已经存在的兵**怎么用。

模型见 `docs/v11编组模型.md`。三条口径（用户 2026-09-15）：

  · **只在目标消失时重编**，且只重编"无目标的那些军"（状态在 `grouping` 模块内存里）；
  · **任何军队一定有目标** ⇒ 没有"待命"这个状态；
  · **人数由判定式算**（`combat.assess`），不是常数 ⇒ 没有 `MIN_SQUAD`。

执行守引擎的两条硬规矩：
  · 落点一律从 `world._reachable`（引擎合法集）里选 ⇒ 结构上不可能撞墙烧额度；
  · 引擎的 `attack` 是**原子**的（任一支到不了就整通全废）⇒ 出手前逐支复核。
"""
from __future__ import annotations

from balance import (V11_FIELD_MAX_COST, V11_MAX_ATTACKS, V11_NEED_CAP,
                     V11_ROUNDS_CAP, V11_SCAN_RADIUS)
from .combat import assess
from game import unit_kind, unit_max_hp

from . import grouping, pathfind


def run(ledger, world, name: str) -> None:
    """把一个回合的军事动作追加进 `ledger`（经济层已经花掉它那份额度）。"""

    # ============================================================ 8. 扩张（★ v11：全局编组）
    # 模型见 `docs/v11编组模型.md`。三条口径（用户 2026-09-15）：
    #   · 编组是**全局**的：三个指标（组内距离最小 / 全体到目标最近 / 目标定人数）；
    #   · **只在目标消失时重编**，且只重编"无目标的那些军"（状态在 `grouping` 模块内存里）；
    #   · **任何军队一定有目标** ⇒ 没有"待命"这个状态。
    # 执行仍然守引擎的两条硬规矩：
    #   · 落点一律从 `world._reachable`（引擎合法集）里选 ⇒ 结构上不可能撞墙烧额度；
    #   · 引擎的 `attack` 是**原子**的（任一支到不了就整通全废）⇒ 出手前逐支复核。
    acts = ledger.acts
    do = ledger.do
    max_actions = ledger.max_actions
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