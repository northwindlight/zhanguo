# -*- coding: utf-8 -*-
"""**批量采集回路值多少** —— 同一个进程里「批量 vs 逐局」跑同一批沙盒。

    python rl/batch_rollout_probe.py [沙盒数] [size_min] [size_max]

★ 为什么这样比才对：批量摊的是**每算子固定开销**，与核数无关
  ⇒ 把两边放在**同一个进程、同一个线程**里比，才量得到它自己的价值
  （混进核数就把两件事搅在一起了）。
★ 两边用**同一批种子** ⇒ 打的是同一批局（数值上会因为合批差最后几个 ulp，
  动作可能分叉，所以**比的是吞吐不是结果**）。
★ `rl/batch_fwd_probe.py` 量的是**前向那一小段**（2.5×）；
  这里量的是**整条采集回路**（观测/引擎/打分/落子都在里面，那些不享受批量）
  ⇒ 两个数的差距就是 Amdahl。
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import train as T                        # noqa: E402
from rl.model import build_model                  # noqa: E402
from rl.sandbox import Sandbox                    # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
SMIN = int(sys.argv[2]) if len(sys.argv) > 2 else 10
SMAX = int(sys.argv[3]) if len(sys.argv) > 3 else 14
torch.set_num_threads(1)


def fresh():
    """N 个沙盒 + **一张网**（同一张网才可能并成一批）。"""
    torch.manual_seed(999)
    net = build_model(mem_slots=8)
    net.eval()
    sbs = []
    for i in range(N):
        sbs.append(Sandbox(seed=8000 + i, size=SMIN + i % (SMAX - SMIN + 1),
                           t_max=100, n_nations=2, halls_known=True,
                           territory=True, alliances="random2v2").reset())
    return {p: net for p in sbs[0].players}, sbs


nets, sbs = fresh()
t0 = time.perf_counter()
tot_a = 0
for i, sb in enumerate(sbs):
    steps, _info = T.collect_episode(nets, sb, rng=np.random.default_rng(i))
    tot_a += len(steps)
ta = time.perf_counter() - t0

nets2, sbs2 = fresh()
rngs = [np.random.default_rng(i) for i in range(N)]
t0 = time.perf_counter()
stepss, _infos = T.collect_episodes_batched(nets2, sbs2, rngs=rngs)
tot_b = sum(len(s) for s in stepss)
tb = time.perf_counter() - t0

print(f"N={N} 沙盒 · size {SMIN}-{SMAX} · t_max 100 · 单线程\n")
print(f"  逐局 `collect_episode`          {tot_a:>6} 决策点  {ta:>7.1f}s"
      f"  = {tot_a / ta:>6.2f} 点/秒")
print(f"  批量 `collect_episodes_batched` {tot_b:>6} 决策点  {tb:>7.1f}s"
      f"  = {tot_b / tb:>6.2f} 点/秒")
print(f"\n  ⇒ **批量 / 逐局 = {(tot_b / tb) / (tot_a / ta):.2f}×**"
      f"（决策点数 {tot_a} vs {tot_b}，差 {abs(tot_a - tot_b) / max(1, tot_a):.1%}"
      f" —— 合批改最后几个 ulp，动作会分叉，正常）")
