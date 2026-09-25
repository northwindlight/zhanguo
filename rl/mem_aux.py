# -*- coding: utf-8 -*-
"""**潜槽的辅助目标**：「**下一帧即将离开视野的那部分**」（用户 2026-09-25 的隐空间设计）。

它要解决什么
────────────
只靠策略梯度，M 个潜槽**学不出"该记什么"** —— 梯度信号太间接，而槽里起初是空的
（见 `vocab.MEM_GATE_BIAS`：写门初值 ≈ 0.047，几乎不写）。所以给一条**直接的**
监督：**看着即将消失的东西，逼槽把它编码下来**。

    在 t 帧我看得见一支军的 (位置/血量/番号)；到 t+1 帧它不在视野里了
    （走开了 / 被打没了 —— 我**看不出是哪一种**，这正是战争迷雾）。
    ⇒ 槽在 t 帧就该已经把它**装进去了**，否则"它再露头时我认不出来"。

★★ 三条纪律（都对着"会静默教错"的形状）：

  ① ★★ **目标用后见之明，输入**绝不**。 "哪些军会离开视野"这件事要**事后**才知道
     （t+1 帧才看得见少了谁）。用在**损失的目标**上是**正当的监督学习**；
     但它**一个字都不许进观测** —— 进了就等于告诉模型"你即将失去什么"，
     那是**真玩家不可能有的情报**。见 `tests/test_rl_memory.py` 里钉这条的守卫。
  ② **口径与番号账本同一份**（`war_memory.visible_foes`）—— "哪些算敌人 / 野人算不算"
     抄两遍就会慢慢漂开，而漂开的后果是**在教模型错的东西**且不报错。
  ③ **什么都不离开时也要训**（目标全 0）。"这一帧没有东西要丢"本身就是信息：
     它让槽学会**区分**"该记"和"不用记"，否则槽会把"看见过的一切"都塞进去
     ⇒ 槽很快饱和成噪声（M 只有 8 个）。
"""
from __future__ import annotations

import numpy as np

from . import vocab as V


def aggregate_lost(lost: list[dict], hx: int, hy: int, size: int) -> np.ndarray:
    """"即将离开视野"的那批军 → **定长**向量（`V.M_AUX` 列，都已归一到 ~O(1)）。

    `lost` = `[{"x","y","kind","hp"}, …]`（见 `mem_aux.diff_lost`）。
    ★ 定长是必须的：槽的辅助头输出宽度必须固定，而"丢了几支军"逐帧变。
    ★ **和**而不是均值 —— 均值会让"丢 1 支"和"丢 4 支、位置恰好平均到同一点"
      给出**同一个目标**（那是把数量信息抹掉，正是 `min(1.0, …)` 那类坑的翻版）。
    """
    out = np.zeros(V.M_AUX, np.float32)
    if not lost:
        return out
    r = float(V.LOST_REF)
    ps = float(max(1, size))
    s_hp = s_dx = s_dy = 0.0
    kind_sum = np.zeros(len(V.UNIT), np.float32)
    for a in lost:
        s_hp += float(a.get("hp", 0))
        s_dx += (float(a["x"]) - hx) / ps
        s_dy += (float(a["y"]) - hy) / ps
        if a.get("kind") in V.UNIT:
            kind_sum[V.UNIT.index(a["kind"])] += 1.0
    out[0] = min(1.0, len(lost) / r)                 # 几支
    out[1] = min(1.0, s_hp / (100.0 * r))            # 总血量 / 满编 r 支
    out[2] = s_dx / r                                # 位置之和（相对我家核心，已归一）
    out[3] = s_dy / r
    out[4:4 + len(V.UNIT)] = np.minimum(1.0, kind_sum / r)
    return out


def diff_lost(prev: dict, now: dict) -> list[dict]:
    """`prev` 里**看得见、而 `now` 里看不见了**的那些（= 即将离开视野的）。

    ★ 输入必须是两次 `visible_foes` 的**快照**（`{gid: {x,y,kind,hp,…}}`）。
    ★ **不区分"走开了"与"被打没了"** —— 我**看不出**是哪一种，这正是迷雾的代价；
      辅助损失要教的是"把当时那个样子记住"，而不是"判断它的生死"（判断是模型的活）。
    """
    return [rec for gid, rec in prev.items() if gid not in now]