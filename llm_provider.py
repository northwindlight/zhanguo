# -*- coding: utf-8 -*-
"""LLM 提供方兼容层：回合循环只认一种消息协议，提供方差异全部收口在这一个文件。

回合循环（mp_ai.run_openai_turn）需要的全部原语：

    backend.chat_turn(messages, tools, cfg, on_retry=None) -> (msg, stats)
        msg   : OpenAI 形态的 assistant 消息 dict
                {content, reasoning_content, tool_calls:[{id,type,function:{name,arguments}}]}
        stats : 用量统计 {"wall","stream","first","maxgap","out_tokens","reason_tokens",
                "hit","miss"}（缺项按 0 计）
        重试全部在提供方内部做完（**配额耗尽这类 429 也照样重试**——重试窗口正是留给"当场续费"的）；
        **耗尽后原样抛出 ⇒ 上层（`mp_ai.run_openai_turn` → `mp_run`）直接终止本局**
        （2026-09-20 用户口径：「任何错误都应该直接终止游戏」；旧行为是吞成一条 user
        消息接着烧步数，一次配额墙白烧了 14 个回合）。

    backend.complete_text(messages, cfg, max_tokens=None) -> str
        无工具的普通非流式调用（记忆压缩用）。

消息在**存储与回放层一律保持 OpenAI 形态**：ctx.py 的 token 估算与缓存前缀布局、
turn_memory 存档、tests 的 mock 断言都以此为契约；换提供方时只改"调用瞬间"的
双向翻译，循环与记忆层无感。

**提供方只有两种**（用户 2026-09-19）：`openai`（OpenAI 兼容，缺省）与 `anthropic`
（兼容，预留未实现）。不再按厂家分名字（deepseek / ark / vLLM 一律写 `openai`），
**OpenAI 兼容这一路的行为一律按 DeepSeek 处理**——thinking / reasoning_effort /
reasoning_content / prompt_cache_* 这套 DeepSeek 语义就是本层的默认行为，扩展字段
不按端点能力分化、一律发（2026-09-13 的 F1「未声明就不发」保护据此撤销；代价是
严格端点（纯 OpenAI / vLLM）收到未知字段可能 400，用户已知并接受）。

**超时按"流式/非流式"分成两套**（用户 2026-09-20 口径：「首字3分钟，sse内60秒，
非流式900」）——量的是**等待**，不是总时长：

  流式（`chat_turn` / `_stream_once`）  `stream_timeout(api_ttft_timeout=180,
                                          api_chunk_timeout=60)`
      · 首字 180s：发起请求到**第一个 chunk**；
      · 流内 60s：收到一块就重新上弦，下一块超过 60s 没来才判死；
      · **没有总时长上限**：一直在吐字就能一直跑（这才是流式的语义——按 ≈27 tok/s，
        原来的 180s 总时长只够 ≈4.9k token，`max_tokens=16384` 根本到不了，
        长思考的回合会被砍在半路，砍掉＝整局终止）。
  非流式（`complete_text`，记忆压缩）  `hard_timeout(api_timeout=900)`
      · 整段就一个响应、没有"块间隔"可看，只能整段计时。

两套都走 SIGALRM：它能**打断阻塞中的读**（"流卡住了"正是要抓这个）。
Windows 没有 SIGALRM——旧代码在函数入口裸用 `signal.SIGALRM`，本机一进 LLM 回合
就 AttributeError 炸掉整局（2026-09-12 修）；现在按 console.py 的 os.name 分支惯例降级：
`stream_timeout` 空转、`hard_timeout` 空转，**只靠 SDK 自带 timeout + 提供方重试**
（流式那边传的是 `timeout=ttft`：首字对得上，流内停滞会宽到 180s 才判死，已知差异）。
"""
from __future__ import annotations

import os
import signal
import time

from ctx import est_tokens      # token 估算（存档校准过的那套），只在"提供方没报用量"时兜底


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


