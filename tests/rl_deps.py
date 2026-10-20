# -*- coding: utf-8 -*-
"""★ **可选依赖的探针** —— 让"没装 torch"的机器**跳过**而不是**报错**。

    为什么需要
    ─────────
    主线的 CI（`.github/workflows/ci.yml`）跑的是**全量** `unittest discover -s tests`，
    依赖只装了 `requirements-vision.txt`（**没有 torch** —— torch 有 450MB+，
    对主线回归是纯负担）。
    ⇒ RL 线那些**要 torch**的测试（模型/训练/联赛）在 CI 上必须**优雅跳过**，
    否则整个 CI 挂掉 —— 而它们**本来就该在装了 torch 的机器上跑**
    （RL 线的开发机就是）。

    ★ **口径**：`skip` 不是"放过"—— 装了 torch 的机器上**照跑**，
    跳过的机器上**明说**"没装 torch"（不是静默）。

    用法：
        from tests.rl_deps import torch  # 或 has_torch()
        @unittest.skipUnless(has_torch(), "需要 torch（RL 线依赖）")
        class TestX(unittest.TestCase): ...
"""
from __future__ import annotations

try:                                     # pragma: no cover - 环境探测
    import torch                          # noqa: F401
    HAS_TORCH = True
    TORCH_ERROR = ""
except Exception as exc:                 # pragma: no cover - 环境探测
    torch = None                          # type: ignore[assignment]
    HAS_TORCH = False
    TORCH_ERROR = str(exc)


def has_torch() -> bool:
    """本机装了 torch 吗（装了才跑 RL 的模型/训练测试）。"""
    return HAS_TORCH


def torch_or_skip() -> str:
    """没装 torch 时给 `unittest.skip` 用的理由。"""
    return f"需要 torch（RL 线依赖）；本机没装：{TORCH_ERROR or 'ModuleNotFoundError'}"
