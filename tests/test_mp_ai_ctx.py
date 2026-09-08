# -*- coding: utf-8 -*-
"""mp_ai 里上下文相关胶水的测试（阶段块总结、配置归一化）。全合成数据。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp_ai  # noqa: E402


class _Msg:
    def __init__(self, content):
        self.content = content


class _Resp:
    def __init__(self, content):
        self.choices = [types.SimpleNamespace(message=_Msg(content))]


class _Completions:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kw):
        self.outer.calls.append(kw)
        return _Resp(self.outer.reply)


class _Client:
    """假 client：记录调用参数，返回固定文本。"""

    def __init__(self, reply="压缩后的记忆：扩地两块，与齐结盟，北方有敌军集结。"):
        self.calls: list[dict] = []
        self.reply = reply
        self.chat = types.SimpleNamespace(completions=_Completions(self))


class _World:
    def __init__(self, turn=30):
        self.turn = turn
        self.nations = {"秦": object()}
        self.summaries = {"秦": [{"turn": t, "text": f"第{t}回合扩地"}
                                 for t in range(1, turn + 1)]}
        self.summary_blocks = {"秦": []}


def _rec(turn):
    return {"turn": turn, "messages": [
        {"role": "user", "content": f"【第{turn}回合 行动记录】"},
        {"role": "assistant", "content": "建农场",
         "reasoning_content": "内部思考内容不该进压缩输入" * 20},
        {"role": "tool", "tool_call_id": "x", "content": "农场已建成"},
    ]}


class TestNormalizeCfg(unittest.TestCase):
    def test_small_ctx_maps_to_256k_window(self):
        cfg = {"small_ctx": True}
        mp_ai.normalize_cfg(cfg)
        self.assertEqual(cfg["ctx_window"], 262144)

    def test_explicit_window_wins(self):
        cfg = {"small_ctx": True, "ctx_window": 1000000}
        mp_ai.normalize_cfg(cfg)
        self.assertEqual(cfg["ctx_window"], 1000000)


class TestToolSchemas(unittest.TestCase):
    def _world(self, huns=False):
        import mp
        w = mp.World(size=16, seed=3, nations=["秦", "林胡"])
        if huns:
            w.apply_polity("林胡", "huns")
        return w

    def test_normal_nation_gets_base_schema(self):
        self.assertIs(mp_ai.tool_schemas(self._world(), "秦"), mp_ai.TOOL_SCHEMAS)

    def test_huns_schema_shows_cheap_cavalry(self):
        s = mp_ai.tool_schemas(self._world(True), "林胡")
        rec = next(t["function"] for t in s if t["function"]["name"] == "recruit")
        self.assertIn("骑=骑兵(8粮+8装", rec["description"])
        # 基础 schema 不能被改坏（其它国家仍看常规价）
        base = next(t["function"] for t in mp_ai.TOOL_SCHEMAS
                    if t["function"]["name"] == "recruit")
        self.assertIn("骑=骑兵(12粮+12装", base["description"])


class TestCompactBlock(unittest.TestCase):
    def test_block_covers_dropped_range_and_strips_reasoning(self):
        w = _World()
        c = _Client()
        dropped = [_rec(21), _rec(22), _rec(23)]
        block = mp_ai._compact_block(c, {"model": "m"}, w, "秦", dropped)
        self.assertIsNotNone(block)
        self.assertEqual((block["from"], block["to"]), (21, 23))
        self.assertEqual(w.summary_blocks["秦"], [block])
        sent = c.calls[0]["messages"]
        self.assertNotIn("内部思考内容不该进压缩输入", sent[1]["content"])
        self.assertIn("第21回合", sent[1]["content"])
        self.assertEqual(c.calls[0]["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertNotIn("tools", c.calls[0])

    def test_too_short_reply_is_discarded(self):
        w = _World()
        c = _Client(reply="嗯")
        self.assertIsNone(mp_ai._compact_block(c, {"model": "m"}, w, "秦", [_rec(21)]))
        self.assertEqual(w.summary_blocks["秦"], [])

    def test_oversized_input_keeps_tail(self):
        w = _World()
        dropped = [_rec(t) for t in range(1, 4)]
        dropped[0]["messages"][1]["content"] = "开头标记"
        dropped[-1]["messages"][1]["content"] = "结尾标记"
        text = mp_ai._compact_input(dropped, [], 1)
        self.assertIn("结尾标记", text)
        self.assertLessEqual(len(text), mp_ai.COMPACT_INPUT_CHARS + 200)


if __name__ == "__main__":
    unittest.main()
