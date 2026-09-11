# -*- coding: utf-8 -*-
"""回合循环的端到端冒烟测试：用假 OpenAI client 跑真实 run_openai_turn。

覆盖：build_context → 流式解析 → 工具执行 → end_turn → 存档 → 下滑 → 阶段块总结。
不联网、不读写真实存档（World 用临时目录里的合成档）。
跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
import mp_ai  # noqa: E402


class _Delta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


class _Usage:
    def __init__(self):
        self.completion_tokens = 100
        self.completion_tokens_details = types.SimpleNamespace(reasoning_tokens=60)
        self.prompt_cache_hit_tokens = 900
        self.prompt_cache_miss_tokens = 100


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, index, id, name, arguments):
        self.index = index
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _FakeCompletions:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kw):
        self.outer.calls.append(dict(kw, messages=list(kw["messages"])))  # 快照，别记引用
        if "tools" not in kw:      # 压缩调用（无 tools）→ 非流式
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content="阶段总结：这几回合在扩地屯田，与邻国保持中立，北方有敌军集结。"))])
        # 本回合第 1 次调用先制定国策，第 2 次才 end_turn（引擎要求先有国策）
        nth = sum(1 for c in self.outer.calls if "tools" in c)
        if nth == 1:
            tc = _TC(0, "p1", "plan", '{"content": "经济发展优先，稳守边境，先探明北面。"}')
        else:
            tc = _TC(0, "e1", "end_turn", '{"summary": "本回合修了两座农场，继续拓荒。"}')
        return iter([
            _Chunk([_Choice(_Delta(reasoning_content="先看看国力，再决定建设。" * 200))]),
            _Chunk([_Choice(_Delta(tool_calls=[tc]))]),
            _Chunk([], _Usage()),
        ])


class _FakeOpenAI:
    """冒充 openai.OpenAI：流式返回一个 end_turn 工具调用。"""

    instances: list = []

    def __init__(self, **kw):
        self.calls: list[dict] = []
        self.turn = 0
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(self))
        _FakeOpenAI.instances.append(self)


class _ContentOnlyOpenAI:
    """冒充 openai.OpenAI：只回一段正文、不调任何工具。"""

    instances: list = []

    def __init__(self, **kw):
        self.calls: list[dict] = []
        self.chat = types.SimpleNamespace(completions=self)
        _ContentOnlyOpenAI.instances.append(self)

    def create(self, **kw):
        self.calls.append(dict(kw, messages=list(kw["messages"])))
        return iter([
            _Chunk([_Choice(_Delta(content="我决定按兵不动，先看看局势再说。"))]),
            _Chunk([], _Usage()),
        ])


class TestContentOnlyExit(unittest.TestCase):
    """"只说话不调工具"不能绕过 end_turn 的门槛。"""

    def setUp(self):
        _ContentOnlyOpenAI.instances.clear()
        import openai
        self._orig = openai.OpenAI
        openai.OpenAI = _ContentOnlyOpenAI
        self.addCleanup(lambda: setattr(openai, "OpenAI", self._orig))

    def _cfg(self, **kw):
        cfg = {"base_url": "http://stub", "api_key": "k", "model": "m",
               "max_tokens": 4000, "max_steps": 4, "ctx_window": 200000}
        cfg.update(kw)
        return cfg

    def test_no_plan_gets_nudged_not_finished(self):
        w = mp.World(size=16, seed=7, nations=["秦", "楚"])
        w.turn = 1
        mp_ai.run_openai_turn(w, "秦", self._cfg())
        msgs = _ContentOnlyOpenAI.instances[0].calls[-1]["messages"]
        self.assertTrue(any("还没有有效国策" in str(m.get("content")) for m in msgs),
                        "缺国策时应催它 plan，而不是直接收尾")
        # 即使它一直不 plan，回合也会被兜底收尾并补一条小结（归档不留空）
        self.assertEqual(w.summaries["秦"][-1]["turn"], 1)

    def test_plan_present_content_ends_turn_with_auto_summary(self):
        w = mp.World(size=16, seed=7, nations=["秦", "楚"])
        w.turn = 3
        w.plans["秦"] = {"text": "先屯田后扩军。", "turn": 3}
        n = mp_ai.run_openai_turn(w, "秦", self._cfg())
        self.assertEqual(n, 0)                       # 没有工具调用
        self.assertEqual(len(_ContentOnlyOpenAI.instances[0].calls), 1,
                         "有国策时应一次宣告就收尾")
        self.assertIn("按兵不动", w.summaries["秦"][-1]["text"])


class TestTurnLoop(unittest.TestCase):
    def setUp(self):
        _FakeOpenAI.instances.clear()
        import openai
        self._orig = openai.OpenAI
        openai.OpenAI = _FakeOpenAI
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: setattr(openai, "OpenAI", self._orig))

    def _world(self, size=16):
        return mp.World(size=size, seed=7, nations=["秦", "楚"])

    def _cfg(self, **kw):
        cfg = {"base_url": "http://stub", "api_key": "k", "model": "m",
               "max_tokens": 4000, "max_steps": 4}
        cfg.update(kw)
        return cfg

    def test_one_turn_builds_context_calls_tool_and_stores(self):
        w = self._world()
        w.turn = 1
        n = mp_ai.run_openai_turn(w, "秦", self._cfg(ctx_window=200000))
        self.assertEqual(n, 2)                      # plan + end_turn
        mem = w.turn_memory["秦"]
        self.assertEqual([r["turn"] for r in mem], [1])
        self.assertEqual(w.summaries["秦"][-1]["turn"], 1)
        # 上下文里 system 在最前，末尾是本回合状态
        sent = _FakeOpenAI.instances[0].calls[0]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("第 1 回合行动", sent[-1]["content"])

    def test_small_window_slides_and_compacts(self):
        w = self._world()
        cfg = self._cfg(ctx_window=20000, ctx_fill=0.6, ctx_slide_keep=0.5,
                        ctx_min_turns=1, max_tokens=1000)
        for t in range(1, 7):
            w.turn = t
            _FakeOpenAI.instances.clear()
            mp_ai.run_openai_turn(w, "秦", cfg)
        self.assertLess(len(w.turn_memory["秦"]), 6, "窗口应已下滑")
        blocks = w.summary_blocks["秦"]
        self.assertTrue(blocks, "下滑后应生成阶段块总结")
        self.assertLessEqual(blocks[0]["from"], blocks[0]["to"])

    def test_fixed_window_mode_still_works(self):
        w = self._world()
        for t in range(1, 4):
            w.turn = t
            mp_ai.run_openai_turn(w, "秦", self._cfg(ctx_full_turns=2))
        self.assertLessEqual(len(w.turn_memory["秦"]), 3)

    def test_save_roundtrip_keeps_summary_blocks(self):
        w = self._world()
        w.turn = 5
        w.summary_blocks["秦"] = [{"from": 1, "to": 3, "text": "早期扩张。", "turn": 4}]
        p = Path(self.tmp.name) / "s.json"
        w.save(p)
        w2 = mp.World.load(p)
        self.assertEqual(w2.summary_blocks["秦"][0]["text"], "早期扩张。")

    def test_events_for_lists_newest_first(self):
        w = self._world()
        w.turn = 3
        w.log("第3回合的事", nation="秦")
        w.turn = 9
        w.log("第9回合的事", nation="秦")
        ev = w.events_for("秦", limit=10)
        self.assertTrue(any("第3回合的事" in e for e in ev))
        self.assertTrue(any("第9回合的事" in e for e in ev))


class _ScriptedOpenAI:
    """可编程假 client：每次回合调用按预置脚本吐 tool_calls / content。"""

    instances: list = []

    def __init__(self, **kw):
        self.calls: list[dict] = []
        self.chat = types.SimpleNamespace(completions=_ScriptedCompletions(self))
        _ScriptedOpenAI.instances.append(self)


class _ScriptedCompletions:
    def __init__(self, outer):
        self.outer = outer
        self.n = 0

    def create(self, **kw):
        self.outer.calls.append(dict(kw, messages=list(kw["messages"])))
        self.n += 1
        script = self.outer.script
        item = script[min(self.n - 1, len(script) - 1)]      # 超出脚本 → 重复最后一项
        tcs = item.get("tool_calls")
        if tcs:
            return iter([_Chunk([_Choice(_Delta(
                reasoning_content=item.get("reasoning"),
                tool_calls=[_TC(i, tc["id"], tc["name"], tc["args"])
                            for i, tc in enumerate(tcs)]))]), _Chunk([], _Usage())])
        return iter([_Chunk([_Choice(_Delta(content=item["content"]))]), _Chunk([], _Usage())])


class TestTurnLoopHardening(unittest.TestCase):
    """bug #2（end_turn 只认引擎回执）与 #5（残批配对）的回归。"""

    def setUp(self):
        _ScriptedOpenAI.instances.clear()
        import openai
        self._orig = openai.OpenAI
        openai.OpenAI = _ScriptedOpenAI
        self.addCleanup(lambda: setattr(openai, "OpenAI", self._orig))

    def _cfg(self, **kw):
        cfg = {"base_url": "http://stub", "api_key": "k", "model": "m",
               "max_tokens": 4000, "max_steps": 4, "ctx_window": 200000}
        cfg.update(kw)
        return cfg

    def test_end_turn_without_plan_not_honored(self):
        """#2：没有国策时 end_turn(summary='好') 会被 execute 拒绝（无 ✅ 回执）——
        旧代码只看 args.summary 非空就收尾（伪造小结、绕过门槛）；新代码继续逼补，
        直到 max_steps 兜底。断言：那句假小结绝不能成为回合收尾。"""
        w = mp.World(size=16, seed=7, nations=["秦"])
        w.turn = 1
        _ScriptedOpenAI.script = [
            {"tool_calls": [{"id": "e1", "name": "end_turn", "args": '{"summary":"好"}'}]}]
        mp_ai.run_openai_turn(w, "秦", self._cfg(), max_steps=3)
        last = w.summaries["秦"][-1]["text"]
        self.assertNotEqual(last.strip(), "好")            # 假小结没被当成收尾
        self.assertIn("上限", last)                        # 走的是 max_steps 兜底小结

    def test_dangling_tool_calls_paired_before_store(self):
        """#5：一批里 end_turn(✅) 在前、build 在后 → end_turn 即刻 return，
        build 的 tool_call 从没收到 tool 响应。_finish 的配对闸必须补齐，
        否则残缺对话进 turn_memory，下回合 replay 直接 400。"""
        w = mp.World(size=16, seed=7, nations=["秦"])
        w.turn = 1
        w.plans["秦"] = {"text": "屯田扩军。", "turn": 1}   # 有国策 → end_turn 会真通过
        _ScriptedOpenAI.script = [
            {"tool_calls": [{"id": "e1", "name": "end_turn", "args": '{"summary":"本回合结束"}'},
                            {"id": "b1", "name": "build", "args": '{"tile":"5 5","building":"农场"}'}]}]
        mp_ai.run_openai_turn(w, "秦", self._cfg(), max_steps=3)
        rec = w.turn_memory["秦"][-1]
        called_ids = {tc["id"] for m in rec["messages"] if m.get("role") == "assistant"
                      for tc in (m.get("tool_calls") or [])}
        resp_ids = {m["tool_call_id"] for m in rec["messages"] if m.get("role") == "tool"}
        self.assertTrue(called_ids, "这批应有工具调用")
        self.assertEqual(called_ids - resp_ids, set(),   # 每个调用都有响应，无悬空
                         f"悬空 tool_call: {called_ids - resp_ids}")


if __name__ == "__main__":
    unittest.main()
