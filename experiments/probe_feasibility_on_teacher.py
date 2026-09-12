# -*- coding: utf-8 -*-
"""在**老师自己的轨迹上**（完全在训练分布内）边走边判：
模型的 argmax 有多少是**引擎会拒**的？

- 高 ⇒ 连在见过的状态上都不会判"买得起" ⇒ **谓词没学会**（表示/训练问题）
- 低 ⇒ 分布内是好的，坏在复合误差 ⇒ **分布漂移问题**
用 deepcopy 的真引擎判定（不是近似）。
"""
import sys, copy, random, collections, torch
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import forward_batch, _one_step
import rl.bc as bc

CKPT, SEED, TURNS = sys.argv[1], int(sys.argv[2]), 70
env = ZhanguoEnv(map_size=16, max_turns=TURNS); env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"]); m.eval()
teacher = bc.get_teacher("v10", turns=TURNS)

stat = collections.Counter()
def on_action(tool, args):
    obs = env._obs()
    lg, _v, cm = forward_batch(m, [_one_step(obs)], [tokenize(env, obs)])
    p = int(lg[0].masked_fill(~cm[0], -1e9).argmax())
    a = obs.cand["actions"][p]
    key = f"build:{a.sub}" if a.kind == "build" else a.kind
    stat["总步数"] += 1
    # 用真引擎判可行性（deepcopy 一份世界来问，不动真的）
    w_real = env.world
    env.world = copy.deepcopy(w_real)
    try:
        ok, msg = env._apply(a)
    finally:
        env.world = w_real
    if ok:
        stat["可行"] += 1
    else:
        stat["不可行"] += 1
        if a.kind == "build": stat["不可行:build:"+a.sub] += 1
        elif a.kind in ("buy", "sell"): stat[f"不可行:{a.kind}:{a.sub}"] += 1
        else: stat[f"不可行:{a.kind}"] += 1
    # 模型在这一步与老师选的是不是同一个
    stat["与老师同" if a.label() == str(args) else "与老师异"] += 0

rng = random.Random(SEED)
for t in range(TURNS):
    teacher(env.world, env.agent, rng, max_actions=10**9, on_action=on_action)
    env.world.resolve_turn()
    if t + 1 < TURNS: env.world.begin_turn()

n = stat["总步数"]
print(f"老师轨迹上 {n} 个决策点（模型在**老师见过的状态**上选）")
print(f"  模型选的候选里，**引擎会拒**的：{stat['不可行']}/{n} = {stat['不可行']/n:.0%}")
print("  按类拆：", dict([(k.replace("不可行:", ""), v) for k, v in stat.most_common() if k.startswith("不可行:")][:10]))
