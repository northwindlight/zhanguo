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

import os
import signal
import sys
import time
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


class _FakeNonStreamClient:
    """冒充非流式端点：`usage` 由用例指定（`None` = 不报用量）。"""

    def __init__(self, text="长期记忆摘要", usage=None):
        self.text = text
        self.usage = usage
        self.calls: list[dict] = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=self.text))],
            usage=self.usage)


class TestCompleteText(unittest.TestCase):
    """`complete_text`（记忆压缩那条非流式路）：2026-10-02 起 ① 可带 tools（**只为
    前缀缓存**，压缩调用要接在主请求后面）、② 回 `(text, stats)`，好让压缩调用自己的
    命中率看得见——在那之前它没有任何统计，改造的收益无法验证。"""

    def _backend(self, client):
        b = llm_provider.OpenAICompat.__new__(llm_provider.OpenAICompat)
        b.client = client
        return b

    def _cfg(self):
        return {"provider": "openai", "base_url": "http://stub", "api_key": "k",
                "model": "m", "ctx_compact_tokens": 1500}

    def test_tools_are_sent_verbatim_for_prefix_reuse(self):
        """★ 压缩调用要复用主请求的前缀 ⇒ tools 与 tool_choice 必须**逐字照发**。
        2026-10-02 真端点实测：`tool_choice="none"` 会让网关整个丢掉 tools 段，
        前缀从 system 之后断掉（命中只剩 40%）；照发 auto 则 93.6%。"""
        c = _FakeNonStreamClient()
        tools = [{"type": "function", "function": {"name": "query", "parameters": {}}}]
        text, _st = self._backend(c).complete_text(
            [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
            self._cfg(), tools=tools)
        self.assertEqual(text, "长期记忆摘要")
        self.assertEqual(c.calls[0]["tools"], tools)
        self.assertEqual(c.calls[0]["tool_choice"], "auto")
        self.assertEqual(c.calls[0]["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(c.calls[0]["max_tokens"], 1500)

    def test_no_tools_keeps_the_old_request_shape(self):
        c = _FakeNonStreamClient()
        self._backend(c).complete_text([{"role": "user", "content": "U"}], self._cfg())
        self.assertNotIn("tools", c.calls[0])
        self.assertNotIn("tool_choice", c.calls[0])

    def test_usage_becomes_stats(self):
        u = types.SimpleNamespace(completion_tokens=30, prompt_tokens=1000,
                                  prompt_cache_hit_tokens=1000 - 200,
                                  prompt_cache_miss_tokens=200)
        _text, st = self._backend(_FakeNonStreamClient(usage=u)).complete_text(
            [{"role": "user", "content": "U"}], self._cfg())
        self.assertTrue(st["usage_reported"])
        self.assertEqual((st["hit"], st["miss"], st["out_tokens"]), (800, 200, 30))
        self.assertGreaterEqual(st["wall"], 0)

    def test_unreported_usage_is_estimated_not_zero(self):
        _text, st = self._backend(_FakeNonStreamClient(usage=None)).complete_text(
            [{"role": "user", "content": "U"}], self._cfg())
        self.assertFalse(st["usage_reported"])
        self.assertTrue(st["estimated"])
        self.assertGreater(st["out_tokens"], 0, "不能把'没报'当成 0")

    def test_standard_cached_tokens_field_is_read(self):
        """★ 只认 DeepSeek 的 `prompt_cache_hit_tokens` 是不够的：标准 OpenAI 拼法把命中
        放在 `prompt_tokens_details.cached_tokens` 里，只认前者会把**真命中报成 0**
        （看着像缓存整段失效，比不报还坑）。"""
        u = types.SimpleNamespace(
            completion_tokens=30, prompt_tokens=1000,
            prompt_tokens_details=types.SimpleNamespace(cached_tokens=640))
        _text, st = self._backend(_FakeNonStreamClient(usage=u)).complete_text(
            [{"role": "user", "content": "U"}], self._cfg())
        self.assertEqual(st["hit"], 640)
        self.assertEqual(st["miss"], 360, "没给 miss 字段就自己减出未命中部分")

    def test_streaming_path_reads_the_standard_field_too(self):
        """流式那条路同一个坑（§9.0 的老账）：顺手一起修。"""
        class _Client:
            def __init__(self):
                self.chat = types.SimpleNamespace(completions=self)

            def create(self, **kw):
                u = types.SimpleNamespace(completion_tokens=10, prompt_tokens=500,
                                          completion_tokens_details=None,
                                          prompt_tokens_details=types.SimpleNamespace(
                                              cached_tokens=400))
                return iter([_Chunk(_Delta(content="好")), _Chunk(None, usage=u)])

        b = llm_provider.OpenAICompat.__new__(llm_provider.OpenAICompat)
        b.client = _Client()
        _msg, st = b.chat_turn([], [], {"model": "m", "max_tokens": 100})
        self.assertEqual((st["hit"], st["miss"]), (400, 100))


class TestGatewayHtmlErrorRetries(unittest.TestCase):
    """★ **网关回的 HTML 错误页**必须走重试（用户 2026-09-20：「压根没有重试就崩了」）。

    实测那一局的异常是 `openai.APIError: HTTP 504 …<center>alb</center>`——错误体是
    HTML、解析不出 JSON ⇒ SDK 抛的是**基类 `APIError`**（**没有** `status_code`），
    而旧代码只接 `APIStatusError` ⇒ 一次都没重试就冒出循环、整局终止
    （197 回合的档停在半路）。
    口径：**4xx（鉴权/参数）直接抛；5xx 与"认不出状态码"一律重试**——"认不出"必须
    按可重试处理，否则就是一条静默绕过重试的后门。
    """

    def setUp(self):
        import openai
        self._orig = openai.OpenAI
        openai.OpenAI = lambda **kw: types.SimpleNamespace()
        self.addCleanup(lambda: setattr(openai, "OpenAI", self._orig))

    def _backend(self, exc: Exception, **cfg):
        calls: list[int] = []
        b = llm_provider.make_backend({"provider": "openai", "base_url": "http://stub",
                                       "api_key": "k", "model": "m"})

        def once(*a, **k):
            calls.append(1)
            raise exc
        b._stream_once = once
        return b, calls, {"api_retries": 3, "api_retry_wait": 0, **cfg}

    def _api_error(self, msg: str):
        import openai
        return openai.APIError(msg, request=None, body=None)

    def test_html_504_is_retried(self):
        import openai
        e = self._api_error("HTTP 504 <html><head><title>504 Gateway Time-out</title>"
                            "</head><body><center>alb</center></body></html>")
        b, calls, cfg = self._backend(e)
        tags: list[str] = []
        with self.assertRaises(openai.APIError):
            b.chat_turn([], [], cfg, on_retry=lambda a, tag, w, t: tags.append(tag))
        self.assertEqual(len(calls), 3, "5xx（HTML 错误页）必须重试满 api_retries 次")
        self.assertIn("504", tags[0], f"状态区该看出是网关的 504：{tags}")

    def test_unknown_status_is_treated_as_retryable(self):
        """认不出状态码（SSE 中途断、上游只说了一句）⇒ 按可重试处理。"""
        import openai
        b, calls, cfg = self._backend(self._api_error("An error occurred during streaming"))
        with self.assertRaises(openai.APIError):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(calls), 3, "认不出状态码不许静默跳过重试")

    def test_html_4xx_is_not_retried(self):
        """4xx（鉴权/参数）重试没意义：一次就抛。"""
        import openai
        b, calls, cfg = self._backend(self._api_error("HTTP 403 <html>forbidden</html>"))
        with self.assertRaises(openai.APIError):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(calls), 1, "4xx 不该重试")


class TestStreamTimeout(unittest.TestCase):
    """★ 流式的超时量的是**等待**，不是总时长（用户 2026-09-20 口径：
    「首字3分钟，sse内60秒，非流式900」）。

    首字 180s / 流内 60s / **流式没有总时长上限**；非流式（记忆压缩）900s 总时长。
    这里用小数秒跑真定时器（生产值是 180/60/900，量纲一样）。
    """

    def setUp(self):
        if os.name == "nt" or not hasattr(signal, "SIGALRM"):
            self.skipTest("Windows / 无 SIGALRM：看门狗空转（那边只有 SDK timeout 兜底）")

    def test_first_chunk_deadline(self):
        """首字：发起后 0.3s 还没第一个块 ⇒ 判死，且消息点明是「首字」。"""
        with self.assertRaises(TimeoutError) as cm:
            with llm_provider.stream_timeout(0.3, 60) as clock:
                time.sleep(0.8)
                clock.kick()
        self.assertIn("首字", str(cm.exception))

    def test_chunk_gap_deadline(self):
        """流内：收到过块之后，隔 0.3s 没来下一块 ⇒ 判死，消息点明是「流内」。"""
        with self.assertRaises(TimeoutError) as cm:
            with llm_provider.stream_timeout(60, 0.3) as clock:
                clock.kick()
                time.sleep(0.8)
                clock.kick()
        self.assertIn("流内", str(cm.exception))

    def test_no_total_cap_while_streaming(self):
        """★★ 核心口径：**一直在吐字就没有总时长上限**——跑的总时长远超"首字数"也不算超时。

        旧的实现是 `hard_timeout(api_timeout)` 拿整段总时长硬砍（180s），这条会红：
        按实测 ≈27 tok/s，180s 只够 ≈4.9k token，`max_tokens=16384` 根本到不了。
        """
        t0 = time.time()
        with llm_provider.stream_timeout(0.5, 0.8) as clock:
            for _ in range(6):
                time.sleep(0.2)
                clock.kick()
        self.assertGreater(time.time() - t0, 0.5, "前提：这一跑的总时长的确超过了首字数")

    def test_clock_always_disarms(self):
        """★ 收工必卸（与 `hard_timeout` 同一条纪律）：看门狗**不许漏到块外**——
        漏了就会在工具执行/引擎结算时突然抛超时，把好好的一回合炸掉。"""
        with llm_provider.stream_timeout(5, 5) as clock:
            clock.kick()
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0.0, "没卸掉 alarm")

    def test_stalled_stream_raises_and_retries(self):
        """接上真循环：流卡住由看门狗判死，异常类型是 TimeoutError ⇒ 走既有重试通道。"""
        import openai

        class _Stalled:
            def __init__(self, **kw):
                self.calls = 0
                self.chat = types.SimpleNamespace(completions=self)

            def create(self, **kw):
                self.calls += 1

                def gen():
                    time.sleep(0.8)          # 首块迟迟不来（> api_ttft_timeout）
                    yield _Chunk(_Delta(content="迟到的正文"))
                return gen()

        fake = _Stalled()
        orig = openai.OpenAI
        openai.OpenAI = lambda **kw: fake
        self.addCleanup(lambda: setattr(openai, "OpenAI", orig))
        b = llm_provider.make_backend({"provider": "openai", "base_url": "http://stub",
                                       "api_key": "k", "model": "m"})
        cfg = {"model": "m", "max_tokens": 100, "api_ttft_timeout": 0.2,
               "api_retries": 2, "api_retry_wait": 0}
        tags: list[str] = []
        with self.assertRaises(TimeoutError):
            b.chat_turn([], [], cfg, on_retry=lambda a, tag, w, t: tags.append(tag))
        self.assertEqual(fake.calls, 2, "超时属可重试类：该重试满 api_retries 次")
        # 状态区上要能看出是哪一段超时（"TimeoutError" 等于没说：180s 和 60s 不是一回事）
        self.assertTrue(tags and "首字" in tags[0], f"重试播报该点明是哪一段超时：{tags}")


if __name__ == "__main__":
    unittest.main()
