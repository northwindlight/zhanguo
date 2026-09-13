# -*- coding: utf-8 -*-
"""诊断：熵涨到底是「概率摊到点不动的候选上」，还是「合法集内真的变随机」？

专家 2026-09-13 给的判据：
    p_inv  = 概率质量落在**不可执行**候选上的比例（随机基线 ≈ 死选项占比）
    H_all  = 全部候选的熵
    H_val  = **valid 内重归一化**后的熵（q = p / Σ_valid p）
             ★只 mask 不重归一会低估，必须报重归一化熵

    判定：H_all↑ 但 H_val 平/↓，且 Δ(H_all−H_val) > 0.1 nat 或 corr(H_all, p_inv) > 0.6
          ⇒ **死选项泄漏**
          H_val 也↑ > 0.1 nat ⇒ **合法集内真随机**

★**本探针的便宜版本**（原方案要逐候选调引擎，184 个候选 × 每步 = deepcopy 184 次，太贵）：
   - `p_inv` 用**实际轨迹估计**：采样出来的动作 `env.step` 就返回 `ok`
     ⇒ 撞墙率 = 「采样落在死选项上的比例」的**无偏估计**，零额外成本。
   - `H_all` 从 logits 直接算，也便宜。
   - `H_val` 略去 —— 它需要"哪些候选此刻可执行"的全集，那正是最贵的那一步。
     **少了它就无法排除"合法集内真随机"，但 `corr(H_all, 撞墙率)` 已能指向泄漏。**

用法：python experiments/probe_validity.py <ckpt> [episodes] [turns]
"""
import sys
import collections
import math

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act, forward_batch, _one_step

CKPT = sys.argv[1]
EPS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 30

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)                      # `_terrain` 要等第一次 reset 才建出来
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
m.load_state_dict(ck["model"])
m.eval()
print(f"权重 {CKPT}   （训练到第 {ck.get('iter', '?')} 块）")
print(f"{EPS} 局 × {TURNS} 回合   采样档\n")

rows = []
for ep in range(EPS):
    torch.manual_seed(2000 + ep)
    obs = env.reset(200 + ep)
    acc = collections.Counter()
    while True:
        # H_all：全部**有效位**（非 padding）候选的熵
        lg, _v, cm = forward_batch(m, [_one_step(obs)], [tokenize(env, obs)])
        p = torch.softmax(lg[0].masked_fill(~cm[0], float("-inf")), dim=-1)
        p = p[p > 0]
        h_all = float(-(p * p.log()).sum())
        idx, _lp, _val = act(m, obs, win=tokenize(env, obs))
        a = obs.cand["actions"][idx]
        kind = a.kind
        obs, _r, done, info = env.step(a)
        acc["n"] += 1
        acc["H"] += h_all
        if not info["ok"]:
            acc["撞"] += 1
        acc["build"] += (kind == "build" and info["ok"])
        acc["nonbuild_ok"] += (kind != "build" and info["ok"])
        if done:
            break
    n = max(1, acc["n"])
    rows.append((ep, acc["H"] / n, acc["撞"] / n,
                 acc["build"] / n, acc["nonbuild_ok"] / n))
    print(f"  局{ep}:  步数{acc['n']:>4}  H_all={acc['H'] / n:.3f}"
          f"  撞墙率={acc['撞'] / n:.1%}  build成功/步={acc['build'] / n:.3f}"
          f"  非build成功/步={acc['nonbuild_ok'] / n:.3f}")

N = len(rows)
H = [r[1] for r in rows]
rt = [r[2] for r in rows]
bs = [r[3] for r in rows]
nb = [r[4] for r in rows]


def corr(x, y):
    mx, my = sum(x) / len(x), sum(y) / len(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (dx * dy) if dx * dy else float("nan")


print(f"\n=== {CKPT} ===")
print(f"  H_all      {sum(H) / N:.3f}")
print(f"  撞墙率     {sum(rt) / N:.1%}   ← 「采样落在死选项上」的比例")
print(f"  build 成功/步   {sum(bs) / N:.3f}")
print(f"  非build 成功/步 {sum(nb) / N:.3f}   ← 合法非 build 动作的活跃度")
print(f"\n  ★corr(H_all, 撞墙率) = {corr(H, rt):+.2f}"
      f"   （> +0.6 ⇒ 熵涨主要来自死选项泄漏；≈0 ⇒ 是合法集内变随机）")
print(f"  前 25% 步 vs 后 25%：撞墙率 {sum(rt[:max(1, N // 4)]) / max(1, N // 4):.1%}"
      f" → {sum(rt[-(N // 4 or 1):]) / (N // 4 or 1):.1%}")
