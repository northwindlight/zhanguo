"""Persistent client assignments, retaining v11plus's global grouping objective."""
from typing import NamedTuple
from .parameters import V11_MAX_GROUP,V11_SOLVER_NODE_CAP,V11_SHORTFALL_PENALTY,V11_NEAR_TARGETS,MAX_CANDIDATES
from .pathfind import chebyshev,distance
from . import targeting,combat

class Candidate(NamedTuple):
    cell:tuple
    need:int


def _army_cost(knowledge,name,army,cell,mask,cache,memo=None):
    key=(army['id'],cell)
    if memo is not None and key in memo:return memo[key]
    value=distance(knowledge,army['kind'],(army['x'],army['y']),cell)
    if memo is not None:memo[key]=value
    return value


def _spread(members,memo=None):
    key=tuple(sorted(a['id'] for a in members))
    if memo is not None and key in memo:return memo[key]
    value=sum(chebyshev((a['x'],a['y']),(b['x'],b['y'])) for i,a in enumerate(members) for b in members[i+1:])
    if memo is not None:memo[key]=value
    return value


def _atom_cost(knowledge,name,members,cand,mask,cache,penalty,committed=0,ac_memo=None,sp_memo=None):
    reach=max(_army_cost(knowledge,name,a,cand.cell,mask,cache,ac_memo) for a in members)
    return float(_spread(members,sp_memo)+reach)+penalty*max(0,cand.need-len(members)-committed)

def _solve(knowledge, name: str, cands: list, armies: list[dict], mask, cache,
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
        near = sorted(cands, key=lambda c: (_army_cost(knowledge, name, me, c.cell, mask,
                                                       cache, ac_memo), c.cell))[:near_targets]
        for c in near:
            rest.sort(key=lambda aid: (_army_cost(knowledge, name, by_id[aid], c.cell, mask,
                                                  cache, ac_memo), aid))
            top = min(len(unassigned), V11_MAX_GROUP)
            for k in range(1, top + 1):
                mem = tuple(sorted((i,) + tuple(rest[:k - 1])))
                mems = [by_id[x] for x in mem]
                yield mem, c.cell, _atom_cost(knowledge, name, mems, c, mask, cache,
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
            if sub is None:                     # 剪枝分支不可能改善已求得的最优值。
                continue
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



def regroup(player,observation,state,armies,*,forecast=None):
    forecast=forecast or combat.forecaster(player,observation)
    cells=targeting.candidates(observation);live={u['id'] for u in armies}
    state.targets={ident:cell for ident,cell in state.targets.items() if ident in live and cell in cells}
    free=[u for u in armies if u['id'] not in state.targets]
    if free and cells:
        near=sorted(cells,key=lambda p:(min(chebyshev(p,(u['x'],u['y'])) for u in free),p))[:MAX_CANDIDATES]
        candidates=[]
        for cell in near:
            ordered=sorted(armies,key=lambda u:(chebyshev((u['x'],u['y']),cell),u['id']))
            need,_=combat.needed(player,cell,ordered[:V11_MAX_GROUP],forecast=forecast)
            candidates.append(Candidate(cell,need))
        allies,_=targeting.relations(observation)
        knowledge={'view':observation['facts']['view'],'allies':allies,'unit_catalog':player.query('unit_catalog')}
        committed={cell:sum(p==cell for p in state.targets.values()) for cell in cells}
        assignments,exact=_solve(knowledge,player.actor,candidates,free,None,{},V11_SOLVER_NODE_CAP,V11_NEAR_TARGETS,committed)
        state.exact=exact
        if not exact:
            # Complete every assignment when the exact solver exhausts its
            # explicit search budget; the saved flag reports the tradeoff.
            for unit in free:
                candidate=min(candidates,key=lambda c:(_atom_cost(knowledge,player.actor,[unit],c,None,{},V11_SHORTFALL_PENALTY,committed.get(c.cell,0)),c.cell))
                assignments[unit['id']]=candidate.cell;committed[candidate.cell]=committed.get(candidate.cell,0)+1
        state.targets.update(assignments)
    groups={}
    for ident,cell in sorted(state.targets.items()):groups.setdefault(cell,[]).append(ident)
    return sorted(groups.items())