class stream_timeout:
    """**流式调用的两段式看门狗**（用户 2026-09-20 口径：「首字3分钟，sse内60秒，非流式900」）。

      · **首字**：从发起请求到**第一个 chunk**，给 `first` 秒（默认 180 = 3 分钟）；
      · **流内**：收到一块就**重新上弦**，下一块超过 `gap` 秒（默认 60）没来即判死。

    ⇒ 流式调用**没有总时长上限**：只要一直在吐字，跑多久都行。此前是拿 `api_timeout`
    当"整段总时长"硬砍（180s），而流式的真实语义是"等待"——按实测 ≈27 tok/s，
    180s 只够 ≈4.9k token，`max_tokens=16384` 那个上限**根本到不了**：长思考的回合会被
    砍在半路，而且砍掉＝整局终止。

    POSIX 用 SIGALRM：它能**打断阻塞中的读**，正是"流卡住了"要抓的那种情形；
    没有 SIGALRM 的平台（Windows）**空转**——那里只有 SDK 自带的 timeout 兜底，
    见 `_stream_once` 里传的 `timeout=`（首字对得上，流内停滞则宽到首字那个数才判死）。
    """

    def __init__(self, first: float, gap: float):
        self.first, self.gap = float(first), float(gap)
        self._phase = "首字"

    def _fire(self, signum, frame):
        lim = self.first if self._phase == "首字" else self.gap
        raise TimeoutError(f"{self._phase}等待超过 {lim:g}s（一直没等到新的输出块）")

    def __enter__(self):
        self._prev = None
        if os.name == "nt" or not hasattr(signal, "SIGALRM"):
            return self                        # Windows：空转，只有 SDK timeout 兜底
        self._prev = signal.signal(signal.SIGALRM, self._fire)
        signal.setitimer(signal.ITIMER_REAL, self.first)
        return self

    def kick(self) -> None:
        """收到一个 chunk ⇒ 踢一脚重新上弦（此后按"流内间隔"算）。"""
        if self._prev is None:
            return
        self._phase = "流内"
        signal.setitimer(signal.ITIMER_REAL, self.gap)

    def __exit__(self, *exc):
        if self._prev is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._prev)
            self._prev = None
        return False


