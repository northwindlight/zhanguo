# -*- coding: utf-8 -*-
"""验机制最后一环：recruit / move 不在老师的计划里，是因为**前提不具备**，还是因为老师压根不排？

三档对照，同一个局面，只改「有没有兵营 / 有没有军队」：
  A 原样（学生局面的真实样子）
  B 给每块自己的地塞一座兵营
  C 在 B 之上再征一支兵
看计划里的 tool 组成怎么变。
"""
import sys, copy, random, collections
from rl.env import ZhanguoEnv
import rl.bc as bc

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 500000
env = ZhanguoEnv(map_size=16, max_turns=70)
env.reset(SEED)
w, me = env.world, env.agent
base = bc.get_teacher("v10", turns=70)

def plan_of(tag):
    rng = random.Random(SEED)
    w2 = copy.deepcopy(w)
    seq = []
    base(w2, me, rng, max_actions=10**9, on_action=lambda t, a: seq.append(t))
    c = collections.Counter(seq)
    print(f"  {tag:<28} 计划 {len(seq):>2} 条  {dict(c)}")
    return c

own = w.own_tiles(me)
print(f"局面：地 {len(own)}  军队 {len(w.nation_armies(me))}  "
      f"兵营 {sum(w.tiles[t]['buildings'].get('兵营',0) for t in own)}")
plan_of("A 原样")

for t in own:
    w.tiles[t]["buildings"]["兵营"] = max(1, w.tiles[t]["buildings"].get("兵营", 0))
print(f"  → 塞完兵营：兵营 {sum(w.tiles[t]['buildings'].get('兵营',0) for t in own)}")
plan_of("B 有兵营、没军队")

ok, msg = w.recruit(me, own[0][0], own[0][1], 1, "步")
print(f"  → 试着征兵：ok={ok}  msg={msg}   现在军队 {len(w.nation_armies(me))}")
if not ok:   # 电网停摆等硬规则挡住了，就直说
    print("     （征不上，说明这条路上还有一道引擎硬门）")
plan_of("C 有兵营 + 有军队")
