import sys, statistics, time
import ruleai.v11plus.grouping as g
from rl.env import Env
from rl.opponents import rule_turn, make_w1, make_w2

def duel(p0, p1, seed, size=13, turns=300):
    g.clear()
    e = Env(size, 2, max_turns=turns, opponents={0: p0, 1: p1}).reset(seed)
    e.advance()
    return e

def main(n=8, turns=300, size=13):
    pols = {"W1": make_w1(), "W2": make_w2()}
    for nm, p in pols.items():
        wins = elim = 0; rs = []; turns_used = []; wars = []
        for sd in range(n):
            for seat in (0, 1):
                a = (p, rule_turn) if seat == 0 else (rule_turn, p)
                t0 = time.time(); e = duel(a[0], a[1], sd, size, turns)
                w = e.winner(); rs.append(e.rewards()[seat]); turns_used.append(e.world.turn)
                wins += (w == seat); elim += (w is not None)
        print(f"{nm} vs 规则AI: 灭国胜 {wins}/{2*n}, 被灭 {elim-wins}, 平均奖励 {statistics.mean(rs):.3f}, 平均终局回合 {statistics.mean(turns_used):.0f}", flush=True)

if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 13)
