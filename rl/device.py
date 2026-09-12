# -*- coding: utf-8 -*-
"""设备选择与**批量搬运**。

为什么单独一个模块：`rl/` 的模型/数据通路原先全部写死在 CPU 上（`hw.py` 只管线程数），
2026-09-12 接 GPU 机房时才加。搬运是**递归**的，因为 `collate_cand`/`collate_window`
返回的是嵌套 dict + tensor，逐个字段 `.to()` 会写漏（写漏的表现是
`Expected all tensors to be on the same device`，而且只在某些分支上炸）。

★**搬运点只有两处**，别在各处零散地 `cuda()`：
1. `ppo.forward_batch` —— 所有前向（训练、评估、DAgger 采样）的唯一入口；
2. `bc.py` 的训练步 —— 它直接调 `model(...)`，没走 `forward_batch`。
"""
from __future__ import annotations

import torch


def pick_device(spec: str = "auto") -> torch.device:
    """`"auto"` → 有 CUDA 用 cuda，否则 cpu；也可显式 `"cuda"` / `"cuda:1"` / `"cpu"`。

    ★显式写了 `cuda` 而没有可用卡时**报错而不是静默退回 CPU** —— 静默退回的代价是
    "这一炉怎么这么慢"，而那种问题在 8 小时的跑法里能被发现得太晚。
    """
    if spec in (None, "", "auto"):
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dev = torch.device(spec)
    if dev.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(f"指定了 {spec} 但没有可用的 CUDA 设备")
    return dev


def model_device(model) -> torch.device:
    """模型参数所在的设备（无参数时退回 cpu）。"""
    try:
        return next(model.parameters()).device
    except StopIteration:
        return torch.device("cpu")


def move_to(x, dev):
    """递归把 tensor 搬到 `dev`；dict/list/tuple 原样保结构；其它类型不动。

    `non_blocking=True` 只在锁页内存下才有意义，这里没有锁页，纯属无害的默认。
    """
    if isinstance(x, torch.Tensor):
        return x.to(dev, non_blocking=True)
    if isinstance(x, dict):
        return {k: move_to(v, dev) for k, v in x.items()}
    if isinstance(x, list):
        return [move_to(v, dev) for v in x]
    if isinstance(x, tuple):
        return tuple(move_to(v, dev) for v in x)
    return x


def sync(dev) -> None:
    """CUDA 是异步的 —— **计时前后都要调**，否则量到的是"提交耗时"不是"算完耗时"。"""
    if torch.device(dev).type == "cuda":
        torch.cuda.synchronize(dev)
