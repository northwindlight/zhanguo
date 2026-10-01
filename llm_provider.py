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

    backend.complete_text(messages, cfg, max_tokens=None, tools=None) -> (text, stats)
        普通**非流式**调用（记忆压缩用）。`tools` 只为**前缀缓存**存在：压缩调用现在
        接着上一次请求的前缀往下发（`mp_ai._compact_block`），所以必须带上与主请求
        **同一份**工具声明、同一个 `tool_choice`（Anthropic 那路 tools 排在 system
        之前，缺了或改了都是零命中，2026-10-02 真端点实测）。工具调用靠尾随指令里那句
        "不要调用任何工具"约束，**不是**靠 `tool_choice=none`。
        stats 与 chat_turn 同键（"hit"/"miss"/"out_tokens"/"usage_reported"/"estimated"…）。

消息在**存储与回放层一律保持 OpenAI 形态**：ctx.py 的 token 估算与缓存前缀布局、
turn_memory 存档、tests 的 mock 断言都以此为契约；换提供方时只改"调用瞬间"的
双向翻译，循环与记忆层无感。

**提供方只有两种**（用户 2026-09-19）：`openai`（OpenAI 兼容，缺省）与 `anthropic`
（Anthropic Messages 协议，2026-10-01 接线完成）。不再按厂家分名字（deepseek / ark /
vLLM 一律写 `openai`），
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

Anthropic 那一路（`provider="anthropic"`，2026-10-01 接线）
---------------------------------------------------------
**只用标准库**（urllib + 手写 SSE 解析）：不新增 `anthropic` 依赖——本机与 rpi5 上都没装
它（实测 `import anthropic` 双双 ModuleNotFoundError），而这条路要的东西不多：一个 POST、
一行行读 SSE、按事件名聚合。代价是错误分类与重试得自己写（下面 `_retryable` 那一套）。

实测取证（2026-10-01，`api.deepseek.com/anthropic` + `deepseek-flash[1m]`；
探针留在交付目录 `tools/anthropic_probe.py`、`tools/anthropic_edge*.py`）：

  · URL：`base_url` 已带 `/v1` 就不再补，否则补 `/v1/messages`——`/apps/anthropic`
    这种 Claude Code 形态的 base 同样适用（拼出来是 `/apps/anthropic/v1/messages`）；
  · 鉴权：`authorization: Bearer <key>` 与 `x-api-key: <key>` **两种都收**（各单独试过）；
    缺省发 Bearer（.claude 里各家网关用的都是 `ANTHROPIC_AUTH_TOKEN` 那条）；
  · 用量：`input_tokens`(只算未命中) + `cache_read_input_tokens`(命中) +
    `cache_creation_input_tokens`(新建，该路由恒 0) + `output_tokens`，**没有** reasoning
    分项 ⇒ hit=cache_read、miss=input+cache_creation；思考 token 只能本地估算（显示打 ≈，
    见 stats 里的 `reason_estimated`——"报错误的会导致估价错误"那条口径照旧）；
  · 流式事件：message_start / content_block_start / ping / content_block_delta
    （thinking_delta / signature_delta / input_json_delta / text_delta）/ content_block_stop
    / message_delta（带 stop_reason **与累计 usage**）/ message_stop；
  · thinking 的 `signature` 是**流末尾单独一条 signature_delta**给的（该路由是 36 字符
    uuid），content_block_start 里的签名是空串 ⇒ 必须按 index 累积；
  · **该路由既不校验签名、也不强制回放 thinking**（把 thinking 整块去掉、把签名改坏，
    回放都 200）。官方 Anthropic 是校验的（缺失/改坏 400）。⇒ 回放层照样存签名
    （`REPLAY_KEY`），签名用不上时**降级为不回放那段 thinking**——宁少一段思考，
    不冒"一个 400 终止整局"的风险；
  · `thinking:{type:enabled|disabled}` 都收，`budget_tokens` 也收（官方必填，Claude Code
    每次都发）。但本实现**默认不发** `budget_tokens`：发了等于给思考设上限、会砍长思考，
    要发就配 `anthropic_thinking_budget`；
  · ★ 反过来，**"不发 thinking" ≠ "关掉思考"**：这条网关不声明时**默认是开的**。
    2026-10-01 冒烟实测：压缩那条路（非流式、预算 1500）不声明 thinking ⇒ 预算被思考
    吃光、**正文返回空串**，压缩白跑一次。所以 `complete_text` 照 OpenAI 那路**显式**发
    `{"type":"disabled"}`（探针里单发是 200、只回 text block）；
  · 以下组合全部 200，翻译层不必为它们绕路：`temperature` 与 `thinking` 同时发、
    `tools: []`、system 用纯字符串、`max_tokens=65536`、**连续两条 user**（工具结果那条
    + 紧随其后的"最新状态"那条，游戏循环里就是长这样）；
  · 前缀缓存：同一前缀隔 3s 再来一次，`cache_read=4992/5211`（95.8%）——这条路做前缀缓存。

