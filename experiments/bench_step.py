# -*- coding: utf-8 -*-
"""微基准：在一局的**真实形状**上量出 ①一次梯度步 ②一次前向 ③一次 tokenize+env.step。

为什么这么量：训练日志里每局 ~720 s，但分不出梯度与采样各占多少 ——
而 250 步梯度是每局固定形状（batch 32），采样侧才是变量。直接计时最干脆。
"""
import time, random, sys
import numpy as np, torch
import torch.nn.functional as F
import rl.bc as bc
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import forward_batch, _one_step

TURNS = 70
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
model = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
model.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
print(f"参数 {model.n_params():,}")

# ---- 采集一小段真实样本当 minibatch ----
t = time.time()
teacher = bc.get_teacher("v10", turns=TURNS)
demos, spend, miss = bc.collect_episode(env, turns=6, seed=2000, teacher_fn=teacher, with_window=True)
t_collect = time.time() - t
print(f"采集 6 回合：{len(demos)} 样本，耗时 {t_collect:.1f}s  → {t_collect/6:.2f} s/回合")

chunk = [demos[i] for i in range(32)] if len(demos) >= 32 else (demos * 2)[:32]
cand, cmask, wb, acts, rets = bc.pack_tf(chunk)
K = cand["type_idx"].shape[1]
print(f"minibatch: batch 32 × 候选 {K}")

opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
model.train()

def one_grad_step():
    logits, v = model(wb, cand, cmask)
    logp = F.log_softmax(logits, dim=-1)
    a_t = torch.as_tensor(acts)
    _lp = logp.gather(1, a_t.unsqueeze(1)).squeeze(1)
    loss_pi = -_lp.mean()
    rt = torch.as_tensor(rets)
    loss = loss_pi + 0.5 * F.mse_loss(v, rt) / max(1.0, float(rt.var()))
    opt.zero_grad(); loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
    opt.step()

for _ in range(3):     # 热身
    one_grad_step()
t = time.time()
N = 20
for _ in range(N):
    one_grad_step()
grad_s = (time.time() - t) / N

model.eval()
with torch.no_grad():
    for _ in range(3):
        forward_batch(model, [_one_step(env._obs())], [tokenize(env, env._obs())])
    t = time.time()
    for _ in range(20):
        ob = env._obs()
        forward_batch(model, [_one_step(ob)], [tokenize(env, ob)])
    fwd_s = (time.time() - t) / 20

ob = env._obs()
t = time.time()
for _ in range(20):
    tokenize(env, ob)
tok_s = (time.time() - t) / 20

steps = 250
print(f"\n  梯度步        {grad_s*1000:8.1f} ms   × 250 = {grad_s*steps:7.1f} s")
print(f"  前向+tokenize {fwd_s*1000:8.1f} ms")
print(f"  只 tokenize   {tok_s*1000:8.1f} ms")
print(f"\n  ⇒ 每局梯度 {grad_s*steps:.0f} s；日志实测每局 700~730 s")
print(f"  ⇒ 采样+tokenize+老师+评估 ≈ {720 - grad_s*steps:.0f} s  "
      f"（占 {100*(720-grad_s*steps)/720:.0f}%）")
