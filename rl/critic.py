# -*- coding: utf-8 -*-
"""Critic 体检：V(s) 到底有没有跟踪「从现在起到局末还能有多少消费」。

为什么需要：γ=1、λ=1 时优势退化成蒙特卡洛 `A_t = 整局剩余消费 − V(s_t)`。
消费单调递增，**剩余消费随回合数急剧下降**（开局还剩十几万，局末剩几千）。
V 若学不出这个结构，A_t 就带上**系统性偏置**：开局一片大正、局末一片大负，
跟动作好坏无关，真实信号被淹没。

判据：
  · V 随回合显著下降 → critic 抓到了结构，信号弱只是数据量问题
  · V 基本持平      → 系统性偏置，先修 critic 再谈训练

对照用**同一条轨迹上的真实剩余回报** `G_t = 局末累计消费 − 此刻累计消费`。
V 该逼近的就是它——两条线贴不贴，比"V 降没降"更直接。

    python3 -m rl.critic --ckpt rl/runs/bc/last.pt --episodes 3
"""
from __future__ import annotations

import argparse
import statistics as st

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import act, value_of


def main() -> None:
    ap = argparse.ArgumentParser(description="Critic 体检：V(s) vs 真实剩余回报")
    ap.add_argument("--ckpt", default="rl/runs/bc/last.pt")
    ap.add_argument("--episodes", type=int, default=3)
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--seed-base", type=int, default=700_000)
    ap.add_argument("--buckets", type=int, default=10, help="把整局切成几段来看")
    ap.add_argument("--sample", action="store_true", help="按概率采样（默认贪心）")
    ap.add_argument("--legacy-scoring", action="store_true",
                    help="装 LayerNorm 之前的旧检查点")
    ap.add_argument("--threads", type=int, default=0, help="torch CPU 线程数；**0 = 自动 = 物理核数**（ECS 1 / Pi 5 4）。SMT 的第二个逻辑核对向量计算收益为零，写死 4 在 ECS 上等于打开超订（实测慢 3.4×）")
    args = ap.parse_args()

    from rl.hw import set_threads
    set_threads(args.threads)
    env = ZhanguoEnv(map_size=args.map_size, max_turns=args.turns)
    env.reset(0)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=args.map_size ** 2)
    if args.legacy_scoring:
        import torch.nn as nn
        model.cand_ln, model.q_ln = nn.Identity(), nn.Identity()
    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()

    # 每局一条轨迹：[(回合, V, 此刻累计消费)]；单位统一成**奖励**（=消费×reward_scale）
    traj: list[list[tuple[int, float, float]]] = []
    for ep in range(args.episodes):
        obs = env.reset(args.seed_base + ep)
        cur: list[tuple[int, float, float]] = []
        while True:
            cur.append((env.world.turn, value_of(model, obs),
                        env.world.spend_total(env.agent) * env.reward_scale))
            i, _lp, _v = act(model, obs, deterministic=not args.sample)
            obs, _r, done, _info = env.step(obs.cand["actions"][i])
            if done:
                break
        traj.append(cur)

    B = args.buckets
    scale = 1.0 / env.reward_scale          # 变回「金」便于读
    print(f"权重 {args.ckpt}   {args.episodes} 局 × {args.turns} 回合"
          f"   （下表已换算成金）\n")
    print(f"{'回合段':>14}{'步数':>7}{'V 均值':>12}{'G 均值':>12}{'G−V':>12}")
    agg: list[tuple[float, float]] = []
    for b in range(B):
        lo = b * args.turns // B
        hi = (b + 1) * args.turns // B
        vs, gs = [], []
        for cur in traj:
            end_spend = cur[-1][2]          # 局末累计消费（奖励单位）
            for (t, v, sp) in cur:
                if lo <= t < hi:
                    vs.append(v)
                    gs.append(max(0.0, end_spend - sp))
        if vs:
            v_m, g_m = st.mean(vs) * scale, st.mean(gs) * scale
            agg.append((v_m, g_m))
            print(f"{f'{lo}~{hi}':>14}{len(vs):>7}{v_m:>12,.0f}{g_m:>12,.0f}"
                  f"{g_m - v_m:>12,.0f}")

    if len(agg) >= 2:
        v0, g0 = agg[0]
        v1, g1 = agg[-1]
        print(f"\nV  首段 {v0:>10,.0f}  →  末段 {v1:>10,.0f}")
        print(f"G  首段 {g0:>10,.0f}  →  末段 {g1:>10,.0f}")
        ratio = v1 / v0 if abs(v0) > 1e-6 else float("nan")
        print(f"V 末段/首段 = {ratio:.2f}"
              f"   （明显小于 1 = 随回合下降；≈1 = 持平）")
        if v0 != 0:
            err = abs(v0 - g0) / max(abs(g0), 1e-6)
            print(f"首段标定误差 |V−G|/G = {err:.1%}   （0% = 完全准确）")
        if ratio > 0.7:
            print("\n⚠ V 没随回合显著下降 → **系统性偏置**，先修 critic 再谈加数据")
        else:
            print("\n✓ V 随回合下降 → critic 抓到了结构；信号问题靠数据量解决")


if __name__ == "__main__":
    main()
