# -*- coding: utf-8 -*-
"""LLM 提供方的**失败语义**：重试到什么时候、什么时候才认输。

★ 2026-09-20 用户口径：「**重试到本回合必须跳过时，再退出，而不是马上退——不然玩家要是
当场续费呢**」。所以配额耗尽（429 `insufficient_quota`）**不特判、不立刻抛**，照样走
`api_retries` 次退避重试：

- 窗口内续费成功 ⇒ 这一次调用就成功了，回合无缝继续（`test_recovers_if_quota_returns`）；
- 窗口耗尽 ⇒ 原样抛出，由上层终止本局（存档停在上一回合结算后，`test_exhausted_raises`）。

窗口长度 = `api_retries × api_retry_wait`（都是配置项；想给"续费"留更长时间就调它们）。

★ 2026-10-24 原生化：openai SDK 已移除，本文件**不再 import openai**。假传输层的打法
与 Anthropic 那套（test_llm_provider_anthropic.py）一致——把 `llm_provider._post_json`
换成一个假的，就能把整条路跑完（翻译 → 发请求 → SSE 聚合 → 用量/重试）；
重试语义那些用例则直接在 backend 上打 `_stream_once` 的桩，连传输层都不用碰。

跑法：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients import provider as llm_provider  # noqa: E402

QUOTA_MSG = ("Error code: 429 - {'error': {'message': 'Your token-plan 1-week quota has been "
             "exhausted. The quota will reset at 09-25 18:11:00 UTC.', "
             "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}")


def _cfg(**kw) -> dict:
    cfg = {"provider": "openai", "base_url": "http://stub", "api_key": "k",
           "model": "m", "ctx_compact_tokens": 1500}
    cfg.update(kw)
    return cfg


class _FakeResp:
    """冒充 `urlopen` 的返回：既能逐行迭代（流式），也能 read()（非流式）。
    `lines` 可以是字符串列表（chat SSE）或生成器（延迟产出——流卡住那种用例要它）。"""

    def __init__(self, lines=None, payload=None, status=200):
        self._lines = lines if lines is not None else []
        self._payload = payload
        self.status = status
        self.closed = False

    def __iter__(self):
        return iter(ln.encode("utf-8") if isinstance(ln, str) else ln for ln in self._lines)

    def read(self):
        return self._payload if self._payload is not None else b"".join(self.__iter__())

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _chat_sse(*chunks) -> list[str]:
    """把 chat/completions 块编成 SSE 行（data-only、以 [DONE] 收尾——chat 流没有 event: 行）。"""
    out: list[str] = []
    for chunk in chunks:
        out.append("data: " + json.dumps(chunk, ensure_ascii=False))
        out.append("")
    out.append("data: [DONE]")
    out.append("")
    return out


class _Transport:
    """按剧本回应的假传输层：每一项要么是异常（抛），要么是 SSE 行表。"""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __enter__(self):
        self._orig = llm_provider._post_json

        def fake(url, headers, body, timeout):
            self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
            item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
            if isinstance(item, Exception):
                raise item
            return _FakeResp(lines=item)

        llm_provider._post_json = fake
        return self

    def __exit__(self, *exc):
        llm_provider._post_json = self._orig
        return False


class TestRetryWindow(unittest.TestCase):
    """★ 配额耗尽也走重试窗口（打 `_stream_once` 桩——重试语义不依赖传输层细节）。"""

    def _backend(self, fail_times: int, err: Exception | None = None):
        """前 fail_times 次调用抛 err，之后返回一条正常消息；返回 (backend, cfg, 计数器)。"""
        err = err or llm_provider.EndpointError(QUOTA_MSG, status_code=429)
        calls: list[int] = []
        b = llm_provider.make_backend(_cfg())

        def once(*a, **k):
            calls.append(1)
            if len(calls) <= fail_times:
                raise err
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
        with self.assertRaises(llm_provider.EndpointError):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(calls), cfg["api_retries"], "该重试满 api_retries 次才认输")

    def test_window_is_configurable(self):
        """窗口长度就是那两个配置项——想给"续费"多留时间，调它们即可（不是写死的）。"""
        b, cfg, calls = self._backend(fail_times=99)
        with self.assertRaises(llm_provider.EndpointError):
            b.chat_turn([], [], {**cfg, "api_retries": 5})
        self.assertEqual(len(calls), 5)


class TestUsageReporting(unittest.TestCase):
    """★ 用户 2026-09-20：「报错误的会导致估价错误……那应该改游戏，而不是改网关」。
    ⇒ 提供方**没报**用量时：本地估算 + 打 `estimated` 标记（显示成 ≈），
      绝不把"没报"当成 0（那会打印 `输出0.0tok`，看起来像模型没说话）。报了就一律用真数。
    """

    def _lines(self, with_usage):
        chunks = [{"choices": [{"delta": {"reasoning_content": "先想一想" * 50}}]},
                  {"choices": [{"delta": {"content": "我决定了"}}]}]
        if with_usage == "zeros":
            # 本机 qoder-flash 网关的真实形态：**回一个全 0 的 usage 对象**（上游不给计数）
            chunks.append({"choices": [], "usage": {"completion_tokens": 0, "prompt_tokens": 0,
                                                    "completion_tokens_details": None,
                                                    "prompt_cache_hit_tokens": 0,
                                                    "prompt_cache_miss_tokens": 0}})
        elif with_usage:
            chunks.append({"choices": [], "usage": {"completion_tokens": 123,
                                                    "completion_tokens_details": {"reasoning_tokens": 45},
                                                    "prompt_tokens": 1000,
                                                    "prompt_cache_hit_tokens": 900,
                                                    "prompt_cache_miss_tokens": 100}})
        return _chat_sse(*chunks)

    def _run(self, with_usage):
        with _Transport(self._lines(with_usage)):
            b = llm_provider.make_backend(_cfg())
            return b.chat_turn([], [], _cfg(max_tokens=100))

    def test_missing_usage_is_estimated_and_marked(self):
        _msg, st = self._run(with_usage=False)
        self.assertTrue(st.get("estimated"), "没报用量必须打标记（客户端据此显示 ≈）")
        self.assertGreater(st["out_tokens"], 0, "不能把'没报'当成 0 输出")
        self.assertGreater(st["reason_tokens"], 0, "思考部分也要估")
        self.assertFalse(st.get("usage_reported"))

    def test_zeroed_usage_counts_as_unreported(self):
        """★ 全 0 的 usage = "没报"（实测网关就是这样）：只看对象在不在会误判成"报了真数"，
        于是估算不触发、显示照旧 `输出0.0tok`。真调用不可能 prompt/completion 同时为 0。"""
        _msg, st = self._run(with_usage="zeros")
        self.assertFalse(st.get("usage_reported"), "全 0 必须判为没报")
        self.assertTrue(st.get("estimated"))
        self.assertGreater(st["out_tokens"], 0)

    def test_real_usage_wins(self):
        _msg, st = self._run(with_usage=True)
        self.assertTrue(st.get("usage_reported"))
        self.assertFalse(st.get("estimated"), "报了真数就不该再估算")
        self.assertEqual(st["out_tokens"], 123)
        self.assertEqual(st["reason_tokens"], 45)
        self.assertEqual((st["hit"], st["miss"]), (900, 100))


class TestStreamAggregation(unittest.TestCase):
    """流式聚合本身：工具调用参数跨 delta 累积、URL/头、请求体形态。"""

    def test_tool_call_arguments_accumulate_across_deltas(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": "query", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"t": '}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '"res"}'}}]}}]},
        ]
        with _Transport(_chat_sse(*chunks)):
            b = llm_provider.make_backend(_cfg())
            msg, _st = b.chat_turn([], [], _cfg(max_tokens=100))
        self.assertEqual(msg["tool_calls"][0]["id"], "call_1")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "query")
        self.assertEqual(json.loads(msg["tool_calls"][0]["function"]["arguments"]), {"t": "res"})

    def test_request_shape_and_url(self):
        """URL 与 SDK 时代逐字一致（base 不擅自补 /v1）；thinking 一律发；流式带 stream_options。"""
        tools = [{"type": "function", "function": {"name": "query", "parameters": {}}}]
        with _Transport(_chat_sse({"choices": [{"delta": {"content": "好"}}]})) as t:
            b = llm_provider.make_backend(_cfg(base_url="https://api.deepseek.com"))
            b.chat_turn([], tools, _cfg(base_url="https://api.deepseek.com",
                                        max_tokens=100, reasoning_effort="low"))
        call = t.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["headers"]["authorization"], "Bearer k")
        body = call["body"]
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})


class TestCompleteText(unittest.TestCase):
    """`complete_text`（记忆压缩那条非流式路）：2026-10-02 起 ① 可带 tools（**只为
    前缀缓存**，压缩调用要接在主请求后面）、② 回 `(text, stats)`，好让压缩调用自己的
    命中率看得见——在那之前它没有任何统计，改造的收益无法验证。"""

    def _run(self, payload, tools=None, **cfgkw):
        captured: dict = {}
        orig = llm_provider._post_json

        def fake(url, headers, body, timeout):
            captured.update(body)
            captured["_url"] = url
            return _FakeResp(payload=json.dumps(payload).encode("utf-8"))

        llm_provider._post_json = fake
        try:
            b = llm_provider.make_backend(_cfg(**cfgkw))
            text, stats = b.complete_text([{"role": "system", "content": "S"},
                                           {"role": "user", "content": "U"}],
                                          _cfg(**cfgkw), tools=tools)
        finally:
            llm_provider._post_json = orig
        return text, stats, captured

    def _payload(self, text="长期记忆摘要", usage=None, finish_reason=None):
        return {"choices": [{"message": {"content": text}, "finish_reason": finish_reason}],
                **({"usage": usage} if usage is not None else {})}

    def test_tools_are_sent_verbatim_for_prefix_reuse(self):
        """★ 压缩调用要复用主请求的前缀 ⇒ tools 与 tool_choice 必须**逐字照发**。
        2026-10-02 真端点实测：`tool_choice="none"` 会让网关整个丢掉 tools 段，
        前缀从 system 之后断掉（命中只剩 40%）；照发 auto 则 93.6%。"""
        tools = [{"type": "function", "function": {"name": "query", "parameters": {}}}]
        text, _st, captured = self._run(self._payload(), tools=tools)
        self.assertEqual(text, "长期记忆摘要")
        self.assertEqual(captured["tools"], tools)
        self.assertEqual(captured["tool_choice"], "auto")
        self.assertEqual(captured["thinking"], {"type": "disabled"})
        self.assertEqual(captured["max_tokens"], 1500)

    def test_no_tools_keeps_the_old_request_shape(self):
        _text, _st, captured = self._run(self._payload())
        self.assertNotIn("tools", captured)
        self.assertNotIn("tool_choice", captured)

    def test_compression_preserves_truncation_reason(self):
        _text, stats, _c = self._run(self._payload(finish_reason="length"))
        self.assertEqual(stats["stop_reason"], "length")

    def test_usage_becomes_stats(self):
        u = {"completion_tokens": 30, "prompt_tokens": 1000,
             "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200}
        _text, st, _c = self._run(self._payload(usage=u))
        self.assertTrue(st["usage_reported"])
        self.assertEqual((st["hit"], st["miss"], st["out_tokens"]), (800, 200, 30))
        self.assertGreaterEqual(st["wall"], 0)

    def test_unreported_usage_is_estimated_not_zero(self):
        _text, st, _c = self._run(self._payload())
        self.assertFalse(st["usage_reported"])
        self.assertTrue(st["estimated"])
        self.assertGreater(st["out_tokens"], 0, "不能把'没报'当成 0")

    def test_standard_cached_tokens_field_is_read(self):
        """★ 只认 DeepSeek 的 `prompt_cache_hit_tokens` 是不够的：标准 OpenAI 拼法把命中
        放在 `prompt_tokens_details.cached_tokens` 里，只认前者会把**真命中报成 0**
        （看着像缓存整段失效，比不报还坑）。"""
        u = {"completion_tokens": 30, "prompt_tokens": 1000,
             "prompt_tokens_details": {"cached_tokens": 640}}
        _text, st, _c = self._run(self._payload(usage=u))
        self.assertEqual(st["hit"], 640)
        self.assertEqual(st["miss"], 360, "没给 miss 字段就自己减出未命中部分")

    def test_streaming_path_reads_the_standard_field_too(self):
        """流式那条路同一个坑（§9.0 的老账）：顺手一起修。"""
        lines = _chat_sse(
            {"choices": [{"delta": {"content": "好"}}]},
            {"choices": [], "usage": {"completion_tokens": 10, "prompt_tokens": 500,
                                      "prompt_tokens_details": {"cached_tokens": 400}}})
        with _Transport(lines):
            b = llm_provider.make_backend(_cfg())
            _msg, st = b.chat_turn([], [], _cfg(max_tokens=100))
        self.assertEqual((st["hit"], st["miss"]), (400, 100))


class TestGatewayHtmlErrorRetries(unittest.TestCase):
    """★ **网关回的 HTML 错误页**必须走重试（用户 2026-09-20：「压根没有重试就崩了」）。

    SDK 时代那一局的异常是 `openai.APIError: HTTP 504 …<center>alb</center>`——错误体是
    HTML、解析不出 JSON ⇒ SDK 抛的是**基类 APIError**（**没有 `status_code`**），旧代码
    一次都没重试就冒出循环、整局终止（197 回合的档停在半路）。
    原生化后：HTTPError 天然带码（504 → 5xx → 重试）；这里同时保留"没有 status_code、
    只有消息里一句 HTTP 504"的老形态用例——`status_code_of` 的正则就是为它留的。
    口径：**4xx（鉴权/参数）直接抛；5xx 与"认不出状态码"一律重试**。
    """

    def _backend(self, exc: Exception, **cfg):
        calls: list[int] = []
        b = llm_provider.make_backend(_cfg())

        def once(*a, **k):
            calls.append(1)
            raise exc
        b._stream_once = once
        return b, calls, {"api_retries": 3, "api_retry_wait": 0, **cfg}

    def test_statusless_504_is_retried_via_message_regex(self):
        e = llm_provider.EndpointError("HTTP 504 <html><head><title>504 Gateway Time-out</title>"
                                       "</head><body><center>alb</center></body></html>")
        b, calls, cfg = self._backend(e)
        tags: list[str] = []
        with self.assertRaises(llm_provider.EndpointError):
            b.chat_turn([], [], cfg, on_retry=lambda a, tag, w, t: tags.append(tag))
        self.assertEqual(len(calls), 3, "认不出状态码也必须重试满 api_retries 次")
        self.assertIn("504", tags[0], f"状态区该看出是网关的 504：{tags}")

    def test_html_504_with_status_is_retried(self):
        b, calls, _cfg2 = self._backend(
            llm_provider.EndpointError("HTTP 504 <html>alb</html>", status_code=504))
        with self.assertRaises(llm_provider.EndpointError):
            b.chat_turn([], [], _cfg2)
        self.assertEqual(len(calls), 3, "5xx（HTML 错误页）必须重试满 api_retries 次")

    def test_unknown_status_is_treated_as_retryable(self):
        """认不出状态码（SSE 中途断、上游只说了一句）⇒ 按可重试处理。"""
        b, calls, cfg = self._backend(
            llm_provider.EndpointError("An error occurred during streaming"))
        with self.assertRaises(llm_provider.EndpointError):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(calls), 3, "认不出状态码不许静默跳过重试")

    def test_html_4xx_is_not_retried(self):
        """4xx（鉴权/参数）重试没意义：一次就抛。"""
        b, calls, cfg = self._backend(
            llm_provider.EndpointError("HTTP 403 <html>forbidden</html>", status_code=403))
        with self.assertRaises(llm_provider.EndpointError):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(calls), 1, "4xx 不该重试")


class TestUrlAndHeaders(unittest.TestCase):
    def test_chat_url_matches_sdk_join(self):
        self.assertEqual(llm_provider.openai_chat_url("https://api.deepseek.com/v1"),
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(llm_provider.openai_chat_url("https://x/"),
                         "https://x/chat/completions", "base 不擅自补 /v1（与 SDK 一致）")

    def test_bearer_auth_and_missing_key(self):
        h = llm_provider._bearer_headers({"api_key": "sk-k"})
        self.assertEqual(h["authorization"], "Bearer sk-k")
        self.assertEqual(h["content-type"], "application/json")
        with self.assertRaises(ValueError):
            llm_provider._bearer_headers({"api_key": ""})


class TestStreamTimeout(unittest.TestCase):
    """★ 流式的超时量的是**等待**，不是总时长（用户 2026-09-20 口径：
    「首字3分钟，sse内60秒，非流式900」）。

    首字 180s / 流内 60s / **流式没有总时长上限**；非流式（记忆压缩）900s 总时长。
    这里用小数秒跑真定时器（生产值是 180/60/900，量纲一样）。
    """

    def setUp(self):
        if os.name == "nt" or not hasattr(signal, "SIGALRM"):
            self.skipTest("Windows / 无 SIGALRM：看门狗空转（那边只有 socket timeout 兜底）")

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

        def fake(url, headers, body, timeout):
            def lines():
                time.sleep(0.8)          # 首块迟迟不来（> api_ttft_timeout）
                yield 'data: {"choices": [{"delta": {"content": "迟到的正文"}}]}'
                yield ""
                yield "data: [DONE]"
                yield ""
            return _FakeResp(lines=lines())

        orig = llm_provider._post_json
        calls: list[int] = []

        def counting_fake(*a, **k):
            calls.append(1)
            return fake(*a, **k)
        llm_provider._post_json = counting_fake
        self.addCleanup(lambda: setattr(llm_provider, "_post_json", orig))
        b = llm_provider.make_backend(_cfg())
        cfg = _cfg(max_tokens=100, api_ttft_timeout=0.2, api_retries=2, api_retry_wait=0)
        tags: list[str] = []
        with self.assertRaises(TimeoutError):
            b.chat_turn([], [], cfg, on_retry=lambda a, tag, w, t: tags.append(tag))
        self.assertEqual(len(calls), 2, "超时属可重试类：该重试满 api_retries 次")
        # 状态区上要能看出是哪一段超时（"TimeoutError" 等于没说：180s 和 60s 不是一回事）
        self.assertTrue(tags and "首字" in tags[0], f"重试播报该点明是哪一段超时：{tags}")


if __name__ == "__main__":
    unittest.main()