**消息里多了个提供方私有键**：`reasoning_signature`（=`REPLAY_KEY`，值形如
`{"model": …, "signature": …}`）。它在 OpenAI 形态的消息上"挂"着走存档，OpenAI 那一路
发请求前由 `_public_messages()` 摘掉（否则兼容端点可能对未知字段 400）；Anthropic 那一路
用它拼回 thinking block，**模型对不上就不用**（跨模型的签名不可移植——DSH 的教训）。
"""
from __future__ import annotations

import http.client
import json
import os
import re
import signal
import time
import urllib.error
import urllib.request

from ctx import REPLAY_KEY, est_tokens   # 估算（存档校准过的那套，只在"提供方没报用量"时兜底）
                                         # + 提供方私有回填键（定义在 ctx：消息形态的契约归它）


def status_code_of(e: Exception) -> int | None:
    """尽最大努力取 HTTP 状态码。

    SDK **认得出结构**的错误（JSON 错误体）是 `APIStatusError`，带 `status_code`；
    但**网关/负载均衡回的 HTML 错误页**（2026-09-20 实测：`HTTP 504 …<center>alb</center>`）
    解析不出 JSON，SDK 只能抛**基类 `APIError`**——**没有** `status_code`。
    那时从消息里捞一把 "HTTP 504"（代理与 SDK 都把状态行带在 message 里）。
    本层的 `AnthropicError` 也走这条：它有 `status_code`，没状态码的（连不上/超时）
    捞不到 ⇒ 返回 None ⇒ **按可重试处理**。
    """
    code = getattr(e, "status_code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\bHTTP (\d{3})\b", str(e))
    return int(m.group(1)) if m else None


def retry_tag(e: Exception) -> str:
    """重试播报里那句 tag（两条提供方共用，口径必须一致）。

    超时要**说明是哪一段**（首字 / 流内）——状态区上写个 "TimeoutError" 等于没说：
    180s 与 60s 两个数不是一回事（用户 2026-09-20 问的就是这个）。
    有状态码就报 `HTTP 504`（HTML 错误页那种裸 APIError 靠它才看得出是网关的问题）；
    没有就退回报类名。（429 那句 message 是整坨 JSON，不能塞进状态区。）
    """
    if isinstance(e, TimeoutError):
        return str(e)
    code = status_code_of(e)
    return f"HTTP {code} {type(e).__name__}" if code else type(e).__name__


def is_fatal(e: Exception) -> bool:
    """4xx（鉴权/参数/模型名）重试没意义 ⇒ 直接抛；其余一律重试。

    ★ 429 **不算致命**：配额耗尽正是"重试窗口留给当场续费"要等的那种错
      （用户 2026-09-20 口径，见 `OpenAICompat.chat_turn` 里那段注释）。
    ★ 认不出状态码的（连不上、SSE 中途断、网关 HTML 错误页）**必须是可重试**，
      否则又是一条静默绕过重试的后门（2026-09-20 实测栽过一次）。
    """
    code = status_code_of(e)
    return code is not None and 400 <= code < 500 and code != 429


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


# ---------------------------------------------------------------------------
# 提供方私有回填字段：在 OpenAI 形态的消息上"挂"着走存档
# ---------------------------------------------------------------------------
_PRIVATE_MSG_KEYS = (REPLAY_KEY,)
"""`REPLAY_KEY`（= `ctx.REPLAY_KEY` = `"reasoning_signature"`）是 Anthropic thinking 签名的
落盘位置，值形如 `{"model": <产出它的模型>, "signature": <串>}`。**定义在 ctx.py**：那才是
"一条消息长什么样"这条契约的拥有者，且它自己有两处会重写消息（`_shrink_messages` /
`merge_same_role`）必须照顾这个键。

为什么要有它：Anthropic 协议回放历史时，thinking block 要**连签名一起**发回（官方 API
缺失/被改就是 400）。而本层的存储与回放一律是 OpenAI 形态（`content` +
`reasoning_content` + `tool_calls`），没有搁签名的地方 ⇒ 挂这么一个私有键：
`AnthropicCompat` 翻译时用它拼回 thinking block，`OpenAICompat` 发请求前把它摘掉
（严格端点收到不认识的 message 字段会 400）。

