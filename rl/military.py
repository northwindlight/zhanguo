# -*- coding: utf-8 -*-
"""沙盒的军事层：**抄 v11plus 的部件，自己写决策**。

    用户 2026-09-24 的口径
    ──────────────────────
    「我让你抄的是 v11plus 的**目标评估**、**编组逻辑**，谁让你一个字都不写了，
      直接拿一个半成品用?」

    ⇒ 所以本模块**不调** `ruleai.v11plus.military.run`（那是"整套拿一个半成品"，
      它只会平推：全局编组 → 能打就打 → 打不了就朝目标走一格，
      **既不会守、也不会侦察**，见 `docs/v11编组模型.md` §十「守土/驻防/撤退」留空）。

    抄（原样调用，它们是打磨过的、不该重写）
    ────────────────────────────────────
      · `v11plus.combat.assess`   —— **目标评估**：打这一格要几支 / 几轮 / 掉多少血
      · `v11plus.grouping._solve` —— **编组逻辑**：全局指派（记忆化 DP，`min Σ(spread+reach+罚×缺员)`）
      · `v11plus.pathfind`        —— 视野掩码 / 多回合代价场 / 本回合落点

    写（v11plus 没有的 —— 那正是它的短板，也是 RL 要学的东西）
    ────────────────────────────────────────────────────────
      ① **目标池**：对手市政厅（国祚，**公开**）· 对手领土（要视野）· 野地（可占）
         · ★**自家核心**（守家）· ★**侦察前沿**（视野外、朝敌一侧，走过去开图）
      ② **守家**：敌军逼近我家核心 ⇒ 编组时把守家目标抬价 ⇒ 求解器自然派人回防
      ③ **侦察**：池子里放"朝敌方向的未探明格"，军队走过去就把视野推开
      ④ **出手时机**：够得着的满血那几支喂给 `assess`，它说赢得下来就打

    ★ 守家与侦察都用**同一套机制**表达（抬价 / 加目标），不另造状态机 ——
      `_solve` 的目标函数是 `Σ(spread + reach + λ·缺员)`，**给它什么候选，它就派谁去哪**。
"""
from __future__ import annotations

from balance import (V11_FIELD_MAX_COST, V11_MAX_CANDIDATES, V11_NEAR_TARGETS,
                     V11_NEED_CAP, V11_ROUNDS_CAP, V11_SCAN_RADIUS,
                     V11_SHORTFALL_PENALTY, V11_SOLVER_NODE_CAP)
from game import unit_kind, unit_max_hp

from ruleai.v11plus import grouping, pathfind
from ruleai.v11plus.combat import assess


class Plan:
    """一条编好的决策：`cell` 是目标，`ids` 是认领它的军队。"""

    def __init__(self, cell, ids, d):
        self.cell = cell
        self.ids = tuple(ids)
        self.d = d                      # `combat.assess` 的 Difficulty（含 need/rounds/losses）


# ============================================================ ① 目标池（自己写的）
def targets(world, name: str, mask, *, enemy: str | None,
            defend: bool = True, scout: bool = True,
            scout_push: int = 2) -> list[tuple]:
    """候选目标格。**这一层 v11plus 完全没有** —— 它的池子只有"可攻格"。

    分五类（去重后按坐标排序，保证确定性）：

      · **对手市政厅**（国祚）：★ **公开**（`_public_buildings`），所以**不受视野限制**
        —— 这是"攻取国祚"能瞄准的前提（引擎原话：看不见就打不着）。
      · **对手领土**：视野内的交战敌国地（v11plus 的源③）。
      · **野地**：自家前沿未物化的格（v11plus 的源①，可占）。
      · ★ **自家核心**：`defend=True` 时放进去 —— 军队认领它就会**往回走**（守家）。
      · ★ **侦察前沿**：`scout=True` 时，把"自家领土朝敌方那一侧的、视野**外**的格"
        放进池子 —— 军队走过去就把视野推开（`scout_push` = 往外推几格）。
    """
    out: set = set()

    for cell, t in sorted(world.tiles.items()):
        if enemy and t["owner"] == enemy and t["buildings"].get("市政厅", 0) > 0:
            out.add(cell)                              # ★ 国祚：公开，不看视野
    for cell, t in sorted(world.tiles.items()):
        if cell in mask and t["owner"] not in (name, None) \
                and world.war_between(name, t["owner"]):
            out.add(cell)                              # 交战敌国领土（要视野）
    for cell in sorted(world.frontier_of(name)):
        if cell in mask:
            out.add(cell)                              # 可占野地

    if defend:
        for cell, t in sorted(world.tiles.items()):
            if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0:
                out.add(cell)                          # ★ 守家目标

    if scout:
        out |= _scout_cells(world, name, mask, enemy, push=scout_push)

    return sorted(out)[:V11_MAX_CANDIDATES]


