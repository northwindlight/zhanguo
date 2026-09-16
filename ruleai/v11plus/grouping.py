# -*- coding: utf-8 -*-
"""编组：**以军队为决策变量的全局编组**（取代"排榜 + 逐个领兵"）。

用户口径（2026-09-15，设计与推导见 `docs/v11编组模型.md`）：

  · **只有三个指标** —— 组内距离最小、全体到目标最近、目标反过来决定成员数量；
  · **重新编组只发生在目标消失时**：此时**全体无目标的军**一起重编，
    有目标的组直到目标被拿下才重编；
  · **任何军队一定有目标**，因为候选池是**全局**的（不是"军队旁边那一圈"）。

「组」不是独立实体 —— **组 = `target` 的等价类**（target 相同的那些军就是一组）。
所以唯一的状态就是一张 `军 id → 目标格` 的表。

## 为什么不能贪心

每一格的最优成员都是"离它最近的 `n_j` 支"，而同一支军常常是好几个目标的最近几支
——**目标之间抢兵**。按榜序逐格从池子里取（先到的先抢、后到的拿残渣）总行程显著劣化。
本模块按用户的三个指标做**全局最优划分**：`min Σ [spread(S_j) + reach(S_j, j)]`，
约束是"每组恰好 `n_j` 人、格与军都互斥、**全体覆盖**"。

## 状态放哪（用户 2026-09-15 定）

**放规则 AI 模块内存**，不进存档 ⇒ 不动 `SAVE_VERSION`/`SAVE_KEYS`。
代价（`docs/v11编组模型.md` §七 写明）：读档续局会丢编组，续局后的第一回合会做一次
全局重编 —— 结果仍确定可复现，只是与"一口气跑"不同。**RL 每局开始必须 `clear()`**。
"""
from __future__ import annotations

from typing import NamedTuple

from balance import (V11_FIELD_MAX_COST, V11_MAX_CANDIDATES, V11_MAX_GROUP,
                     V11_NEAR_TARGETS, V11_SHORTFALL_PENALTY, V11_SOLVER_NODE_CAP)
from game import unit_kind

from .combat import assess
from .pathfind import chebyshev, cost_field

# ---------------------------------------------------------------- 模块状态
_STATE: dict[str, dict[int, tuple[int, int]]] = {}      # 国名 → {军 id: 目标格}


def clear(name: str | None = None) -> None:
    """清空编组状态（`None` = 全清）。**RL 每局开始必须调**，否则上一局的编组会漏进来。"""
    if name is None:
        _STATE.clear()
    else:
        _STATE.pop(name, None)


def targets_of(name: str) -> dict[int, tuple[int, int]]:
    """当前编组（拷贝）——探针/测试用。"""
    return dict(_STATE.get(name, {}))


def target_of(name: str, aid: int) -> tuple[int, int] | None:
    """这一军当前的目标（没有 = `None`，只可能出现在"当回合还没编组"的瞬间）。"""
    return _STATE.get(name, {}).get(aid)


class Group(NamedTuple):
    """一支编好的队 = 一个目标 + 认领它的那几支军。"""
    cell: tuple            # 目标格
    ids: tuple             # 认领它的军 id（升序）
    need: int              # `n_j`：打赢要几支（判定式算出来的，不是常数）
    empty: bool            # 看得见的空目标（走进去就占地）
    winnable: bool         # 当前兵力够不够赢
    cost: float            # 本组在目标函数里的代价 = spread + reach


# ---------------------------------------------------------------- 距离与代价
def _army_cost(world, name: str, army: dict, cell: tuple, mask, cache,
               memo: dict | None = None) -> int:
    """这一军**还需要多少移动力**才到得了 `cell`（多回合，逐格地形代价）。

    走代价场（以 `cell` 为源反向铺，截断在 `V11_FIELD_MAX_COST`）；场够不到（离得太远）
    就退回**切比雪夫距离** —— 与 `pathfind.best_step` 的兜底同口径：先按直线走，
    走进场的半径之后再交给地形代价。

    `memo`（可选）= `{(军 id, 格): 代价}` —— **只在一次 `_solve` 内部用**。
    为什么它是对的：求解器只读世界（一格不动、一兵不移），所以同一对
    (军, 格) 的答案在一次求解里是常数。实测（40x40、第 120 回合）：
    一次求解问 43,995 次，**去重后只有 1,646 对**（96% 是重复问同一个问题）。
    """
    if memo is not None:
        key = (army["id"], cell)
        got = memo.get(key)
        if got is not None:
            return got
    fld = cost_field(world, name, {cell}, unit_kind(army), mask,
                     max_cost=V11_FIELD_MAX_COST, cache=cache)
    got = fld.get((army["x"], army["y"]))
    out = got if got is not None else chebyshev((army["x"], army["y"]), cell)
    if memo is not None:
        memo[key] = out
    return out


