# -*- coding: utf-8 -*-
"""**搜索逻辑**：MCTS / PUCT（用户 2026-09-24：「你先写搜索逻辑和打分器」）。

    为什么策略要交给搜索（而不是手写）
    ──────────────────────────────────
    手写"守家/侦察"走不通 —— 实测把"自家核心"当候选扔进 `grouping._solve`，
    它 `reach=0`/`spread=0` ⇒ 代价恒 0 ⇒ 求解器把全军派回家、谁也不动。
    **搜索没有这个毛病**：它能看见"我若全军压上，几步后家里被端"，
    这类"跨期的、要靠对手配合才看得出"的东西，正是规则最难写、搜索最擅长的。

    形状（双人零和，AlphaZero 式）
    ─────────────────────────────
      · 每个节点 = 一个局面 + **该谁动**（`sandbox.current_player()`）
      · 选择用 **PUCT**：`argmax( q + c·P·√ΣN / (1+N) )`
        —— 父子**同一玩家**时不翻转（甲可以连走多步），**换人**才翻转视角
      · 评估用**打分器**（`rl/evaluate.py`）：终局用 ±1，非终局用 `tanh(score/σ)`
        ⇒ 两条尺度被压到同一个 (-1,1) 区间，PUCT 的 Q 才可比
      · 试演靠 `sandbox.clone()`（`deepcopy`，8×8 上实测 ~1.8ms）

    ★ 现在先验是**均匀**的（`prior_fn=None`）—— 任务 2 的网络接上来之后，
      把它替换成网络的策略头输出即可，本文件的其余部分一行不用动。
"""
from __future__ import annotations

import math
import random

from . import evaluate
from .sandbox import END, PLAYERS


class Node:
    """MCTS 的一个节点。`W` 是**该节点玩家视角**的累计收益（回传时按视角翻转）。"""

    __slots__ = ("sb", "player", "parent", "action", "children",
                 "N", "W", "P", "untried", "priors")

    def __init__(self, sb, player, parent=None, action=None, priors: dict | None = None):
        self.sb = sb
        self.player = player                 # 该谁动（`None` = 已终局）
        self.parent = parent
        self.action = action                 # 从父节点走到这里的那一步
        self.children: list[Node] = []
        self.N = 0
        self.W = 0.0
        self.P = 0.0                         # 父节点视角的先验
        self.priors = priors or {}
        acts = [] if player is None or sb.is_terminal() else sb.legal()
        self.untried = list(acts)

    # ---------------------------------------------------------- 查询
    def q(self) -> float:
        return self.W / self.N if self.N else 0.0

    def is_leaf(self) -> bool:
        return not self.children


