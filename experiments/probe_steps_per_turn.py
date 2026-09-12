# -*- coding: utf-8 -*-
"""学生每回合走几步？（贪心 vs 采样）

怀疑：**贪心档不学停手** —— 每回合磨到 `ACT_SAFETY`(512) 上限。
若是，则"分叉回合"那个探针跑不完，而且它本身就是一个独立发现：
argmax 塌成"永不停手"，采样档才会停。
"""
import sys, collections
import torch
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act

CKPT, N = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3
env = ZhanguoEnv(map_size=16, max_turns=70); env.rules_jitter = 0.1
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"]); m.eval()
torch.manual_seed(0)

for det in (True, False):
    per, tot_steps = [], 0
    for k in range(N):
        obs = env.reset(920000 + k, map_seed=920000 + k)
        n = 0; cur = -1; cnt = 0; last_turn = env.world.turn
        while True:
            i, _lp, _v = act(m, obs, deterministic=det, win=tokenize(env, obs))
            obs, _r, done, _info = env.step(obs.cand["actions"][i])
            n += 1
            if env.world.turn != last_turn:
                per.append(n - cnt); cnt = n
                last_turn = env.world.turn
            if done: break
        tot_steps += n
    s = env.summary()
    print(f"  {'贪心' if det else '采样'}：一局总步数 {tot_steps/N:,.0f}   "
          f"每回合步数 中位 {sorted(per)[len(per)//2]}  均值 {sum(per)/len(per):,.1f}  "
          f"最大 {max(per)}   （ACT_SAFETY=512）")