def _scout_cells(world, name: str, mask, enemy: str | None, *, push: int) -> set:
    """★ 侦察前沿：自家地块中**朝敌那一侧**的、视野罩不到的格，往外推 `push` 格。

    口径保守：只在"自家已有地的邻域"里挑（不凭空指远方），所以军队是**一步步**把
    视野推出去的，不会为了侦察跑断腿。没有敌人（或敌人已亡）⇒ 不侦察。
    """
    if not enemy or enemy not in world.nations:
        return set()
    ecore = None
    for cell, t in sorted(world.tiles.items()):
        if t["owner"] == enemy and t["buildings"].get("市政厅", 0) > 0:
            ecore = cell
            break
    if ecore is None:
        return set()
    ex, ey = ecore
    out: set = set()
    frontier = world.frontier_of(name)
    for (x, y) in sorted(frontier):
        if (x, y) in mask:
            continue                                   # 已经看得见 ⇒ 不用派侦察
        # 只收"朝敌方向"的那一片：到敌方核心的切比雪夫距离比我家核心更近
        home = _home_of(world, name)
        if home is None:
            continue
        d_me = max(abs(x - home[0]), abs(y - home[1]))
        d_en = max(abs(x - ex), abs(y - ey))
        if d_en <= d_me:                               # 朝敌一侧
            out.add((x, y))
    return out


def _home_of(world, name: str) -> tuple[int, int] | None:
    for cell, t in sorted(world.tiles.items()):
        if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0:
            return cell
    return None


# ============================================================ ② 威胁评估（自己写的）
def threat_at_home(world, name: str, mask, *, enemy: str | None) -> int:
    """★ 家里有多危险：**看得见**的敌军里，离我家核心最近的有多近（切比雪夫）。

    返回 `99` = 视野内没有威胁。这个数用来决定"守家目标抬多少价"。
    """
    home = _home_of(world, name)
    if home is None or not enemy:
        return 99
    best = 99
    for a in world.armies:
        if a["owner"] != enemy or a.get("hp", 0) <= 0:
            continue
        if (a["x"], a["y"]) not in mask:               # ★ 看不见的敌人不算（迷雾纪律）
            continue
        d = max(abs(a["x"] - home[0]), abs(a["y"] - home[1]))
        best = min(best, d)
    return best


