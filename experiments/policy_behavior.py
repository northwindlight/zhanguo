"""策略行为审计：它到底有没有在打仗？

**为什么必须先量这个再决定上不上 PPO**：
PPO 靠"做了动作 → 拿到回报"来学。如果策略**根本不采样 attack**，那么土地永远
不会增加，梯度里就没有任何关于 attack 的信号 —— 加多少 PPO 都是空转。
反过来，如果它**采了 attack 但打不下来**（军队没到位、凑不齐两支），那病在
"序列协调"，而协调恰恰要靠结果梯度来教 —— 那时 PPO 才有用。

所以这一个数决定下一步：**一局里 attack 被采样了几次？成了几次？**

用法：.venv/bin/python -m experiments.policy_behavior <ckpt> [turns] [episodes]
"""
from __future__ import annotations

import random
import sys
from collections import Counter

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import act

CKPT = sys.argv[1] if len(sys.argv) > 1 else "rl/runs/bc_v2/last.pt"
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 70
EPS = int(sys.argv[3]) if len(sys.argv) > 3 else 5

env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=512)
obs = env.reset(0)
model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                  sub_sizes=[len(env.sub_tables[k]) for k in KINDS], n_tiles=16 ** 2)
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
model.load_state_dict(ck["model"])
model.eval()
print(f"权重 {CKPT}   {EPS} 局 × {TURNS} 回合   **采样**策略（PPO 里的实际行为）\n")

tot = Counter()          # 采到的动作
okc = Counter()          # 其中引擎接受的
att_adj = 0              # 采到 attack 时，目标格旁边有几支自家满血兵
spends, tiles, arm = [], [], []

for ep in range(EPS):
    o = env.reset(1000 + ep)
    torch.manual_seed(0)
    kinds_ep = Counter()
    ok_ep = Counter()
    while True:
        idx, _lp, _v = act(model, o, deterministic=False)
        a = o.cand["actions"][idx]
        kinds_ep[a.kind] += 1
        if a.kind == "attack":
            near = [q for q in env.world.nation_armies(env.agent)
                    if q["hp"] > 0 and max(abs(q["x"] - a.tile[0]), abs(q["y"] - a.tile[1])) <= 1]
            att_adj += len(near)
        o, _r, done, info = env.step(a)
        if info.get("ok"):
            ok_ep[a.kind] += 1
        if done:
            break
    s = env.summary()
    spends.append(s["spend_total"]); tiles.append(s["tiles"]); arm.append(s["armies"])
    tot.update(kinds_ep); okc.update(ok_ep)
    print(f"  局{ep+1}: 消费 {s['spend_total']:>6,.0f}  地 {s['tiles']:>3}  兵 {s['armies']:>3} | "
          f"attack 采到 {kinds_ep['attack']:>3} 次、成功 {ok_ep['attack']:>3} | "
          f"move {kinds_ep['move']:>3}")

n = sum(tot.values())
print(f"\n全部动作 {n} 步；各类被采样到的次数与成功率：")
print(f"  {'类别':<10}{'采样':>7}{'占比':>8}{'成功':>7}{'成功率':>8}")
for k in KINDS:
    if tot[k]:
        print(f"  {k:<10}{tot[k]:>7}{tot[k]/n:>7.1%}{okc[k]:>7}{okc[k]/tot[k]:>7.1%}")
print(f"\n★ attack：一局平均采到 **{tot['attack']/EPS:.1f}** 次，成功 **{okc['attack']/EPS:.1f}** 次")
print(f"  采样 attack 时，目标格旁边的自家满血兵数（要 ≥2 才打得动）：平均 {att_adj/max(1,tot['attack']):.1f}")
print(f"\n均值：消费 {sum(spends)/EPS:,.0f}   地 {sum(tiles)/EPS:.1f}   兵 {sum(arm)/EPS:.1f}")
