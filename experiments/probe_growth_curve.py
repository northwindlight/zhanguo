# -*- coding: utf-8 -*-
"""老师 v10 的**消费增长曲线**（默认 300 回合）。

用户 2026-09-13：「爬的慢，远不如扩张，你可以画一条 v10 300 回合的增长曲线」

用途是给一条**参照形状**：靠扩张滚起来的消费增长长什么样 ——
用来对照「原地建造刷消费」那条慢得多的路。

每回合记一次 `spend_total`（累计消费，= 支出法 GDP：建造+征兵+军费）+ 领地数。

用法：python experiments/probe_growth_curve.py [回合数] [地图边长] [seed]
"""
import sys
import random
from rl.env import ZhanguoEnv
import rl.bc as bc

TURNS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
MAP = int(sys.argv[2]) if len(sys.argv) > 2 else 16
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 0

env = ZhanguoEnv(map_size=MAP, max_turns=TURNS)
env.reset(SEED)
teacher = bc.get_teacher("v10", turns=TURNS)
# ★ROI 回收期窗口要按**本局**回合数设：老师默认按 500 回合规划，会去造一堆
#   局末才回本的楼（见 `bc.set_horizon` 的注释）。
bc.set_horizon(teacher, TURNS)

rng = random.Random(SEED)
curve = []
while True:
    t = env.world.turn
    teacher(env.world, env.agent, rng, max_actions=10 ** 9, on_action=lambda *a: None)
    curve.append((t, env.world.spend_total(env.agent),
                  len(env.world.own_tiles(env.agent))))
    env.world.resolve_turn()
    if t + 1 >= TURNS:
        break
    env.world.begin_turn()

print(f"老师 v10   {MAP}×{MAP} 图   {TURNS} 回合   seed {SEED}")
print(f"终局：消费 {curve[-1][1]:.0f}   领地 {curve[-1][2]}\n")

step = max(1, TURNS // 20)
print("  回合     累计消费    本回合增量    领地")
prev = 0.0
for t, sp, tl in curve:
    if t % step == 0 or t == TURNS - 1:
        print(f"  {t + 1:>4}   {sp:>10.0f}   {sp - prev:>10.0f}   {tl:>5}")
    prev = sp

# ---------------------------------------------------------------- ASCII 曲线
sp = [c[1] for c in curve]
mx = max(sp) or 1.0
W, H = 60, 16
cols = []
for i in range(W):
    a = int(i * len(sp) / W)
    b = max(a + 1, int((i + 1) * len(sp) / W))
    cols.append(max(sp[a:b]))          # 取每列峰值，别把尖顶抹平

print()
for r in range(H, -1, -1):
    if r % 4 == 0:
        head = f"{mx * r / H:>8.0f} |"
    else:
        head = " " * 8 + " |"
    print(head + "".join("█" if cols[i] / mx * H >= r else " " for i in range(W)))
print(" " * 8 + " +" + "-" * W)
print(" " * 9 + f"1{' ' * (W - 12)}{TURNS} 回合")
