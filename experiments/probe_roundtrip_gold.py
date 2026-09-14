# -*- coding: utf-8 -*-
"""**空转金损失**：同回合往返（买回卖回）到底烧掉多少金？

## 为什么这是**纯损失**（用户口径 + 引擎口径，两条独立）

1. **引擎**：`MARKET_SPREAD = 0.10`（买 +5% / 卖 −5%）⇒ 同回合买回卖回一次，
   **固定烧掉成交额的 10%**（`game.py:107`；翻转套利必亏）。
2. **目标函数**：用户口径「**买卖不记消费**」，而计分板是**总消费**
   ⇒ 空转**不进口标**，还倒烧 10% ⇒ **纯损失**，不是"低效但有用"。

## 口径（与 `probe_market_drift.py` 的 W1 完全同款，只是分物资算）

- **往返量** = 每回合、每物资 `min(该回合买入量, 该回合卖出量)`（老师**结构上恒为 0**）
- **金损失** = `Σ_物资 往返量 × 中间价 × MARKET_SPREAD`

⚠ **这是下界**：真实成交价还受**价格冲击**影响
（买推高、卖压低，`PRICE_IMPACT=0.02/深度`），那部分没算进来。
⚠ 中间价用 `world.prices`（当回合均衡价），不是基准价 —— 与引擎结算同源。

用法：python experiments/probe_roundtrip_gold.py <ckpt> [<ckpt>...] [--episodes 8] [--turns 200]
"""
from __future__ import annotations

import collections
import sys

import torch

from game import MARKET, MARKET_SPREAD
from rl.env import KINDS, ZhanguoEnv
from rl.ppo import act
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

EPS, TURNS = 8, 200
CKPTS: list[str] = []
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--episodes":
        i += 1; EPS = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    else:
        CKPTS.append(a)
    i += 1
if not CKPTS:
    print(__doc__); sys.exit(2)

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
NAME = {"粮": "粮食", "木": "木头", "铁": "矿石", "马": "石油", "油": "石油"}


def mid(env, g: str) -> float:
    g2 = NAME.get(g, g)
    p = env.world.prices.get(g2)
    return float(p) if p is not None else float(MARKET.get(g2, 0))


for ck_path in CKPTS:
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(ck_path, map_location="cpu", weights_only=False)
    m.load_state_dict(ck["model"], strict=False)
    m.eval()

    per_ep_gold, per_ep_units, per_ep_spend = [], [], []
    by_good = collections.Counter()
    for ep in range(EPS):
        torch.manual_seed(2000 + ep)          # 与 probe_market_drift / probe_validity 同款
        obs = env.reset(900_000 + ep)
        cur, buy, sell = None, {}, {}
        ep_gold = 0.0; ep_units = 0; spend_end = 0.0
        while True:
            win = tokenize(env, obs)
            t_now = env.world.turn
            if t_now != cur:
                if cur is not None:
                    for g, b in buy.items():
                        u = min(b, sell.get(g, 0))
                        if u:
                            ep_units += u
                            ep_gold += u * mid(env, g) * MARKET_SPREAD
                            by_good[NAME.get(g, g)] += u
                cur, buy, sell = t_now, {}, {}
            idx, _lp, _v = act(m, obs, win=win)
            a = obs.cand["actions"][idx]
            kind = a.kind
            obs, _r, done, info = env.step(a)
            if info["ok"] and kind in ("buy", "sell"):
                d = buy if kind == "buy" else sell
                d[a.sub] = d.get(a.sub, 0) + a.amount
            if done:
                spend_end = info["spend_total"]
                break
        per_ep_gold.append(ep_gold); per_ep_units.append(ep_units)
        per_ep_spend.append(spend_end)

    n = EPS
    tg = sum(per_ep_gold); tu = sum(per_ep_units); ts = sum(per_ep_spend)
    print(f"===== {ck_path}（第 {ck.get('iter','?')} 块）=====")
    print(f"  往返量 {tu/n:8.1f} 件/局      空转金损失 {tg/n:8.1f} 金/局"
          f"      终局总消费 {ts/n:10,.0f}")
    print(f"  ⇒ 空转占终局消费 {tg/ts:6.2%}   （每件平均折 {tg/max(1,tu):.2f} 金）")
    print("  逐物资往返件数：" + "  ".join(f"{k}={v}" for k, v in by_good.most_common()))
    print("  逐局金损失：" + " ".join(f"{x:,.0f}" for x in per_ep_gold))
    print()