# ============================================================ ③ 一个回合（自己写的编排）
def run(world, name: str, *, enemy: str | None, max_actions: int = 10 ** 9,
        defend: bool = True, scout: bool = True, verbose: bool = False) -> list:
    """跑一个回合的军事动作，返回 `[(tool, args0based, ok, msg), …]`。

    编排（**部件全来自 v11plus，顺序与取舍是自己写的**）：

      1. **目标评估**：每个候选格喂 `combat.assess`（★抄）→ `need/rounds/empty/winnable`
      2. **威胁**：`threat_at_home` 看敌军离我家多近（★写）
      3. **编组**：`grouping._solve`（★抄）在候选上做全局指派 → `{军id: 目标格}`
      4. **出手 / 推进**：够得着且满血就打，否则用 `pathfind` 朝目标走一格（★抄）
    """
    mask = pathfind.vision_mask(world, name)
    armies = [a for a in world.troops if a["owner"] == name and a.get("hp", 0) > 0]
    pool = [a for a in armies
            if not a.get("engaged") and a.get("moved_turn") != world.turn]
    acts: list = []
    if not pool:
        return acts

    cells = targets(world, name, mask, enemy=enemy, defend=defend, scout=scout)
    if not cells:
        return acts

    # ---- 1. 目标评估（★抄 v11plus 的 assess）----
    cands = []
    for cell in cells:
        d = assess(world, name, cell, armies, visible=cell in mask,
                   need_cap=V11_NEED_CAP, rounds_cap=V11_ROUNDS_CAP)
        cands.append(grouping.Candidate(cell, d.need, d.rounds, d.empty, d.winnable,
                                        world.tile_terrain(*cell) if cell in mask else None,
                                        ()))
    diff_of = {c.cell: c for c in cands}

    # ---- 2. 威胁（★写）：家里越危险，守家目标越"便宜" ⇒ 求解器越愿意派人回去 ----
    near = threat_at_home(world, name, mask, enemy=enemy)
    if near <= 3 and defend:
        # ★ 家里告急：把池子**收缩**到「守家 + 侦察前沿」⇒ 求解器只能派兵往回走。
        #   为什么不用"加成"：`grouping._solve` 的目标函数是固定的（`Σ(spread+reach+λ·缺员)`），
        #   它**不收**外部权重 ⇒ 表达优先级只能靠「给它什么候选」，这也顺带不用改 v11plus 的代码。
        home = _home_of(world, name)
        keep = _scout_cells(world, name, mask, enemy, push=1)
        cells = [c for c in cells if c == home or c in keep]
        if not cells and home is not None:
            cells = [home]

    # ---- 3. 编组（★抄 v11plus 的全局指派求解器）----
    cache: dict = {}
    picked, exact = grouping._solve(
        world, name, cands, pool, mask, cache,
        V11_SOLVER_NODE_CAP, V11_NEAR_TARGETS, {})
    if not picked:
        return acts
    if verbose:
        print(f"[{name}] mask={len(mask)} 候选={len(cands)} 军={len(pool)} "
              f"威胁={near} exact={exact}")

    by_id = {a["id"]: a for a in pool}
    groups: dict = {}
    for aid, cell in sorted(picked.items()):
        groups.setdefault(cell, []).append(aid)

    reach: dict = {}
    used: set = set()

    def reach_of(a, *, for_attack: bool):
        key = (a["id"], for_attack)
        if key not in reach:
            reach[key] = world._reachable(name, a, for_attack=for_attack)
        return reach[key]

    home = _home_of(world, name)

    # ---- 4. 出手 / 推进（★抄 pathfind，取舍自己写）----
    for cell in sorted(groups):
        ids = [i for i in groups[cell] if i in by_id and i not in used]
        if not ids:
            continue
        members = [by_id[i] for i in ids]
        # 守家那一组：只要求「朝家走」，不主动打自家格
        is_home = (cell == home)
        able = [] if is_home else [
            a for a in members
            if a["hp"] >= unit_max_hp(a)
            and pathfind.marchable(world, name, a, cell, reach=reach_of(a, for_attack=True))]
        d = diff_of.get(cell)
        if able and d is not None and d.winnable and cell in mask and not _taken(world, name, cell):
            ok, msg = world.attack(name, [a["id"] for a in able], cell[0], cell[1])
            acts.append(("attack", {"army_ids": [a["id"] for a in able],
                                    "x": cell[0] + 1, "y": cell[1] + 1}, ok, msg))
            if ok:
                used.update(a["id"] for a in able)
                continue
        if _taken(world, name, cell):
            continue
        for a in members:
            if a["id"] in used:
                continue
            fld = pathfind.cost_field(world, name, {cell}, unit_kind(a), mask,
                                      max_cost=V11_FIELD_MAX_COST, cache=cache)
            nxt = pathfind.best_step(world, name, a, fld,
                                     reach=reach_of(a, for_attack=False), goal=cell)
            if nxt is None:
                continue
            ok, msg = world.move(name, a["id"], nxt[0], nxt[1])
            acts.append(("move", {"army_id": a["id"], "x": nxt[0] + 1, "y": nxt[1] + 1},
                         ok, msg))
    return acts


def _taken(world, name: str, cell) -> bool:
    """这一格已经不是能打的了（变成自己的/盟国的）。"""
    owner = world.owned_by(*cell)
    return owner is not None and (owner == name or world.allied_between(name, owner)
                                  or not world.war_between(name, owner))