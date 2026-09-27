# -*- coding: utf-8 -*-
"""**加核到底有用吗** —— 拿真实的采集回路量，不推算。

    python rl/cores_bench.py <每worker局数> <核数> [proc|thread]

★ 为什么必须**现在**重验：轮廓变了。DP 被 C 扩展干掉之后，
  前向从 17% 涨到 **~50%**（见 `rl/batch_fwd_probe.py`），而前向是 **torch 多线程**的
  ⇒ N 个 worker 各开多线程会互相抢（`run_ecs.sh` 因此钉死 `--threads 1`）。
  另外 **`rl/_combat_fast.c` 没有释放 GIL** ⇒ 它对"线程"这条路是硬阻塞，
  对"进程"无所谓。**两种都量**，别拿进程的结论去推线程。

★★ 口径：**固定工作量、量时间**（每 worker 打**固定 M 局**）。
  **不要用"跑 T 秒数局数"** —— 那个循环每局才检查一次截止，一局 50 秒就超时
  55 秒，分母是假的（我第一版就这么错：4 核报出 145.8 秒的"并发窗口"）。
  吞吐 = `M × 核数 / 最慢那个 worker 的秒数`。

★ torch 与 rl 模块都在**父进程**先 import，fork 直接继承（省掉每个 child 的重导入）。

★★★ 2026-09-27 实测（Pi 5，每 worker 固定 5 局，`torch.set_num_threads(1)`）：

    | 核数 | **聚合吞吐**（Σ n_i/t_i） | vs 1 核 | 各 worker 耗时 |
    |---|---|---|---|
    | 1 | **53.06** 点/秒 | 1.00× | 182.1 s |
    | 2 | **96.58** | **1.82×** | 203.5 / 258.1 |
    | 4 | **131.12** | **2.47×** | 302 / 379 / 407 / 403 |

  ★ `throttled=0x0`、58.7°C ⇒ **不是降频/掉压**（这台 Pi 是超频过、余量为零的机器，
    所以这条必须排除掉才敢用这个数）。

  ★★★ **反直觉但重要：我们刚把"加核"这条路削弱了。**

    | | 单核 | 4 核倍数 |
    |---|---|---|
    | 优化**前**（DP 是纯 Python） | 16.0 步/秒 | **3.30×** |
    | 优化**后**（DP 是 C，前向占 50%） | **53.1** 点/秒 | **2.47×** |

    纯 Python 的 DP 是**指令受限**（缓存驻留、几乎不吃带宽）⇒ 4 核近乎线性；
    现在前向占一半，torch 的矩阵乘是**内存带宽受限** ⇒ 4 核共享带宽，
    单核速率掉到 45~66% ⇒ **两条路部分重叠，不是相乘的**。

  ★★ 两个**测法坑**（都踩过）：
    ① **不要用"跑 T 秒数局数"** —— 循环每局才检查一次截止，一局 50 秒就超时 55 秒
       ⇒ 分母是假的（4 核报出 145.8 秒的"并发窗口"）。**固定工作量、量时间。**
    ② `sum(n) / 最慢worker` 会把"早收工的 worker 空转"算进分母 ⇒ 偏小。
       要报 `Σ n_i/t_i`（每个 worker 自己的速率之和）。

  ★★★ **线程这条路是死的**（同一台机、同一工作量）：

    | 核数 | 进程 | **线程** |
    |---|---|---|
    | 2 | 96.58 | **72.39** |
    | 4 | 131.12 | **71.10** |

    **4 个线程 71.10 ≈ 2 个线程 72.39 —— 完全平的。** GIL 锁死（`throttled=0x0`，
    不是降频）。线程相对单进程的 53.06 只有 **1.34×** —— 那 1.34× 正是
    「torch 算子在 C 里会放 GIL」漏出来的部分。
    ⇒ **多核只能用进程。** ★ 但注意 `rl/_combat_fast.c` **没有释放 GIL**：
      进程无所谓，**线程是硬阻塞**。而**批量前向会把更多时间挪进 torch**
      （torch 算子放 GIL）⇒ **批量之后线程的 1.34× 上限会抬高**，
      那时"一个进程装 N 个沙盒"才可能比 N 个进程更划算（尤其内存小的机器）。
"""
import os
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rl import train as T                             # noqa: E402
from rl.model import build_model                      # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402

M = int(sys.argv[1]) if len(sys.argv) > 1 else 5      # 每 worker 固定局数
K = int(sys.argv[2]) if len(sys.argv) > 2 else 1
MODE = sys.argv[3] if len(sys.argv) > 3 else "proc"
torch.set_num_threads(1)                              # 与生产同口径


def work(wid: int):
    """打固定 M 局（种子确定），返回 `(决策点数, 秒数)`。"""
    n = 0
    t0 = time.time()
    for g in range(wid * 1000, wid * 1000 + M):
        size = 10 + (g % 5)
        sb = Sandbox(seed=6000 + g, size=size, t_max=100, n_nations=2,
                     halls_known=True, territory=True,
                     alliances="random2v2").reset()
        torch.manual_seed(g)
        nets = {p: build_model(mem_slots=8) for p in sb.players}
        for net in nets.values():
            net.eval()
        steps, _info = T.collect_episode(nets, sb, rng=np.random.default_rng(g))
        n += len(steps)
    return n, time.time() - t0


if __name__ == "__main__":
    if MODE == "thread":
        import threading
        res = [None] * K

        def run(i):
            res[i] = work(i)

        ts = [threading.Thread(target=run, args=(i,)) for i in range(K)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    else:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(K) as pool:
            res = pool.map(work, range(K))

    per = [r[0] for r in res]
    els = [r[1] for r in res]
    span = max(els)
    total = sum(per)
    # ★ 两个口径都报：`sum/span` 把"早收工的 worker 空转"也算进分母；
    #   `Σ n_i/t_i` 是**每个 worker 自己的速率之和**，才是真正的聚合吞吐。
    sumrate = sum(n / e for n, e in zip(per, els))
    print(f"{MODE} 核数={K}  最慢 worker {span:.1f}s")
    print(f"  各 worker: 决策点 {per}")
    print(f"             秒数   {[round(e, 1) for e in els]}")
    print(f"  **聚合吞吐 = {sumrate:.2f} 点/秒**"
          f"（总决策点 {total}；sum/span 口径 {total / span:.1f}）")