**为什么连模型名一起存**：签名不可跨模型移植（DSH 的 `validateReplay` 就是先比 model
再比签名）。存档被换个模型继续玩时，旧签名直接作废、降级为"不回放那段 thinking"，
比拿旧签名去撞 400 好。
"""


def _public_messages(messages: list[dict]) -> list[dict]:
    """发请求前摘掉提供方私有键，返回**新列表**（绝不动调用方那份——ctx.py 的逐字节契约）。

    只在真有键时才复制（常态零拷贝）。
    """
    out = None
    for i, m in enumerate(messages):
        if isinstance(m, dict) and any(k in m for k in _PRIVATE_MSG_KEYS):
            if out is None:
                out = list(messages[:i])
            out.append({k: v for k, v in m.items() if k not in _PRIVATE_MSG_KEYS})
        elif out is not None:
            out.append(m)
    return messages if out is None else out


def _cached_tokens(usage) -> int:
    """缓存命中数的**标准 OpenAI 拼法**：`prompt_tokens_details.cached_tokens`。

    DeepSeek 系走 `prompt_cache_hit_tokens`，标准 OpenAI / 一部分网关只有这个字段。
    两个都读一遍、谁有数用谁（2026-10-02）：只认 DeepSeek 那一个的话，遇到只报标准
    字段的网关会把**真命中报成 0**——命中率凭空变 0，看起来像缓存整段失效，比不报还坑。
    """
    det = getattr(usage, "prompt_tokens_details", None)
    return int(getattr(det, "cached_tokens", 0) or 0) if det is not None else 0


def _nonstream_stats(usage, text: str, wall: float) -> dict:
    """非流式响应的 usage → 与 `chat_turn` **同键**的 stats（OpenAI 拼法）。

    `hit`/`miss` 的算法照抄 `_stream_once` 那一套：认不出、或全 0，一律按"提供方没报"
    处理（`estimated=True`，`out_tokens` 用本地估算）——**绝不把没报当 0**，否则 📊 上
    会显示一个"0% 命中"的假数，和真的缓存失效分不开。
    """
    out_n = int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
    in_n = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
    hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0) if usage is not None else 0
    miss = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0) if usage is not None else 0
    if usage is not None and not hit:
        hit = _cached_tokens(usage)
    if not miss and in_n:
        miss = max(0, in_n - hit)
    stats: dict = {"wall": wall, "out_tokens": out_n, "reason_tokens": 0,
                   "hit": hit, "miss": miss,
                   "usage_reported": bool(out_n or in_n or hit or miss)}
    if not stats["usage_reported"]:
        stats["estimated"] = True
        stats["out_tokens"] = est_tokens(text)
    return stats


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
        from openai import (APIError, APIConnectionError,
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
            except APIError as e:
                # ★ 这里接的是**基类**（`APIStatusError`/`APIResponseValidationError` 都是它）：
                #   只接 `APIStatusError` 会漏掉一整类——**网关回的 HTML 错误页**。
                #   2026-09-20 实测：`openai.APIError: HTTP 504 …<center>alb</center>`
                #   （端点前面的负载均衡超时），SDK 解析不出 JSON ⇒ 抛**裸 APIError**、
                #   **没有 `status_code`** ⇒ 旧代码不接它 ⇒ 一次都没重试就冒出循环、
                #   整局终止（197 回合的档停在半路，用户：「压根没有重试就崩了」）。
                #   现在的口径：**4xx（鉴权/参数）直接抛，其余（5xx / 认不出状态码）一律重试**
                #   ——"认不出"必须是**可重试**，否则又是一条静默绕过重试的后门。
                #   （判定收在 `is_fatal` 里：两条提供方共用同一口径。）
                if is_fatal(e):
                    raise
                last = e
            if attempt < retries and last is not None:
                if on_retry:
                    on_retry(attempt, retry_tag(last), backoff * attempt, retries)
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
                # 摘掉提供方私有键（`reasoning_signature` 是 Anthropic 那一路的东西，
                # 严格端点收到不认识的 message 字段会 400；本路发的是 OpenAI 形态）。
                model=cfg["model"], messages=_public_messages(messages),
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
                    hit_n = getattr(u, "prompt_cache_hit_tokens", 0) or 0
                    # ★ 2026-10-02：只认 DeepSeek 的 `prompt_cache_hit_tokens` 是不够的——
                    #   一部分网关（标准 OpenAI 拼法）只给 `prompt_tokens_details.cached_tokens`，
                    #   那时命中会被报成 0（看着像缓存整段失效，比不报还坑）。两个都读。
                    if not hit_n:
                        hit_n = _cached_tokens(u)
                    if not miss_n and in_n:
                        miss_n = max(0, in_n - hit_n)
                    # ★ "usage 对象在不在" **不足以**判断有没有真数：本机 qoder-flash 网关会回
                    #   一个**全 0 的 usage 对象**（上游不给计数）——只看"在不在"就会把它当成
                    #   "报了真数"，于是估算分支不触发、显示照旧 `输出0.0tok`（2026-09-20 实测栽过）。
                    #   真调用不可能 prompt/completion 同时为 0 ⇒ **全 0 一律按"没报"处理**。
                    stream_stats["usage_reported"] = bool(out_n or in_n or miss_n or hit_n)
                    stream_stats["out_tokens"] = out_n
                    det = getattr(u, "completion_tokens_details", None)
                    stream_stats["reason_tokens"] = getattr(det, "reasoning_tokens", 0) if det else 0
                    stream_stats["hit"] = hit_n
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

    def complete_text(self, messages, cfg, max_tokens=None, tools=None):
        """普通**非流式**调用（记忆压缩用）：**总时长**上限（默认 900s，
        用户 2026-09-20「非流式900」）。

        非流式没有"块间隔"可看（整段就一个响应），只能整段计时 ⇒ 用 `hard_timeout`
        而不是流式那套两段看门狗（`stream_timeout`）。900 是"一次压缩能跑多久"的余量，
        跟流式那两个数不是一个量纲，别混。

        `tools`（2026-10-02 加）：**只为前缀缓存**——压缩调用现在接着上一次请求的前缀
        往下发（`mp_ai._compact_block`），工具声明也在那段前缀里，少发一份就整段不命中。
        ★ **不许改成 `tool_choice="none"`**：真端点实测（`tools/anthropic_prefix_probe.py`，
        冷缓存那一轮）——`none` 会让网关**整个丢掉 tools 段**（prompt 直接少 265 token），
        前缀从 system 之后当场断掉，命中率塌到 40%（只剩 system 那点）；照发
        `tool_choice="auto"`（与主请求逐字一致）则命中 93.6%、miss 只剩尾随指令。
        所以这里发的就是主请求**同一份** tools + auto，靠尾随指令里那句
        "不要调用任何工具"约束模型（DSH 的 compact 也是这么做的）。
        `tools=None` 时请求与旧版逐字段相同。
        返回值改成 `(text, stats)`（2026-10-02）：压缩调用的命中率此前**没有任何统计**
        （📊 只统计 `chat_turn`），改完必须看得到——否则这次改动只能"感觉快了"。
        """
        # 压缩不需要思考：一律发 thinking:disabled（用户 2026-09-19「行为按 deepseek
        #   处理」——和 chat_turn 同源，扩展字段不再按端点能力分化）。
        extra = {"thinking": {"type": "disabled"}}
        limit = float(cfg.get("api_timeout", 900))
        kw: dict = {"model": cfg["model"], "messages": _public_messages(messages),
                    "max_tokens": int(max_tokens if max_tokens is not None
                                       else cfg.get("ctx_compact_tokens", 1500)),
                    "temperature": 0.3,
                    "extra_body": extra or None,
                    "timeout": limit}
        if tools:
            kw["tools"] = list(tools)
            kw["tool_choice"] = "auto"       # 与 chat_turn 同值：前缀必须逐字节一致
        wall0 = time.time()
        with hard_timeout(limit, "非流式调用超时"):
            resp = self.client.chat.completions.create(**kw)
        text = (resp.choices[0].message.content or "").strip()
        return text, _nonstream_stats(getattr(resp, "usage", None), text,
                                      time.time() - wall0)


# ---------------------------------------------------------------------------
# Anthropic Messages 协议（provider="anthropic"，2026-10-01 接线）
# ---------------------------------------------------------------------------
ANTHROPIC_VERSION = "2023-06-01"
_CONTINUE_NUDGE = ("（上一段输出被 max_tokens 截断）请**接着断点继续**："
                   "直接往下写/往下调用，不要重复已经写过的内容，也不要重头再来。")


class AnthropicError(Exception):
    """Anthropic 端点的一次失败。

    带 HTTP 状态码的（4xx/5xx）`status_code` 有值；连不上、读超时、SSE 中途报错
    没有状态码 ⇒ None ⇒ `is_fatal` 判为**可重试**（与 OpenAI 那路同一条纪律：
    "认不出"必须可重试，否则就是一条静默绕过重试的后门）。
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def anthropic_messages_url(base_url: str) -> str:
    """`base_url` → messages 端点。**已经带 `/v1` 的不再补一层**（`/v1/v1/messages` 是坑）。

    两种 base 都要对：`https://api.deepseek.com/anthropic` → `…/anthropic/v1/messages`；
    Claude Code 形态的 `https://…/apps/anthropic` → `…/apps/anthropic/v1/messages`。
    """
    base = str(base_url or "").rstrip("/")
    return base + "/messages" if base.endswith("/v1") else base + "/v1/messages"