class MCTS:
    """PUCT 搜索。`n_sim` 次模拟后返回**访问分布**（给 RL 当策略目标）。"""

    def __init__(self, n_sim: int = 120, c_puct: float = 1.5, max_depth: int = 30,
                 sigma: float = 50.0, rng: random.Random | None = None,
                 prior_fn=None, rollout_steps: int = 0):
        self.n_sim = n_sim
        self.c_puct = c_puct
        self.max_depth = max_depth
        self.sigma = sigma                   # `tanh(score / sigma)` 的尺度
        self.rng = rng or random.Random(0)
        self.prior_fn = prior_fn             # `(sb, actions) -> list[float]`；None = 均匀
        self.rollout_steps = rollout_steps   # >0 则叶子再随机走几步（默认只打分）

    # ---------------------------------------------------------- 主循环
    def search(self, sb) -> dict:
        """在 `sb` 上搜索，返回 `{action: 访问次数}`（根节点玩家的视角）。"""
        root_player = sb.current_player()
        if root_player is None:
            return {}
        root = Node(sb.clone(), root_player, priors=self._priors(sb))
        for _ in range(self.n_sim):
            node, path = root, [root]
            # ---- 选择（PUCT 一路到底，或撞到未扩展）----
            while not node.untried and node.children:
                node = self._select(node)
                path.append(node)
                if node.sb.is_terminal():
                    break
            # ---- 扩展（一次一步）----
            if node.untried and not node.sb.is_terminal():
                action = node.untried.pop(0)
                child_sb = node.sb.clone()
                child_sb.step(action)
                child = Node(child_sb, child_sb.current_player(), node, action,
                             priors=self._priors(child_sb))
                child.P = node.priors.get(action, 1.0)
                node.children.append(child)
                node = child
                path.append(node)
            # ---- 评估 + 回传 ----
            z = self._evaluate(node.sb, root_player, len(path))
            for n in path:
                n.N += 1
                n.W += z if n.player == root_player else -z
        return {c.action: c.N for c in root.children}

    # ---------------------------------------------------------- 选择
    def _select(self, node) -> Node:
        total = sum(c.N for c in node.children) or 1
        sqrt_total = math.sqrt(total)
        best, best_u = None, -math.inf
        for c in node.children:
            q = c.q()
            if c.player != node.player:      # ★ 换人了 ⇒ 视角翻转（父在看"对我的好处"）
                q = -q
            u = q + self.c_puct * c.P * sqrt_total / (1 + c.N)
            if u > best_u:
                best, best_u = c, u
        return best

    # ---------------------------------------------------------- 评估
    def _evaluate(self, sb, root_player: str, depth: int) -> float:
        """叶子评估：**终局 ⇒ ±1**；否则打分器压到 `tanh(score/σ)`，再按需 rollout。"""
        enemy = next((n for n in PLAYERS if n != root_player), None)
        w = sb.world
        t = evaluate.terminal(w, root_player, enemy)
        if t is not None:
            return 1.0 if t > 0 else (-1.0 if t < 0 else 0.0)
        if depth >= self.max_depth:
            return self._shaped(w, root_player, enemy)
        if self.rollout_steps <= 0:
            return self._shaped(w, root_player, enemy)
        # 随机 rollout（可选；默认关）
        sim = sb.clone()
        for _ in range(self.rollout_steps):
            if sim.is_terminal():
                break
            acts = sim.legal()
            sim.step(self.rng.choice(acts))
        t = evaluate.terminal(sim.world, root_player, enemy)
        if t is not None:
            return 1.0 if t > 0 else (-1.0 if t < 0 else 0.0)
        return self._shaped(sim.world, root_player, enemy)

    def _shaped(self, w, root_player: str, enemy: str) -> float:
        return math.tanh(evaluate.score(w, root_player, enemy) / self.sigma)

    # ---------------------------------------------------------- 先验
    def _priors(self, sb) -> dict:
        """动作先验。**现在是均匀的** —— 任务 2 的网络接上来后改成网络策略头。"""
        if sb.current_player() is None or sb.is_terminal():
            return {}
        acts = sb.legal()
        if self.prior_fn is None:
            p = 1.0 / max(1, len(acts))
            return {a: p for a in acts}
        ps = self.prior_fn(sb, acts)
        tot = sum(ps) or 1.0
        return {a: p / tot for a, p in zip(acts, ps)}


# ---------------------------------------------------------------- 对外：策略 / 选动作
def policy(sb, *, n_sim: int = 120, temperature: float = 1.0, rng=None,
           **kw) -> dict:
    """搜索 → **归一化访问分布** `{action: π}`（给 RL 当策略目标）。

    `temperature → 0` ⇒ 退化成"访问最多那一个"（贪心）。
    """
    visits = MCTS(n_sim=n_sim, rng=rng, **kw).search(sb)
    if not visits:
        return {}
    if temperature <= 1e-6:
        best = max(visits, key=lambda a: visits[a])
        return {best: 1.0}
    weights = {a: v ** (1.0 / temperature) for a, v in visits.items()}
    tot = sum(weights.values()) or 1.0
    return {a: w / tot for a, w in weights.items()}


def best_action(sb, *, n_sim: int = 120, rng=None, **kw):
    """搜索 → 访问最多的那个动作（贪心，评估/对照用）。"""
    p = policy(sb, n_sim=n_sim, temperature=0.0, rng=rng, **kw)
    return next(iter(p)) if p else (END, None, None, None)