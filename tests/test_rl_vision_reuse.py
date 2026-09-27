# -*- coding: utf-8 -*-
"""`legal()` 里的视野**只许算一次** —— 2026-09-27 那一刀的守卫。

★ 病灶：`_probe_cells` 原来**在每支军队上**各算一次
  `pathfind.vision_mask(self.world, name)`，而 `name` 在整次 `legal()` 里**恒定**、
  world 在 `legal()` 期间**不变** ⇒ 那是**纯重复计算**。
  实测（`cProfile` + 抓调用栈）：`vision_mask` **5 次/决策点**里，
  `legal()` 占 1 次、`obs_of` 占 1、`visible_enemies` 占 1、`_score` 占 2；
  修之前 `legal()` 那一路是**按军数**翻倍的（军队多时到 10.9 次/决策点）。

★★ 为什么这里必须量**次数**而不是量结果：
  **结果本来就一样** —— 这正是"它是纯重复"的证据。只钉结果等于没钉
  （改动被回退也照样绿）。所以直接数调用次数：
  把 `mask` 提回循环里 ⇒ 这里立刻红。
  ★ 配套的语义验证（一次性做过的）：12 个局面的 `legal()` 全量候选
    改前/改后**逐条相同**；永久兜底是 `tests/` 里那一整套 `legal()` 用例。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.sandbox import Sandbox                        # noqa: E402
from ruleai.v11plus import pathfind                   # noqa: E402


def _sandbox_with_several_armies():
    """找一个**当前玩家有好几支可动军队**的局面（否则量不出重复）。"""
    for seed in (7000, 7001, 7002, 7003):
        for size in (12, 14):
            sb = Sandbox(seed=seed, size=size, t_max=60, n_nations=2,
                         halls_known=True, territory=True,
                         alliances="random2v2").reset()
            for _ in range(30):
                name = sb.current_player()
                if name is not None:
                    ready = [a for a in sb.armies_of(name)
                             if not a.get("engaged")
                             and a.get("moved_turn") != sb.world.turn]
                    if len(ready) >= 2:
                        return sb, name, len(ready)
                acts = sb.legal()
                if not acts or sb.is_terminal():
                    break
                sb.step(acts[len(acts) // 2])
    return None, None, 0


class TestVisionComputedOncePerLegal(unittest.TestCase):

    def test_legal_calls_vision_mask_exactly_once(self):
        sb, name, n_ready = _sandbox_with_several_armies()
        self.assertIsNotNone(sb, "没找到有多支可动军队的局面 —— 换个种子")
        # ★ 非空泛性：只有一支军的话，"提前算一次"和"每军算一次"没区别
        self.assertGreaterEqual(n_ready, 2,
                                f"用例太小：当前玩家只有 {n_ready} 支可动军队")

        calls = 0
        real = pathfind.vision_mask

        def spy(world, who):
            nonlocal calls
            calls += 1
            return real(world, who)

        pathfind.vision_mask = spy
        try:
            acts = sb.legal()
        finally:
            pathfind.vision_mask = real

        self.assertTrue(acts, "这个局面本该有合法动作")
        self.assertEqual(
            calls, 1,
            f"`legal()` 里 vision_mask 算了 **{calls}** 次（应为 1）—— "
            f"视野是按「军」重复算了（该提到循环外）")

    def test_two_legal_calls_do_not_share_a_stale_mask(self):
        """★ 反向：**两次 `legal()` 之间世界变过**就必须重算，不能吃旧视野。

        防的是"缓存加过头"——把 remember 做成跨帧的就错了（军队会在回合中移动）。
        """
        sb, _name, n_ready = _sandbox_with_several_armies()
        self.assertIsNotNone(sb)
        acts = sb.legal()
        if not acts or sb.is_terminal():
            self.skipTest("这个局面没法再走一步")
        sb.step(acts[0])                     # ★ 世界变了

        calls = 0
        real = pathfind.vision_mask

        def spy(world, who):
            nonlocal calls
            calls += 1
            return real(world, who)

        pathfind.vision_mask = spy
        try:
            sb.legal()
        finally:
            pathfind.vision_mask = real
        self.assertGreaterEqual(calls, 1, "世界变过之后必须重新算视野")
