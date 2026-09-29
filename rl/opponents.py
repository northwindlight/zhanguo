# -*- coding: utf-8 -*-
"""**对手池** —— 冻结、不学、可插拔的对手（用户 2026-09-29：「支持多种对手模型」）。

★★ 为什么要有它（用户 2026-09-29 的诊断）：
  · **一直以来对抗的都是 PPO** ⇒ 对手**和我们一样弱、还在动**（非平稳）；
  · 而这个游戏**防守占优**（地形防御 森林+10/丘陵+25/山地+50、城堡每级 +10%，
    外加 `_turn_order` 写的**后手信息优势**：后手能看到先手已落子的世界状态再应对）；
  · 加上**势函数太烂**（实测 93.5% 由「兵力+国土」驱动，击杀/守家/厅贡献为 0）
  ⇒ 学到的占优方式是「**顶住**」，不是「打赢」。
  ⇒ 用户的处方：**换个平稳的弱对手**（「专打差生」），**去掉势函数**，让胜负自己说话。

★ 为什么接口开在**策略**层而不是**网络**层：
  `collect_episode` 原来只把**观测**交给对手那一席。而"逐候选打分"要
  **沙盒 + 候选动作表**（`_score` 是对**局面**算的）—— 光有观测拿不到。
  ⇒ 所以对手的接口是 `act(sb, me, acts, probs)`，**在前向之后**被调用。

★★ 「贪心稻草人」为什么是 **top-k 短名单**而不是 45 个全贪心（用户 2026-09-29 拍板）：
  · **全贪心**要对**每个候选**做 `sb.clone()`，实测 **3.68 ms/次**（`deepcopy(world)` 占 94%）
    ⇒ 45 候选 = **167 ms/决策点** ⇒ 200 局 **13.3 小时**；
  · **top-k** 只要 k 次 ⇒ k=8 时 ~32 ms ⇒ **~4 小时**（同样的量级，但**不用碰 `clone()`**）。
  · ★ 而且**语义更对**：稻草人 = 「用自己的网挑 k 个，再用打分器在里面挑最好的」。
    全贪心反而会挑出很蠢的着法 —— **因为打分器本身很烂**（那正是用户说的"势函数太烂"）。

★ **冻结是硬要求**：09-26 的口径是「**谁上场谁学**」⇒ 抽到谁谁吃梯度。
  对手**必须**显式冻结（`frozen=True`），否则差生会被练掉、不再是差生。
  守卫 `tests/test_rl_opponents.py` 钉住"冻结席位不进梯度/不进 buf"。
"""
from __future__ import annotations

import numpy as np

__all__ = ["Opponent", "ScorerGreedy", "KINDS", "make", "DEFAULT_K"]


DEFAULT_K = 8             # 短名单长度（用户没指定，取 8）


class Opponent:
    """对手基类。**冻结**：不进梯度、不进 `buf`、不归档成新成员。"""

    kind = "?"
    frozen = True

    def act(self, sb, me: str, acts: list, probs: np.ndarray) -> int:
        """给**候选下标**。`probs` = 这一席自己的网给出的动作分布（可能没用）。"""
        raise NotImplementedError

    def __repr__(self) -> str:                       # pragma: no cover
        return f"<{type(self).__name__} kind={self.kind} frozen={self.frozen}>"


class ScorerGreedy(Opponent):
    """★ **贪心稻草人**：用自己的网挑 top-k，再用**打分器**在里面挑最好的。

    ★ 骨架由调用方给（用户 2026-09-29：「就正常的 L0，但是不学」）——
      本类**只管怎么挑动作**，不管权重从哪来。
    """

    kind = "scorer_greedy"

    def __init__(self, k: int = DEFAULT_K):
        self.k = int(k)

    def act(self, sb, me: str, acts: list, probs: np.ndarray) -> int:
        # ★ 局部 import：`train` 会 import 本模块 ⇒ 顶层 import 会成环。
        #   调用时 `train` 早已导入完 ✔
        from .train import _score

        k = max(1, min(self.k, len(acts)))
        order = np.argsort(-np.asarray(probs, dtype=float))[:k]
        best_i = int(order[0])
        # ★ `hold` 不改局面 ⇒ `Δscore ≡ 0`，**不用克隆**（省一次 deepcopy）。
        #   其余候选各克隆一次算"走完之后打多少分"。
        best_v = None
        for i in order:
            i = int(i)
            if acts[i][1] == "hold":
                v = 0.0                              # 相对基准：不动 = 0，够比较了
            else:
                trial = sb.clone()
                ok, _msg = trial.step(acts[i])
                if not ok:
                    continue                         # 引擎拒收（`legal()` 里本来就有这种，见 09-24 口径）
                v = float(_score(trial, me))
            if best_v is None or v > best_v:
                best_v, best_i = v, i
        return best_i


# ★ 注册表 —— 「**支持多种对手模型**」（用户 2026-09-29）。加一种 = 加一行。
KINDS: dict[str, type[Opponent]] = {
    "scorer_greedy": ScorerGreedy,
}


def make(kind: str, **kw) -> Opponent:
    if kind not in KINDS:
        raise KeyError(f"未知对手类型 {kind!r}；已知：{sorted(KINDS)}")
    return KINDS[kind](**kw)
