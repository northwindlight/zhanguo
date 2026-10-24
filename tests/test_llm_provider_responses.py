# -*- coding: utf-8 -*-
"""Responses 提供方（`provider="responses"`，2026-10-24 接线）的翻译层与失败语义。

★ 这一路**只用标准库**（urllib + 手写 SSE），测试**不需要任何 SDK 包**——把
  `llm_provider._post_json` 换成假传输层就能把整条路跑完（与另两套提供方测试同一个
  打法）：翻译 → 发请求 → 事件流聚合 → 用量/重试/续写。

口径来源（OpenAI Responses API 的公开协议形态）：

- 请求：system → 顶层 `instructions`；user → `input_text`/`input_image`；assistant →
  `output_text` + `function_call` 项；工具结果 → `function_call_output` 项；tools 拆平。
- 事件流：`event: response.xxx` 形态——output_item.added/done、output_text.delta/done、
  reasoning_summary_text.delta、function_call_arguments.delta/done、
  response.completed / incomplete / failed、error。
- 用量：`input_tokens` / `input_tokens_details.cached_tokens`（=hit）/
  `output_tokens` / `output_tokens_details.reasoning_tokens`（思考 token **真报**）。
- 截断：`status="incomplete"` + `incomplete_details.reason="max_output_tokens"`
  → stop_reason="max_tokens" ⇒ 与 Anthropic 路共用的 message tailing 续写。

跑法：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients import provider as llm_provider  # noqa: E402


def _cfg(**kw) -> dict:
    cfg = {"provider": "responses", "base_url": "https://api.openai.com/v1",
           "api_key": "sk-test", "model": "gpt-test", "max_tokens": 4096,
           "temperature": 0.3}
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


def _rsse(*events) -> list[str]:
    """把 (事件名, payload) 编成 SSE 行（Responses 事件流带 event: 行）。"""
    out: list[str] = []
    for name, payload in events:
        out.append(f"event: {name}")
        out.append("data: " + json.dumps(payload, ensure_ascii=False))
        out.append("")
    return out


def _usage(input_tokens=1000, cached=900, output=7, reasoning=3) -> dict:
    """Responses 的 usage：`input_tokens` 是**总量**（命中 + 未命中都在里面，
    与 Anthropic 的 input_tokens 只算未命中不同）——miss = input − cached。"""
    return {"input_tokens": input_tokens, "output_tokens": output,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens_details": {"reasoning_tokens": reasoning}}


def _completed(usage=None, output=None) -> tuple:
    return ("response.completed", {"type": "response.completed",
                                   "response": {"status": "completed",
                                                "usage": usage if usage is not None else _usage(),
                                                "output": output or []}})


def _incomplete(usage=None) -> tuple:
    return ("response.incomplete",
            {"type": "response.incomplete",
             "response": {"status": "incomplete",
                          "incomplete_details": {"reason": "max_output_tokens"},
                          "usage": usage if usage is not None else _usage()}})


def _msg_item(idx: str, text: str = "") -> tuple:
    return ("response.output_item.added",
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "message", "id": idx, "role": "assistant",
                      "content": [{"type": "output_text", "text": ""}]}})


def _text_delta(idx: str, text: str) -> tuple:
    return ("response.output_text.delta",
            {"type": "response.output_text.delta", "item_id": idx,
             "output_index": 0, "content_index": 0, "delta": text})


def _reasoning_item(idx: str) -> tuple:
    return ("response.output_item.added",
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "reasoning", "id": idx, "summary": []}})


def _summary_delta(idx: str, text: str) -> tuple:
    return ("response.reasoning_summary_text.delta",
            {"type": "response.reasoning_summary_text.delta", "item_id": idx,
             "summary_index": 0, "delta": text})


def _fc_item(idx: str, call_id: str, name: str) -> tuple:
    return ("response.output_item.added",
            {"type": "response.output_item.added", "output_index": 1,
             "item": {"type": "function_call", "id": idx, "call_id": call_id,
                      "name": name, "arguments": ""}})


def _fc_args_delta(idx: str, piece: str) -> tuple:
    return ("response.function_call_arguments.delta",
            {"type": "response.function_call_arguments.delta", "item_id": idx, "delta": piece})


class _Transport:
    """按剧本回应的假传输层：每一项要么是异常（抛），要么是 SSE 事件表。"""

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


# ---------------------------------------------------------------------------
# 请求体翻译
# ---------------------------------------------------------------------------
class TestTranslation(unittest.TestCase):
    def test_instructions_tools_flat_and_defaults(self):
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "你好"}]
        tools = [{"type": "function",
                  "function": {"name": "query", "description": "查面板",
                               "parameters": {"type": "object",
                                              "properties": {"t": {"type": "string"}}}}}]
        body = llm_provider._responses_body(msgs, tools, _cfg(), stream=True)
        self.assertEqual(body["instructions"], "SYS")
        self.assertEqual(body["tools"][0]["name"], "query")
        self.assertEqual(body["tools"][0]["parameters"]["type"], "object")
        self.assertNotIn("function", body["tools"][0], "chat 那层 function 壳要拆掉")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertTrue(body["stream"])
        self.assertFalse(body["store"], "不用 previous_response_id ⇒ 不在服务端留状态")
        self.assertEqual(body["max_output_tokens"], 4096)
        self.assertEqual(body["reasoning"], {"effort": "medium", "summary": "auto"},
                         "thinking 缺省 enabled：effort 缺省 medium，summary 必须开")
        self.assertEqual(body["input"],
                         [{"role": "user", "content": [{"type": "input_text", "text": "你好"}]}])

    def test_reasoning_effort_passthrough_and_disabled(self):
        body = llm_provider._responses_body([{"role": "user", "content": "u"}], None,
                                            _cfg(reasoning_effort="low"), stream=True)
        self.assertEqual(body["reasoning"], {"effort": "low", "summary": "auto"})
        body = llm_provider._responses_body([{"role": "user", "content": "u"}], None,
                                            _cfg(thinking="disabled"), stream=False)
        self.assertEqual(body["reasoning"], {"effort": "none"},
                         "disabled 要显式发 effort=none（「不发 ≠ 关掉」那条教训）")
        self.assertNotIn("stream", body)

    def test_messages_translation_roundtrip_shapes(self):
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "状态"},
                {"role": "assistant", "content": "我决定", "reasoning_content": "想过",
                 "tool_calls": [
                     {"id": "a", "type": "function",
                      "function": {"name": "query", "arguments": '{"t": "res"}'}},
                     {"id": "b", "type": "function",
                      "function": {"name": "query", "arguments": '{"t": "army"}'}}]},
                {"role": "tool", "tool_call_id": "a", "content": "结果A"},
                {"role": "tool", "tool_call_id": "b", "content": "结果B"},
                {"role": "user", "content": "以上为最新状态"}]
        out = llm_provider._responses_messages(msgs, _cfg())
        self.assertEqual([item.get("role") or item.get("type") for item in out],
                         ["user", "assistant", "function_call", "function_call",
                          "function_call_output", "function_call_output", "user"])
        self.assertEqual(out[1]["content"], [{"type": "output_text", "text": "我决定"}])
        self.assertEqual(out[2], {"type": "function_call", "call_id": "a",
                                  "name": "query", "arguments": '{"t": "res"}'})
        self.assertEqual(out[4], {"type": "function_call_output",
                                  "call_id": "a", "output": "结果A"})
        # reasoning_content 不回放（思考项要连 encrypted_content 才可跨请求回放）
        self.assertNotIn(json.dumps("想过"), json.dumps(out, ensure_ascii=False))

    def test_image_blocks_become_input_image(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,QUJD"}}]}]
        out = llm_provider._responses_messages(msgs, _cfg())
        self.assertEqual(out[0]["content"], [
            {"type": "input_text", "text": "看图"},
            {"type": "input_image", "image_url": "data:image/png;base64,QUJD"}])

    def test_empty_assistant_produces_nothing(self):
        """正文空、没调工具（或 thinking 被剥）⇒ 该条 assistant 不产生任何 input 项。"""
        out = llm_provider._responses_messages(
            [{"role": "user", "content": "u"},
             {"role": "assistant", "content": None, "reasoning_content": "只想了"}],
            _cfg())
        self.assertEqual([item.get("role") or item.get("type") for item in out], ["user"])

    def test_url_join_matches_sdk(self):
        self.assertEqual(llm_provider.responses_url("https://api.openai.com/v1"),
                         "https://api.openai.com/v1/responses")
        self.assertEqual(llm_provider.responses_url("https://x/"),
                         "https://x/responses", "base 不擅自补 /v1")


class TestStreaming(unittest.TestCase):
    def test_text_reasoning_toolcall_and_usage(self):
        events = [_reasoning_item("rs_1"),
                  _summary_delta("rs_1", "先想一想"),
                  _msg_item("msg_1"),
                  _text_delta("msg_1", "我决定了"),
                  _fc_item("fc_1", "call_1", "query"),
                  _fc_args_delta("fc_1", '{"topic": "res"'),
                  _fc_args_delta("fc_1", ', "n": 2}'),
                  _completed()]
        with _Transport(_rsse(*events)) as t:
            msg, st = llm_provider.ResponsesCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}],
                [{"type": "function", "function": {"name": "query", "parameters": {}}}],
                _cfg(api_retry_wait=0))
        self.assertEqual(msg["content"], "我决定了")
        self.assertEqual(msg["reasoning_content"], "先想一想")
        tc = msg["tool_calls"][0]
        self.assertEqual(tc["id"], "call_1")
        self.assertEqual(tc["function"]["name"], "query")
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"topic": "res", "n": 2})
        self.assertEqual((st["hit"], st["miss"]), (900, 100))
        self.assertEqual(st["out_tokens"], 7)
        self.assertEqual(st["reason_tokens"], 3, "该路真报思考 token，不用估算")
        self.assertFalse(st.get("estimated"))
        self.assertFalse(st.get("reason_estimated"), "Responses 路思考 token 是真数，不打 ≈")
        self.assertEqual(st["stop_reason"], "stop")
        self.assertEqual(t.calls[0]["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(t.calls[0]["headers"]["authorization"], "Bearer sk-test")

    def test_item_done_fills_function_call(self):
        """只发 done 不发 delta 的网关：整条 function_call 在 done 里给齐。"""
        events = [("response.output_item.done",
                   {"type": "response.output_item.done", "output_index": 0,
                    "item": {"type": "function_call", "id": "fc_1", "call_id": "call_9",
                             "name": "query", "arguments": '{"t": "res"}'}}),
                  _completed()]
        with _Transport(_rsse(*events)):
            msg, _st = llm_provider.ResponsesCompat(_cfg())._stream_once(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertEqual(msg["tool_calls"][0]["id"], "call_9")
        self.assertEqual(json.loads(msg["tool_calls"][0]["function"]["arguments"]),
                         {"t": "res"})

    def test_incomplete_max_output_tokens_tails(self):
        """★ incomplete/max_output_tokens → stop_reason=max_tokens ⇒ message tailing 续写
        （与 Anthropic 路同一套 `_tailing_continue`）：只续正文，两次用量都算。"""
        first = [_msg_item("msg_1"), _text_delta("msg_1", "前半"), _incomplete()]
        second = [_msg_item("msg_1"), _text_delta("msg_1", "后半"), _completed()]
        with _Transport(_rsse(*first), _rsse(*second)) as t:
            msg, st = llm_provider.ResponsesCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}], None, _cfg(api_retry_wait=0))
        self.assertEqual(msg["content"], "前半后半")
        self.assertEqual(len(t.calls), 2, "截断 ⇒ 续写一次")
        second_input = t.calls[1]["body"]["input"]
        self.assertEqual(second_input[-1]["role"], "user")
        self.assertIn("接着断点继续", second_input[-1]["content"][0]["text"])
        self.assertEqual(second_input[-2]["content"],
                         [{"type": "output_text", "text": "前半"}],
                         "已吐的半段当 assistant 消息发回去")
        self.assertEqual(st["stop_reason"], "stop")
        self.assertEqual(st["out_tokens"], 14, "两次调用的用量都算进来")

    def test_no_tail_when_tool_calls_present(self):
        ev = [_fc_item("fc_1", "call_1", "query"),
              _fc_args_delta("fc_1", '{"t": "res"'),
              _incomplete()]
        with _Transport(_rsse(*ev)) as t:
            msg, _st = llm_provider.ResponsesCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}], None, _cfg(api_retry_wait=0))
        self.assertEqual(len(t.calls), 1, "吐了 function_call 就不续写（参数 JSON 可能被截断）")
        self.assertTrue(msg["tool_calls"])

    def test_zeroed_usage_is_estimated_not_zero(self):
        zero = _usage(input_tokens=0, cached=0, output=0, reasoning=0)
        events = [_msg_item("msg_1"), _text_delta("msg_1", "正文" * 20),
                  _completed(usage=zero)]
        with _Transport(_rsse(*events)):
            msg, st = llm_provider.ResponsesCompat(_cfg())._stream_once(
                [{"role": "user", "content": "u"}], None, _cfg())
        self.assertFalse(st["usage_reported"])
        self.assertTrue(st["estimated"])
        self.assertGreater(st["out_tokens"], 0, "绝不把'没报'当 0 显示")
        self.assertEqual(msg["content"], "正文" * 20)

    def test_error_event_is_retried_not_fatal(self):
        events = [("error", {"type": "error", "code": "server_error",
                             "message": "忙"})]
        err = None
        try:
            with _Transport(_rsse(*events)):
                llm_provider.ResponsesCompat(_cfg())._stream_once(
                    [{"role": "user", "content": "u"}], None, _cfg())
        except llm_provider.EndpointError as e:
            err = e
        self.assertIsNotNone(err)
        self.assertIsNone(err.status_code, "没有状态码 ⇒ is_fatal 判为可重试")
        self.assertFalse(llm_provider.is_fatal(err))

    def test_failed_event_is_retried(self):
        events = [("response.failed",
                   {"type": "response.failed",
                    "response": {"status": "failed",
                                 "error": {"code": "x", "message": "上游炸了"}}})]
        err = None
        try:
            with _Transport(_rsse(*events)):
                llm_provider.ResponsesCompat(_cfg())._stream_once(
                    [{"role": "user", "content": "u"}], None, _cfg())
        except llm_provider.EndpointError as e:
            err = e
        self.assertIsNotNone(err)
        self.assertFalse(llm_provider.is_fatal(err), "response.failed 没状态码 ⇒ 可重试")


class TestRetry(unittest.TestCase):
    def test_429_is_retried_and_tag_shows_status(self):
        """★ 配额耗尽（429）也要重试：窗口正是留给"当场续费"的（用户 2026-09-20）。"""
        boom = llm_provider.EndpointError("HTTP 429 配额用尽", status_code=429)
        ok = _rsse(_msg_item("msg_1"), _text_delta("msg_1", "好"), _completed())
        tags: list[str] = []
        with _Transport(boom, ok) as t:
            msg, _st = llm_provider.ResponsesCompat(_cfg()).chat_turn(
                [{"role": "user", "content": "u"}], None,
                _cfg(api_retry_wait=0), on_retry=lambda a, tag, w, n: tags.append(tag))
        self.assertEqual(msg["content"], "好")
        self.assertEqual(len(t.calls), 2)
        self.assertIn("429", tags[0])

    def test_400_is_fatal_and_not_retried(self):
        boom = llm_provider.EndpointError("HTTP 400 参数错", status_code=400)
        with _Transport(boom) as t:
            with self.assertRaises(llm_provider.EndpointError):
                llm_provider.ResponsesCompat(_cfg()).chat_turn(
                    [{"role": "user", "content": "u"}], None, _cfg(api_retry_wait=0))
        self.assertEqual(len(t.calls), 1, "4xx 重试没意义：一次就抛")


# ---------------------------------------------------------------------------
# 非流式（记忆压缩）
# ---------------------------------------------------------------------------
class TestCompleteText(unittest.TestCase):
    def _run(self, response_payload, tools=None, **cfgkw):
        captured: dict = {}
        orig = llm_provider._post_json

        def fake(url, headers, body, timeout):
            captured.update(body)
            captured["_url"] = url
            return _FakeResp(payload=json.dumps({"response": response_payload}).encode("utf-8"))

        llm_provider._post_json = fake
        try:
            b = llm_provider.ResponsesCompat(_cfg(**cfgkw))
            text, stats = b.complete_text([{"role": "system", "content": "S"},
                                           {"role": "user", "content": "U"}],
                                          _cfg(**cfgkw), tools=tools)
        finally:
            llm_provider._post_json = orig
        return text, stats, captured

    def test_reads_output_text_and_disables_reasoning(self):
        payload = {"status": "completed",
                   "output": [
                       {"type": "reasoning", "id": "rs", "summary": [
                           {"type": "summary_text", "text": "内部思考"}]},
                       {"type": "message", "content": [
                           {"type": "output_text", "text": "  长期记忆摘要  "}]}],
                   "usage": _usage()}
        text, stats, captured = self._run(payload)
        self.assertEqual(text, "长期记忆摘要")
        # ★ 思考必须显式关（effort=none、不发 summary）——与 Anthropic 路同一条教训
        self.assertEqual(captured["reasoning"], {"effort": "none"})
        self.assertEqual(captured["max_output_tokens"], 1500, "缺省跟 ctx_compact_tokens 走")
        self.assertNotIn("tools", captured, "不带 tools 的老口径不变")
        self.assertEqual(stats["hit"], 900)
        self.assertEqual(stats["miss"], 100)
        self.assertEqual(stats["out_tokens"], 7)
        self.assertTrue(stats["usage_reported"])
        self.assertEqual(stats["stop_reason"], "stop")

    def test_tools_are_sent_flat_for_prefix_reuse(self):
        """★ 压缩调用要复用主请求的缓存前缀 ⇒ 工具声明必须照发（拆平后的同一份）、
        tool_choice 保持 auto（别改 none——那条实测教训见 OpenAI 路的注）。"""
        tools = [{"type": "function", "function": {"name": "query", "description": "d",
                                                   "parameters": {"type": "object"}}}]
        payload = {"status": "completed",
                   "output": [{"type": "message",
                               "content": [{"type": "output_text", "text": "记忆"}]}],
                   "usage": _usage()}
        text, _stats, captured = self._run(payload, tools=tools)
        self.assertEqual(text, "记忆")
        self.assertEqual(captured["tools"][0]["name"], "query")
        self.assertNotIn("function", captured["tools"][0])
        self.assertEqual(captured["tool_choice"], "auto")

    def test_incomplete_maps_to_max_tokens(self):
        payload = {"status": "incomplete",
                   "incomplete_details": {"reason": "max_output_tokens"},
                   "output": [{"type": "message",
                               "content": [{"type": "output_text", "text": "截断摘要"}]}],
                   "usage": _usage()}
        _text, stats, _c = self._run(payload)
        self.assertEqual(stats["stop_reason"], "max_tokens",
                         "压缩循环认 length/max_tokens 两个词，续写也靠它触发")

    def test_no_usage_reports_estimated_not_zero(self):
        payload = {"status": "completed",
                   "output": [{"type": "message",
                               "content": [{"type": "output_text", "text": "摘要正文"}]}]}
        _text, stats, _c = self._run(payload)
        self.assertFalse(stats["usage_reported"])
        self.assertTrue(stats["estimated"])
        self.assertGreater(stats["out_tokens"], 0, "不能把'没报'当成 0")


class TestBackendSelection(unittest.TestCase):
    def test_make_backend_responses(self):
        b = llm_provider.make_backend(_cfg())
        self.assertIsInstance(b, llm_provider.ResponsesCompat)

    def test_unknown_provider_still_rejected(self):
        with self.assertRaises(ValueError):
            llm_provider.make_backend(_cfg(provider="claude"))


if __name__ == "__main__":
    unittest.main()
