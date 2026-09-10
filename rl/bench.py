# -*- coding: utf-8 -*-
"""卷积规模实测：量这台机器上一次 forward+backward 的耗时，用来定网络规模。

内存不是瓶颈（本机 16G），瓶颈是 CPU 算力——所以规模要靠实测选，别拍脑袋。

    python3 -m rl.bench --map-size 16 --batch 256 --k 300
"""
from __future__ import annotations

import argparse
import time

import torch

from rl.env import AMOUNTS, ARMY_FEAT, KINDS, ZhanguoEnv
from rl.model import PolicyNet

CONFIGS = {
    "tiny  (1×1→24 + 3×3×1, 48)": dict(d_bottle=24, d_conv=48, n_conv3=1),
    "base  (1×1→32 + 3×3×2, 64)": dict(d_bottle=32, d_conv=64, n_conv3=2),
    "wide  (1×1→48 + 3×3×2, 96)": dict(d_bottle=48, d_conv=96, n_conv3=2),
    "deep  (1×1→32 + 3×3×3, 64)": dict(d_bottle=32, d_conv=64, n_conv3=3),
    "plain (3×3×2, 64)": dict(d_bottle=64, d_conv=64, n_conv3=2),
}


def make_batch(env: ZhanguoEnv, b: int, k: int, a: int):
    grid = torch.randn(b, len(env.obs_channels()), env.map_size, env.map_size)
    glob = torch.randn(b, env.glob_size())
    cand = {
        "type_idx": torch.randint(0, len(KINDS), (b, k)),
        "sub_idx": torch.randint(0, 8, (b, k)),
        "tile_idx": torch.randint(0, env.map_size ** 2 + 1, (b, k)),
        "army_idx": torch.randint(0, a + 1, (b, k)),
        "amount_idx": torch.randint(0, len(AMOUNTS), (b, k)),
        "army_feats": torch.randn(b, a, ARMY_FEAT),
    }
    return grid, glob, cand, torch.ones(b, k, dtype=torch.bool)


def bench(env: ZhanguoEnv, cfg: dict, b: int, k: int, a: int, iters: int = 5):
    net = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                    sub_sizes=[len(env.sub_tables[x]) for x in KINDS],
                    n_tiles=env.map_size ** 2, **cfg)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3)
    grid, glob, cand, mask = make_batch(env, b, k, a)

    def one():
        logits, value = net(grid, glob, cand, mask)
        (logits.sum() + value.sum()).backward()
        opt.step()
        opt.zero_grad()

    for _ in range(2):
        one()
    t0 = time.time()
    for _ in range(iters):
        one()
    dt = (time.time() - t0) / iters
    n_param = sum(p.numel() for p in net.parameters())
    return dt, n_param


def main() -> None:
    ap = argparse.ArgumentParser(description="卷积规模实测")
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--batch", type=int, default=256, help="minibatch 步数")
    ap.add_argument("--k", type=int, default=300, help="候选动作数（候选集规模）")
    ap.add_argument("--armies", type=int, default=8)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--steps", type=int, default=600, help="一轮采样的总步数")
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    torch.set_num_threads(max(1, args.threads))

    env = ZhanguoEnv(map_size=args.map_size)
    passes = args.epochs * max(1, -(-args.steps // args.batch))   # 每轮 PPO 的前后向次数
    print(f"map={args.map_size} batch={args.batch} K={args.k} A={args.armies} "
          f"每轮 PPO 约 {passes} 次前后向（epochs={args.epochs}, rollout={args.steps} 步）")
    print(f"{'配置':<28}{'参数量':>10}{'ms/次':>10}{'秒/轮':>9}")
    for name, cfg in CONFIGS.items():
        dt, n_param = bench(env, cfg, args.batch, args.k, args.armies, args.iters)
        print(f"{name:<28}{n_param / 1e6:>9.2f}M{dt * 1000:>10.1f}{dt * passes:>9.1f}")


if __name__ == "__main__":
    main()