def _spread(members: list[dict], memo: dict | None = None) -> int:
    """组内距离：**两两切比雪夫距离之和**（`V11_MAX_GROUP` 军以内，O(n²) 无所谓）。

    ★ 为什么是"和"而不是"平均"或"直径"：和会**随人数增长**，于是目标函数天然偏向
      "多开几条战线、每线少带人" —— 而这正是扩张效率要的（并行拿地）。
      平均值会把这个倾向抹掉，直径则容易被单个飞将主导。

    `memo`（可选）= `{成员 id 元组: 和}` —— 同一次求解内按**成员集合**记忆
    （两两距离之和与成员顺序、与谁先谁后无关 ⇒ 用 id 元组当键是准的；
    位置在一次求解里不变 ⇒ 值是常数）。实测重复率 97.5%。
    """
    key = None
    if memo is not None:
        key = tuple(sorted(m["id"] for m in members))
        got = memo.get(key)
        if got is not None:
            return got
    out = 0
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            out += chebyshev((members[i]["x"], members[i]["y"]),
                             (members[j]["x"], members[j]["y"]))
    if memo is not None:
        memo[key] = out
    return out


# ---------------------------------------------------------------- 候选目标
class Candidate(NamedTuple):
    cell: tuple
    need: int
    rounds: int
    empty: bool
    winnable: bool
    terrain: str | None
    field_cache_key: tuple


def candidates(world, name: str, mask, armies: list[dict], *, radius: int,
               need_cap: int, rounds_cap: int, cache: dict) -> list[Candidate]:
    """可攻的目标池（**全局**：视野内全部，不是军队旁边那一圈）。

    池子由 `targeting.candidates` 铺（野地 / 野人驻守格 / 交战敌国格，按坐标排序），
    这里再补上每个目标的 `n_j`（判定式）与地形。

    ★ **不排除"已经有编组在打的格"**：新征的兵、刚打完仗的兵要能**并进**那一组
      （那一格要 2 支、现在只到了 1 支，第 3 支就该往那儿走）。排除掉它，等于逼每支新兵
      各自认领一个**新**目标 ⇒ 一支军打一块要 2 支的野地 ⇒ 谁也不动手。
      真发生过：300 回合只有 11 次移动、0 次进攻、领土停在 5 格。
      "认领互斥"只体现为**一格里已有的兵力**（见 `_solve` 的 `committed`），
      不是"这格不许别人来"。
    """
    from . import targeting
    out = []
    for cell in targeting.candidates(world, name, mask, radius=radius,
                                    limit=V11_MAX_CANDIDATES):
        d = assess(world, name, cell, armies, visible=cell in mask,
                   need_cap=need_cap, rounds_cap=rounds_cap)
        out.append(Candidate(cell, d.need, d.rounds, d.empty, d.winnable,
                             world.tile_terrain(*cell) if cell in mask else None, ()))
    return out


def _atom_cost(world, name, members: list[dict], cand: Candidate, mask, cache,
               penalty: float, committed: int = 0, ac_memo=None, sp_memo=None) -> float:
    """一个候选组的代价 = `spread + reach + 罚×缺员`。

    罚项是**目标函数的一部分**（不是兜底）："给这一格派的人不够 `n_j`"意味着
    这一仗打不下来 —— 那几支兵只是朝那儿走（下回合人齐了再打）。它必须吃亏，
    否则求解器会到处派"打不动的小队"来省路。罚得比任何路程都大
    （`V11_SHORTFALL_PENALTY`）⇒ **能打下来永远优先于省路**。
    """
    reach = max(_army_cost(world, name, a, cand.cell, mask, cache, ac_memo)
                for a in members)
    # ★ `committed` = **已经有编组在打这一格**的兵力（状态里查得到）：它们也在往那儿走，
    #   所以要算进"够不够 n_j"，否则新兵会以为这格还差 2 支、跑去另开一块。
    short = max(0, cand.need - len(members) - committed)
    return float(_spread(members, sp_memo) + reach) + penalty * short


