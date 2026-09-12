# -*- coding: utf-8 -*-
"""(1) 同 seed 的两局 BC 是不是**逐条相同**（专家的 B 论证）
   (2) DAgger 里老师计划中的 build 到底是哪些楼（专家说"缺的那一环"）"""
import sys, collections, copy, random, hashlib
import rl.bc as bc
from rl.env import ZhanguoEnv

SEED, TURNS = 2000, 70
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
teacher = bc.get_teacher("v10", turns=TURNS)

def sig(demos):
    out = []
    for o, i, g, _w in demos:
        a = o.cand["actions"][i]
        out.append(f"{a.kind}|{a.sub}|{a.tile}|{a.amount}|{a.army}")
    return out

print("=== (1) 同 seed 跑两遍纯 BC，标签序列是否逐条相同 ===")
sigs = []
for k in range(2):
    demos, spend, miss = bc.collect_episode(env, turns=TURNS, seed=SEED, teacher_fn=teacher,
                                            with_window=False)
    s = sig(demos)
    sigs.append(s)
    h = hashlib.sha1("\n".join(s).encode()).hexdigest()[:12]
    print(f"  第{k+1}遍：样本 {len(s)}  标签指纹 {h}  老师消费 {spend:,.0f}")
print(f"  ⇒ 两遍逐条相同？ {'★是，完全一样' if sigs[0] == sigs[1] else '否，有差异'}")
if sigs[0] != sigs[1]:
    diff = [(x, y) for x, y in zip(sigs[0], sigs[1]) if x != y]
    print(f"     差异 {len(diff)} 条，前 3：{diff[:3]}")

print("\n=== (2) DAgger 里老师计划中的 build 是哪些楼 ===")
base = bc.get_teacher("v10", turns=TURNS)
cnt = collections.Counter(); nb_barracks = 0; n_plans = 0
def wrapped(world, agent, rng, max_actions=10**9, on_action=None, on_result=None):
    global nb_barracks, n_plans
    seq = []
    base(world, agent, rng, max_actions=max_actions, on_action=lambda t, a: seq.append((t, a)))
    n_plans += 1
    for tool, args in seq:
        if tool == "build":
            b = str(args.get("building"))
            cnt[b] += 1
            if b == "兵营":
                nb_barracks += 1
    if on_action:
        for x in seq: on_action(*x)
    return None

demos, spend, miss = bc.collect_episode(env, turns=TURNS, seed=SEED, teacher_fn=wrapped,
                                        student=None, with_window=False)
print(f"  纯 BC 局 {n_plans} 回合的计划里，build 明细：{dict(cnt.most_common())}")
print(f"  ⇒ 老师计划里出现「兵营」的次数：{nb_barracks}")
