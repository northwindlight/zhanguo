# -*- coding: utf-8 -*-
"""机器相关的线程数决策：**按物理核数**，不按逻辑核数。

为什么值得单独一个模块（2026-09-11 实测，两台机背靠背）：

| | 物理核 | 逻辑核 | 1 线程 | N 线程 |
|---|---|---|---|---|
| ECS（EPYC，1 物理核 + SMT） | **1** | 2 | 0.35 s/梯度步 | 2 线程 **慢 3.4×** |
| Pi 5（A76，4 真核） | **4** | 4 | 1.26 s/梯度步 | 4 线程 **快 1.61×** |

SMT 那多出来的一个逻辑核**对向量计算收益为零**（FP 单元单线程就吃满了，见
`训练机器性能简报.md`），而 torch 的 OpenMP 池照样按逻辑核起线程 ——
于是纯粹是同步开销。`torch.get_num_threads()` 默认按**物理核**取，本来就是对的；
踩坑的是各个脚本把 `--threads` 的默认值**写死成 4**，在 ECS 上等于手动打开超订。

所以默认值改成 **0 = 自动**：ECS 上自动得到 1，Pi 上自动得到 4，
两边都不用记着传参（`--threads N` 仍然可以覆盖）。
"""
from __future__ import annotations


def physical_cores() -> int:
    """物理核数。

    x86：`/proc/cpuinfo` 里 `(physical id, core id)` 去重 —— 同一物理核的两个
    超线程**共享 core id**，所以去重后就是物理核数（ECS 上 2 条 processor → 1）。
    ARM（Pi 5，无 SMT）：没有这两个字段，processor 条数即物理核数。
    读不到 `/proc/cpuinfo`（非 Linux）时退回 `os.cpu_count()`。
    """
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        import os
        return os.cpu_count() or 1

    cores = set()
    n_proc = 0
    phys = core = None
    for line in text.splitlines() + [""]:
        if not line.strip():                      # 块分隔（末尾补一空行收尾）
            if phys is not None and core is not None:
                cores.add((phys, core))
            phys = core = None
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "processor":
            n_proc += 1
        elif key == "physical id":
            phys = val
        elif key == "core id":
            core = val
    if cores:
        return len(cores)
    return max(1, n_proc)


def set_threads(n: int = 0) -> int:
    """设 torch 线程数并返回实际生效值。`n <= 0` = 自动（物理核数）。

    只动 torch 自己的 OpenMP 池（`torch.set_num_threads` 就是干这个的）。
    进程级的 `OMP_NUM_THREADS` 等环境变量要在**启动前**设才有意义 ——
    那是 `rl/run_ecs.sh` 的活，不在这里做（import torch 已经发生，设了也晚）。
    """
    import torch

    want = n if n and n > 0 else physical_cores()
    torch.set_num_threads(max(1, int(want)))
    return torch.get_num_threads()
