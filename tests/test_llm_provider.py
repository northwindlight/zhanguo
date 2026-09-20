# -*- coding: utf-8 -*-
"""LLM 提供方的**失败语义**：重试到什么时候、什么时候才认输。

★ 2026-09-20 用户口径：「**重试到本回合必须跳过时，再退出，而不是马上退——不然玩家要是
当场续费呢**」。所以配额耗尽（429 `insufficient_quota`）**不特判、不立刻抛**，照样走
`api_retries` 次退避重试：

- 窗口内续费成功 ⇒ 这一次调用就成功了，回合无缝继续（`test_recovers_if_quota_returns`）；
- 窗口耗尽 ⇒ 原样抛出，由上层终止本局（存档停在上一回合结算后，`test_exhausted_raises`）。

窗口长度 = `api_retries × api_retry_wait`（都是配置项；想给"续费"留更长时间就调它们）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_provider  # noqa: E402

QUOTA_MSG = ("Error code: 429 - {'error': {'message': 'Your token-plan 1-week quota has been "
             "exhausted. The quota will reset at 09-25 18:11:00 UTC.', "
             "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}")


class _Fake429(Exception):
    """冒充 `openai.RateLimitError`：`chat_turn` 里是运行时 `from openai import RateLimitError`，
    所以把这个名字指过来，`except` 就能接住。"""


class TestRetryWindow(unittest.TestCase):
    def setUp(self):
        import openai
        self._saved = getattr(openai, "RateLimitError", None)
        openai.RateLimitError = _Fake429
        self._orig_openai = openai.OpenAI
        openai.OpenAI = lambda **kw: types.SimpleNamespace()
        self.addCleanup(self._restore)

    def _restore(self):
        import openai
        openai.OpenAI = self._orig_openai
        if self._saved is not None:
            openai.RateLimitError = self._saved

    def _backend(self, fail_times: int, msg: str = QUOTA_MSG):
        """前 fail_times 次调用抛 `msg`，之后返回一条正常消息；返回 (backend, cfg, 计数器)。"""
        calls: list[int] = []
        b = llm_provider.make_backend({"provider": "openai", "base_url": "http://stub",
                                       "api_key": "k", "model": "m"})
        def once(*a, **k):
            calls.append(1)
            if len(calls) <= fail_times:
                raise _Fake429(msg)
            return ({"role": "assistant", "content": "好"}, {})
        b._stream_once = once
        cfg = {"api_retries": 3, "api_retry_wait": 0}
        return b, cfg, calls

    def test_recovers_if_quota_returns(self):
        """★ 配额第 2 次就恢复了（＝玩家当场续费）⇒ 这一回合照常继续，不许终止。"""
        b, cfg, calls = self._backend(fail_times=1)
        msg, _stats = b.chat_turn([], [], cfg)
        self.assertEqual(msg["content"], "好")
        self.assertEqual(len(calls), 2, "该重试一次就拿到结果")

    def test_exhausted_raises(self):
        """★ 重试窗口内没续上 ⇒ 原样抛出，交给上层终止本局（而不是接着烧步数）。"""
        b, cfg, calls = self._backend(fail_times=99)
        with self.assertRaises(_Fake429):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(calls), cfg["api_retries"], "该重试满 api_retries 次才认输")

    def test_window_is_configurable(self):
        """窗口长度就是那两个配置项——想给"续费"多留时间，调它们即可（不是写死的）。"""
        b, cfg, calls = self._backend(fail_times=99, msg=QUOTA_MSG)
        with self.assertRaises(_Fake429):
            b.chat_turn([], [], {**cfg, "api_retries": 5})
        self.assertEqual(len(calls), 5)


class _Delta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls


class _Chunk:
    def __init__(self, delta=None, usage=None):
        self.choices = [types.SimpleNamespace(delta=delta)] if delta is not None else []
        self.usage = usage


class _FakeStreamClient:
    """冒充 openai.OpenAI 的流式端点：可指定**报不报 usage**（实测本机 qoder-flash 网关恒不报）。"""

    def __init__(self, with_usage: bool):
        self.with_usage = with_usage
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        out = [_Chunk(_Delta(reasoning_content="先想一想" * 50)),
               _Chunk(_Delta(content="我决定了"))]
        if self.with_usage == "zeros":
            # 本机 qoder-flash 网关的真实形态：**回一个全 0 的 usage 对象**（上游不给计数）
            u0 = types.SimpleNamespace(completion_tokens=0, prompt_tokens=0,
                                       completion_tokens_details=None,
                                       prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0)
            out.append(_Chunk(None, usage=u0))
        elif self.with_usage:
            u = types.SimpleNamespace(completion_tokens=123,
                                      completion_tokens_details=types.SimpleNamespace(
                                          reasoning_tokens=45),
                                      prompt_cache_hit_tokens=900, prompt_cache_miss_tokens=100)
            out.append(_Chunk(None, usage=u))
        return iter(out)


class TestUsageReporting(unittest.TestCase):
    """★ 用户 2026-09-20：「报错误的会导致估价错误……那应该改游戏，而不是改网关」。
    ⇒ 提供方**没报**用量时：本地估算 + 打 `estimated` 标记（显示成 ≈），
      绝不把"没报"当成 0（那会打印 `输出0.0tok`，看起来像模型没说话）。报了就一律用真数。
    """

    def _backend(self, with_usage: bool):
        import openai
        orig = openai.OpenAI
        openai.OpenAI = lambda **kw: _FakeStreamClient(with_usage)
        self.addCleanup(lambda: setattr(openai, "OpenAI", orig))
        return llm_provider.make_backend({"provider": "openai", "base_url": "http://stub",
                                          "api_key": "k", "model": "m"})

    def test_missing_usage_is_estimated_and_marked(self):
        b = self._backend(with_usage=False)
        _msg, st = b.chat_turn([], [], {"model": "m", "max_tokens": 100})
        self.assertTrue(st.get("estimated"), "没报用量必须打标记（客户端据此显示 ≈）")
        self.assertGreater(st["out_tokens"], 0, "不能把'没报'当成 0 输出")
        self.assertGreater(st["reason_tokens"], 0, "思考部分也要估")
        self.assertFalse(st.get("usage_reported"))

    def test_zeroed_usage_counts_as_unreported(self):
        """★ 全 0 的 usage 对象 = "没报"（实测网关就是这样）：只看对象在不在会误判成"报了真数"，
        于是估算不触发、显示照旧 `输出0.0tok`。真调用不可能 prompt/completion 同时为 0。"""
        b = self._backend(with_usage="zeros")
        _msg, st = b.chat_turn([], [], {"model": "m", "max_tokens": 100})
        self.assertFalse(st.get("usage_reported"), "全 0 必须判为没报")
        self.assertTrue(st.get("estimated"))
        self.assertGreater(st["out_tokens"], 0)

    def test_real_usage_wins(self):
        b = self._backend(with_usage=True)
        _msg, st = b.chat_turn([], [], {"model": "m", "max_tokens": 100})
        self.assertTrue(st.get("usage_reported"))
        self.assertFalse(st.get("estimated"), "报了真数就不该再估算")
        self.assertEqual(st["out_tokens"], 123)
        self.assertEqual(st["reason_tokens"], 45)
        self.assertEqual((st["hit"], st["miss"]), (900, 100))


if __name__ == "__main__":
    unittest.main()