def _anthropic_headers(cfg: dict) -> dict:
    """请求头。

    · 鉴权缺省 `authorization: Bearer <key>`——.claude 里各家网关用的都是
      `ANTHROPIC_AUTH_TOKEN` 那条；实测 `x-api-key` **单独发**也收，用 `anthropic_auth`
      切到 `x-api-key` / `both`（官方 Anthropic 的 API key 就是 x-api-key 那套）。
    · `anthropic_beta` 原样透传（如 `context-1m-2025-08-07` 这类 beta 头；不配就不发）。
    """
    key = str(cfg.get("api_key") or "")
    if not key:
        raise ValueError("provider=anthropic 需要 api_key")
    headers = {"content-type": "application/json",
               "accept": "application/json",
               "anthropic-version": str(cfg.get("anthropic_version") or ANTHROPIC_VERSION)}
    mode = str(cfg.get("anthropic_auth") or "bearer").strip().lower()
    if mode in ("bearer", "both"):
        headers["authorization"] = "Bearer " + key
    if mode in ("x-api-key", "both"):
        headers["x-api-key"] = key
    beta = cfg.get("anthropic_beta")
    if beta:
        headers["anthropic-beta"] = (beta if isinstance(beta, str)
                                     else ",".join(str(b) for b in beta))
    return headers


def _anthropic_thinking(cfg: dict) -> dict | None:
    """`thinking` 参数：开关与 OpenAI 那路同源（`enabled`/`disabled`），缺省 enabled
    （2026-09-19「行为按 deepseek 处理」的同一口径，不按端点能力分化）。

    `budget_tokens` **默认不发**：官方 Anthropic 必填它，但本实现实际服务的两条网关
    （DeepSeek 官方 anthropic 路由 / Aliyun token-plan）都不要求（实测不带也 200），
    而**发了就是给思考设上限**——会砍掉长思考。要发就配 `anthropic_thinking_budget`。
    """
    mode = str(cfg.get("thinking") or "enabled").strip().lower()
    if mode in ("disabled", "off", "false", "0", "none", "no"):
        return None
    out: dict = {"type": "enabled"}
    budget = cfg.get("anthropic_thinking_budget")
    if budget:
        out["budget_tokens"] = max(1024, int(budget))
    return out