class OpenAICompat:
    """任意 OpenAI 兼容端点（DeepSeek / 火山方舟 / vLLM / ...）。

    **行为按 DeepSeek 处理**（用户 2026-09-19）：本类不再区分端点脾气，DeepSeek 系
    语义即默认行为——`reasoning_content` 照收，`thinking` / `reasoning_effort` 照发、
    不因 cfg 没声明就省（见 `_stream_once` / `complete_text` 内注释）。"""

    def __init__(self, cfg: dict):
        from openai import OpenAI          # 运行时取属性：测试 patch openai.OpenAI 即生效
        # 客户端级 timeout 只是**兜底**：两条路各自在请求上显式传（流式传首字数、
        # 非流式传总时长），见 `_stream_once` / `complete_text`。
        self.client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"],
                             timeout=float(cfg.get("api_timeout", 900)))

    def chat_turn(self, messages, tools, cfg, on_retry=None):
        from openai import (APIStatusError, APIConnectionError,
                            APITimeoutError, RateLimitError)
        retries = int(cfg.get("api_retries", 3))
        backoff = float(cfg.get("api_retry_wait", 2.0))
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return self._stream_once(messages, tools, cfg)
            except (APIConnectionError, APITimeoutError, RateLimitError, TimeoutError) as e:
                # ★ 配额耗尽（429 insufficient_quota）**也走重试**，不特判、不立刻抛：
                #   用户 2026-09-20：「重试到本回合必须跳过时，再退出，而不是马上退——
                #   不然玩家要是当场续费呢」。重试窗口内续费成功 ⇒ 无缝继续；
                #   窗口耗尽 ⇒ 抛给上层终止本局（存档停在上一回合结算后）。
                #   窗口长度 = api_retries × api_retry_wait（配置项，想给"续费"留更长时间就调它）。
                last = e
            except APIStatusError as e:       # 5xx 可重试；4xx（鉴权/参数）直接抛
                if 500 <= e.status_code < 600:
                    last = e
                else:
                    raise
            if attempt < retries and last is not None:
                if on_retry:
                    # 超时要**说明是哪一段**（首字 / 流内）——状态区上写个 "TimeoutError"
                    # 等于没说：180s 与 60s 两个数不是一回事（用户 2026-09-20 问的就是这个）。
                    # 其它异常仍只报类名（429 那句 message 是整坨 JSON，塞进状态区没法看）。
                    tag = str(last) if isinstance(last, TimeoutError) else type(last).__name__
                    on_retry(attempt, tag, backoff * attempt, retries)
                time.sleep(backoff * attempt)
        assert last is not None
        raise last

    def _stream_once(self, messages, tools, cfg):
        """一次流式调用 + 聚合：返回 (msg, stats)。

        **超时按流式的语义走**（用户 2026-09-20：「首字3分钟，sse内60秒」）：
        `stream_timeout` 两段看门狗管首字与流内间隔，**没有总时长上限**。
        `timeout=ttft` 那个参数是给**没有 SIGALRM 的平台**（Windows）用的 SDK 兜底。"""
        # deepseek-v4：thinking 开关 + reasoning_effort（low/medium/high）。
        # 一律发（用户 2026-09-19「行为按 deepseek 处理」）：cfg 没写 thinking 就按
        # enabled 走，不再有「没声明推理模型就省掉」的分叉（原 F1 保护已撤销）。
        extra = {"thinking": {"type": cfg.get("thinking") or "enabled"}}
        if cfg.get("reasoning_effort"):
            extra["reasoning_effort"] = cfg["reasoning_effort"]
        # 首字 180s（3 分钟）/ 流内 60s：**都是"等待"，不是总时长**（用户 2026-09-20）。
        ttft = float(cfg.get("api_ttft_timeout", 180))
        gap = float(cfg.get("api_chunk_timeout", 60))
        stream_stats: dict = {}
        wall0 = time.time()
        with stream_timeout(ttft, gap) as clock:
            stream = self.client.chat.completions.create(
                model=cfg["model"], messages=messages,
                tools=tools, tool_choice="auto",
                temperature=cfg.get("temperature", 0.3),
                # 默认输出上限 16k（2026-09-20 用户口径：「默认改大一点 16k」）。
                # 原默认 4000 太小：长回合一说话就被截断 ⇒ 模型提前收尾（实测：配置里漏写
                # max_tokens 时，一口气做十几个动作的国家会被 4000 砍在半路）。
                max_tokens=cfg.get("max_tokens", 16384),
                extra_body=extra or None,
                # SDK 自己的 timeout 只在**没有 SIGALRM 的平台**（Windows）才说了算：
                # 那里首字 180s 对得上，流内停滞则要等到 180s 才判死（宽于 60s，已知）。
                timeout=ttft,
                stream=True, stream_options={"include_usage": True})
            c_s, r_s, tool_acc = "", "", {}
            first_t = None
            last_t = time.time()
            for chunk in stream:
                clock.kick()               # 收到块 ⇒ 重新上弦（首字那一段到此结束）
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
                    out_n = getattr(u, "completion_tokens", 0) or 0
                    in_n = getattr(u, "prompt_tokens", 0) or 0
                    miss_n = getattr(u, "prompt_cache_miss_tokens", 0) or 0
                    # ★ "usage 对象在不在" **不足以**判断有没有真数：本机 qoder-flash 网关会回
                    #   一个**全 0 的 usage 对象**（上游不给计数）——只看"在不在"就会把它当成
                    #   "报了真数"，于是估算分支不触发、显示照旧 `输出0.0tok`（2026-09-20 实测栽过）。
                    #   真调用不可能 prompt/completion 同时为 0 ⇒ **全 0 一律按"没报"处理**。
                    stream_stats["usage_reported"] = bool(out_n or in_n or miss_n)
                    stream_stats["out_tokens"] = out_n
                    det = getattr(u, "completion_tokens_details", None)
                    stream_stats["reason_tokens"] = getattr(det, "reasoning_tokens", 0) if det else 0
                    stream_stats["hit"] = getattr(u, "prompt_cache_hit_tokens", 0) or 0
                    stream_stats["miss"] = miss_n
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
        # ★ 提供方没报用量（实测：本机 qoder-flash 网关的 usage 恒为 0）⇒ 用**本地估算**兜底
        #   并打 `estimated` 标记。绝不把"没报"当成 0：那样看海台会打印 `输出0.0tok(思考0.0)`，
        #   看起来像"模型一个字都没说"，是骗人的显示（用户 2026-09-20：「报错误的会导致估价
        #   错误……那应该改游戏，而不是改网关」）。客户端据此把数字显示成 `≈`。
        #   注意：这组数只服务**展示**；上下文规划器用的是记录体积（`ctx.size_fn`），不吃它。
        if not stream_stats.get("usage_reported"):
            stream_stats["estimated"] = True
            stream_stats["out_tokens"] = (est_tokens(c_s) + est_tokens(r_s)
                                          + sum(est_tokens(tc["function"]["arguments"])
                                                for tc in msg.get("tool_calls") or []))
            stream_stats["reason_tokens"] = est_tokens(r_s)
        return msg, stream_stats

    def complete_text(self, messages, cfg, max_tokens=None):
        """无工具的普通**非流式**调用（记忆压缩用）：**总时长**上限（默认 900s，
        用户 2026-09-20「非流式900」）。

        非流式没有"块间隔"可看（整段就一个响应），只能整段计时 ⇒ 用 `hard_timeout`
        而不是流式那套两段看门狗（`stream_timeout`）。900 是"一次压缩能跑多久"的余量，
        跟流式那两个数不是一个量纲，别混。
        """
        # 压缩不需要思考：一律发 thinking:disabled（用户 2026-09-19「行为按 deepseek
        #   处理」——和 chat_turn 同源，扩展字段不再按端点能力分化）。
        extra = {"thinking": {"type": "disabled"}}
        limit = float(cfg.get("api_timeout", 900))
        with hard_timeout(limit, "非流式调用超时"):
            resp = self.client.chat.completions.create(
                model=cfg["model"], messages=messages,
                max_tokens=int(max_tokens if max_tokens is not None
                               else cfg.get("ctx_compact_tokens", 1500)),
                temperature=0.3,
                extra_body=extra or None,
                timeout=limit)
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
    """按配置里的 provider 字段选提供方，**不按 base_url 猜**（2026-09-13：猜 URL 会
    踩代理/网关地址）。
      openai    -> OpenAICompat（任意 OpenAI 兼容端点，**行为按 DeepSeek 处理**）
      anthropic -> AnthropicCompat（**预留未实现**，会抛）
    **只有这两种**（用户 2026-09-19）；**provider 缺省即 openai**（不再强制显式声明），
    deepseek / ark / vLLM / openai-compat / claude 这些厂家名一律不再接受——它们本就是
    「OpenAI 兼容」，写 `openai` 即可。其它值 -> ValueError。"""
    prov = str(cfg.get("provider") or "openai").strip().lower()
    if prov == "openai":
        return OpenAICompat(cfg)
    if prov == "anthropic":
        return AnthropicCompat(cfg)
    raise ValueError(f"未知 provider：{prov!r}（只有两种：openai / anthropic；"
                     f"deepseek、ark、vLLM 等 OpenAI 兼容端点一律写 openai）")
