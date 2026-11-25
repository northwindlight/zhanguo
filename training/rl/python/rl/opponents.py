"""脚本对手：规则 AI（经济+军事）外加"什么时候开战"的规则。

规则 AI 本身只对**已处于战争**的敌国出兵，100 回合内基本不开战；好战版补上宣战决策：
- W1 好战：到第 `start` 回合起，只要自己有足够兵力就对最近的邻国宣战，之后规则 AI 的军事段自动进攻。
- W2 偷袭：先隐忍囤兵，兵力达到 `ratio`×对手估计兵力（或超过阈值）才突然宣战。
都放在 `rl/` 里，不碰 `ruleai/`（契约代码）。
"""
from __future__ import annotations

import random

import mp
import rule_ai

_, _rule = rule_ai.resolve("v11plus")


def rule_turn(w: mp.World, name: str, rng: random.Random) -> None:
    _rule(w, name, rng, max_actions=10 ** 9)


def rule_economy_only(w: mp.World, name: str, rng: random.Random) -> None:
    """只跑规则 AI 的经济层（建设/买卖/征兵预算），军事交给自己写的推进逻辑——
    否则规则 AI 的军事段会把军队调回去，和"朝敌都推进"来回拉扯。"""
    from ruleai.v11plus import economy
    from ruleai.v11plus.ledger import Ledger
    economy.run(Ledger(w, name, 10 ** 9, None, None), w, name)


def _dist(w: mp.World, a: str, b: str) -> float:
    ta, tb = w.own_tiles(a), w.own_tiles(b)
    if not ta or not tb:
        return 1e9
    return min(abs(x1 - x2) + abs(y1 - y2) for x1, y1 in ta for x2, y2 in tb)


def _can_declare(w: mp.World, a: str, b: str) -> bool:
    return (b in w.nations and b != a and not w.war_between(a, b) and w.active_truce(a, b) is None
            and w.entity_of(a) != w.entity_of(b)
            and not any(c["actor"] == a and c["action"] == "declare_war" for c in w.diplo_pending))


def _nearest_enemy(w: mp.World, name: str) -> str | None:
    cands = [n for n in w.alive() if _can_declare(w, name, n)]
    return min(cands, key=lambda n: _dist(w, name, n)) if cands else None


_BLOCKED = ("高山", "海洋")


def _bfs_next(w: mp.World, src: tuple[int, int], goals: set[tuple[int, int]]):
    """从 src 到最近目标格的最短路（四邻、避开高山/海洋）的第一步；到不了返回 None。"""
    from collections import deque
    if src in goals:
        return None
    prev = {src: None}
    q = deque([src])
    while q:
        cur = q.popleft()
        for nb in w.neighbors(*cur):
            if nb in prev or w.tile_terrain(*nb) in _BLOCKED:
                continue
            prev[nb] = cur
            if nb in goals:
                step = nb
                while prev[step] != src:
                    step = prev[step]
                return step
            q.append(nb)
    return None


def war_moves(w: mp.World, name: str) -> None:
    """已处于战争时：把所有能动的军队成堆推向敌方市政厅（拿下市政厅 = 灭国）。
    规则 AI 的军事段受视野门控，不认识远处的敌国，所以这里补上"朝敌都推进"。"""
    enemies = [e for e in w.alive() if w.war_between(name, e)]
    if not enemies:
        return
    goals: set[tuple[int, int]] = set()
    for e in enemies:
        halls = {(x, y) for (x, y) in w.own_tiles(e) if w.tiles[(x, y)]["buildings"].get("市政厅", 0)}
        goals |= halls or set(w.own_tiles(e))
    if not goals:
        return
    groups: dict[tuple[int, int], list[dict]] = {}
    for a in w.nation_armies(name):
        if a.get("moved_turn") == w.turn:
            continue
        groups.setdefault((a["x"], a["y"]), []).append(a)
    for (x, y), arms in groups.items():
        ids = [a["id"] for a in arms]
        if any(a.get("engaged") for a in arms):
            w.attack(name, ids, x, y)              # 交战中：原地继续打
            continue
        step = _bfs_next(w, (x, y), goals)
        if step is None:
            continue
        # 野地上的野人只打进攻方、不打驻留者：路过无主地直接 move，不用打；
        # 只有敌国领土 / 敌国军队所在格才需要 attack。
        hostile = (w.owned_by(*step) in enemies) or any(
            d["owner"] != "野人" and d["owner"] != name and (d["x"], d["y"]) == step and w.war_between(name, d["owner"])
            for d in w.armies)
        if hostile:
            w.attack(name, ids, *step)
        else:
            for i in ids:
                w.move(name, i, *step)


def boost_recruit(w: mp.World, name: str, cap: int = 2) -> None:
    """好战方多征兵：规则 AI 的经济只养 3–5 支军，打不下远处的敌都。
    只在补给有富余时再征（每支军要吃补给，征了养不起会饿死）。"""
    n_arm = len(w.nation_armies(name))
    for (x, y) in w.own_tiles(name):
        if cap <= 0:
            break
        if w.res(name, "补给") < 4 * n_arm + 6:
            break
        ok, _ = w.recruit(name, x, y, 1, "步")
        if ok:
            cap -= 1
            n_arm += 1


def make_w1(start: int = 25, min_armies: int = 2):
    """好战：第 start 回合后、有 ≥ min_armies 支兵就宣战；战争结束后（议和/休战到期）再找下一个。"""
    def w1(w, name, rng):
        if w.turn >= start and not w.at_war(name) and len(w.nation_armies(name)) >= min_armies:
            tgt = _nearest_enemy(w, name)
            if tgt is not None:
                w.declare_war(name, tgt)
        (rule_economy_only if w.at_war(name) else rule_turn)(w, name, rng)
        if w.turn >= start - 15:
            boost_recruit(w, name)
        war_moves(w, name)
    w1.__name__ = f"W1(start={start},min={min_armies})"
    return w1


def make_w2(ratio: float = 1.5, min_armies: int = 4, start: int = 20):
    """偷袭：自己兵力 ≥ ratio × 目标兵力（且 ≥ min_armies）才宣战。兵力是真实值（对手 AI 也看得见同样的信息量级）。"""
    def w2(w, name, rng):
        if w.turn >= start and not w.at_war(name):
            tgt = _nearest_enemy(w, name)
            mine = len(w.nation_armies(name))
            if tgt is not None and mine >= min_armies and mine >= ratio * max(1, len(w.nation_armies(tgt))):
                w.declare_war(name, tgt)
        (rule_economy_only if w.at_war(name) else rule_turn)(w, name, rng)
        war_moves(w, name)
    w2.__name__ = f"W2(ratio={ratio},min={min_armies})"
    return w2
