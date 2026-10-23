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


def execute_order(acts, order, step, current_player) -> tuple[bool, int | None,
                                                                bool, int]:
    """★ 按 `order` 依次尝试动作 —— **失败后必须检查状态变了没有**。

    返回 `(ok, used_pos, switched, refused)`：
      · `ok`       —— 有一个动作真的执行了；
      · `used_pos` —— 用的是 `order` 里的第几个（`0` = 首选就成）；
      · `switched` —— ★★ **失败把回合交给了下一位玩家**（⇒ 这批候选**作废**）；
      · `refused`  —— 被引擎拒收的次数。

    ★★★★ 2026-10-23 修（review P1-1）：**"失败"不等于"状态没变"**。
      引擎的"**盲推**"（打看不见的目标）**失败时也会消耗行动**
      ⇒ `_auto_advance` 把回合**交给下一位玩家**
      ⇒ 剩下那几个候选**是上一位玩家的** ⇒ 继续执行就等于**替下一位玩家落子**
      （实测：甲前向一次、乙前向零次，评估器却替乙执行了一个 hold）。
      ⇒ 现在：**一旦当前玩家变了，这批候选立刻作废**（返回 `switched=True`），
        由调用方**重新取观测与候选**（下一位玩家自己的决策点）。
      ★ 判据用 `current_player()`（**引擎/沙盒的事实**），不猜。

    ★ 为什么放在这个模块：这里**不依赖 torch** ⇒ CI（不装 torch）**也测得到**
      （`rl/eval_fixed.py` 顶部 import torch ⇒ 那边测不到）。
    """
    me = current_player()
    refused = 0
    for pos, aidx in enumerate(order):
        ok, _msg = step(acts[int(aidx)])
        if ok:
            return True, pos, False, refused
        refused += 1
        if current_player() != me:
            return False, None, True, refused
    return False, None, False, refused
