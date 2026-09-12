# -*- coding: utf-8 -*-
"""★开局那一个**固定状态**上，模型 vs 老师的逐决策读数。

为什么这个探针特别干净（2026-09-12 实测）：
  开局资源是**硬常量** —— 跨 20 张图 × 抖动开关全是 `黄金 1500 / 木 60 / 粮 20 /
  矿 10 / 补 10 / 装 5 / 油 0 / 地 5`。也就是说 **"早攒钱还是早复利"这个决策，
  每一局都在逐字相同的状态上做**。
⇒ 这里没有分布漂移、没有覆盖问题掺进来 —— **它读的是纯粹的"拟合得上吗"**。

做法：在第 1 回合逐动作走老师的计划，每一步都拿**动作执行前的状态**问模型 argmax，
对比（1）与老师同一个候选？（2）引擎收不收？跨多张图聚合。
"""
import sys, copy, random, collections
import torch
import rl.bc as bc
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import forward_batch, _one_step

CKPT, N, JIT = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 30, 0.1
env = ZhanguoEnv(map_size=16, max_turns=70); env.rules_jitter = JIT
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"]); m.eval()
teacher = bc.get_teacher("v10", turns=70)
torch.manual_seed(0)

S = collections.Counter()
first = collections.Counter()
confuse = collections.Counter()
for seed in range(900000, 900000 + N):
    env.reset(seed, map_seed=seed)
    rng = random.Random(seed)
    n_this = [0]                    # ★本图已走几步（不是全局计数 —— 踩过：全局计数
                                    #   让"第一个决策点"只在整轮里发生过一次）
    def on_action(tool, args):
        obs = env._obs()
        lg, _v, cm = forward_batch(m, [_one_step(obs)], [tokenize(env, obs)])
        p = int(lg[0].masked_fill(~cm[0], -1e9).argmax())
        a = obs.cand["actions"][p]
        t = bc.match(obs.cand["actions"], bc.to_action(tool, args))
        S["步数"] += 1; n_this[0] += 1
        if t is not None and p == t:
            S["精确同"] += 1
        if t is not None and a.kind == obs.cand["actions"][t].kind:
            S["类别同"] += 1
        if n_this[0] == 1:
            first[a.kind if a.kind != "build" else f"build:{a.sub}"] += 1
            if t is not None:
                confuse[(a.kind, obs.cand["actions"][t].kind)] += 1
        # 引擎收不收（deepcopy 真引擎，不动真的）
        w_real = env.world; env.world = copy.deepcopy(w_real)
        try:
            ok, _msg = env._apply(a)
        finally:
            env.world = w_real
        S["可行" if ok else "不可行"] += 1
    teacher(env.world, env.agent, rng, max_actions=10**9, on_action=on_action)

n = S["步数"]
print(f"{CKPT}   第 1 回合 × {N} 张图 = {n} 个决策点（同一套开局资源）")
print(f"  与老师**精确**同候选：{S['精确同']:>4}/{n} = {S['精确同']/n:6.1%}")
print(f"  与老师**类别**相同  ：{S['类别同']:>4}/{n} = {S['类别同']/n:6.1%}")
print(f"  模型选的引擎**收**  ：{S['可行']:>4}/{n} = {S['可行']/n:6.1%}")
print(f"  第一个决策点模型选什么：{dict(first.most_common(5))}")
print(f"  （第一个决策点）模型类别 → 老师类别 混淆：{confuse.most_common(4)}")
