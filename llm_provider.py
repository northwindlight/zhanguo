# -*- coding: utf-8 -*-
"""LLM 提供方兼容层：回合循环只认一种消息协议，提供方差异全部收口在这一个文件。

回合循环（mp_ai.run_openai_turn）需要的全部原语：

    backend.chat_turn(messages, tools, cfg, on_retry=None) -> (msg, stats)
        msg   : OpenAI 形态的 assistant 消息 dict
                {content, reasoning_content, tool_calls:[{id,type,function:{name,arguments}}]}
        stats : 用量统计 {"wall","stream","first","maxgap","out_tokens","reason_tokens",
                "hit","miss"}（缺项按 0 计）
        重试全部在提供方内部做完；耗尽后原样抛出（调用方记一条 API 错误继续逼）。

    backend.complete_text(messages, cfg, max_tokens=None) -> str
        无工具的普通非流式调用（记忆压缩用）。

消息在**存储与回放层一律保持 OpenAI 形态**：ctx.py 的 token 估算与缓存前缀布局、
turn_memory 存档、tests 的 mock 断言都以此为契约；换提供方时只改"调用瞬间"的
双向翻译，循环与记忆层无感。

hard_timeout：POSIX 用 SIGALRM 硬超时兜底（SDK 因网络黑洞不抛时强制抛）。
Windows 没有 SIGALRM——旧代码在函数入口裸用 `signal.SIGALRM`，本机一进 LLM 回合
就 AttributeError 炸掉整局（2026-09-12 修）；现在按 console.py 的 os.name 分支
惯例降级：Windows 只靠 SDK 自带 timeout + 提供方重试。
"""
from __future__ import annotations

import os
import signal
import time


def hard_timeout(seconds: float, label: str):
    """上下文管理器：POSIX 下 arm SIGALRM 硬超时；Windows/无 SIGALRM 平台空转。
    handler 装了必卸（恢复 prev），alarm 只在 with 块内生效——不会打断块外的
    工具执行/引擎结算。"""

    class _CM:
        def _raise(self, signum, frame):
            raise TimeoutError(f"{label}：API 调用超过 {seconds:.0f}s")

        def __enter__(self):
            self.prev = None
            if os.name == "nt" or not hasattr(signal, "SIGALRM"):
                return self
            self.prev = signal.signal(signal.SIGALRM, self._raise)
            signal.setitimer(signal.ITIMER_REAL, seconds)
            return self

        def __exit__(self, *exc):
            if self.prev is not None:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, self.prev)
            return False

    return _CM()


