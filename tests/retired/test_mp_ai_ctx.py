# -*- coding: utf-8 -*-
"""mp_ai 里上下文相关胶水的测试（阶段块总结、配置归一化）。全合成数据。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
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
    def __init__(self, content, usage=None):
        self.choices = [types.SimpleNamespace(message=_Msg(content))]
        self.usage = usage


class _Completions:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kw):
        self.outer.calls.append(kw)
        return _Resp(self.outer.reply, self.outer.usage)


class _Client:
    """假 client：记录调用参数，返回固定文本（`usage` 可选，用来验命中率统计）。"""

    def __init__(self, reply="压缩后的记忆：扩地两块，与齐结盟，北方有敌军集结。",
                 usage=None):
        self.calls: list[dict] = []
        self.reply = reply
        self.usage = usage if usage is not None else types.SimpleNamespace(
            completion_tokens=12, prompt_tokens=1000,
            prompt_cache_hit_tokens=900, prompt_cache_miss_tokens=100)
        self.chat = types.SimpleNamespace(completions=_Completions(self))


class _World:
    def __init__(self, turn=30, head_turns=6):
        self.turn = turn
        self.nations = {"秦": object()}
        self.summaries = {"秦": [{"turn": t, "text": f"第{t}回合扩地"}
                                 for t in range(1, turn + 1)]}
        self.summary_blocks = {"秦": []}
        self.long_memory = {}
        # 真形状的回合记录：本回合状态(user) → 带 tool_calls 的 assistant → tool 结果。
        # 首条是 user —— 正是"归档(user) 与它会被 assemble 合成一条"的那条边界。
        self.turn_memory = {"秦": [
            {"turn": t, "messages": [
                {"role": "user",
                 "content": f"【第 {t} 回合】以上为过往回合记录，现在开始行动。"},
                {"role": "assistant", "content": f"第{t}回合：修了农场",
                 "reasoning_content": f"第{t}回合的内部思考（原文回放要带上）" * 8,
                 "tool_calls": [{"id": f"c{t}", "type": "function",
                                 "function": {"name": "build",
                                              "arguments": f'{{"city": {t}}}'}}]},
                {"role": "tool", "tool_call_id": f"c{t}", "content": f"第{t}回合结果"}]}
            for t in range(1, head_turns + 1)]}


def _rec(turn):
    return {"turn": turn, "messages": [
        {"role": "user", "content": f"【第{turn}回合 行动记录】"},
        {"role": "assistant", "content": "建农场",
         "reasoning_content": "内部思考内容不该进压缩输入" * 20},
        {"role": "tool", "tool_call_id": "x", "content": "农场已建成"},
    ]}


_SYS = "你是国家元首【秦】。"
_TAIL = "以上为过往回合记录，现在开始第 31 回合行动。"


def _sent(w, name="秦", fixed=4, drop_turns=(3, 4)):
    """真 `ctxlib.build` 造一份「本回合真发出去的 messages」+ plan，并给出滑出去的回合。

    ★ `drop_turns` 必须落在 `plan.before_turn` 之后才是**真实**情形：`slide` 按低水位
    裁，被裁掉的回合里大部分当初就在这次请求里（`build` 按高水位装满 → 裁到低水位），
    压缩要逐字回放的就是它们。比 `before_turn` 更老的回合则根本不在请求里——那种只能
    给小结（另有一条用例专门测它）。
    """
    import ctx as ctxlib
    mem, sums, blocks = mp_ai._ctx_parts(w, name)
    dropped = [r for r in mem if int(r["turn"]) in drop_turns]
    msgs, plan = ctxlib.build(cfg={"ctx_full_turns": fixed}, mem=mem, sums=sums,
                              blocks=blocks, system_text=_SYS, tail_text=_TAIL,
                              long_memory=w.long_memory.get(name, ""))
    return msgs, plan, dropped


class TestCompactBlock(unittest.TestCase):
    def test_payload_is_the_sent_prefix_plus_instruction(self):
        """★ 核心契约：压缩请求 = **上一次请求的前缀切片** + 尾随指令（命中缓存的那条路）。

        这批旧回合（第 3、4 回合）当初就在本回合请求里 ⇒ 逐字回放，且切点正好落在
        它们末尾——下一回合的原文（第 5 回合）不许混进来。
        """
        w = _World()
        c = _Client()
        msgs, plan, dropped = _sent(w)
        self.assertEqual(plan.before_turn, 3)
        self.assertEqual([r["turn"] for r in dropped], [3, 4])
        text = mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦", dropped,
                                    plan=plan, messages=msgs, tools=mp_ai.TOOL_SCHEMAS)
        self.assertEqual(text, c.reply)
        self.assertEqual(w.long_memory["秦"], text)
        (block,) = w.summary_blocks["秦"]
        self.assertEqual((block["from"], block["to"]), (3, 4))
        sent = c.calls[0]["messages"]
        # ① 尾随指令是最后一条 user 消息，且明说别调工具
        self.assertEqual(sent[-1], {"role": "user", "content": mp_ai.COMPACT_INSTRUCTION})
        self.assertIn("不要调用任何工具", sent[-1]["content"])
        # ② 前缀逐字节 = 主请求前 N 条（不是"另一份长得像的请求"）
        k = len(sent) - 1
        self.assertGreater(k, 1)
        self.assertEqual(sent[:k], msgs[:k], "压缩调用的输入必须是主请求的前缀切片")
        body = json.dumps(sent[:k], ensure_ascii=False)
        self.assertIn("第4回合结果", body, "被滑掉的回合要逐字回放")
        self.assertNotIn("第5回合", body, "没滑掉的回合不许混进前缀（切点算错了）")
        # ③ 归档(user) 与请求首条回合记录的首条(user) 被 assemble 合成了一条
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[0]["content"], _SYS)
        self.assertEqual(sent[1]["role"], "user")
        self.assertIn("历史归档", sent[1]["content"])
        self.assertIn("现在开始行动", sent[1]["content"], "第3回合原文要合进那条归档消息里")
        # ④ 原文回放（含思考与工具往返），不再是"行动：/结果："改写稿
        self.assertIn("内部思考（原文回放要带上）", body)
        # ⑤ tools 照发 + 关掉工具调用；压缩不需要思考
        self.assertIn("tools", c.calls[0])
        self.assertEqual(c.calls[0]["tool_choice"], "auto",
                         "tool_choice 必须与主请求同值（none 会让网关丢掉 tools 段、前缀断掉）")
        self.assertEqual(c.calls[0]["extra_body"], {"thinking": {"type": "disabled"}})

    def test_turns_outside_the_request_are_digested_not_replayed(self):
        """★ 比 `before_turn` 更老的回合**不在这次请求里**：硬回放会毁掉前缀性质，
        只能给小结（补在指令之前）。"""
        w = _World()
        c = _Client()
        msgs, plan, dropped = _sent(w, drop_turns=(1, 2))
        self.assertEqual([r["turn"] for r in dropped], [1, 2])
        prefix, over = mp_ai._compact_prefix(msgs, plan, dropped)
        self.assertEqual(over, dropped, "不在请求里的回合一个都不许逐字回放")
        self.assertEqual(prefix, msgs[:len(prefix)])
        self.assertLessEqual(len(prefix), 2, "只剩 system + 归档可回放（第3回合是另外那条）")
        payload = mp_ai._compact_messages(msgs, plan, dropped, [])
        self.assertIn("不在上一次请求里", payload[-2]["content"])
        self.assertIn("第1回合", payload[-2]["content"])
        self.assertEqual(payload[-1]["content"], mp_ai.COMPACT_INSTRUCTION)
        mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦", dropped,
                             plan=plan, messages=msgs, tools=mp_ai.TOOL_SCHEMAS)
        sent = c.calls[0]["messages"]
        self.assertEqual(sent[:len(prefix)], prefix)

    def test_emits_the_auxiliary_calls_own_hit_rate(self):
        """压缩调用自己的命中率必须报出来——这次改造的意义全在这个数上。"""
        w = _World()
        c = _Client()
        lines: list[str] = []
        msgs, plan, dropped = _sent(w)
        mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦", dropped,
                             plan=plan, messages=msgs, tools=mp_ai.TOOL_SCHEMAS,
                             emit=lines.append)
        hit = [ln for ln in lines if "压缩调用" in ln]
        self.assertTrue(hit, f"没报压缩命中率：{lines}")
        self.assertIn("90%", hit[0])
        self.assertIn("900/1000", hit[0])

    def test_recursive_with_previous_memory(self):
        """旧记忆在归档里（前缀内）⇒ 压缩调用天然带着它递归扩写。"""
        w = _World()
        c = _Client()
        w.long_memory["秦"] = "旧记忆：与齐结盟十年，约定共抗林胡。"
        msgs, plan, dropped = _sent(w)
        mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦", dropped,
                             plan=plan, messages=msgs, tools=mp_ai.TOOL_SCHEMAS)
        self.assertIn("与齐结盟十年，约定共抗林胡", c.calls[0]["messages"][1]["content"])
        self.assertEqual(w.long_memory["秦"], c.reply)      # 结果整体替换为扩写后的记忆

    def test_too_short_reply_is_discarded(self):
        w = _World()
        c = _Client(reply="嗯")
        msgs, plan, dropped = _sent(w)
        self.assertIsNone(mp_ai._compact_block(_backend(c), {"model": "m"}, w, "秦",
                                               dropped, plan=plan, messages=msgs))
        self.assertEqual(w.summary_blocks["秦"], [])
        self.assertNotIn("秦", w.long_memory)   # 太短的回复不视为记忆，不回写

    def test_over_budget_keeps_head_and_digests_the_rest(self):
        """超预算**只砍尾部**：前缀性质必须保住，砍掉的旧回合降级成小结行补在指令前。"""
        import ctx as ctxlib
        w = _World()
        msgs, plan, dropped = _sent(w)
        # 砍到"只放得下第 3 回合"：第 4 回合被降级成小结
        one = len(ctxlib.replay_head(msgs[0]["content"], plan.archive_text, dropped[:1]))
        prefix, over = mp_ai._compact_prefix(msgs, plan, dropped,
                                             budget=ctxlib.messages_tokens(msgs[:one]))
        self.assertEqual(prefix, msgs[:one])
        self.assertEqual([r["turn"] for r in over], [4])
        payload = mp_ai._compact_messages(msgs, plan, dropped, [])
        self.assertEqual(payload[-1]["content"], mp_ai.COMPACT_INSTRUCTION)
        # 预算够（默认 120k）时**不**降级：逐字原文一路到指令，没有小结那一块
        self.assertNotIn("不在上一次请求里", json.dumps(payload, ensure_ascii=False))
        # 预算连一回合都放不下 ⇒ 退回"只剩 system + 归档"，其余全给小结
        tiny, over2 = mp_ai._compact_prefix(msgs, plan, dropped, budget=1)
        self.assertEqual(tiny, msgs[:2])
        self.assertEqual([r["turn"] for r in over2], [3, 4])

    def test_no_plan_or_no_system_head_degrades_to_digest(self):
        """认不出布局时退回"全走小结"，绝不猜前缀（猜错就是静默不命中）。"""
        w = _World()
        msgs, plan, dropped = _sent(w)
        self.assertEqual(mp_ai._compact_prefix(msgs, None, dropped), ([], dropped))
        self.assertEqual(mp_ai._compact_prefix([{"role": "user", "content": "u"}],
                                               plan, dropped), ([], dropped))
        payload = mp_ai._compact_messages(msgs, None, dropped, [])
        self.assertEqual(payload[0]["role"], "user")
        self.assertIn("逐回合小结", payload[0]["content"])
        self.assertEqual(payload[-1]["content"], mp_ai.COMPACT_INSTRUCTION)

    def test_oversized_input_keeps_tail(self):
        w = _World()
        dropped = [_rec(t) for t in range(1, 4)]
        dropped[0]["messages"][1]["content"] = "开头标记"
        dropped[-1]["messages"][1]["content"] = "结尾标记"
        text = mp_ai._compact_input(dropped, [], 1)
        self.assertIn("结尾标记", text)
        self.assertLessEqual(len(text), mp_ai.COMPACT_INPUT_CHARS + 200)

    def test_oversized_input_keeps_tail(self):
        w = _World()
        dropped = [_rec(t) for t in range(1, 4)]
        dropped[0]["messages"][1]["content"] = "开头标记"
        dropped[-1]["messages"][1]["content"] = "结尾标记"
        text = mp_ai._compact_input(dropped, [], 1)
        self.assertIn("结尾标记", text)
        self.assertLessEqual(len(text), mp_ai.COMPACT_INPUT_CHARS + 200)


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
