# -*- coding: utf-8 -*-
"""一局 BC 的**分相计时**探针：把「老师采样」和「梯度步」分开量，判断训练放哪台机。

为什么不能只看 `rl/bc.py` 日志里的「累计 Ns」：那个数把两件性质完全不同的事混在一起——

  · **老师采样**（`collect_episode` 的纯 BC 路径）= 引擎 + 规则 AI，**全是 Python**，
    GIL 绑死，`torch.set_num_threads` 对它**一点用没有**；它只随**单核/IPC** 变。
  · **梯度步** = torch 矩阵运算，**多线程才有意义**，且随核数线性放大。

所以「本地开多线程 vs 服务器单线程」这个问题的答案，取决于两者的配比。
这个脚本就是为了把这个配比量出来。

    python3 -m experiments.speed_probe --threads 4
    python3 -m experiments.speed_probe --threads 1

输出一行机器可读的 `PROBE|...`，方便两台机对拍。
"""
from __future__ import annotations

import argparse
import random
import time

import numpy as np
import torch
import torch.nn.functional as F

from rl.bc import collect_episode, get_teacher, hit_rate, pack
from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import collate  # noqa: F401  （pack 内部用）


class _null:
    """空上下文（不用 bf16 时占位），省得在循环里写 if。"""
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=0, help="0 = 自动 = 物理核数")
    ap.add_argument("--turns", type=int, default=70)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--steps", type=int, default=250, help="每局梯度步（同 bc.py）")
    ap.add_argument("--minibatch", type=int, default=256)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--teacher", default="v9")
    ap.add_argument("--dagger-turns", type=int, default=0,
                    help="额外量一段 DAgger 局（学生=未训练模型走，老师打标签），"
                         "跑 N 回合后按每回合外推。0=不量。")
    ap.add_argument("--bf16", action="store_true",
                    help="梯度步前向用 bf16 autocast（ECS 有 AVX512-BF16）。"
                         "★必须在**真实模型**上量：简报里那个 8.6 万参数玩具上 bf16 反而更慢。")
    args = ap.parse_args()

    from rl.hw import physical_cores, set_threads
    actual = set_threads(args.threads)

    env = ZhanguoEnv(map_size=args.map_size, max_turns=args.turns)
    env.reset(0)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=args.map_size ** 2)
    opt = torch.optim.Adam(model.parameters(), lr=3e-4)
    teacher_fn = get_teacher(args.teacher, args.turns)

    # ---- 相 1：老师采样（纯 Python，线程数无关）----
    t0 = time.time()
    demos, spend, miss = collect_episode(env, args.turns, seed=args.seed,
                                         teacher_fn=teacher_fn)
    t_roll = time.time() - t0

    # ---- 相 2：250 个梯度步（torch，线程数相关）----
    # 照抄 rl/bc.py 的内层循环，别自己简化 —— 简化过的计时不是那个计时。
    amp = torch.autocast("cpu", dtype=torch.bfloat16) if args.bf16 else _null()
    rng = random.Random(args.seed)
    buf = list(demos)
    t0 = time.time()
    for _ in range(args.steps):
        chunk = [buf[rng.randrange(len(buf))] for _ in range(args.minibatch)]
        (grid, glob, cand, mask), acts, rets = pack(chunk, model.n_tiles)
        with amp:
            logits, v = model(grid, glob, cand, mask)
        logp = F.log_softmax(logits.float(), dim=-1)
        _lp = logp.gather(1, torch.as_tensor(acts).unsqueeze(1)).squeeze(1)
        loss_pi = -_lp.mean()
        rt = torch.as_tensor(rets)
        loss_v = F.mse_loss(v.float(), rt) / max(1.0, float(rt.var()))
        loss = loss_pi + 0.5 * loss_v
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
        opt.step()
    t_grad = time.time() - t0

    # ---- 相 3：日志尾部那 4 次命中率（每局都跑，不能漏记）----
    t0 = time.time()
    hit_rate(model, buf[-600:], model.n_tiles)
    hit_rate(model, buf[-600:], model.n_tiles, skip_end_turn=True)
    t_hit = time.time() - t0

    total = t_roll + t_grad + t_hit
    n_par = sum(p.numel() for p in model.parameters())
    print(f"\nPROBE|threads={actual}|cores={physical_cores()}"
          f"|prec={'bf16' if args.bf16 else 'fp32'}"
          f"|params={n_par}|samples={len(demos)}|miss={miss}"
          f"|roll={t_roll:.1f}|grad={t_grad:.1f}|hit={t_hit:.1f}|total={total:.1f}"
          f"|roll_pct={100 * t_roll / total:.0f}|grad_pct={100 * t_grad / total:.0f}",
          flush=True)

    # ---- 相 4（可选）：DAgger 局的每回合成本 ----
    # DAgger 局（bc.py 后半程）**学生自己走**，每步一次前向；学生没练好时一回合能磨到
    # ACT_SAFETY=512 步，而纯 BC 局每回合只有老师那 5~15 步。两者不同量级，
    # 用纯 BC 的秒/局去外推 DAgger 会严重低估。
    if args.dagger_turns > 0:
        t0 = time.time()
        _d, _sp, _m = collect_episode(env, args.dagger_turns, seed=args.seed + 999,
                                      teacher_fn=teacher_fn, student=model)
        dt = time.time() - t0
        print(f"DAGGER|turns={args.dagger_turns}|samples={len(_d)}|sec={dt:.1f}"
              f"|per_turn={dt / args.dagger_turns:.2f}"
              f"|est_70turns={dt / args.dagger_turns * args.turns:.0f}", flush=True)


if __name__ == "__main__":
    main()