def _anthropic_tool(t: dict) -> dict:
    """OpenAI 工具 schema → Anthropic：`function.parameters` → **顶层** `input_schema`。"""
    fn = t.get("function") or {}
    out = {"name": str(fn.get("name") or ""),
           "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}}
    if fn.get("description"):
        out["description"] = str(fn["description"])
    return out


def _anthropic_assistant_blocks(m: dict, model: str) -> list[dict]:
    """一条 OpenAI 形态的 assistant 消息 → Anthropic content blocks
    （顺序固定：thinking → text → tool_use，Anthropic 要求 thinking 在最前）。

    thinking **只在签名齐全且模型对得上时才回放**：签名由提供方产出、绑死在具体模型上
    （跨模型不可移植），缺了就整块不发——官方 API 对"签名缺失/被改"直接 400，
    而 400 在本项目里等于**终止整局**（用户 2026-09-20 口径），宁少一段思考。
    （实测 DeepSeek 那条路由既不校验签名也不强制回放，但这是端点脾气，不是协议保证。）
    """
    blocks: list[dict] = []
    reasoning = str(m.get("reasoning_content") or "").strip()
    sig = m.get(REPLAY_KEY) or {}
    if (reasoning and isinstance(sig, dict) and sig.get("signature")
            and str(sig.get("model") or "") == str(model)):
        blocks.append({"type": "thinking", "thinking": reasoning,
                       "signature": str(sig["signature"])})
    if m.get("content"):
        blocks.append({"type": "text", "text": str(m["content"])})
    for i, tc in enumerate(m.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        try:
            parsed = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            parsed = {}          # 参数曾被 max_tokens 截断：按空参发（引擎自己会拒这一条）
        if not isinstance(parsed, dict):
            parsed = {}
        blocks.append({"type": "tool_use", "id": str(tc.get("id") or f"call_{i}"),
                       "name": str(fn.get("name") or ""), "input": parsed})
    return blocks


def _anthropic_system(messages: list[dict], cfg: dict) -> list[dict] | None:
    """把 role="system" 提到顶层 `system` 参数（前缀布局不变：system 依旧在最前）。

    `anthropic_cache_control` 为真时在末尾打一个显式缓存断点
    （`cache_control:{type:"ephemeral"}`）——实测这条网关收、且与隐式缓存表现一致；
    缺省不打（隐式缓存已经够用，少一个字段少一分 400 的面）。
    """
    parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    text = "\n\n".join(p for p in parts if p)
    if not text:
        return None
    block: dict = {"type": "text", "text": text}
    if cfg.get("anthropic_cache_control"):
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def _anthropic_messages(messages: list[dict], model: str, cfg: dict) -> list[dict]:
    """OpenAI 形态 messages → Anthropic messages（**只读不写**调用方那些 dict）。

    三条规则：
      · 连续的 `role="tool"` **合成一条 user 消息里的多个 tool_result**——Anthropic 的
        工具结果就该长这样（一条 user 里跟 N 个 block），不是每个调用一条消息；
      · `role="system"` 由 `_anthropic_system` 提到顶层，这里跳过；
      · assistant 若一个 block 都拼不出来（正文空、没调工具、thinking 又因缺签名被丢）
        ⇒ **整条跳过**：Anthropic 不收空 content。

    工具结果那条 user 后面紧跟"最新状态"那条 user，是**连续两条 user**——实测这条网关收
    （不必为了交替角色把状态并进 tool_result 里，那反而会把状态面板塞进工具结果）。
    """
    out: list[dict] = []
    pending: list[dict] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()

    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            tid = str(m.get("tool_call_id") or "")
            text = str(m.get("content") or "")
            # 没有 tool_call_id 的（配不上 tool_use）当普通文本发：造一个空 id 出去必 400
            pending.append({"type": "tool_result", "tool_use_id": tid, "content": text}
                           if tid else {"type": "text", "text": text})
            continue
        flush()
        if role == "assistant":
            blocks = _anthropic_assistant_blocks(m, model)
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        else:
            # user（含 role 认不出的）：内容照发，别丢
            out.append({"role": "user",
                        "content": [{"type": "text", "text": str(m.get("content") or "")}]})
    flush()
    return out


def _anthropic_body(messages: list[dict], tools, cfg: dict, stream: bool) -> dict:
    """拼请求体（默认值都收在这里）。

    `max_tokens` 缺省 16384 = OpenAI 那路的同一个数，好让 ctx.py 的 `ctx_reserve_out`
    对两条路都成立；`temperature` **夹到 [0,1]**（OpenAI 兼容那路允许 0~2，Anthropic
    只到 1；实测这条网关 1.5 也照收，但官方 API 会 400——夹一下比崩一局便宜）。
    `tools` 空就不发这个字段（`tools: []` 实测也收，但不发更干净）。
    """
    body: dict = {"model": cfg["model"],
                  "max_tokens": int(cfg.get("max_tokens", 16384)),
                  "messages": _anthropic_messages(messages, cfg["model"], cfg)}
    system = _anthropic_system(messages, cfg)
    if system:
        body["system"] = system
    tlist = [_anthropic_tool(t) for t in (tools or [])]
    if tlist:
        body["tools"] = tlist
        # 一律 auto：**开思考时 Anthropic 只许 auto/none**，而循环本来也只发 auto
        body["tool_choice"] = {"type": "auto"}
    thinking = _anthropic_thinking(cfg)
    if thinking:
        body["thinking"] = thinking
    if cfg.get("temperature") is not None:
        body["temperature"] = min(1.0, max(0.0, float(cfg.get("temperature", 0.3))))
    if stream:
        body["stream"] = True
    return body


def _iter_sse(resp):
    """把 SSE 响应体拆成 `(事件名, data 文本)` 逐条产出。

    实测这条网关每个事件都同时给 `event:` 行与 data 里的 `type`（两者一致）；
    只认 `data:` 的网关（少数）由调用方退回 `payload["type"]`。
    注释行（`:` 开头，含 `ping` 的心跳）、未知字段按 SSE 规矩忽略。
    """
    event: str | None = None
    buf: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if buf:
                yield event, "\n".join(buf)
                event, buf = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            buf.append(value)
    if buf:
        yield event, "\n".join(buf)


def _block_index(payload: dict) -> int:
    """content block 的下标归一成 int（认不出就 0）。

    聚合与 `sorted(blocks.items())` 都靠它——真端点上 `index` 是整数，但**不能让一个字符串
    下标混进来**：那会让排序抛 TypeError，而这里抛异常＝整局终止，代价完全不对等。
    """
    try:
        return int(payload.get("index") or 0)
    except (TypeError, ValueError):
        return 0


def _absorb_delta(blocks: dict, payload: dict) -> None:
    """把一条 `content_block_delta` 并进对应 index 的块。

    四种 delta（实测这条网关都会发）：text_delta / thinking_delta / signature_delta /
    input_json_delta。**签名是流末尾单独一条 signature_delta 给的**（content_block_start
    里签名是空串）⇒ 必须按 index 累积，只看 start 拿不到签名。
    """
    idx = _block_index(payload)
    delta = payload.get("delta") or {}
    kind = delta.get("type")
    b = blocks.setdefault(idx, {})
    if kind == "text_delta":
        b.setdefault("type", "text")
        b["text"] = (b.get("text") or "") + (delta.get("text") or "")
    elif kind == "thinking_delta":
        b.setdefault("type", "thinking")
        b["thinking"] = (b.get("thinking") or "") + (delta.get("thinking") or "")
    elif kind == "signature_delta":
        b.setdefault("type", "thinking")
        b["signature"] = (b.get("signature") or "") + (delta.get("signature") or "")
    elif kind == "input_json_delta":
        b.setdefault("type", "tool_use")
        b["partial_json"] = (b.get("partial_json") or "") + (delta.get("partial_json") or "")


def _anthropic_open(url: str, headers: dict, body: dict, timeout: float):
    """POST 一次，返回响应对象（**只做错误分类与编码**）。

    `timeout` 是 socket 级的：连接、响应头、**每一次读**都按它计时——所以它天然就是
    "流内间隔"的看门狗。Windows 上没有 SIGALRM，`stream_timeout` 空转，这条是那边
    唯一能抓住"流卡住"的东西（openai SDK 那路在 Windows 上做不到，见模块 docstring）。
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        # 错误体可能是 JSON（{"type":"error","error":{…}}），也可能是网关的 HTML/纯文本
        detail = e.read().decode("utf-8", "replace")[:600]
        raise AnthropicError(f"HTTP {e.code} {detail}", status_code=e.code) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TimeoutError(f"连接/首字等待超过 {timeout:g}s 没等到响应头") from None
        raise AnthropicError(f"连接失败：{e.reason}") from None


class AnthropicCompat:
    """Anthropic Messages 协议（`provider="anthropic"`）。2026-10-01 接线，实测依据见模块
    docstring「Anthropic 那一路」。映射配方（即旧版"预留接口"注释的 ①~⑥）：

    ① tools：`{function:{name,description,parameters}}` → `{name,description,input_schema}`；
       `tool_choice` 一律 `{"type":"auto"}`（开思考时只许 auto/none，正合循环所发）；
    ② system 消息 → 顶层 `system`（前缀布局不变，要显式缓存断点就配 `anthropic_cache_control`）；
    ③ `assistant.tool_calls` → tool_use block；连续 `role="tool"` → **一条** user 消息里的
       多个 tool_result（`_anthropic_messages`）；
    ④ `reasoning_content` ↔ thinking block：回放要连签名，签名缺失/换了模型就整块不发
       （`_anthropic_assistant_blocks`），本路把签名顺手存回 assistant 消息的 `REPLAY_KEY`；
    ⑤ 重试分类与 OpenAI 那路同一口径（`is_fatal`，429 也算可重试=给"当场续费"留窗口）；
       `stop_reason="max_tokens"` 用 message tailing 续写（只在**没吐工具调用**时续）；
    ⑥ 用量映射：cache_read→hit，input+creation→miss，output→out_tokens；思考 token 该路
       不单独报 ⇒ 本地估算并打 `reason_estimated`（显示成 ≈）。

    **不新增依赖**：整条路只用标准库（urllib + 手写 SSE 解析），装不装 `anthropic` 包都能跑
    ——本机与 rpi5 上都没装它（实测 `import anthropic` 双双 ModuleNotFoundError）。
    """

    def __init__(self, cfg: dict):
        # 不建客户端对象：标准库那套没有会话状态，URL/头每次现算（cfg 逐国覆盖也天然生效）
        self.cfg = cfg

    def chat_turn(self, messages, tools, cfg, on_retry=None):
        msg, stats = self._call(messages, tools, cfg, on_retry)
        return self._tail(messages, tools, cfg, msg, stats)

    def _call(self, messages, tools, cfg, on_retry=None):
        """带重试窗口的流式调用（分类与退避口径与 `OpenAICompat.chat_turn` 一致）。

        ★ 429（配额耗尽）**也走重试**：用户 2026-09-20「重试到本回合必须跳过时，再退出，
          而不是马上退——不然玩家要是当场续费呢」。窗口 = api_retries × api_retry_wait。
        ★ 4xx（鉴权/模型名/参数）直接抛——重试没意义，而抛出去由上层终止本局
          （这是"任何错误都应该直接终止游戏"那条口径，两条提供方一致）。
        """
        retries = int(cfg.get("api_retries", 3))
        backoff = float(cfg.get("api_retry_wait", 2.0))
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return self._stream_once(messages, tools, cfg)
            except AnthropicError as e:
                if is_fatal(e):
                    raise
                last = e
            except (TimeoutError, OSError, http.client.HTTPException) as e:
                last = e          # 连不上 / 读超时 / SSE 读到一半断：都可重试
            if attempt < retries:
                if on_retry:
                    on_retry(attempt, retry_tag(last), backoff * attempt, retries)
                time.sleep(backoff * attempt)
        assert last is not None
        raise last

    def _tail(self, messages, tools, cfg, msg, stats):
        """`stop_reason="max_tokens"` 的**续写**（message tailing）。

        只在两种情况续：**没吐 tool_use**（工具调用被截断时参数 JSON 只写了一半，续写反而
        更乱——那种残批交给循环"没调工具就催它继续"那条路更稳）且还有次数（
        `anthropic_tail_max`，缺省 2）。每次把已吐出的正文当 assistant 消息发回去、
        再跟一条"接着断点写"的 user 消息，把新吐的字并回同一条 msg。

        **续写失败一律吞掉**：宁可少半段正文，也不能因为一次补救把整局终止
        （2026-09-20 那条"任何错误终止整局"针对的是主调用，不是这种补救）。
        续写请求里**不带 reasoning_content**：被截断的思考没有签名，带着它去撞 400 不值得
        （开思考时 prefill 也是官方明令不许的）。
        """
        left = int(cfg.get("anthropic_tail_max", 2))
        while (left > 0 and stats.get("stop_reason") == "max_tokens"
               and not msg.get("tool_calls")):
            left -= 1
            partial = dict(msg)
            partial.pop("reasoning_content", None)
            partial.pop(REPLAY_KEY, None)
            partial.setdefault("role", "assistant")
            try:
                more, st2 = self._stream_once(
                    list(messages) + [partial, {"role": "user", "content": _CONTINUE_NUDGE}],
                    tools, cfg)
            except Exception:      # noqa: BLE001 —— 补救失败就当没续过
                break
            msg["content"] = (msg.get("content") or "") + (more.get("content") or "")
            if more.get("reasoning_content"):
                msg["reasoning_content"] = ((msg.get("reasoning_content") or "")
                                            + more["reasoning_content"])
            if more.get(REPLAY_KEY):
                msg[REPLAY_KEY] = more[REPLAY_KEY]      # 签名取最新那份（旧的已被截断作废）
            if more.get("tool_calls"):
                msg["tool_calls"] = more["tool_calls"]
            stats["stop_reason"] = st2.get("stop_reason")
            # 用量按"两次调用之和"记（这次续写也是真花了钱的）；first 保持首调用那次的
            # 语义，maxgap 取两者较大——它们是"看数"用的，不该被求和成假数。
            stats["wall"] = (stats.get("wall") or 0) + (st2.get("wall") or 0)
            stats["stream"] = (stats.get("stream") or 0) + (st2.get("stream") or 0)
            stats["maxgap"] = max(stats.get("maxgap") or 0, st2.get("maxgap") or 0)
            for k in ("out_tokens", "reason_tokens", "hit", "miss"):
                stats[k] = (stats.get(k) or 0) + (st2.get(k) or 0)
            if st2.get("estimated"):
                stats["estimated"] = True
                stats["reason_estimated"] = True
        return msg, stats

    def _stream_once(self, messages, tools, cfg):
        """一次流式调用 + 聚合：返回 `(msg, stats)`。

        字段与 `OpenAICompat._stream_once` **完全同名同义**（wall/stream/first/maxgap/
        out_tokens/reason_tokens/hit/miss/usage_reported/estimated），这样回合循环、
        看海台与 `ctx.record_hit` 一行都不用改。

        超时也照流式语义：`stream_timeout` 看"首字（第一个 content_block_delta）"与
        "流内间隔"，**没有总时长上限**；Windows 上它空转，靠 socket timeout 兜
        （那边 `first_t` 的判定把超时消息补成"首字/流内"，不然状态区看不出卡在哪）。
        """
        url = anthropic_messages_url(cfg["base_url"])
        headers = _anthropic_headers(cfg)
        body = _anthropic_body(messages, tools, cfg, stream=True)
        ttft = float(cfg.get("api_ttft_timeout", 180))
        gap = float(cfg.get("api_chunk_timeout", 60))
        usage: dict = {}
        stop: str | None = None
        blocks: dict = {}
        stats: dict = {}
        wall0 = time.time()
        first_t = None
        last_t = wall0
        with stream_timeout(ttft, gap) as clock:
            resp = _anthropic_open(url, headers, body, ttft)
            try:
                for name, data in _iter_sse(resp):
                    clock.kick()          # 有字节进来就重新上弦（ping 心跳也算"还活着"）
                    now = time.time()
                    stats["maxgap"] = max(stats.get("maxgap", 0.0), now - last_t)
                    last_t = now
                    try:
                        payload = json.loads(data)
                    except ValueError:
                        continue          # 认不出的行：跳过（不为一行脏数据终止整局）
                    kind = name or payload.get("type")
                    if kind == "error":
                        err = payload.get("error") or {}
                        raise AnthropicError(f"{err.get('type') or 'error'}: "
                                             f"{str(err.get('message'))[:300]}")
                    if kind == "message_start":
                        usage.update(((payload.get("message") or {}).get("usage") or {}))
                    elif kind == "content_block_start":
                        blocks[_block_index(payload)] = dict(payload.get("content_block") or {})
                    elif kind == "content_block_delta":
                        if first_t is None:
                            first_t = now        # "首字"= 第一个真吐字的块，不是响应头
                        _absorb_delta(blocks, payload)
                    elif kind == "message_delta":
                        stop = (payload.get("delta") or {}).get("stop_reason") or stop
                        usage.update(payload.get("usage") or {})   # ★ 累计用量在这条上
            except TimeoutError:
                raise TimeoutError(
                    ("首字" if first_t is None else "流内")
                    + f"等待超过 {(ttft if first_t is None else gap):g}s"
                      f"（一直没等到新的输出块）") from None
            finally:
                resp.close()

        text = "".join(str(b.get("text") or "") for _, b in sorted(blocks.items())
                       if b.get("type") == "text")
        think = "".join(str(b.get("thinking") or "") for _, b in sorted(blocks.items())
                        if b.get("type") == "thinking")
        # 签名只留"最后一块带签名的"那份：本层把整条 assistant 压平成 content+reasoning_content
        # 一个字符串，多 thinking 块（罕见）本就分不回去了——多块时回放的是合并文本+末块签名，
        # 这点简化写在 `_anthropic_assistant_blocks` 的注释里（该路由不校验，官方路由会拒，
        # 但那种请求本来就该走"分块存"的重构，不在这次接线的范围内）。
        signature = ""
        for _, b in sorted(blocks.items()):
            if b.get("type") == "thinking" and b.get("signature"):
                signature = str(b["signature"])
        calls = []
        for idx, b in sorted(blocks.items()):
            if b.get("type") != "tool_use":
                continue
            args = b.get("partial_json")
            if not args:                     # 有些网关直接在 block_start 里给完整 input
                inp = b.get("input")
                args = json.dumps(inp, ensure_ascii=False) if inp else "{}"
            calls.append({"id": str(b.get("id") or f"call_{idx}"), "type": "function",
                          "function": {"name": str(b.get("name") or ""), "arguments": args}})

        msg: dict = {"content": text, "reasoning_content": think}
        if signature and think:
            msg[REPLAY_KEY] = {"model": str(cfg["model"]), "signature": signature}
        if calls:
            msg["tool_calls"] = calls

        hit = int(usage.get("cache_read_input_tokens") or 0)
        miss = (int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0))
        out_n = int(usage.get("output_tokens") or 0)
        stats["usage_reported"] = bool(hit or miss or out_n)
        stats["out_tokens"] = out_n
        stats["reason_tokens"] = est_tokens(think) if think else 0
        stats["hit"] = hit
        stats["miss"] = miss
        stats["stop_reason"] = stop
        stats["wall"] = time.time() - wall0
        if first_t:
            stats["first"] = first_t - wall0
            stats["stream"] = max(0.0, last_t - first_t)
        if not stats["usage_reported"]:
            # 与 OpenAI 那路同一条纪律：没报就当"没报"（本地估算 + 打 ≈），绝不把没报当 0
            stats["estimated"] = True
            stats["out_tokens"] = (est_tokens(text) + est_tokens(think)
                                   + sum(est_tokens(c["function"]["arguments"]) for c in calls))
            stats["reason_tokens"] = est_tokens(think)
        elif think:
            # 这条路不单独报思考 token（在 output_tokens 里，分不出来）⇒ 本地估、显示打 ≈
            stats["reason_estimated"] = True
        return msg, stats

    def complete_text(self, messages, cfg, max_tokens=None, tools=None):
        """普通**非流式**调用（记忆压缩用）：**总时长**上限（默认 900s）。

        与 `OpenAICompat.complete_text` 同一口径：**显式发 `thinking:{"type":"disabled"}`**。
        ★ **不能只是"不发这个字段"**：2026-10-01 真端点冒烟实测——这条网关**不声明时默认开
        思考**，于是 1500 的压缩预算被思考吃光、正文返回**空字符串**（压缩白跑一次）。
        探针里 `{"type":"disabled"}` 单发是 200（只回 text block），照 OpenAI 那路的写法发即可。
        **不重试**：压缩失败由 mp_ai 兜住（归档退回逐回合小结），与 OpenAI 那路一致。

        `tools`（2026-10-02 加）：这条协议的请求前缀顺序是 **tools → system → messages**，
        压缩调用要复用上一次请求的缓存前缀，工具声明就必须**一模一样地照发**——少发一份
        等于整个前缀从第一个字节就对不上。`tool_choice` 保持 `_anthropic_body` 默认的
        `{"type": "auto"}`（与主请求同值）；**别改 none**：真端点实测 `none` 会让网关丢掉
        tools 段，前缀从 system 之后断掉（详见 `OpenAICompat.complete_text` 那段注）。
        返回值改成 `(text, stats)`，usage 口径与 `chat_turn` 那条路相同。
        """
        url = anthropic_messages_url(cfg["base_url"])
        headers = _anthropic_headers(cfg)
        body = _anthropic_body(messages, tools, cfg, stream=False)
        body["thinking"] = {"type": "disabled"}
        body["max_tokens"] = int(max_tokens if max_tokens is not None
                                 else cfg.get("ctx_compact_tokens", 1500))
        limit = float(cfg.get("api_timeout", 900))
        wall0 = time.time()
        with hard_timeout(limit, "非流式调用超时"):
            resp = _anthropic_open(url, headers, body, limit)
            with resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        text = "".join(str(b.get("text") or "") for b in (payload.get("content") or [])
                       if b.get("type") == "text").strip()
        usage = payload.get("usage") or {}
        hit = int(usage.get("cache_read_input_tokens") or 0)
        miss = (int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0))
        out_n = int(usage.get("output_tokens") or 0)
        stats: dict = {"wall": time.time() - wall0, "out_tokens": out_n, "reason_tokens": 0,
                       "hit": hit, "miss": miss,
                       "usage_reported": bool(hit or miss or out_n)}
        if not stats["usage_reported"]:
            stats["estimated"] = True               # 同 chat_turn：没报就当没报，别装成 0
            stats["out_tokens"] = est_tokens(text)
        return text, stats


def make_backend(cfg: dict):
    """按配置里的 provider 字段选提供方，**不按 base_url 猜**（2026-09-13：猜 URL 会
    踩代理/网关地址）。
      openai    -> OpenAICompat（任意 OpenAI 兼容端点，**行为按 DeepSeek 处理**）
      anthropic -> AnthropicCompat（Anthropic Messages 协议，2026-10-01 接线完成）
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

