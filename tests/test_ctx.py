# -*- coding: utf-8 -*-
"""ctx.py 的单元测试：预算分配、下滑水位、前缀缓存不变量。

全部用合成数据，不读写任何真实存档（mp_save.json 是玩家在跑的档）。
跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctx  # noqa: E402

SYSTEM = "你是国家元首。这里是静态系统提示词。" * 60


class StubWorld:
    """只带 ctx.slide 需要的字段。"""

    def __init__(self):
        self.turn_memory: dict[str, list] = {}
        self.summaries: dict[str, list] = {}
        self.summary_blocks: dict[str, list] = {}


def rec(turn: int, chars: int = 3000) -> dict:
    body = "这一回合的行动与结果。" * (chars // 11)
    return {"turn": turn, "messages": [
        {"role": "user", "content": f"【第{turn}回合 行动记录】"},
        {"role": "assistant", "content": "发言", "reasoning_content": body},
        {"role": "tool", "tool_call_id": f"t{turn}", "content": body},
    ]}


def sums_upto(n: int) -> list[dict]:
    return [{"turn": i, "text": f"第{i}回合做了些事，扩了两块地，和邻国换了信。"} for i in range(1, n + 1)]


class TestEstimate(unittest.TestCase):
    def test_cjk_costs_more_than_ascii(self):
        self.assertGreater(ctx.est_tokens("你好" * 500), ctx.est_tokens("ab" * 500))

    def test_empty_and_growth(self):
        self.assertEqual(ctx.est_tokens(""), 0)
        self.assertLess(ctx.est_tokens("abc"), ctx.est_tokens("abcd"))

    def test_record_tokens_counts_reasoning_and_tools(self):
        r = rec(1, chars=1000)
        self.assertGreater(ctx.record_tokens(r), ctx.est_tokens(r["messages"][1]["reasoning_content"]))


class TestPlan(unittest.TestCase):
    def test_falls_back_to_fixed_window(self):
        self.assertEqual(ctx.make_plan({}).mode, "fixed")
        self.assertEqual(ctx.make_plan({}, fallback_turns=12).fixed_turns, 12)
        self.assertEqual(ctx.make_plan({"ctx_full_turns": 7}).fixed_turns, 7)

    def test_budget_from_window(self):
        p = ctx.make_plan({"ctx_window": 1_000_000, "max_tokens": 32768})
        self.assertEqual(p.mode, "budget")
        self.assertEqual(p.budget, 800_000)          # 默认 ctx_fill=0.8
        p2 = ctx.make_plan({"ctx_window": 100_000, "ctx_fill": 0.5, "max_tokens": 4000})
        self.assertEqual(p2.budget, 50_000)

    def test_budget_never_eats_output_room(self):
        p = ctx.make_plan({"ctx_window": 60_000, "ctx_fill": 0.95, "max_tokens": 32_000})
        self.assertLessEqual(p.budget, 60_000 - 32_000)

    def test_apply_defaults_top_level_wins_only_when_missing(self):
        cfg = {"ctx_window": 1_000_000,
               "nations": [{"name": "秦"}, {"name": "楚", "ctx_window": 262_144}]}
        ctx.apply_defaults(cfg)
        self.assertEqual(cfg["nations"][0]["ctx_window"], 1_000_000)
        self.assertEqual(cfg["nations"][1]["ctx_window"], 262_144)


class TestSelectReplay(unittest.TestCase):
    def test_respects_budget(self):
        mem = [rec(i) for i in range(1, 21)]
        per = ctx.record_tokens(mem[0])
        kept, used = ctx.select_replay(mem, per * 5 + 10, min_turns=1)
        self.assertEqual(len(kept), 5)
        self.assertEqual([r["turn"] for r in kept], [16, 17, 18, 19, 20])
        self.assertLessEqual(used, per * 5 + 10)

    def test_keeps_newest_and_min_turns(self):
        mem = [rec(i) for i in range(1, 11)]
        kept, _ = ctx.select_replay(mem, 1, min_turns=3)   # 预算不可能满足
        self.assertEqual([r["turn"] for r in kept], [8, 9, 10])


class TestSlide(unittest.TestCase):
    def test_fixed_mode_slides_in_chunks(self):
        w = StubWorld()
        w.turn_memory["秦"] = [rec(i) for i in range(1, 40)]
        plan = ctx.make_plan({"ctx_full_turns": 10})
        dropped = ctx.slide(w, "秦", plan)
        self.assertEqual(len(w.turn_memory["秦"]), 10)          # 裁回窗口
        self.assertEqual(dropped[0]["turn"], 1)
        self.assertEqual(w.turn_memory["秦"][0]["turn"], 30)

    def test_fixed_mode_does_not_slide_before_high_watermark(self):
        w = StubWorld()
        w.turn_memory["秦"] = [rec(i) for i in range(1, 15)]
        self.assertEqual(ctx.slide(w, "秦", ctx.make_plan({"ctx_full_turns": 10})), [])

    def test_budget_mode_slides_to_low_watermark(self):
        w = StubWorld()
        mem = [rec(i) for i in range(1, 61)]
        w.turn_memory["秦"] = mem
        per = ctx.record_tokens(mem[0])
        plan = ctx.make_plan({"ctx_window": 1_000_000, "ctx_fill": 0.1,
                              "ctx_slide_keep": 0.5, "max_tokens": 1000})
        plan.replay_budget = per * 40          # 手设预算：超了才裁
        dropped = ctx.slide(w, "秦", plan)
        self.assertTrue(dropped)
        self.assertLessEqual(sum(ctx.record_tokens(r) for r in mem), per * 20)
        self.assertGreater(len(mem), 5)        # 不裁穿


class TestArchive(unittest.TestCase):
    def test_stable_for_same_inputs(self):
        s = sums_upto(50)
        self.assertEqual(ctx.render_archive(s, [], 51, 99999),
                         ctx.render_archive(s, [], 51, 99999))

    def test_growing_archive_keeps_old_bytes_as_prefix(self):
        """只追加新回合 → 旧归档（去掉尾部区间行）应是新归档的前缀（下滑时仍能命中）。"""
        a1 = ctx.render_archive(sums_upto(50), [], 51, 99999)
        a2 = ctx.render_archive(sums_upto(60), [], 61, 99999)
        self.assertTrue(a2.startswith(a1.rsplit("\n", 1)[0]))

    def test_block_summary_covers_its_turns(self):
        s = sums_upto(30)
        blocks = [{"from": 1, "to": 20, "text": "前二十回合：扩地、结盟、被袭一次。", "turn": 21}]
        text = ctx.render_archive(s, blocks, 31, 99999)
        self.assertIn("阶段总结", text)
        self.assertNotIn("  第5回合：", text)     # 已被块总结覆盖，不再重复列
        self.assertIn("  第25回合：", text)

    def test_cap_truncates_oldest(self):
        text = ctx.render_archive(sums_upto(200), [], 201, cap=2000)
        self.assertIn("已省略", text)
        self.assertIn("第200回合", text)
        self.assertNotIn("  第1回合：", text)

    def test_empty_before_first_turn(self):
        self.assertEqual(ctx.render_archive(sums_upto(5), [], 1, 9999), "")


class TestCachePrefixInvariant(unittest.TestCase):
    """核心不变量：没下滑的回合，上一回合的消息（除尾部状态）必须是新消息的前缀。"""

    def _run(self, cfg, turns=80):
        w = StubWorld()
        w.turn_memory["秦"] = []
        s = sums_upto(turns)
        prev = None
        slid = False
        slides = 0
        for t in range(1, turns + 1):
            msgs, plan = ctx.build(cfg=cfg, mem=w.turn_memory["秦"], sums=s, blocks=[],
                                   system_text=SYSTEM, tail_text=f"【本回合状态】第{t}回合",
                                   tool_tokens=5000)
            self.assertEqual(msgs[0]["role"], "system")
            self.assertIn("第" + str(t) + "回合", msgs[-1]["content"])
            if prev is not None:
                if slid:
                    # 下滑那一回合：只有 system 保证一致（归档因追加而几乎整段可命中，
                    # replay 整体位移必然失效）
                    self.assertEqual(prev[0], msgs[0])
                else:
                    head = prev[:-1]          # 去掉上一回合的尾部状态
                    self.assertEqual(head, msgs[:len(head)],
                                     f"第{t}回合未下滑，前缀必须逐字节一致")
            prev = msgs
            w.turn_memory["秦"].append(rec(t))
            dropped = ctx.slide(w, "秦", plan)
            slid = bool(dropped)
            slides += 1 if dropped else 0
        return slides, w

    def test_no_slide_turns_keep_prefix(self):
        cfg = {"ctx_window": 400_000, "ctx_fill": 0.3, "max_tokens": 4000,
               "ctx_slide_keep": 0.6}
        slides, w = self._run(cfg)
        self.assertGreater(slides, 0, "窗口应至少下滑一次，否则测不到失效场景")
        self.assertLess(slides, 40, "下滑太频繁——分块水位没起作用")

    def test_bigger_window_keeps_more_turns(self):
        small = {"ctx_window": 100_000, "ctx_fill": 0.2, "max_tokens": 4000}
        big = {"ctx_window": 1_000_000, "ctx_fill": 0.8, "max_tokens": 4000}
        w = StubWorld()
        w.turn_memory["秦"] = [rec(i) for i in range(1, 61)]
        _m1, p1 = ctx.build(cfg=small, mem=w.turn_memory["秦"], sums=[], blocks=[],
                            system_text=SYSTEM, tail_text="状态")
        _m2, p2 = ctx.build(cfg=big, mem=w.turn_memory["秦"], sums=[], blocks=[],
                            system_text=SYSTEM, tail_text="状态")
        self.assertGreaterEqual(p2.replay_turns, p1.replay_turns)
        self.assertLessEqual(p2.total_tokens, big["ctx_window"] * big["ctx_fill"])


if __name__ == "__main__":
    unittest.main()
