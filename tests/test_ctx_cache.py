# -*- coding: utf-8 -*-
"""ctx.py 缓存瘦身（ctx_old_reasoning / ctx_trim_tool_chars）与滚动实测命中的单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctx  # noqa: E402

SYSTEM = "系统提示词。" * 60


def rec(turn: int, reason_chars: int = 3000, tool_chars: int = 2000) -> dict:
    return {"turn": turn, "messages": [
        {"role": "user", "content": f"【第{turn}回合 行动记录】"},
        {"role": "assistant", "content": "发言", "reasoning_content": "想" * reason_chars},
        {"role": "tool", "tool_call_id": f"t{turn}", "content": "果" * tool_chars},
    ]}


class TestShrink(unittest.TestCase):
    def test_default_full_keeps_everything(self):
        r = rec(5)
        sr = ctx.shrink_records([r], newest_turn=5, old_reasoning="full")
        self.assertEqual(sr[0]["messages"][1]["reasoning_content"],
                         r["messages"][1]["reasoning_content"])

    def test_strip_old_but_keep_newest_reasoning(self):
        r1, r2 = rec(1), rec(2)
        sr = ctx.shrink_records([r1, r2], newest_turn=2, old_reasoning="strip")
        self.assertNotEqual(sr[0]["messages"][1]["reasoning_content"],
                            r1["messages"][1]["reasoning_content"])
        self.assertLess(len(sr[0]["messages"][1]["reasoning_content"]), 200)
        self.assertEqual(sr[1]["messages"][1]["reasoning_content"],
                         r2["messages"][1]["reasoning_content"])

    def test_tool_trim(self):
        r = rec(3, tool_chars=5000)
        sr = ctx.shrink_records([r], newest_turn=3, old_reasoning="full", tool_trim=100)
        self.assertLessEqual(len(sr[0]["messages"][2]["content"]), 150)
        self.assertIn("截断", sr[0]["messages"][2]["content"])

    def test_send_tokens_smaller_than_record(self):
        r = rec(1, reason_chars=5000)
        full = ctx.record_tokens(r)
        small = ctx.send_tokens(r, newest_turn=2, old_reasoning="strip")  # turn1 视为旧回合
        self.assertLess(small, full / 2)
        # 同一记录若是最新（newest_turn=其自身），思考保留、体积不变
        self.assertEqual(ctx.send_tokens(r, newest_turn=1, old_reasoning="strip"), full)


class TestPlanKeys(unittest.TestCase):
    def test_parses_new_keys(self):
        p = ctx.make_plan({"ctx_window": 100_000, "ctx_old_reasoning": "strip",
                           "ctx_trim_tool_chars": 400})
        self.assertEqual(p.old_reasoning, "strip")
        self.assertEqual(p.tool_trim, 400)
        p2 = ctx.make_plan({"ctx_window": 100_000})
        self.assertEqual(p2.old_reasoning, "full")
        self.assertEqual(p2.tool_trim, 0)


class TestRolling(unittest.TestCase):
    def test_rolling_hit(self):
        ctx.record_hit("A", 80, 20)
        ctx.record_hit("A", 90, 10)
        self.assertAlmostEqual(ctx.rolling_hit("A"), 0.85)
        ctx.record_hit("A", 60, 40)
        self.assertAlmostEqual(ctx.rolling_hit("A"), (80 + 90 + 60) / 300)
        self.assertIsNone(ctx.rolling_hit("B"))


class TestBuildShrunk(unittest.TestCase):
    def test_build_strip_shrinks_replay_and_assembles(self):
        mem = [rec(t, reason_chars=2000, tool_chars=800) for t in range(1, 21)]
        cfg = {"ctx_window": 200_000, "ctx_fill": 0.8, "ctx_slide_keep": 0.6,
               "ctx_min_turns": 3, "max_tokens": 4000}
        cfg_s = dict(cfg, ctx_old_reasoning="strip")
        out_full, p1 = ctx.build(cfg=cfg, mem=mem, sums=[], blocks=[],
                                 system_text=SYSTEM, tail_text="状态" * 300, tool_tokens=2000)
        out_s, p2 = ctx.build(cfg=cfg_s, mem=mem, sums=[], blocks=[],
                              system_text=SYSTEM, tail_text="状态" * 300, tool_tokens=2000)
        self.assertLess(p2.replay_tokens, p1.replay_tokens)
        self.assertLessEqual(p2.total_tokens, p1.total_tokens)
        self.assertEqual(out_s[0]["role"], "system")
        self.assertGreaterEqual(len(out_s), len(out_full))  # 瘦身塞得下更多回合



class _W:
    """带 .turn 的 StubWorld 变体，供 slide 测试。"""
    def __init__(self):
        self.turn = 1
        self.turn_memory = {}
        self.summaries = {}
        self.summary_blocks = {}


class TestPeriodic(unittest.TestCase):
    def test_make_plan_roll(self):
        p = ctx.make_plan({"ctx_window": 100_000, "ctx_roll": "period",
                           "ctx_period": 12, "ctx_slice_keep": 0.5})
        self.assertEqual(p.roll, "period")
        self.assertEqual(p.period, 12)
        self.assertEqual(p.slice_keep, 0.5)
        p2 = ctx.make_plan({"ctx_window": 100_000})
        self.assertEqual(p2.roll, "slide")

    def test_period_slide_only_on_boundary(self):
        w = _W()
        p = ctx.make_plan({"ctx_window": 100_000, "ctx_roll": "period",
                           "ctx_period": 10, "ctx_slice_keep": 0.5, "ctx_slide_keep": 0.5})
        # 大 mem：总量远超周期片上限(40k)，测"非边界绝不碰 + 边界超了才压"
        w.turn_memory["秦"] = [rec(t, reason_chars=1200, tool_chars=900) for t in range(1, 41)]
        for turn, expect in ((5, 0), (10, 1), (11, 0), (21, 0)):
            w.turn = turn
            dropped = ctx.slide(w, "秦", p)
            if expect:
                self.assertGreater(len(dropped), 0, f"周期回合{turn}应压缩")
            else:
                self.assertEqual(dropped, [], f"回合{turn}绝不能动前缀")

    def test_period_build_uses_slice_cap(self):
        cfg = {"ctx_window": 200_000, "ctx_roll": "period", "ctx_period": 8,
               "ctx_slice_keep": 0.5, "ctx_min_turns": 2, "max_tokens": 4000}
        mem = [rec(t, reason_chars=1000, tool_chars=500) for t in range(1, 41)]
        out, plan = ctx.build(cfg=cfg, mem=mem, sums=[], blocks=[],
                              system_text=SYSTEM, tail_text="状态" * 200, tool_tokens=2000)
        self.assertEqual(plan.roll, "period")
        self.assertEqual(plan.replay_budget, 80_000)   # 160k预算 × 0.5
        self.assertLessEqual(plan.replay_tokens, 80_000)

if __name__ == "__main__":
    unittest.main(verbosity=2)