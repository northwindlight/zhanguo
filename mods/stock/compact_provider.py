"""Stock-local streaming compression adapter for OpenAI-compatible endpoints."""
import json
import time
from clients.provider import (OpenAICompat, _public_messages, _post_json, openai_chat_url,
    _bearer_headers, stream_timeout, _iter_sse, _cached_tokens, _stream_notify, est_tokens)


class StockOpenAICompat(OpenAICompat):
    def _stream_once(self, messages, tools, cfg, on_delta=None):
        """一次流式调用 + 聚合：返回 (msg, stats)。

        **超时按流式的语义走**（用户 2026-09-20：「首字3分钟，sse内60秒」）：
        `stream_timeout` 两段看门狗管首字与流内间隔，**没有总时长上限**。
        `_post_json` 的 `timeout=ttft` 是给**没有 SIGALRM 的平台**（Windows）的兜底
        ——socket 级超时管连接、响应头与每一次读（原生化后 Windows 也能抓住流内
        停滞了，只是宽到 ttft 才判死，已知差异）。"""
        # deepseek-v4：thinking 开关 + reasoning_effort（low/medium/high）。
        # 一律发（用户 2026-09-19「行为按 deepseek 处理」）：cfg 没写 thinking 就按
        # enabled 走，不再有「没声明推理模型就省掉」的分叉（原 F1 保护已撤销）。
        # （SDK 时代它们走 extra_body 合并进顶层；原生化后直接并进 body，逐字段等价。）
        body: dict = {"model": cfg["model"],
                      # 摘掉提供方私有键（`reasoning_signature` 是 Anthropic 那一路的东西，
                      # 严格端点收到不认识的 message 字段会 400；本路发的是 OpenAI 形态）。
                      "messages": _public_messages(messages, vision=cfg.get("vision", False),
                                                   renderer=cfg.get("_ui_expand_message")),
                      "tools": list(tools or []), "tool_choice": "auto",
                      "temperature": cfg.get("temperature", 0.3),
                      # 默认输出上限 16k（2026-09-20 用户口径：「默认改大一点 16k」）。
                      # 原默认 4000 太小：长回合一说话就被截断 ⇒ 模型提前收尾（实测：配置里
                      # 漏写 max_tokens 时，一口气做十几个动作的国家会被 4000 砍在半路）。
                      "max_tokens": cfg.get("max_tokens", 16384),
                      "thinking": {"type": cfg.get("thinking") or "enabled"},
                      "stream": True, "stream_options": {"include_usage": True}}
        if cfg.get("_compact_stream") and not tools:
            body.pop("tools", None)
            body.pop("tool_choice", None)
        if cfg.get("reasoning_effort") and not cfg.get("_compact_stream"):
            body["reasoning_effort"] = cfg["reasoning_effort"]
        # 首字 180s（3 分钟）/ 流内 60s：**都是"等待"，不是总时长**（用户 2026-09-20）。
        ttft = float(cfg.get("api_ttft_timeout", 180))
        gap = float(cfg.get("api_chunk_timeout", 60))
        stream_stats: dict = {}
        c_s, r_s, tool_acc = "", "", {}
        wall0 = time.time()
        with stream_timeout(ttft, gap) as clock:
            resp = _post_json(openai_chat_url(cfg["base_url"]),
                              _bearer_headers(cfg), body, ttft)
            first_t = None
            last_t = time.time()
            try:
                for _name, data in _iter_sse(resp):
                    clock.kick()               # 收到块 ⇒ 重新上弦（首字那一段到此结束）
                    if data == "[DONE]":       # chat 流的收尾哨兵（Responses 流没有它）
                        break
                    now = time.time()
                    try:
                        payload = json.loads(data)
                    except ValueError:
                        continue          # 认不出的行：跳过（不为一行脏数据终止整局）
                    if not isinstance(payload, dict):
                        continue
                    if first_t is None and payload.get("choices"):
                        d0 = payload["choices"][0].get("delta") or {}
                        if (d0.get("content") or d0.get("reasoning_content")
                                or d0.get("tool_calls")):
                            first_t = now
                    stream_stats["maxgap"] = max(stream_stats.get("maxgap", 0.0), now - last_t)
                    last_t = now
                    u = payload.get("usage")
                    if u:
                        out_n = int(u.get("completion_tokens") or 0)
                        in_n = int(u.get("prompt_tokens") or 0)
                        miss_n = int(u.get("prompt_cache_miss_tokens") or 0)
                        hit_n = int(u.get("prompt_cache_hit_tokens") or 0)
                        # ★ 2026-10-02：只认 DeepSeek 的 `prompt_cache_hit_tokens` 是不够的——
                        #   一部分网关（标准 OpenAI 拼法）只给 `prompt_tokens_details.cached_tokens`，
                        #   那时命中会被报成 0（看着像缓存整段失效，比不报还坑）。两个都读。
                        if not hit_n:
                            hit_n = _cached_tokens(u)
                        if not miss_n and in_n:
                            miss_n = max(0, in_n - hit_n)
                        # ★ "usage 在不在" **不足以**判断有没有真数：本机 qoder-flash 网关**原先**
                        #   会回一个**全 0 的 usage 对象**——只看"在不在"就会把它当成"报了真数"，
                        #   于是估算分支不触发、显示照旧 `输出0.0tok`（2026-09-20 实测栽过）。
                        #   真调用不可能 prompt/completion 同时为 0 ⇒ **全 0 一律按"没报"处理**。
                        stream_stats["usage_reported"] = bool(out_n or in_n or miss_n or hit_n)
                        stream_stats["out_tokens"] = out_n
                        det = u.get("completion_tokens_details")
                        stream_stats["reason_tokens"] = int((det or {}).get("reasoning_tokens") or 0)
                        stream_stats["hit"] = hit_n
                        stream_stats["miss"] = miss_n
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    if choice.get("finish_reason") is not None:
                        stream_stats["stop_reason"] = choice["finish_reason"]
                    d = choice.get("delta") or {}
                    rd = d.get("reasoning_content")
                    if rd:
                        r_s += rd
                        _stream_notify(on_delta, 'thinking', rd)
                    cd = d.get("content")
                    if cd:
                        c_s += cd
                        _stream_notify(on_delta, 'body', cd)
                    for i, tc in enumerate(d.get("tool_calls") or []):
                        # index 缺失的网关按出现位次排（int 化防字符串下标混进排序）
                        try:
                            slot_key = int(tc.get("index", i))
                        except (TypeError, ValueError):
                            slot_key = i
                        slot = tool_acc.setdefault(slot_key, {"id": None, "type": "function",
                                                              "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        if tc.get("type"):
                            slot["type"] = tc["type"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
            except TimeoutError:
                raise TimeoutError(
                    ("首字" if first_t is None else "流内")
                    + f"等待超过 {(ttft if first_t is None else gap):g}s"
                      f"（一直没等到新的输出块）") from None
            finally:
                resp.close()
        stream_stats["wall"] = time.time() - wall0
        if first_t:
            stream_stats["first"] = first_t - wall0
            stream_stats["stream"] = max(0.0, last_t - first_t)
        msg = {"content": c_s, "reasoning_content": r_s}
        if tool_acc:
            msg["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
        # ★ 提供方没报用量 ⇒ 用**本地估算**兜底并打 `estimated` 标记。绝不把"没报"当成 0：
        #   那样看海台会打印 `输出0.0tok(思考0.0)`，看起来像"模型一个字都没说"，是骗人的显示
        #   （用户 2026-09-20：「报错误的会导致估价错误……那应该改游戏，而不是改网关」）。
        #   客户端据此把数字显示成 `≈`。
        #   ★ 2026-10-02 更正：本机 qoder-flash 网关**原先**回全 0，但根因不在"上游不给计数"——
        #   上游每块都带 usage（含 cached_tokens/credits），是网关 `_parse_chunk` 把它丢了。
        #   网关已修，现在这条路会带上真数；这里的估算分支留着兜底**没报用量的网关**，
        #   全 0 一律按"没报"处理这条纪律照旧（真调用不可能 prompt/completion 同时为 0）。
        #   注意：这组数只服务**展示**；上下文规划器用的是记录体积（`ctx.size_fn`），不吃它。
        if not stream_stats.get("usage_reported"):
            stream_stats["estimated"] = True
            stream_stats["out_tokens"] = (est_tokens(c_s) + est_tokens(r_s)
                                          + sum(est_tokens(tc["function"]["arguments"])
                                                for tc in msg.get("tool_calls") or []))
            stream_stats["reason_tokens"] = est_tokens(r_s)
        if cfg.get("_compact_stream") and stream_stats.get("usage_reported"):
            total_chars = len(c_s) + len(r_s)
            if total_chars:
                stream_stats["body_tokens_estimate"] = round(stream_stats["out_tokens"] * len(c_s) / total_chars)
        return msg, stream_stats

    def complete_text_stream(self, messages, cfg, max_tokens=None, tools=None):
        """Stream a compression request and estimate body tokens from character proportions."""
        # Stream so reasoning and body can be measured separately. The provider
        # may ignore the explicit request to disable reasoning.
        limit = int(max_tokens if max_tokens is not None else cfg.get("ctx_compact_tokens", 1500))
        stream_cfg = {**cfg, "thinking": "disabled", "max_tokens": limit,
                      "temperature": 0.3, "_compact_stream": True}
        msg, stats = self._stream_once(messages, tools, stream_cfg)
        if not stats.get("stop_reason"):
            stats["stop_reason"] = "incomplete"
        if msg.get("tool_calls"):
            stats["stop_reason"] = "tool_calls"
        return str(msg.get("content") or "").strip(), stats

