# -*- coding: utf-8 -*-
"""★**分叉回合**：同一张图，学生的黄金曲线与老师的从第几回合分开。

为什么这是最直接的理论判据：`J(π̂) ≤ J(π*) + O(T²ε)` 里那个平方，来源就是
**第 t 步的错会改变之后 T−t 步的状态分布**。而"状态分布变了"在盘面上的第一个
可见信号就是**金曲线分叉** —— 老师整局 95% 的回合金<350（贴着 0 跑），
所以它的可行集完全由流量决定，金一岔开，后面面对的就是另一个问题。

不花 GPU：只跑模型前向 + 引擎。
"""
import sys, random, statistics as st
import torch
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act
import rl.bc as bc

CKPT, N, TURNS, JIT = sys.argv[1], int(sys.argv[2]), 70, 0.1
env = ZhanguoEnv(map_size=16, max_turns=TURNS); env.rules_jitter = JIT
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"]); m.eval()
teacher = bc.get_teacher("v10", turns=TURNS)
torch.manual_seed(0)

def gold_curve(w, me):
    return [w.nations[me].res.get("黄金", 0)]

def run_teacher(seed):
    env.reset(seed, map_seed=seed)
    w, me, rng = env.world, env.agent, random.Random(seed)
    g = []
    for t in range(TURNS):
        teacher(w, me, rng, max_actions=10**9)
        w.resolve_turn()
        if t + 1 < TURNS: w.begin_turn()
        g.append(w.nations[me].res.get("黄金", 0))
    return g, len(w.own_tiles(me))

def run_student(seed, det=True):
    obs = env.reset(seed, map_seed=seed)
    w, me = env.world, env.agent
    g = []
    while True:
        i, _lp, _v = act(m, obs, deterministic=det, win=tokenize(env, obs))
        obs, _r, done, _info = env.step(obs.cand["actions"][i])
        if done: break
        if env.turn_actions == 0:            # 刚过回合边界
            g.append(w.nations[me].res.get("黄金", 0))
    return g, len(w.own_tiles(me))

THR = 100                                     # 分叉判据：|Δ金| > 100（兵营 350 的三成）
divs, tg, sg, tt, stt = [], [], [], [], []
for k in range(N):
    seed = 910000 + k
    gt, tile_t = run_teacher(seed)
    gs, tile_s = run_student(seed, det=True)
    n = min(len(gt), len(gs))
    d = next((i + 1 for i in range(n) if abs(gs[i] - gt[i]) > THR), None)
    divs.append(d if d else n + 1)
    tg.append(st.mean(gt[:12])); sg.append(st.mean(gs[:12]))
    tt.append(tile_t); stt.append(tile_s)
    print(f"  seed {seed}  分叉回合 {str(d):>4}   老师金(前12) {st.mean(gt[:12]):>6,.0f}"
          f"  学生金(前12) {st.mean(gs[:12]):>6,.0f}   地 老师{tile_t:>3} 学生{tile_s:>3}")

print(f"\n{N} 张图：分叉回合 中位 {int(st.median(divs))}  范围 [{min(divs)},{max(divs)}]")
print(f"  地数：老师均值 {st.mean(tt):.1f}   学生均值 {st.mean(stt):.1f}")
