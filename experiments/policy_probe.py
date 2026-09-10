"""探针：模型的「偏好方向」到底随不随局面变化？

模型的打分是  logits = cos(q_ln(q), cand_ln(c)) * sqrt(d)，
其中 `q = query(glob_mlp(glob))` —— **只取决于全局状态**。
所以整个策略 = 「从局面算一个方向，再把所有候选按与它的夹角排序」。

**要证伪的怀疑**：如果 `q` 几乎不随局面变，模型就退化成**一张固定的动作偏好表** ——
marginal 分布看着像（sell/buy 占多数），但完全不看局面。那样既不会真扩张也不会真经营，
且**加多少训练量都救不了**（它在优化一个表达不了目标的东西）。

三个读数：
  ① `q` 的两两余弦相似度 —— 接近 1.0 = 方向恒定（退化）
  ② 每类动作的"最好那个候选"的得分（类型偏好向量），看它跨局面变不变
  ③ argmax 落在哪一类，以及这个分布的熵

用法：.venv/bin/python -m experiments.policy_probe <ckpt> [turns] [episodes]
"""
from __future__ import annotations

import random
import sys
from collections import Counter

import numpy as np
import torch

from rl.bc import get_teacher
from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import collate

CKPT = sys.argv[1] if len(sys.argv) > 1 else "rl/runs/bc/ep3.pt"
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 70
EPS = int(sys.argv[3]) if len(sys.argv) > 3 else 3

env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=512)
obs = env.reset(0)
model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                  sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                  n_tiles=16 ** 2)
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
model.load_state_dict(ck["model"])
model.eval()
print(f"权重 {CKPT}（iter {ck.get('iter')}）  老师驱动采状态，{EPS} 局 × {TURNS} 回合")

teacher = get_teacher("v8", TURNS)
qs, type_pref, kinds = [], [], []


def probe(o):
    grid, glob, cand, mask = collate([{"grid": o.grid, "glob": o.glob,
                                       "cand": o.cand, "act": 0, "logp": 0.0,
                                       "val": 0.0, "rew": 0.0, "done": False}],
                                     model.n_tiles)
    with torch.no_grad():
        logits, _v = model(grid, glob, cand, mask)
        q = model.q_ln(model.query(model.glob_mlp(glob)))[0].numpy()
        lg = logits[0].numpy()
    qs.append(q)
    kinds.append(o.cand["actions"][int(lg.argmax())].kind)   # argmax 是**候选下标**，不是类别下标
    # 每类动作里"最好的那个候选"的得分 → 类型偏好向量
    row = {}
    for i, a in enumerate(o.cand["actions"]):
        if a.kind not in row or lg[i] > row[a.kind]:
            row[a.kind] = float(lg[i])
    type_pref.append([row.get(k, np.nan) for k in KINDS])


for ep in range(EPS):
    o = env.reset(1000 + ep)
    rng = random.Random(1000 + ep)
    for t in range(TURNS):
        probe(o)
        teacher(env.world, env.agent, rng, max_actions=10 ** 9)
        env.world.resolve_turn()
        if t + 1 < TURNS:
            env.world.begin_turn()
        o = env._obs()

Q = np.stack(qs)
Qn = Q / np.maximum(np.linalg.norm(Q, axis=1, keepdims=True), 1e-9)
C = Qn @ Qn.T
iu = np.triu_indices(len(Q), 1)
cos = C[iu]
print(f"\n采集 {len(Q)} 个状态")
print(f"① `q` 的两两余弦相似度：均值 {cos.mean():.3f}  中位 {np.median(cos):.3f}  "
      f"最小 {cos.min():.3f}   <5%分位 {np.percentile(cos,5):.3f}")

P = np.array(type_pref, dtype=np.float64)
print("\n② 类型偏好向量（每类「最好那个候选」的得分）跨局面的波动：")
print(f"   {'类别':<10}{'均值':>10}{'标准差':>10}{'极差':>10}")
for j, k in enumerate(KINDS):
    col = P[:, j]
    col = col[~np.isnan(col)]
    if len(col):
        print(f"   {k:<10}{col.mean():>10.2f}{col.std():>10.2f}"
              f"{col.max()-col.min():>10.2f}")

cnt = Counter(kinds)
n = sum(cnt.values())
H = -sum((v / n) * np.log(v / n) for v in cnt.values())
print(f"\n③ argmax 落在哪一类（{n} 个状态，熵 {H:.2f} nats，最大 {np.log(len(KINDS)):.2f}）：")
for k, v in cnt.most_common():
    print(f"   {k:<10}{v:>6}  {v/n:>6.1%}")
