# -*- coding: utf-8 -*-
"""**批量前向到底摊薄掉多少** —— 直接量，不推算。

    python rl/batch_fwd_probe.py [批大小列表] [每档重复] [size_min] [size_max] [局数]

★ 背景：DP 被 C 扩展干掉之后（43.60 → 1.77 ms/点），**前向成了第一位**
  （91-100 段 11.25 ms/点 = 决策点的 50%），而它**全程是平的**（8.03 → 11.25）
  ⇒ 成本是**每步 1062 个算子的固定开销**（B=1），不是真算。
  profile 说真算 ~52% / PyTorch 调度 ~48% ⇒ 批量**只能吃掉后者**。

★ ★★ 这个探针要回答的是：`collate(rows)` **本来就是批处理器**（网格/窗口/候选
  都按批内最大补齐），那"一次喂 N 行"到底能换回多少？
  **要连 `collate` + `to_dev` 一起算** —— 批量会让**网格补到批内最大**，
  那是白烧的算力，不能只量 `forward_state`。

★ 判据：`ms/样本` 随 B 下降多少。若 B=8 只降到 0.8 ⇒ 批量没用
  （说明前向不是调度受限）；若降到 0.5 ⇒ 批量值 2×。

★★★ 2026-09-27 实测（Pi 5 / aarch64，`torch.set_num_threads(1)`，16 个真实局面
    size 12 各推 40 步，每档 9 次取中位）：

    | B | collate+搬运 | 前向 | ms/样本 | **相对 B=1** | 网格填充率 |
    |---|---|---|---|---|---|
    | 1 | 0.34 | 7.43 | 7.77 | 1.00× | 100% |
    | 2 | 0.43 | 9.61 | 5.02 | **1.55×** | 78% |
    | 4 | 0.77 | 14.02 | 3.70 | **2.10×** | 76% |
    | 8 | 1.34 | 23.58 | 3.12 | **2.50×** | 55% |
    | 16 | 2.43 | 44.28 | 2.92 | **2.66×** | 52% |

  ★ **比我推算的 1.72× 好得多。** 我按"真算 52% / 调度 48%"（x86/size16 的 profile）
    推 `1/(0.52+0.48/B)`，实测 B=8 是 **2.50×** ⇒ **Pi 上每算子的开销占比更高（~62%）**。
  ★ 这已经是**净收益**：已扣掉 `collate` 与**网格按批内最大补零**的代价
    （填充率掉到 52% 还能赢这么多）。
  ★ **还有余量**：填充率只剩 52% ⇒ **按 size 分组批（或固定 size）能再收回来一块**。
  ★ 附带好处：**一个进程装 N 个沙盒**比 N 个进程各装一份**内存省得多**
    （Pi 上每 worker 1.5 GB 是真实约束）。
  ★★ **口径警告**：这是 **Pi/aarch64** 的数。ECS 是 x86+AVX512，真算那半边更快、
    调度占比不一定同 ⇒ **要拿 ECS 的数才算数**（同一个探针，一分钟）。
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import train as T                        # noqa: E402
from rl.encode import obs_of                     # noqa: E402
from rl.model import build_model                  # noqa: E402
from rl.sandbox import Sandbox                    # noqa: E402

BS = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1
                       else "1,2,4,8,16".split(","))]
REP = int(sys.argv[2]) if len(sys.argv) > 2 else 9
SMIN = int(sys.argv[3]) if len(sys.argv) > 3 else 12
SMAX = int(sys.argv[4]) if len(sys.argv) > 4 else 12
NG = int(sys.argv[5]) if len(sys.argv) > 5 else max(BS)

torch.set_num_threads(1)      # ★ Pi 是用户干活的机器；也和 `run_ecs.sh --threads 1` 同口径

# ---- 造 NG 个**真实局面**：各推 40 步，取那时的观测 ----
sbs, obs = [], []
for i in range(NG):
    size = SMIN + (i % (SMAX - SMIN + 1))
    sb = Sandbox(seed=4000 + i, size=size, t_max=100, n_nations=2,
                 halls_known=True, territory=True,
                 alliances="random2v2").reset()
    torch.manual_seed(i)
    nets = {p: build_model(mem_slots=8) for p in sb.players}
    for n in nets.values():
        n.eval()
    rng = np.random.default_rng(i)
    # 推 40 个决策点，让局面长起来（早期观测太小、不代表真实开销）
    for _ in range(40):
        if sb.is_terminal():
            break
        me = sb.current_player()
        acts = sb.legal()
        if not acts:
            break
        a = acts[int(rng.integers(len(acts)))]
        sb.step(a)
    me = sb.current_player() or sb.players[0]
    obs.append(obs_of(sb, me, sb.legal()))
    sbs.append((sb, me, nets))

net = build_model(mem_slots=8)
net.eval()
print(f"批大小 {BS} · 每档 {REP} 次取中位 · {NG} 个真实局面"
      f"（size {SMIN}-{SMAX}，各推 40 步后取观测）\n")
print(f"{'B':>4}{'collate+to_dev ms':>19}{'forward ms':>13}{'合计 ms':>10}"
      f"{'ms/样本':>10}{'相对 B=1':>10}{'网格填充率':>12}")

base = None
for B in BS:
    if B > NG:
        continue
    rows = obs[:B]
    mem = net.mem_init(B, device="cpu")
    tc, tf = [], []
    for _ in range(REP):
        t = time.perf_counter()
        batch = T.to_dev(T.collate(rows), "cpu")
        tc.append((time.perf_counter() - t) * 1000)
        with torch.inference_mode():
            t = time.perf_counter()
            net.forward_state(batch, mem)
            tf.append((time.perf_counter() - t) * 1000)
    c, f = sorted(tc)[REP // 2], sorted(tf)[REP // 2]
    per = (c + f) / B
    if base is None:
        base = per
    # 网格填充率：真实格子数 / （批内最大 × B）—— 批越大补得越浪费
    gs = [r["grid"].shape for r in rows]
    fill = sum(h * w for _c, h, w in gs) / (max(h for _c, h, w in gs)
                                            * max(w for _c, h, w in gs) * B)
    print(f"{B:>4}{c:>19.2f}{f:>13.2f}{c + f:>10.2f}{per:>10.2f}"
          f"{base / per:>9.2f}×{fill:>11.0%}", flush=True)

print("\n★ `相对 B=1` 就是批量的真实收益（**已含 collate 与网格补零的代价**）。"
      "\n★ 若它远低于 `1/(0.52+0.48/B)`（纯调度摊薄的理想值），差额就是"
      "\n  网格/窗口补零白烧掉的那部分 —— 那就该**按 size 分组**再批。", flush=True)
