# -*- coding: utf-8 -*-
"""候选数分桶 vs 随机组批 —— 只量**秒/步**和**批内最大候选数**。

问题：`collate` 把候选补齐到**批内最大**。随机抽 minibatch 时，只要抽到一个
大候选的样本，**整批**都按那个宽度算 —— 等于每批都被最长的那个撑满。
候选数中位 105、峰值 376，于是 3.6× 的算力花在 padding 上。

做法（标准 NLP 的长度分桶）：
    先打散 → 再按候选数排序 → 连续切片成批 → 打乱批序
打散是为了让桶不和"第几局"相关；打乱批序是为了让梯度步不按长度单调排列。

★两句丑话：
1. **这不只是速度改动，也是训练语义改动**：随机抽是"有放回"，分桶是"每轮遍历一遍"。
   这个探针只量**速度**，不判断哪个学得好。
2. 池子是本脚本按 seed 现采的（mode 两次跑采法完全相同 → 池子逐位相同），
   所以两次的差别只有组批方式。

    python3 -m experiments.bucket_probe --mode random
    python3 -m experiments.bucket_probe --mode bucket
    python3 -m experiments.bucket_probe --mode stats     # 只看分布，不训练
"""
from __future__ import annotations

import argparse
import random
import resource
import time

import numpy as np
import torch
import torch.nn.functional as F

from rl.bc import collect_episode, get_teacher, pack
from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet


def build_pool(episodes: int, turns: int, map_size: int, teacher: str):
    """按 seed 0..N-1 采池子 —— 确定性，两个 mode 拿到的是同一份。"""
    env = ZhanguoEnv(map_size=map_size, max_turns=turns)
    env.reset(0)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=map_size ** 2)
    tfn = get_teacher(teacher, turns)
    pool: list = []
    for ep in range(episodes):
        d, _sp, _m = collect_episode(env, turns, seed=ep, teacher_fn=tfn)
        pool.extend(d)
    return env, model, pool


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="stats", choices=("random", "bucket", "stats"))
    ap.add_argument("--episodes", type=int, default=20)
    ap.add_argument("--turns", type=int, default=70)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--teacher", default="v9")
    ap.add_argument("--batch", type=int, default=256, help="minibatch（同 bc.py）")
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--threads", type=int, default=0, help="0 = 自动 = 物理核数")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from rl.hw import set_threads
    n_threads = set_threads(args.threads)

    _env, model, pool = build_pool(args.episodes, args.turns, args.map_size, args.teacher)
    n = len(pool)
    n_cand = np.array([len(o.cand["actions"]) for o, _i, _g in pool])
    rng = random.Random(args.seed)

    # ---- 两种组批方式各自的「批内最大候选数」分布 ----
    def random_batches():
        while True:
            yield [rng.randrange(n) for _ in range(args.batch)]

    idx = list(range(n))
    rng.shuffle(idx)                                     # 先打散（不与"第几局"相关）
    idx.sort(key=lambda i: n_cand[i])                    # 再按候选数排
    bucketed = [idx[i:i + args.batch] for i in range(0, n, args.batch)]
    rng.shuffle(bucketed)                                # 打乱批序

    def bucket_batches():
        while True:
            for b in bucketed:
                yield b

    gen = random_batches() if args.mode == "random" else bucket_batches()
    bmax = [int(max(n_cand[i] for i in b)) for b in
            ([[rng.randrange(n) for _ in range(args.batch)] for _ in range(200)]
             if args.mode == "random" else bucketed)]

    print(f"池子 {n} 样本（{args.episodes} 局 × {args.turns} 回合，图 {args.map_size}）"
          f"  候选数 中位 {int(np.median(n_cand))} / p90 {int(np.percentile(n_cand, 90))}"
          f" / max {n_cand.max()}", flush=True)
    print(f"批内最大候选数：中位 {int(np.median(bmax))}  均值 {np.mean(bmax):.0f}  "
          f"→ 相对随机组批的算力比 {np.mean(bmax) / 1:.0f}（mode={args.mode}）", flush=True)

    if args.mode == "stats":
        return

    # ---- 计时（照抄 bc.py 的内层循环）----
    opt = torch.optim.Adam(model.parameters(), lr=3e-4)
    t0 = time.time()
    for _ in range(args.steps):
        chunk = [pool[i] for i in next(gen)]
        (grid, glob, cand, mask), acts, rets = pack(chunk, model.n_tiles)
        logits, v = model(grid, glob, cand, mask)
        logp = F.log_softmax(logits, dim=-1)
        _lp = logp.gather(1, torch.as_tensor(acts).unsqueeze(1)).squeeze(1)
        loss = -_lp.mean() + 0.5 * F.mse_loss(v, torch.as_tensor(rets)) \
            / max(1.0, float(torch.as_tensor(rets).var()))
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
        opt.step()
    dt = time.time() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"\nBUCKET|mode={args.mode}|threads={n_threads}|pool={n}"
          f"|batch_max_median={int(np.median(bmax))}"
          f"|steps={args.steps}|sec={dt:.1f}|per_step={dt / args.steps:.3f}"
          f"|peak_rss_mb={rss:.0f}", flush=True)


if __name__ == "__main__":
    main()
