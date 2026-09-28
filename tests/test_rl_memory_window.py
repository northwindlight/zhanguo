# -*- coding: utf-8 -*-
"""`k` 组（记忆中的敌军）的**遗忘规则** —— 2026-09-28 从"硬窗"改成"指数衰减"的守卫。

★ 病灶（用户当场两句）：
  · 「**至少能记忆 30 回合**，和 LLM 一个水平」
  · 「**k 组不要悬崖**，弄类似的逻辑」（= 与潜槽那套指数保留同一形状）

  原来 `war_memory.DEFAULT_MAX_AGE = 20` 是一个**硬窗**：
  `age > 20` ⇒ `known()` / `cell_ages()` **一条都不给** —— **第 21 回合整条蒸发**。
  现在：
    · `decay(age) = 0.5 ** (age / HALF_LIFE)`，`HALF_LIFE = 30`（与潜槽的 `(1-gate)` 同形）；
    · **排名按 decay 降序**取前 `cap` ⇒ 老记录是被**更新的记录挤出去**的（连续容量压力），
      不是被一个阈值一刀砍掉；
    · `DEFAULT_MAX_AGE` 退化成**很远的安全网**（`FLOOR_HALVES=10` 个半衰期 = 300 回合）
      —— 一局才 ~38 回合，**碰不到它**。
    · "幽灵"风险改由**明说的陈旧度**兜底：`K_AGE = age/HALF_LIFE` 读作"几个半衰期之前"。

★★ 这里钉**五件事**：
  ① `vocab.AGE_SCALE` **必须等于** `war_memory.HALF_LIFE`（两个独立字面量，不许各自漂）；
  ② `decay` 是指数：半衰期处正好 0.5、单调、`decay(0)=1`、负 age 夹到 1；
  ③ ★ **一局的实际年龄范围内不许有悬崖** —— 这条是"不要悬崖"的**直接检验**
     （旧规则下 age=21 会返回空 ⇒ 这条会红）；
  ④ 容量顶掉的是**最老的**（连续压力，不是阈值）；
  ⑤ 安全网**远在一局之外**（否则"没有悬崖"只是把悬崖挪远了一点）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import vocab as V                             # noqa: E402
from rl import war_memory as W                        # noqa: E402

GAME_TURNS = 38           # 实测一局平均回合数（`train` 日志）
TYPICAL_AGES = range(1, 61)   # 一局里能出现的年龄（含长局余量）


def _mem(name: str = "甲", gid: int = 7, turn: int = 100) -> W.WarMemory:
    """手工塞一条"最后在 turn 看见 gid"的记录。

    ★ 不走 `observe()`（那要一整个 `World` + 视野 mask），这条用例问的是**读路径的老化**，
      与"怎么看见的"无关 —— 直接写账本更聚焦，也快得多。
    """
    m = W.WarMemory()
    m._by.setdefault(name, {})[gid] = {
        "turn": int(turn), "x": 3, "y": 4, "kind": "步", "hp": 100, "no": 1}
    return m


class TestKWindowHasNoCliff(unittest.TestCase):

    def test_age_scale_matches_the_half_life(self):
        """① 两个独立字面量（`vocab` 引 `scoring`，没做 import 耦合）—— 不许各自漂。"""
        self.assertEqual(
            V.AGE_SCALE, float(W.HALF_LIFE),
            f"`AGE_SCALE={V.AGE_SCALE}` ≠ `HALF_LIFE={W.HALF_LIFE}` —— `K_AGE` 的分母"
            f"与它要表达的半衰期脱节了")

    def test_decay_is_exponential(self):
        """② 与潜槽的 `(1-gate)` 同一个形状。"""
        self.assertAlmostEqual(W.decay(0), 1.0, places=12)
        self.assertAlmostEqual(W.decay(W.HALF_LIFE), 0.5, places=12,
                               msg="半衰期处不是 0.5 ⇒ 名字撒谎了")
        self.assertAlmostEqual(W.decay(2 * W.HALF_LIFE), 0.25, places=12)
        self.assertAlmostEqual(W.decay(-5), 1.0, places=12, msg="负 age（调用方给错 turn）该夹到 1")
        seq = [W.decay(a) for a in range(0, 121)]
        self.assertTrue(all(x >= y for x, y in zip(seq, seq[1:])), "decay 不是单调不增")

    def test_no_cliff_within_a_games_ages(self):
        """③ ★「不要悬崖」的直接检验：一局里会出现的每个年龄都必须还回得来。

        ★ 非空泛性：旧的硬窗（`max_age=20`）下 `age≥21` 会返回 **[]** ⇒ 这条**会红**。
        """
        m = _mem(turn=100)
        for age in TYPICAL_AGES:
            got = m.known("甲", 100 + age)
            self.assertEqual(
                len(got), 1,
                f"age={age} 时那条记录就没了 —— 悬崖回来了（硬窗的形状）。"
                f"`HALF_LIFE={W.HALF_LIFE}`、`DEFAULT_MAX_AGE={W.DEFAULT_MAX_AGE}`")
            self.assertEqual(got[0]["age"], age, "返回的 age 不对")

    def test_the_safety_net_is_far_outside_a_game(self):
        """⑤ 安全网必须远在一局之外，否则只是把悬崖挪远了。"""
        self.assertEqual(W.DEFAULT_MAX_AGE, W.HALF_LIFE * W.FLOOR_HALVES)
        self.assertGreaterEqual(
            W.DEFAULT_MAX_AGE, 5 * GAME_TURNS,
            f"安全网在 {W.DEFAULT_MAX_AGE} 回合，而一局 ~{GAME_TURNS} 回合 —— 太近了")

    def test_decay_is_actually_used_to_rank(self):
        """④ 容量顶掉的是**最老的** —— 连续压力，不是阈值一刀切。"""
        m = W.WarMemory()
        n = m.cap + 3
        for gid in range(n):
            # gid 越大越**新**（turn 越大）
            m._by.setdefault("甲", {})[gid] = {
                "turn": 1000 + gid, "x": 1, "y": 1, "kind": "步", "hp": 100, "no": gid}
        got = m.known("甲", 1000 + n - 1)          # 以最新那回合为"现在"
        self.assertEqual(len(got), m.cap, f"没按 cap={m.cap} 截断（拿到 {len(got)} 条）")
        ages = sorted(r["age"] for r in got)
        self.assertEqual(ages, list(range(1, m.cap + 1)),
                         f"被顶掉的不是最老的（留下的 age={ages}）—— 排名没按 decay 走")
        self.assertTrue(all(W.decay(r["age"]) > 0 for r in got))

    def test_cell_ages_uses_the_same_window(self):
        """`cell_ages()`（按格填网格那两列）必须与 `known()` 同一口径。"""
        m = _mem(turn=100)
        for age in TYPICAL_AGES:
            self.assertIn((3, 4), m.cell_ages("甲", 100 + age),
                          f"age={age} 时格子读法先掉了 —— 两个读法口径不一致")