# ---------------------------------------------------------------- 全局求解
def _solve(world, name: str, cands: list, armies: list[dict], mask, cache,
           node_cap: int, near_targets: int, committed: dict | None = None) -> tuple[dict, bool]:
    """**全局最优指派**：每支军认领一个目标，`min Σ 组代价`。

    返回 `({军 id: 目标格}, 是否在预算内拿到最优)`。

    ★★ 模型（用户 2026-09-15：「全体无解是求解器的问题，而不是应该兜底」）：

      **这不是"划分成若干恰好 n_j 人的组"，而是"每支军都认领一个目标"** ——
      同一个目标下的那些军就是一组；某个目标**够 `n_j` 支**才打得下来（不够就只是
      朝它走、下回合人齐了再打，罚项让它吃亏）。这样**结构上永远有解**：
      只要还有一支军没认领，就给它一个目标（最坏情况是单独朝最近的格走）。
      "目标决定成员数量"仍然成立 —— `n_j` 是**该目标能不能打**的门槛。

    ★ 状态 = 还没认领的军（记忆化 DP）：固定取**最小的未认领军** `i`，
      枚举"给 `i` 认领哪个目标"（只考虑离 `i` 最近的 `near_targets` 个候选格 ——
      更远的目标在目标函数里本来就不会赢），并顺手带上几个离那个目标最近的同伴
      （0～`V11_MAX_GROUP-1` 个）。所以每一步都**必然覆盖 `i`** ⇒ 递归必然到底 ⇒ 无解不存在。

    ★ 预算（`node_cap`）只用来兜"局面特别大"，超了如实返回 `exact=False`，**不假装最优**。
    """
    by_id = {a["id"]: a for a in armies}
    committed = committed or {}
    memo: dict = {(): 0.0}          # 空集也要进 memo：回溯时直接查 `memo[rest]`
    nodes = [0]
    # ★ 2026-09-16：两张**求解内**记忆表（求解器只读世界 ⇒ 答案在一次求解里是常数）
    #   实测：`_army_cost` 43,995 次调用里 96% 是重复问题，`_spread` 97.5%
    ac_memo: dict = {}
    sp_memo: dict = {}

    def options(i: int, unassigned: tuple):
        """给军 `i` 的候选方案：`(成员元组, 目标格, 代价)` —— 必然包含 `i`。"""
        me = by_id[i]
        rest = [x for x in unassigned if x != i]
        near = sorted(cands, key=lambda c: (_army_cost(world, name, me, c.cell, mask,
                                                       cache, ac_memo), c.cell))[:near_targets]
        for c in near:
            rest.sort(key=lambda aid: (_army_cost(world, name, by_id[aid], c.cell, mask,
                                                  cache, ac_memo), aid))
            top = min(len(unassigned), V11_MAX_GROUP)
            for k in range(1, top + 1):
                mem = tuple(sorted((i,) + tuple(rest[:k - 1])))
                mems = [by_id[x] for x in mem]
                yield mem, c.cell, _atom_cost(world, name, mems, c, mask, cache,
                                              V11_SHORTFALL_PENALTY,
                                              committed.get(c.cell, 0), ac_memo, sp_memo)

    def best_from(unassigned: tuple) -> float:
        """把这批军全部认领完的**最小代价**。"""
        if not unassigned:
            return 0.0
        got = memo.get(unassigned)
        if got is not None:
            return got
        nodes[0] += 1
        if nodes[0] > node_cap:
            raise _Budget
        i = unassigned[0]
        best = float("inf")
        for mem, cell, cost in options(i, unassigned):
            if cost >= best:
                continue
            rest = tuple(x for x in unassigned if x not in mem)
            sub = best_from(rest)
            if sub < float("inf"):
                best = min(best, cost + sub)
        memo[unassigned] = best
        return best

    try:
        best_from(tuple(sorted(by_id)))
    except _Budget:
        return {}, False                        # 预算内没算完：如实说"不是最优"
    out: dict = {}
    unassigned = tuple(sorted(by_id))
    while unassigned:
        i = unassigned[0]
        pick = None
        for mem, cell, cost in options(i, unassigned):
            rest = tuple(x for x in unassigned if x not in mem)
            sub = memo.get(rest)
            if sub is None:                     # 剪枝时没展开过 ⇒ 现在补算（记忆化，很便宜）
                sub = best_from(rest)
            key = (cost + sub, cost, cell, len(mem))
            if pick is None or key < pick[0]:
                pick = (key, mem, cell, rest)
        _, mem, cell, rest = pick
        for x in mem:
            out[x] = cell
        unassigned = rest
    return out, True


