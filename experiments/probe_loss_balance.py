# -*- coding: utf-8 -*-
"""策略项与价值项的**梯度**谁大？—— 一个可能解释"PPO 学不动"的机制。

## 为什么要问

`loss = pg + vf_coef·vf − ent_coef·ent`（`rl/ppo.py:456`，`vf_coef=0.5` 没被
`train.py` 覆盖）。日志里 `pg ≈ 0.01~0.03` 而 `vf ≈ 148~638`
⇒ **损失数值上价值项是策略项的 ~15000 倍**。

策略头 `score(...)` 与价值头 `value(...)` **共用主干 `h`** ⇒ 若价值项的梯度
压倒策略项，**主干就只在拟合价值函数，策略梯度被淹没**。

⚠ 但"损失值大"不等于"梯度大"（`pg` 是零均值量的平均，天然小）。
**必须实测梯度范数**，这就是本探针存在的理由。

## 本探针做什么

用真模型 + 真环境收一小段 rollout，算出 `pg / vf / ent` 三项，
再**分别对参数求梯度**，报三个范数：

    ||∂pg/∂θ||        ← 策略项
    ||∂(vf_coef·vf)/∂θ||  ← 价值项
    ||∂(−ent_coef·ent)/∂θ||

并按**参数分组**报（主干 / 策略头 / 价值头），看主干被谁主导。

用法：python experiments/probe_loss_balance.py <ckpt> [episodes] [turns]
"""
import sys

import numpy as np
import torch

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import Rollout, act, collate

CKPT = sys.argv[1]
EPS = int(sys.argv[2]) if len(sys.argv) > 2 else 2
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 60
# ★第 4 个参数：要不要按 `--norm-reward` 那样归一化奖励。
#   必须可切 —— 否则探针永远只测"归一化关着"那一档，
#   而我们要回答的正是"开了归一化，梯度失衡治好了没有"。
NORM = bool(int(sys.argv[4])) if len(sys.argv) > 4 else False
VF_COEF, ENT_COEF, CLIP = 0.5, 0.01, 0.2

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
_miss, _ = m.load_state_dict(ck["model"], strict=False)
m.train()
print(f"权重 {CKPT}（第 {ck.get('iter', '?')} 块）  奖励归一化 {NORM}  收 {EPS} 局 × {TURNS} 回合\n")

# ---- 收一段真 rollout（与训练同款零件）
roll = Rollout(lam=1.0, normalize=NORM)
for ep in range(EPS):
    obs = env.reset(500 + ep)
    while True:
        w = tokenize(env, obs)
        idx, logp, val = act(m, obs, win=w)
        keep = obs
        obs, r, done, info = env.step(keep.cand["actions"][idx])
        roll.add(keep, idx, logp, val, r, done, win=w, ok=info["ok"])
        if done:
            break
roll.gae(last_value=0.0)
steps = roll.steps
print(f"收满 {len(steps)} 步\n")

# ---- 拼一整批（这里只关心量级，不做 minibatch 切分）
grid, glob, cand, mask = collate(steps)
from rl.ppo import forward_batch  # noqa: E402
logits, value, mask = forward_batch(m, steps, [s["win"] for s in steps])

d = logits.device
act_t = torch.as_tensor([s["act"] for s in steps], dtype=torch.long, device=d)
adv = torch.as_tensor([s["adv"] for s in steps], dtype=torch.float32, device=d)
adv = (adv - adv.mean()) / (adv.std() + 1e-8)
old_logp = torch.as_tensor([s["logp"] for s in steps], dtype=torch.float32, device=d)
ret = torch.as_tensor([s["ret"] for s in steps], dtype=torch.float32, device=d)
old_v = torch.as_tensor([s["val"] for s in steps], dtype=torch.float32, device=d)

import torch.nn.functional as F  # noqa: E402
logp_all = F.log_softmax(logits, dim=-1)
logp = logp_all.gather(1, act_t.unsqueeze(1)).squeeze(1)
ratio = (logp - old_logp).exp()
pg1 = -adv * ratio
pg2 = -adv * ratio.clamp(1 - CLIP, 1 + CLIP)
pg = torch.max(pg1, pg2).mean()
v_clip = old_v + (value - old_v).clamp(-CLIP, CLIP)
vf = torch.max((value - ret) ** 2, (v_clip - ret) ** 2).mean()
p = logp_all.exp()
ent = -(p * logp_all.masked_fill(~mask, 0.0)).sum(-1).mean()

print("=== 三项的**损失数值** ===")
print(f"  pg          = {float(pg):+.6f}")
print(f"  vf_coef·vf  = {VF_COEF * float(vf):.3f}      （vf 原始 = {float(vf):.1f}）")
print(f"  ent_coef·ent= {ENT_COEF * float(ent):.4f}")
print(f"  ⇒ 价值项 / 策略项 = {abs(VF_COEF * float(vf)) / max(1e-9, abs(float(pg))):,.0f} 倍\n")

# ---- 分组：主干 / 策略头 / 价值头
groups = {"主干": [], "策略头 score": [], "价值头 value": [], "其它": []}
for n, p_ in m.named_parameters():
    if not p_.requires_grad:
        continue
    if n.startswith("score"):
        groups["策略头 score"].append((n, p_))
    elif n.startswith("value"):
        groups["价值头 value"].append((n, p_))
    elif n.startswith("exec_head"):
        groups["其它"].append((n, p_))
    else:
        groups["主干"].append((n, p_))


def gnorm(loss, ps):
    """★空组必须返回 nan 而不是 0：`sum([])` 是 int 0，`torch.sqrt(0)` 会抛
    TypeError（第一版就这么崩的）。"""
    ps = [p_ for _, p_ in ps]
    if not ps:
        return float("nan")
    gs = [g for g in torch.autograd.grad(loss, ps, retain_graph=True, allow_unused=True)
          if g is not None]
    if not gs:
        return float("nan")
    return float(torch.sqrt(sum((g ** 2).sum() for g in gs)))


print("=== 三项各自的**梯度范数**（按参数分组）===")
print(f"{'组':<14}{'∂pg':>12}{'∂(vf_coef·vf)':>18}{'∂(ent_coef·ent)':>18}{'价值/策略':>12}")
for gname, ps in groups.items():
    if not ps:
        continue
    gp = gnorm(pg, ps)
    gv = gnorm(VF_COEF * vf, ps)
    ge = gnorm(-ENT_COEF * ent, ps)
    r = gv / max(1e-12, gp)
    print(f"{gname:<14}{gp:>12.4e}{gv:>18.4e}{ge:>18.4e}{r:>12.2f}")

print("\n判据：主干那行若「价值/策略」≫1（比如 >10），说明主干主要在拟合价值函数，")
print("      策略梯度被淹没 —— 那才是「PPO 学不动 BC 起点」的机制。")
print("      ≈1 或 <1 ⇒ 这条排除，得另找。")
