# -*- coding: utf-8 -*-
"""★ **策略采样的取动作顺序**（纯 numpy，**不依赖 torch**）。

    为什么单独一个模块：`rl/eval_fixed.py` 顶部 `import torch` ⇒ CI（不装 torch）
    连它都导不进来 ⇒ 纯 numpy 的这段逻辑**测不到**。拆出来 ⇒ 两边都能用。

★ 口径（2026-10-23 修，review P1-1）：
  · **贪心臂** = 按概率从高到低试，取**第一个真能执行**的
    （候选集里本来就有"引擎会拒"的动作 —— 那是**设计**，见 `eval_fixed.play`）。
  · **采样臂** = **按策略概率抽**（`rng.choice(..., p=…)`），
    被拒了就在**剩下的里按剩下的概率重抽**（**重新归一化**，不是退化成均匀）。

★★ **旧口径是 `rng.permutation(len(p))`（均匀随机，完全不看策略概率）**
  ⇒ 把策略从"强烈偏好 hold"换成"强烈偏好 attack"，同种子的行动**一模一样**
  ⇒ `--sample`（用户定的**唯一判据**）**测不出训练效果**。
"""
from __future__ import annotations

import numpy as np


def action_order(p: np.ndarray, rng: np.random.Generator, greedy: bool) -> list[int]:
    """★ 取动作的**顺序**（下标表）。

    `p` = 策略概率（长度 = 候选数）；`rng` = 随机源（**固定种子**才可复现）。
    返回的顺序会被逐个尝试，**第一个能执行的就用**（其余是"退到次优"的备选）。
    """
    p = np.asarray(p, dtype=float)
    if greedy:
        return [int(i) for i in np.argsort(-p)]
    tried = np.zeros(len(p), bool)
    order: list[int] = []
    for _ in range(len(p)):
        w = np.where(tried, 0.0, p)
        total = float(w.sum())
        if total <= 0.0:
            break
        idx = int(rng.choice(len(p), p=w / total))
        order.append(idx)
        tried[idx] = True
    return order
