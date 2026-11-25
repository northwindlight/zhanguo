"""Goodhart 检验（plan.md §2.3）：构造几种"刷分/摆烂"策略，确认它们在终局国力分上低于正常规则 AI。

通过标准：每个刷分策略的平均奖励 r（按第一名比例）显著 < 规则 AI 对规则 AI 的平均奖励（≈ 0.5–1.0 区间的对称值），
且**不能靠它拿到灭国胜**。
"""
from __future__ import annotations

import statistics
import sys

import mp
import rule_ai
from rl.env import Env

_, _rule = rule_ai.resolve("v11plus")


def rule_ai_turn(w, name, rng):
    _rule(w, name, rng, max_actions=10 ** 9)


def idle(w, name, rng):
    """龟缩：什么都不做。"""


def burn(w, name, rng):
    """烧资本：把能卖的全卖掉换金，再把金全部花在征兵上（只为消费/军事数字，不建设）。"""
    for g in mp.TRADEABLE:
        n = w.res(name, g)
        if g not in ("粮食", "装备") and n > 0:
            w.sell(name, g, int(n))
    for (x, y) in w.own_tiles(name):
        for _ in range(3):
            ok, _m = w.recruit(name, x, y, 1, "民")
            if not ok:
                break


def hoard(w, name, rng):
    """清仓囤金：把全部库存卖成金后不再投资（金不增值）。"""
    for g in mp.TRADEABLE:
        n = w.res(name, g)
        if n > 0:
            w.sell(name, g, int(n))


def land_only(w, name, rng):
    """只买地不设防：只做扩张进攻，不建设。这里用规则 AI 的军事段近似难以拆，改为只招民兵并外出占地。"""
    for (x, y) in w.own_tiles(name)[:2]:
        w.recruit(name, x, y, 1, "民")
    for a in list(w.nation_armies(name)):
        for (nx, ny) in w.neighbors(a["x"], a["y"]):
            if w.owned_by(nx, ny) is None:
                ok, _ = w.attack(name, [a["id"]], nx, ny)
                if ok:
                    break


POLICIES = {"规则AI": rule_ai_turn, "龟缩": idle, "烧资本": burn, "清仓囤金": hoard, "只占地": land_only}


def duel(p0, p1, seed, size=13, turns=150):
    e = Env(size, 2, max_turns=turns, opponents={0: p0, 1: p1}).reset(seed)
    import ruleai.v11plus.grouping as g
    g.clear()
    e.advance()
    return e.rewards(), e.winner()


def run(seeds=range(6), turns=150):
    base = {}
    out = {}
    for name, pol in POLICIES.items():
        rs, wins = [], 0
        for sd in seeds:
            # 座位互换：策略各坐 0、1 座一次
            r0, w0 = duel(pol, rule_ai_turn, sd, turns=turns)
            r1, w1 = duel(rule_ai_turn, pol, sd, turns=turns)
            rs += [r0[0], r1[1]]
            wins += (w0 == 0) + (w1 == 1)
        out[name] = (statistics.mean(rs), wins, len(rs))
        print(f"{name}: 对规则AI 平均奖励 {out[name][0]:.3f}  灭国胜 {wins}/{len(rs)}", flush=True)
    return out


if __name__ == "__main__":
    run(range(int(sys.argv[1]) if len(sys.argv) > 1 else 6), int(sys.argv[2]) if len(sys.argv) > 2 else 150)
