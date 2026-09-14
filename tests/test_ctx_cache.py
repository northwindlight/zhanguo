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

    def test_period_slide_only_trims_when_due_or_over_cap(self):
        w = _W()
        p = ctx.make_plan({"ctx_window": 100_000, "ctx_roll": "period",
                           "ctx_period": 10, "ctx_slide_keep": 0.5})
        # ① 未超水位：到没到点都不动前缀（纯追加）
        w.turn_memory["秦"] = [rec(t, reason_chars=100, tool_chars=50) for t in range(1, 21)]
        for turn in (5, 10, 11, 21):
            w.turn = turn
            self.assertEqual(ctx.slide(w, "秦", p), [], f"未超水位回合{turn}绝不能动前缀")
        # ② 已超水位：到期(10)与非到期(7)都兜底压缩（避免逐回合从请求头裁切）
        #   （每次用全新 world，避免上次修剪的残留影响判断）
        for turn in (7, 10):
            w2 = _W()
            p2 = ctx.make_plan({"ctx_window": 100_000, "ctx_roll": "period",
                                "ctx_period": 10, "ctx_slide_keep": 0.5})
            w2.turn = turn
            w2.turn_memory["秦"] = [rec(t, reason_chars=6000, tool_chars=4000)
                                    for t in range(1, 41)]
            self.assertGreater(len(ctx.slide(w2, "秦", p2)), 0, f"超水位回合{turn}应兜底压缩")

    def test_period_shares_budget_and_auto_period(self):
        cfg = {"ctx_window": 200_000, "ctx_roll": "period",
               "ctx_min_turns": 2, "max_tokens": 4000}
        mem = [rec(t, reason_chars=1000, tool_chars=500) for t in range(1, 41)]
        out, plan = ctx.build(cfg=cfg, mem=mem, sums=[], blocks=[],
                              system_text=SYSTEM, tail_text="状态" * 200, tool_tokens=2000)
        self.assertEqual(plan.roll, "period")
        # B：与 slide 共用预算分配——replay 顶满请求，不再被缩成半片
        self.assertGreater(plan.replay_budget, 150_000)      # 200k窗口预算160k，扣开销后仍接近顶格
        self.assertLessEqual(plan.replay_tokens, plan.replay_budget)
        # 未配 ctx_period → 自动推导有效周期，落在合法区间
        self.assertIsNotNone(plan.effective_period)
        self.assertGreaterEqual(plan.effective_period, 6)
        self.assertLessEqual(plan.effective_period, 120)

class TestAutoPeriod(unittest.TestCase):
    """ctx_period 不配 → 自动：窗口几何 × 实测回合体积推导有效周期。"""

    def _cfg(self, window):
        return {"ctx_window": window, "ctx_roll": "period", "ctx_slice_keep": 0.5,
                "ctx_slide_keep": 0.5, "ctx_min_turns": 2, "max_tokens": 4000}

    def test_default_is_auto_and_in_range(self):
        p = ctx.make_plan({"ctx_window": 200_000, "ctx_roll": "period"})
        self.assertEqual(p.period, 0)                    # 0 = 自动
        w = _W()
        w.turn_memory["秦"] = [rec(t, reason_chars=2000, tool_chars=1000) for t in range(1, 41)]
        out, plan = ctx.build(cfg=self._cfg(200_000), mem=w.turn_memory["秦"], sums=[], blocks=[],
                              system_text=SYSTEM, tail_text="状态" * 200, tool_tokens=2000)
        self.assertIsNotNone(plan.effective_period)
        self.assertGreaterEqual(plan.effective_period, 6)
        self.assertLessEqual(plan.effective_period, 120)

    def test_bigger_window_gives_longer_period(self):
        w = _W()
        mem = [rec(t, reason_chars=2000, tool_chars=1000) for t in range(1, 41)]
        _, p_small = ctx.build(cfg=self._cfg(200_000), mem=mem, sums=[], blocks=[],
                               system_text=SYSTEM, tail_text="s" * 100, tool_tokens=800)
        _, p_big = ctx.build(cfg=self._cfg(800_000), mem=mem, sums=[], blocks=[],
                             system_text=SYSTEM, tail_text="s" * 100, tool_tokens=800)
        # 同体积回合，窗口越大 → 高位-低位余量越大 → 周期越长
        self.assertGreater(p_big.effective_period, p_small.effective_period)

    def test_auto_slide_waits_until_due_or_over_cap(self):
        w = _W()
        p = ctx.make_plan(self._cfg(100_000))
        p.effective_period = 40
        w.summary_blocks["秦"] = [{"from": 1, "to": 5, "text": "s", "turn": 5}]
        # ① 未到点、切片未满 → 绝不动前缀
        w.turn = 8
        w.turn_memory["秦"] = [rec(t, reason_chars=300, tool_chars=200) for t in range(1, 8)]
        self.assertEqual(ctx.slide(w, "秦", p), [])
        # ② 切片已满 → 兜底压缩（不必等周期）
        w.turn_memory["秦"] = [rec(t, reason_chars=6000, tool_chars=4000) for t in range(1, 40)]
        w.turn = 9
        self.assertGreater(len(ctx.slide(w, "秦", p)), 0)

    def test_manual_period_still_fixed(self):
        p = ctx.make_plan({"ctx_window": 100_000, "ctx_roll": "period", "ctx_period": 10})
        self.assertEqual(p.period, 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)