class _Budget(Exception):
    pass


# ---------------------------------------------------------------- 状态机
def refresh(world, name: str, mask, armies: list[dict]) -> list[int]:
    """状态机转移：**目标消失** ⇒ 那一组解散，成员变"无目标"。

    目标算"已消失"的三种情形：
      · 已是自家的/盟国的（拿下了）；
      · 已经不是可攻格（中立化/被盟友占/野地上有非敌非盟在打野）；
      · 格上已有别人在打（引擎不让插足，`attack` 会拒）。
    **交战中的军**不参与：它的目标钉在**脚下那格**（它动不了也改不了攻），
    等战斗结束再放回池子 —— 这样"任何军都有目标"不因交战而破例。
    """
    st = _STATE.setdefault(name, {})
    freed: list[int] = []
    for a in armies:
        aid = a["id"]
        if a.get("engaged"):
            st[aid] = (a["x"], a["y"])          # 钉在战场上
            continue
        cell = st.get(aid)
        if cell is None:
            continue
        owner = world.owned_by(*cell)
        if owner == name or (owner is not None and world.allied_between(name, owner)):
            st.pop(aid, None)
            freed.append(aid)
            continue
        if not _attackable(world, name, cell, mask):
            st.pop(aid, None)
            freed.append(aid)
    return freed


def _attackable(world, name: str, cell: tuple, mask) -> bool:
    """这一格现在还能不能打（与引擎 `attack` 的墙判定同口径）。"""
    if cell not in mask:
        return False
    if world.tile_terrain(*cell) is None:       # 防御性：拿不到地形就当不可攻
        return False
    owner = world.owned_by(*cell)
    if owner is not None:
        return owner != name and not world.allied_between(name, owner) \
            and world.war_between(name, owner)
    busy = [a for a in world.armies
            if (a["x"], a["y"]) == cell and a.get("engaged")]
    if not busy:
        return True
    return any(world.war_between(name, a["owner"]) or world.allied_between(name, a["owner"])
               for a in busy)


def regroup(world, name: str, mask, armies: list[dict], *, radius: int,
            need_cap: int, rounds_cap: int, cache: dict,
            node_cap: int | None = None) -> tuple[list[Group], bool]:
    """**无目标的军**一起做一次全局编组 → 写进状态，返回 `(编好的组, 是否全局最优)`。

    只有"无目标"的军参与（有目标的组不动），候选池排除已被认领的格 ⇒ 这就是用户要的
    "只在目标消失时重编、且只重编无目标的那些军"。
    """
    st = _STATE.setdefault(name, {})
    aim = sorted(a["id"] for a in armies if a["id"] not in st)
    if not aim:
        return [], True
    by_id = {a["id"]: a for a in armies}
    pool = [by_id[i] for i in aim]

    cands = candidates(world, name, mask, armies, radius=radius,
                       need_cap=need_cap, rounds_cap=rounds_cap, cache=cache)
    if not cands:
        return [], True                         # 一个可攻格都没有（边境为空 ⇒ 无事可做）

    # 已经有编组在打的格 → 各已有几支兵（新兵据此决定"并进去"还是"另开一块"）
    committed: dict = {}
    for aid, cell in st.items():
        if aid in by_id:
            committed[cell] = committed.get(cell, 0) + 1
    picked, exact = _solve(world, name, cands, pool, mask, cache,
                           node_cap or V11_SOLVER_NODE_CAP, V11_NEAR_TARGETS, committed)
    if not picked:
        return [], exact                        # 超预算：这一回合没编成，下回合重编

    by_cell: dict = {}
    for aid, cell in sorted(picked.items()):
        st[aid] = cell
        by_cell.setdefault(cell, []).append(aid)
    need_of = {c.cell: c.need for c in cands}
    out = [Group(cell, tuple(ids), need_of.get(cell, len(ids)), False, True, 0.0)
           for cell, ids in sorted(by_cell.items())]
    return out, exact