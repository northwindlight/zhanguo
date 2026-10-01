# -*- coding: utf-8 -*-
"""Anthropic 提供方（`provider="anthropic"`）的翻译层与失败语义。

★ 这一路**只用标准库**（urllib + 手写 SSE 解析），所以这些测试**不需要 `anthropic` 包**，
  也不需要 `openai` 包——把 `llm_provider._anthropic_open` 换成一个假的传输层就能把
  整条路跑完（翻译 → 发请求 → 聚合 → 用量/重试/续写）。

口径来源（2026-10-01 对 `api.deepseek.com/anthropic` 的实测，见交付目录里的
`tools/anthropic_probe.py` 与 `tools/anthropic_edge*.py`）：

- 事件流是标准 Anthropic 形态：message_start / content_block_start / content_block_delta
  （thinking_delta、**signature_delta**、input_json_delta、text_delta）/ content_block_stop /
  message_delta（带 stop_reason 与累计 usage）/ message_stop；
- **签名是流末尾单独一条 signature_delta 给的**，content_block_start 里是空串；
- 用量：`input_tokens`(只算未命中) + `cache_read_input_tokens` + `cache_creation_input_tokens`
  + `output_tokens`；hit=cache_read、miss=input+creation（这是"缓存命中率"那个显示的口径）；
- 工具结果必须**合成一条 user 消息里的多个 tool_result**；tool_result 那条 user 后面紧跟
  一条"最新状态"的 user 是允许的（实测 200），别为了交替角色去改循环；
- 该路由不校验签名，但**官方 Anthropic 校验** ⇒ 缺签名/换模型时一律降级为不回放 thinking
  （拿假签名去撞 400 ＝ 终止整局，代价完全不对称）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_provider  # noqa: E402


def _cfg(**kw) -> dict:
    cfg = {"provider": "anthropic",
           "base_url": "https://api.deepseek.com/anthropic",
           "api_key": "sk-test", "model": "deepseek-flash[1m]",
           "max_tokens": 4096, "temperature": 0.3}
    cfg.update(kw)
    return cfg


class _FakeResp:
    """冒充 `urllib.request.urlopen` 的返回：既能逐行迭代（流式），也能 read()（非流式）。"""

    def __init__(self, lines=None, payload=None, status=200):
        self._lines = [ln.encode("utf-8") for ln in (lines or [])]
        self._payload = payload
        self.status = status
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._payload if self._payload is not None else b"".join(self._lines)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _sse(*events) -> list[str]:
    """把 (事件名, data) 编成 SSE 行（含事件之间的空行——分隔符不能省）。"""
    out: list[str] = []
    for name, payload in events:
        out.append(f"event: {name}")
        out.append("data: " + json.dumps(payload, ensure_ascii=False))
        out.append("")
    return out


def _usage(input_tokens=100, read=900, creation=0, output=7) -> dict:
    return {"input_tokens": input_tokens, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": creation, "output_tokens": output}


def _msg_start(usage=None) -> tuple:
    return ("message_start", {"type": "message_start",
                              "message": {"usage": usage if usage is not None else _usage()}})


def _text_block(idx: int, text: str) -> list[tuple]:
    return [("content_block_start", {"index": idx,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"index": idx,
                                     "delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {"index": idx})]


def _thinking_block(idx: int, think: str, signature: str = "SIG") -> list[tuple]:
    ev = [("content_block_start", {"index": idx,
                                   "content_block": {"type": "thinking", "thinking": "",
                                                     "signature": ""}}),
          ("content_block_delta", {"index": idx,
                                   "delta": {"type": "thinking_delta", "thinking": think}})]
    if signature:
        ev.append(("content_block_delta", {"index": idx,
                                           "delta": {"type": "signature_delta",
                                                     "signature": signature}}))
    ev.append(("content_block_stop", {"index": idx}))
    return ev


def _tool_block(idx: int, tid: str, name: str, args_json: str) -> list[tuple]:
    half = len(args_json) // 2
    return [("content_block_start", {"index": idx,
                                     "content_block": {"type": "tool_use", "id": tid,
                                                       "name": name, "input": {}}}),
            ("content_block_delta", {"index": idx,
                                     "delta": {"type": "input_json_delta",
                                               "partial_json": args_json[:half]}}),
            ("content_block_delta", {"index": idx,
                                     "delta": {"type": "input_json_delta",
                                               "partial_json": args_json[half:]}}),
            ("content_block_stop", {"index": idx})]


def _stop(stop_reason: str, usage=None) -> list[tuple]:
    return [("message_delta", {"type": "message_delta",
                               "delta": {"stop_reason": stop_reason},
                               "usage": usage if usage is not None else _usage()}),
            ("message_stop", {"type": "message_stop"})]


class _Transport:
    """按剧本回应的假传输层：每一项要么是异常（抛），要么是 SSE 事件表。"""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []
        self._orig = None

    def __enter__(self):
        self._orig = llm_provider._anthropic_open

        def fake(url, headers, body, timeout):
            self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
            item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
            if isinstance(item, Exception):
                raise item
            return _FakeResp(lines=_sse(*item))

        llm_provider._anthropic_open = fake
        return self

    def __exit__(self, *exc):
        llm_provider._anthropic_open = self._orig
        return False


# ---------------------------------------------------------------------------
# 请求体翻译
# ---------------------------------------------------------------------------
class TestTranslation(unittest.TestCase):
    def test_system_top_level_and_tools_to_input_schema(self):
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "你好"}]
        tools = [{"type": "function",
                  "function": {"name": "query", "description": "查面板",
                               "parameters": {"type": "object",
                                              "properties": {"t": {"type": "string"}}}}}]
        body = llm_provider._anthropic_body(msgs, tools, _cfg(), stream=True)
        self.assertEqual(body["system"], [{"type": "text", "text": "SYS"}])
        self.assertEqual(body["tools"][0]["name"], "query")
        self.assertEqual(body["tools"][0]["input_schema"]["type"], "object")
        self.assertNotIn("function", body["tools"][0], "OpenAI 那层壳要拆掉")
        self.assertEqual(body["tool_choice"], {"type": "auto"})
        self.assertTrue(body["stream"])
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["temperature"], 0.3)
        self.assertEqual(body["messages"],
                         [{"role": "user", "content": [{"type": "text", "text": "你好"}]}])

    def test_consecutive_tool_results_merge_into_one_user(self):
        """★ 工具结果必须合成**一条** user 里的多个 tool_result（每个调用一条消息是错的）。"""
        msgs = [{"role": "user", "content": "状态"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "a", "type": "function",
                     "function": {"name": "query", "arguments": '{"t": "res"}'}},
                    {"id": "b", "type": "function",
                     "function": {"name": "query", "arguments": '{"t": "army"}'}}]},
                {"role": "tool", "tool_call_id": "a", "content": "结果A"},
                {"role": "tool", "tool_call_id": "b", "content": "结果B"},
                {"role": "user", "content": "以上为最新状态"}]
        out = llm_provider._anthropic_messages(msgs, "m", _cfg())
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user", "user"])
        self.assertEqual([b["type"] for b in out[1]["content"]], ["tool_use", "tool_use"])
        self.assertEqual(out[1]["content"][0]["input"], {"t": "res"})
        self.assertEqual(out[1]["content"][0]["id"], "a")
        self.assertEqual([b["type"] for b in out[2]["content"]],
                         ["tool_result", "tool_result"])
        self.assertEqual(out[2]["content"][1]["tool_use_id"], "b")

    def test_orphan_tool_result_becomes_text(self):
        """没有 tool_call_id 的结果当普通文本发——空 tool_use_id 必被拒，别造 400。"""
        out = llm_provider._anthropic_messages(
            [{"role": "tool", "content": "没 id 的结果"}], "m", _cfg())
        self.assertEqual(out[0]["content"], [{"type": "text", "text": "没 id 的结果"}])

    def test_thinking_replayed_only_with_matching_signature(self):
        base = {"role": "assistant", "content": "正文", "reasoning_content": "想过"}
        with_sig = {**base, "reasoning_signature": {"model": "m", "signature": "S"}}
        self.assertEqual([b["type"] for b in
                          llm_provider._anthropic_assistant_blocks(with_sig, "m")],
                         ["thinking", "text"])
        self.assertEqual(llm_provider._anthropic_assistant_blocks(with_sig, "m")[0]["signature"],
                         "S")
        # 换了模型 ⇒ 签名不可移植 ⇒ 整块不回放（假签名去撞 400 ＝ 终止整局，代价不对称）
        self.assertEqual([b["type"] for b in
                          llm_provider._anthropic_assistant_blocks(with_sig, "别的模型")],
                         ["text"])
        # 没有签名（旧存档 / ctx 剥过思考）⇒ 同样只回放正文
        self.assertEqual([b["type"] for b in
                          llm_provider._anthropic_assistant_blocks(base, "m")], ["text"])

    def test_empty_assistant_message_is_dropped(self):
        """正文空、没调工具、thinking 又缺签名 ⇒ 一个 block 都拼不出来：整条跳过（空 content 会被拒）。"""
        out = llm_provider._anthropic_messages(
            [{"role": "user", "content": "u"},
             {"role": "assistant", "content": None, "reasoning_content": "只想了没签名"}],
            "m", _cfg())
        self.assertEqual([m["role"] for m in out], ["user"])

    def test_truncated_tool_arguments_fall_back_to_empty_object(self):
        blocks = llm_provider._anthropic_assistant_blocks(
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "a", "type": "function",
                             "function": {"name": "query", "arguments": '{"t": "re'}}]}, "m")
        self.assertEqual(blocks[0]["input"], {}, "截断的 JSON 只能按空参发（引擎自己会拒）")

    def test_cache_control_off_by_default_and_temperature_clamped(self):
        body = llm_provider._anthropic_body([{"role": "system", "content": "S"}], None,
                                            _cfg(), stream=False)
        self.assertNotIn("cache_control", body["system"][0])
        self.assertNotIn("tools", body)
        self.assertNotIn("stream", body)
        body = llm_provider._anthropic_body([{"role": "system", "content": "S"}], None,
                                            _cfg(anthropic_cache_control=True,
                                                 temperature=1.8), stream=False)
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["temperature"], 1.0, "Anthropic 只到 1：夹一下比崩一局便宜")

    def test_thinking_switch_and_budget(self):
        self.assertIsNone(llm_provider._anthropic_thinking(_cfg(thinking="disabled")))
        body = llm_provider._anthropic_body([{"role": "user", "content": "u"}], None,
                                           _cfg(anthropic_thinking_budget=8192), stream=False)
        self.assertEqual(body["thinking"], {"type": "enabled", "budget_tokens": 8192})
        body = llm_provider._anthropic_body([{"role": "user", "content": "u"}], None,
                                           _cfg(), stream=False)
        self.assertEqual(body["thinking"], {"type": "enabled"},
                         "缺省**不发** budget_tokens：发了就是给长思考设上限")


class TestUrlAndHeaders(unittest.TestCase):
    def test_messages_url_never_doubles_v1(self):
        self.assertEqual(llm_provider.anthropic_messages_url("https://api.deepseek.com/anthropic"),
                         "https://api.deepseek.com/anthropic/v1/messages")
        self.assertEqual(llm_provider.anthropic_messages_url("https://x/apps/anthropic/"),
                         "https://x/apps/anthropic/v1/messages")
        self.assertEqual(llm_provider.anthropic_messages_url("https://x/v1"),
                         "https://x/v1/messages")

    def test_auth_modes(self):
        h = llm_provider._anthropic_headers(_cfg())
        self.assertEqual(h["authorization"], "Bearer sk-test")
        self.assertNotIn("x-api-key", h)
        h = llm_provider._anthropic_headers(_cfg(anthropic_auth="x-api-key"))
        self.assertEqual(h["x-api-key"], "sk-test")
        self.assertNotIn("authorization", h)
        h = llm_provider._anthropic_headers(_cfg(anthropic_auth="both"))
        self.assertTrue(h["authorization"] and h["x-api-key"])
        self.assertEqual(h["anthropic-version"], "2023-06-01")

    def test_beta_header_only_when_configured(self):
        self.assertNotIn("anthropic-beta", llm_provider._anthropic_headers(_cfg()))
        self.assertEqual(
            llm_provider._anthropic_headers(_cfg(anthropic_beta=["a", "b"]))["anthropic-beta"],
            "a,b")
        self.assertEqual(
            llm_provider._anthropic_headers(_cfg(anthropic_beta="c"))["anthropic-beta"], "c")

    def test_missing_key_raises(self):
        with self.assertRaises(ValueError):
            llm_provider._anthropic_headers(_cfg(api_key=""))


# ---------------------------------------------------------------------------
# 流式聚合 + 用量映射
# ---------------------------------------------------------------------------
class TestStreaming(unittest.TestCase):
    def test_text_thinking_signature_and_usage(self):
        events = [_msg_start(),
                  *_thinking_block(0, "先想一想", "SIG-1"),
                  *_text_block(1, "我决定了"),
                  *_stop("end_turn")]
        with _Transport(events) as t:
            msg, st = llm_provider.AnthropicCompat(_cfg())._stream_once(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertEqual(msg["content"], "我决定了")
        self.assertEqual(msg["reasoning_content"], "先想一想")
        self.assertEqual(msg["reasoning_signature"],
                         {"model": "deepseek-flash[1m]", "signature": "SIG-1"})
        self.assertEqual((st["hit"], st["miss"]), (900, 100))
        self.assertEqual(st["out_tokens"], 7)
        self.assertTrue(st["usage_reported"])
        self.assertFalse(st.get("estimated"))
        self.assertTrue(st.get("reason_estimated"), "该路不单独报思考 token ⇒ 显示要打 ≈")
        self.assertEqual(st["stop_reason"], "end_turn")
        self.assertEqual(t.calls[0]["url"], "https://api.deepseek.com/anthropic/v1/messages")
        self.assertEqual(t.calls[0]["headers"]["authorization"], "Bearer sk-test")

    def test_tool_use_arguments_rebuilt_from_partial_json(self):
        events = [_msg_start(),
                  *_thinking_block(0, "要查面板", "SIG-2"),
                  *_tool_block(1, "call_1", "query", '{"topic": "res", "n": 2}'),
                  *_stop("tool_use")]
        with _Transport(events):
            msg, st = llm_provider.AnthropicCompat(_cfg())._stream_once(
                [{"role": "user", "content": "u"}],
                [{"type": "function", "function": {"name": "query", "parameters": {}}}], _cfg())
        tc = msg["tool_calls"][0]
        self.assertEqual(tc["id"], "call_1")
        self.assertEqual(tc["function"]["name"], "query")
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"topic": "res", "n": 2})
        self.assertEqual(st["stop_reason"], "tool_use")

    def test_message_delta_usage_wins_over_message_start(self):
        """累计用量在 message_delta 上（实测）；message_start 那份只算首段。"""
        events = [_msg_start(usage={"input_tokens": 5, "output_tokens": 1}),
                  *_text_block(0, "好"),
                  *_stop("end_turn", usage={"input_tokens": 211,
                                            "cache_read_input_tokens": 4992,
                                            "cache_creation_input_tokens": 0,
                                            "output_tokens": 15})]
        with _Transport(events):
            _msg, st = llm_provider.AnthropicCompat(_cfg())._stream_once(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertEqual((st["hit"], st["miss"]), (4992, 211))
        self.assertEqual(st["out_tokens"], 15)

    def test_zeroed_usage_is_estimated_not_zero(self):
        """全 0 的 usage = "没报"（与 OpenAI 那路同一条纪律），本地估算并打 ≈。"""
        zero = {"input_tokens": 0, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0, "output_tokens": 0}
        events = [_msg_start(usage=zero), *_text_block(0, "正文" * 20), *_stop("end_turn", zero)]
        with _Transport(events):
            msg, st = llm_provider.AnthropicCompat(_cfg())._stream_once(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertFalse(st["usage_reported"])
        self.assertTrue(st["estimated"])
        self.assertGreater(st["out_tokens"], 0, "绝不把'没报'当 0 显示")
        self.assertEqual(msg["content"], "正文" * 20)

    def test_error_event_is_retried_not_fatal(self):
        events = [("error", {"type": "error",
                             "error": {"type": "overloaded_error", "message": "忙"}})]
        err = None
        try:
            with _Transport(events):
                llm_provider.AnthropicCompat(_cfg())._stream_once(
                    [{"role": "user", "content": "u"}], None, _cfg())
        except llm_provider.AnthropicError as e:
            err = e
        self.assertIsNotNone(err)
        self.assertIsNone(err.status_code, "没有状态码 ⇒ is_fatal 判为可重试")
        self.assertFalse(llm_provider.is_fatal(err))


# ---------------------------------------------------------------------------
# 重试窗口 / 续写
# ---------------------------------------------------------------------------
class TestRetryAndTail(unittest.TestCase):
    def test_429_is_retried_and_tag_shows_status(self):
        """★ 配额耗尽（429）也要重试：窗口正是留给"当场续费"的（用户 2026-09-20）。"""
        boom = llm_provider.AnthropicError("HTTP 429 配额用尽", status_code=429)
        ok = [_msg_start(), *_text_block(0, "好"), *_stop("end_turn")]
        tags: list[str] = []
        with _Transport(boom, ok) as t:
            msg, _st = llm_provider.AnthropicCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}], None,
                _cfg(api_retry_wait=0), on_retry=lambda a, tag, w, n: tags.append(tag))
        self.assertEqual(msg["content"], "好")
        self.assertEqual(len(t.calls), 2)
        self.assertIn("429", tags[0])

    def test_400_is_fatal_and_not_retried(self):
        boom = llm_provider.AnthropicError("HTTP 400 参数错", status_code=400)
        with _Transport(boom) as t:
            with self.assertRaises(llm_provider.AnthropicError):
                llm_provider.AnthropicCompat(_cfg()).chat_turn(
                    [{"role": "user", "content": "u"}], None, _cfg(api_retry_wait=0))
        self.assertEqual(len(t.calls), 1, "4xx 重试没意义：一次就抛")

    def test_5xx_and_timeout_are_retried_with_clear_tag(self):
        boom = llm_provider.AnthropicError("HTTP 503 网关", status_code=503)
        tags: list[str] = []
        with _Transport(boom, boom) as t:
            with self.assertRaises(llm_provider.AnthropicError):
                llm_provider.AnthropicCompat(_cfg()).chat_turn(
                    [{"role": "user", "content": "u"}], None,
                    _cfg(api_retries=2, api_retry_wait=0),
                    on_retry=lambda a, tag, w, n: tags.append(tag))
        self.assertEqual(len(t.calls), 2)
        self.assertIn("503", tags[0])
        tags.clear()
        with _Transport(TimeoutError("首字等待超过 180s（一直没等到新的输出块）"),
                        TimeoutError("首字等待超过 180s")) as t:
            with self.assertRaises(TimeoutError):
                llm_provider.AnthropicCompat(_cfg()).chat_turn(
                    [{"role": "user", "content": "u"}], None,
                    _cfg(api_retries=2, api_retry_wait=0),
                    on_retry=lambda a, tag, w, n: tags.append(tag))
        self.assertIn("首字", tags[0], "状态区该看出卡在哪一段（180s 与 60s 不是一回事）")

    def test_max_tokens_continues_when_no_tool_call(self):
        first = [_msg_start(), *_text_block(0, "前半"), *_stop("max_tokens")]
        second = [_msg_start(), *_text_block(0, "后半"), *_stop("end_turn")]
        with _Transport(first, second) as t:
            msg, st = llm_provider.AnthropicCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertEqual(msg["content"], "前半后半")
        self.assertEqual(len(t.calls), 2, "stop_reason=max_tokens ⇒ 续写一次")
        self.assertEqual(t.calls[1]["body"]["messages"][-1]["role"], "user")
        self.assertIn("接着断点继续", t.calls[1]["body"]["messages"][-1]["content"][0]["text"])
        self.assertEqual(st["stop_reason"], "end_turn")
        self.assertEqual(st["out_tokens"], 14, "两次调用的用量都算进来")

    def test_no_tail_when_tool_calls_present(self):
        """吐了 tool_use 就不续写：参数 JSON 可能被截在半路，交给循环那条路更稳。"""
        ev = [_msg_start(), *_tool_block(0, "call_1", "query", '{"t": "res"}'), *_stop("max_tokens")]
        with _Transport(ev) as t:
            msg, _st = llm_provider.AnthropicCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertEqual(len(t.calls), 1)
        self.assertTrue(msg["tool_calls"])

    def test_tail_failure_is_swallowed(self):
        """★ 续写失败绝不冒泡：宁可少半段正文，也不能因为一次补救终止整局。"""
        first = [_msg_start(), *_text_block(0, "前半"), *_stop("max_tokens")]
        boom = llm_provider.AnthropicError("HTTP 400 续写被拒", status_code=400)
        with _Transport(first, boom):
            msg, _st = llm_provider.AnthropicCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertEqual(msg["content"], "前半")


# ---------------------------------------------------------------------------
# 非流式（记忆压缩）
# ---------------------------------------------------------------------------
class TestCompleteText(unittest.TestCase):
    def _run(self, payload, tools=None, **cfgkw):
        captured: dict = {}
        orig = llm_provider._anthropic_open

        def fake(url, headers, body, timeout):
            captured.update(body)
            return _FakeResp(payload=json.dumps(payload).encode("utf-8"))

        llm_provider._anthropic_open = fake
        try:
            b = llm_provider.AnthropicCompat(_cfg(**cfgkw))
            text, stats = b.complete_text([{"role": "system", "content": "S"},
                                           {"role": "user", "content": "U"}],
                                          _cfg(**cfgkw), tools=tools)
        finally:
            llm_provider._anthropic_open = orig
        return text, stats, captured

    def test_reads_text_blocks_and_disables_thinking(self):
        payload = {"content": [{"type": "thinking", "thinking": "内部思考"},
                               {"type": "text", "text": "  长期记忆摘要  "}],
                   "usage": _usage()}
        text, stats, captured = self._run(payload)
        self.assertEqual(text, "长期记忆摘要")
        # ★ 必须**显式**发 disabled：2026-10-01 真端点冒烟实测，不声明时这条网关默认开思考，
        #   1500 的压缩预算会被思考吃光、正文返回空串（压缩白跑一次）。
        self.assertEqual(captured["thinking"], {"type": "disabled"})
        self.assertNotIn("tools", captured, "不带 tools 的老口径不变")
        self.assertEqual(captured["max_tokens"], 1500, "缺省跟 ctx_compact_tokens 走")
        # usage → 与 chat_turn 同键的 stats（命中/未命中），压缩调用从此看得见命中率
        self.assertEqual(stats["hit"], 900)
        self.assertEqual(stats["miss"], 100)
        self.assertEqual(stats["out_tokens"], 7)
        self.assertTrue(stats["usage_reported"])

    def test_tools_are_sent_for_prefix_reuse_and_tool_choice_stays_auto(self):
        """★ 这条协议的前缀顺序是 tools → system → messages：压缩调用要复用主请求的
        缓存前缀，工具声明与 `tool_choice` 就必须**逐字照发**。
        2026-10-02 真端点实测：改成 `{"type":"none"}` 会让网关丢掉 tools 段、前缀从
        system 之后断掉（冷缓存那轮命中 40% vs 照发 auto 的 93.6%）。"""
        tools = [{"type": "function", "function": {"name": "query", "description": "d",
                                                   "parameters": {"type": "object"}}}]
        payload = {"content": [{"type": "text", "text": "记忆"}], "usage": _usage()}
        text, _stats, captured = self._run(payload, tools=tools)
        self.assertEqual(text, "记忆")
        self.assertEqual(captured["tools"][0]["name"], "query")
        self.assertEqual(captured["tool_choice"], {"type": "auto"})

    def test_no_usage_reports_estimated_not_zero(self):
        """提供方没报用量时打 estimated，别把没报装成 0（否则命中率看着像整段失效）。"""
        payload = {"content": [{"type": "text", "text": "摘要正文"}], "usage": {}}
        _text, stats, _captured = self._run(payload)
        self.assertFalse(stats["usage_reported"])
        self.assertTrue(stats["estimated"])
        self.assertEqual(stats["hit"], 0)


class TestPrivateKeyHygiene(unittest.TestCase):
    """★ `reasoning_signature` 是 Anthropic 那一路的东西，**不许漏给 OpenAI 兼容端点**
    （严格端点收到不认识的 message 字段会 400），也不许把调用方的 dict 改坏
    （ctx.py 的逐字节前缀契约靠它）。"""

    def test_public_messages_strips_and_copies(self):
        msgs = [{"role": "assistant", "content": "c",
                 "reasoning_signature": {"model": "m", "signature": "s"}},
                {"role": "user", "content": "u"}]
        out = llm_provider._public_messages(msgs)
        self.assertNotIn("reasoning_signature", out[0])
        self.assertIn("reasoning_signature", msgs[0], "原对象不许被改")
        self.assertIs(out[1], msgs[1], "没私有键的消息零拷贝")

    def test_public_messages_is_identity_when_nothing_to_strip(self):
        msgs = [{"role": "user", "content": "u"}]
        self.assertIs(llm_provider._public_messages(msgs), msgs)


if __name__ == "__main__":
    unittest.main()
