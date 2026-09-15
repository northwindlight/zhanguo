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
import llm_provider  # noqa: E402


def _backend(client):
    """真 OpenAICompat，但把 client 换成 fake——让 _compact_block 走真实的
    complete_text 胶水（含 extra_body / 不带 tools 等口径），而非测试里重造。"""
    b = llm_provider.OpenAICompat.__new__(llm_provider.OpenAICompat)
    b.client = client
    return b


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
        self.long_memory = {}


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
        text = mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦", dropped)
        self.assertEqual(text, c.reply)
        # 递归累积：首次无旧记忆 → 提示词带"（暂无）"地基，结果写回 long_memory
        self.assertEqual(w.long_memory["秦"], text)
        (block,) = w.summary_blocks["秦"]
        self.assertEqual((block["from"], block["to"]), (21, 23))
        self.assertEqual(block["text"], text)
        sent = c.calls[0]["messages"]
        self.assertIn("已有的长期记忆", sent[1]["content"])
        self.assertIn("暂无", sent[1]["content"])
        self.assertNotIn("内部思考内容不该进压缩输入", sent[1]["content"])
        self.assertIn("第21回合", sent[1]["content"])
        # F1（2026-09-13）：cfg 未声明推理模型 → 不向请求体塞 DeepSeek 专属 thinking 参数
        self.assertIsNone(c.calls[0]["extra_body"])
        self.assertNotIn("tools", c.calls[0])

    def test_recursive_with_previous_memory(self):
        w = _World()
        c = _Client()
        w.long_memory["秦"] = "旧记忆：与齐结盟十年，约定共抗林胡。"
        mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦", [_rec(31)])
        sent = c.calls[0]["messages"][1]["content"]
        self.assertIn("与齐结盟十年，约定共抗林胡", sent)   # 旧记忆进提示词作扩写基数
        self.assertEqual(w.long_memory["秦"], c.reply)      # 结果整体替换为扩写后的记忆

    def test_too_short_reply_is_discarded(self):
        w = _World()
        c = _Client(reply="嗯")
        self.assertIsNone(mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦", [_rec(21)]))
        self.assertEqual(w.summary_blocks["秦"], [])
        self.assertNotIn("秦", w.long_memory)   # 太短的回复不视为记忆，不回写

    def test_oversized_input_keeps_tail(self):
        w = _World()
        dropped = [_rec(t) for t in range(1, 4)]
        dropped[0]["messages"][1]["content"] = "开头标记"
        dropped[-1]["messages"][1]["content"] = "结尾标记"
        text = mp_ai._compact_input(dropped, [], 1)
        self.assertIn("结尾标记", text)
        self.assertLessEqual(len(text), mp_ai.COMPACT_INPUT_CHARS + 200)


class TestMemorySearch(unittest.TestCase):
    """memory_search / 检索记忆：翻旧账（只搜正文、不含思考；只搜本国）。"""

    def _w(self):
        import mp
        w = mp.World(size=12, seed=7, nations=["秦"])
        w.turn_memory["秦"] = [
            {"turn": 12, "messages": [
                {"role": "user", "content": "【第12回合 行动记录】"},
                {"role": "assistant", "content": "与楚缔结盟约，五年互不侵犯",
                 "reasoning_content": "思考：为保卫西部粮区必须稳住南线……"},
                {"role": "tool", "tool_call_id": "t1", "content": "楚 接受了盟约"}]},
            {"turn": 13, "messages": [
                {"role": "user", "content": "【第13回合 行动记录】"},
                {"role": "assistant", "content": "北境遭林胡袭扰",
                 "reasoning_content": "思考：骑兵布防……"}]},
        ]
        w.plans["秦"] = {"text": "北方拒林胡，南联楚", "turn": 8}
        w.long_memory["秦"] = "与楚盟约五年；林胡为心腹之患。"
        return w

    def test_schema_registered(self):
        names = {t["function"]["name"] for t in mp_ai.TOOL_SCHEMAS}
        self.assertIn("memory_search", names)

    def test_finds_body_not_reasoning(self):
        w = self._w()
        out = mp_ai.execute(w, "秦", "检索记忆", {"query": "盟约"})
        self.assertIn("第12回合", out)
        self.assertIn("与楚缔结盟约", out)
        self.assertNotIn("为保卫西部粮区", out)   # reasoning 不索引
        self.assertNotIn("骑兵布防", out)         # 无关回合的 thinking 更不该进

    def test_gives_turn_and_adjacent(self):
        w = self._w()
        out = mp_ai.execute(w, "秦", "检索记忆", {"query": "楚 接受"})
        self.assertIn("第12回合", out)
        self.assertIn("相邻", out)
        self.assertIn("与楚缔结盟约", out)

    def test_no_hit_graceful(self):
        w = self._w()
        out = mp_ai.execute(w, "秦", "memory_search", {"query": "海战"})
        self.assertIn("没有命中", out)

    def test_empty_query_usage(self):
        w = self._w()
        out = mp_ai.execute(w, "秦", "记忆检索", {})
        self.assertIn("用法", out)

    def test_huns_not_blocked(self):
        import mp
        w = mp.World(size=12, seed=7, nations=["林胡"])
        w.apply_polity("林胡", "huns")
        out = mp_ai.execute(w, "林胡", "检索记忆", {"query": "补给"})
        self.assertNotIn("被禁", out)            # 内部记忆，不属被禁外交工具


if __name__ == "__main__":
    unittest.main()
