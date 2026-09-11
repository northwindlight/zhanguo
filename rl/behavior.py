# -*- coding: utf-8 -*-
"""BC 策略**实际在干什么**：跑一局，统计它选的动作类别分布。

分数低有两种完全不同的原因，这个脚本用来分开它们：
  · 它选的动作五花八门但都选错 → 学得不够，继续训就行
  · 它几乎只选 end_turn / 同一类 → 塌缩了，得改训练目标
看分数分不出这两种，看分布能。

    python3 -m rl.behavior --ckpt rl/runs/bc/last.pt --turns 200
"""
from __future__ import annotations

import argparse
from collections import Counter

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import act


def main() -> None:
    ap = argparse.ArgumentParser(description="BC 策略的动作分布诊断")
    ap.add_argument("--ckpt", default="rl/runs/bc/last.pt")
    ap.add_argument("--turns", type=int, default=200)
    ap.add_argument("--seed", type=int, default=900_001)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--sample", action="store_true", help="按概率采样（默认贪心）")
    ap.add_argument("--threads", type=int, default=0, help="torch CPU 线程数；**0 = 自动 = 物理核数**（ECS 1 / Pi 5 4）。SMT 的第二个逻辑核对向量计算收益为零，写死 4 在 ECS 上等于打开超订（实测慢 3.4×）")
    args = ap.parse_args()

    from rl.hw import set_threads
    set_threads(args.threads)
    env = ZhanguoEnv(map_size=args.map_size, max_turns=args.turns)
    obs = env.reset(args.seed)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=args.map_size ** 2)
    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()

    kinds: Counter = Counter()
    subs: Counter = Counter()
    n_end_early = 0
    steps = 0
    while True:
        i, _lp, _v = act(model, obs, deterministic=not args.sample)
        a = obs.cand["actions"][i]
        kinds[a.kind] += 1
        if a.sub:
            subs[(a.kind, a.sub)] += 1
        steps += 1
        if a.kind == "end_turn":
            n_end_early += 1
        obs, _r, done, _info = env.step(a)
        if done:
            break

    print(f"{args.ckpt}  {'采样' if args.sample else '贪心'}  {args.turns} 回合  "
          f"共 {steps} 步  →  消费 {env.summary()['spend_total']:,.0f}  "
          f"地 {env.summary()['tiles']}\n")
    print(f"{'动作类别':<12}{'次数':>8}{'占比':>9}")
    for k in KINDS:
        if kinds.get(k):
            print(f"{k:<12}{kinds[k]:>8}{kinds[k]/steps:>9.1%}")
    if subs:
        print(f"\n{'子项 top10':<20}{'次数':>8}")
        for (k, s), n in subs.most_common(10):
            print(f"{k + ' ' + s:<20}{n:>8}")


if __name__ == "__main__":
    main()
