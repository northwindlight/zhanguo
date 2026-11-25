import sys, statistics, itertools
from multiprocessing import Pool
import ruleai.v11plus.grouping as g
from rl.env import Env
from rl.opponents import rule_turn, make_w1

def game(args):
    start, mn, seed, seat, turns, size = args
    g.clear()
    p = make_w1(start, mn)
    opp = {0: p, 1: rule_turn} if seat == 0 else {0: rule_turn, 1: p}
    e = Env(size, 2, max_turns=turns, opponents=opp).reset(seed); e.advance()
    return (start, mn, e.winner() == seat, e.winner() is not None, e.rewards()[seat], e.world.turn)

if __name__ == "__main__":
    seeds, turns, size = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    cfgs = list(itertools.product((25, 40), (2, 3)))
    jobs = [(s, m, sd, seat, turns, size) for s, m in cfgs for sd in range(seeds) for seat in (0, 1)]
    with Pool(4) as p:
        rows = p.map(game, jobs)
    for s, m in cfgs:
        rr = [r for r in rows if r[0] == s and r[1] == m]
        print(f"start={s} min={m}: W1灭国胜 {sum(r[2] for r in rr)}/{len(rr)}  有人被灭 {sum(r[3] for r in rr)}  平均奖励 {statistics.mean(r[4] for r in rr):.3f}  平均终局回合 {statistics.mean(r[5] for r in rr):.0f}")
