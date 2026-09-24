# -*- coding: utf-8 -*-
"""**纯打分贪心**（1 步前瞻）—— 用户 2026-09-24 的退路：

> 「要是搜索也写不好就**纯打分**，越近敌方分越高，越多地分越高，
>   丢地扣小分，损兵扣大分，丢家直接输」

对每个合法动作：在 `clone()` 上执行 → `evaluate.score()` 打分 → 选最高的那个。
**没有搜索、没有 rollout** —— 走成什么样**完全由打分函数决定**（打分器怎么写，它就怎么走）。

★ 它同时是 MCTS 的**对照线**：搜索若不能显著强过 1 步贪心，就说明搜索没起作用、
  该退回纯打分。这个对比本身就是一条判据，值得先跑出来。
"""
from __future__ import annotations

import math

from . import evaluate
from .sandbox import PLAYERS


def action_scores(sb) -> list[tuple]:
    """每个合法动作的**事后打分**：`[(action, ok, score), …]`（诊断/调权重用）。"""
    me = sb.current_player()
    if me is None:
        return []
    enemy = next((n for n in PLAYERS if n != me), None)
    out = []
    for a in sb.legal():
        sim = sb.clone()
        ok, _ = sim.step(a)
        s = evaluate.score(sim.world, me, enemy) if ok else -math.inf
        out.append((a, ok, s))
    return out


def best_action(sb):
    """选"执行后打分最高"的动作。全部动作同分时，返回最后一个（**收手 END** 排在末尾
    ⇒ 无事可做时自然会收手，而不是拿同一支军反复试）。"""
    best, best_s = None, -math.inf
    for a, ok, s in action_scores(sb):
        if ok and s >= best_s:
            best, best_s = a, s
    if best is None:                       # 一个都执行不了 ⇒ 收手
        from .sandbox import END
        return (END, None, None, None)
    return best