class OpenAICompat:
    """任意 OpenAI 兼容端点（DeepSeek / 火山方舟 / vLLM / ...）。"""

    def __init__(self, cfg: dict):
        from openai import OpenAI          # 运行时取属性：测试 patch openai.OpenAI 即生效
        self.client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"],
                             timeout=float(cfg.get("api_timeout", 180)))

    def chat_turn(self, messages, tools, cfg, on_retry=None):
        from openai import (APIStatusError, APIConnectionError,
                            APITimeoutError, RateLimitError)
        api_timeout = int(cfg.get("api_timeout", 180))
        retries = int(cfg.get("api_retries", 3))
        backoff = float(cfg.get("api_retry_wait", 2.0))
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return self._stream_once(messages, tools, cfg, api_timeout)
            except (APIConnectionError, APITimeoutError, RateLimitError, TimeoutError) as e:
                last = e                      # 网络/限流/硬超时：可重试
            except APIStatusError as e:       # 5xx 可重试；4xx（鉴权/参数）直接抛
                if 500 <= e.status_code < 600:
                    last = e
                else:
                    raise
            if attempt < retries and last is not None:
                if on_retry:
                    on_retry(attempt, type(last).__name__, backoff * attempt, retries)
                time.sleep(backoff * attempt)
        assert last is not None
        raise last

    def _stream_once(self, messages, tools, cfg, api_timeout):
        """一次流式调用 + 聚合：返回 (msg, stats)。"""
        extra = {}
        # deepseek-v4：thinking 开关 + reasoning_effort（low/medium/high）
        if "thinking" in cfg:
            extra["thinking"] = {"type": cfg["thinking"]}      # "enabled"/"disabled"
        if cfg.get("reasoning_effort"):
            extra["reasoning_effort"] = cfg["reasoning_effort"]
        stream_stats: dict = {}
        wall0 = time.time()
        with hard_timeout(api_timeout, "超时"):
            stream = self.client.chat.completions.create(
                model=cfg["model"], messages=messages,
                tools=tools, tool_choice="auto",
                temperature=cfg.get("temperature", 0.3),
                max_tokens=cfg.get("max_tokens", 4000),
                extra_body=extra or None,
                stream=True, stream_options={"include_usage": True})
            c_s, r_s, tool_acc = "", "", {}
            first_t = None
            last_t = time.time()
            for chunk in stream:
                now = time.time()
                if first_t is None and chunk.choices:
                    d0 = chunk.choices[0].delta
                    if (getattr(d0, "content", None) or getattr(d0, "reasoning_content", None)
                            or getattr(d0, "tool_calls", None)):
                        first_t = now
                stream_stats["maxgap"] = max(stream_stats.get("maxgap", 0.0), now - last_t)
                last_t = now
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    stream_stats["out_tokens"] = getattr(u, "completion_tokens", 0) or 0
                    det = getattr(u, "completion_tokens_details", None)
                    stream_stats["reason_tokens"] = getattr(det, "reasoning_tokens", 0) if det else 0
                    stream_stats["hit"] = getattr(u, "prompt_cache_hit_tokens", 0) or 0
                    stream_stats["miss"] = getattr(u, "prompt_cache_miss_tokens", 0) or 0
                if not chunk.choices:
                    continue
                d = chunk.choices[0].delta
                rd = getattr(d, "reasoning_content", None)
                if rd:
                    r_s += rd
                cd = getattr(d, "content", None)
                if cd:
                    c_s += cd
                for tc in (getattr(d, "tool_calls", None) or []):
                    slot = tool_acc.setdefault(tc.index, {"id": None, "type": "function",
                                                          "function": {"name": "", "arguments": ""}})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.type:
                        slot["type"] = tc.type
                    if tc.function:
                        if tc.function.name:
                            slot["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["function"]["arguments"] += tc.function.arguments
        stream_stats["wall"] = time.time() - wall0
        if first_t:
            stream_stats["first"] = first_t - wall0
            stream_stats["stream"] = max(0.0, last_t - first_t)
        msg = {"content": c_s, "reasoning_content": r_s}
        if tool_acc:
            msg["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
        return msg, stream_stats

    def complete_text(self, messages, cfg, max_tokens=None):
        resp = self.client.chat.completions.create(
            model=cfg["model"], messages=messages,
            max_tokens=int(max_tokens if max_tokens is not None
                           else cfg.get("ctx_compact_tokens", 1500)),
            temperature=0.3,
            extra_body={"thinking": {"type": "disabled"}})   # 压缩不需要思考
        return (resp.choices[0].message.content or "").strip()


class AnthropicCompat:
    """**预留接口**（用户 2026-09-12：未来兼容 anthropic）。实现待接；
    接入时回合循环与 ctx.py 一行不用动，只填这两个方法。映射配方：

    ① tools：OpenAI {function:{name,description,parameters}} → Anthropic
       {name,description,input_schema}；tool_choice="auto" → {"type":"auto"}；
    ② system 消息 → 顶层 system 参数（ctx.py 的前缀布局不变：breakpoint 打在
       system 末尾与归档末尾，正对"稳定前缀"设计）；
    ③ assistant.tool_calls → tool_use content block；role="tool" 消息 →
       user 消息里的 tool_result block（tool_use_id 一一对应，配对修复器同规则）；
    ④ reasoning_content ↔ thinking block（回传需带 signature，否则 400——
       与 DeepSeek 的 reasoning_content 必须回传是同类坑，回放层已原样存）；
    ⑤ 重试分类：429/5xx/overloaded_error 为 transient；stop_reason="max_tokens"
       续写用 message tailing；
    ⑥ 用量字段映射：usage.cache_read_input_tokens → hit，cache_creation/非缓存
       input → miss；思考 token 在 thinking block 内自计。
    """

    TODO = ("Anthropic 后端尚未实现：配置 provider='anthropic' 已识别，"
            "接线步骤见 llm_provider.AnthropicCompat 文档字符串。")

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def chat_turn(self, messages, tools, cfg, on_retry=None):
        raise NotImplementedError(self.TODO)

    def complete_text(self, messages, cfg, max_tokens=None):
        raise NotImplementedError(self.TODO)


def make_backend(cfg: dict):
    """按配置选提供方：provider 键显式指定；缺省时看 base_url（含 anthropic.com 即它），
    其余一律 OpenAI 兼容。"""
    prov = str(cfg.get("provider") or "").lower()
    if not prov:
        prov = "anthropic" if "anthropic" in str(cfg.get("base_url", "")).lower() else "openai"
    if prov in ("openai", "openai-compat", "deepseek", "ark"):
        return OpenAICompat(cfg)
    if prov in ("anthropic", "claude"):
        return AnthropicCompat(cfg)
    raise ValueError(f"未知 provider：{prov!r}（可用：openai / anthropic）")
