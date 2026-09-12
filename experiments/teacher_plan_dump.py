# -*- coding: utf-8 -*-
"""验机制：DAgger 里老师的**整回合计划**到底长什么样。

每回合在学生的局面副本上问一次 v10，把这份计划原样记下来：
  - 计划里有几条动作、分别是什么 tool
  - 排计划那一刻，学生的世界里有没有兵营 / 军队
  - 学生这一回合实际走了几步（决定计划里第 k 条能不能被贴成标签）
"""
import sys, collections, copy, random, torch
import rl.bc as bc
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act as _act

CKPT, SEED, TURNS = sys.argv[1], int(sys.argv[2]), 70
env = ZhanguoEnv(map_size=16, max_turns=TURNS); env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"]); m.eval()
base = bc.get_teacher("v10", turns=TURNS)

plans = []
def wrapped(world, agent, rng, max_actions=10**9, on_action=None):
    seq = []
    def rec(tool, args):
        seq.append((tool, args))
        if on_action: on_action(tool, args)
    base(world, agent, rng, max_actions=max_actions, on_action=rec)
    # 排计划时的世界快照特征
    barr = sum(1 for (x, y) in world.own_tiles(agent) if world.tiles[(x, y)]["buildings"].get("兵营", 0) > 0)
    plans.append({"turn": world.turn, "tools": [t for t, _ in seq], "barracks": barr,
                  "armies": len(world.nation_armies(agent)), "tiles": len(world.own_tiles(agent))})
    return None

bc.get_teacher_orig = base
# collect_episode 每回合调 teacher_fn(w2, ...)；我们把 wrapped 传进去
demos, spend, miss = bc.collect_episode(env, turns=TURNS, seed=SEED, teacher_fn=wrapped,
                                        student=m, endturn_cap=1, with_window=True)
print(f"DAgger 局：样本 {len(demos)}  消费 {spend:,.0f}  未匹配 {miss}  回合 {len(plans)}")
tc = collections.Counter(t for p in plans for t in p["tools"])
print(f"\n老师计划里的动作（70 份计划合计）：{dict(tc.most_common())}")
print(f"含 recruit 的计划份数：{sum(1 for p in plans if 'recruit' in p['tools'])} / {len(plans)}")
print(f"排计划时有兵营的回合：{sum(1 for p in plans if p['barracks'] > 0)} / {len(plans)}")
print(f"排计划时有军队的回合：{sum(1 for p in plans if p['armies'] > 0)} / {len(plans)}")
print("\n  回合  兵营 军队 地  计划（前 8 条）")
for p in plans[:14]:
    print(f"  {p['turn']:>4}  {p['barracks']:>3} {p['armies']:>4} {p['tiles']:>3}  {p['tools'][:8]}")
print("  ...")
for p in plans[-6:]:
    print(f"  {p['turn']:>4}  {p['barracks']:>3} {p['armies']:>4} {p['tiles']:>3}  {p['tools'][:8]}")
