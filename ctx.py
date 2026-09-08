# -*- coding: utf-8 -*-
"""上下文窗口管理：按配置的模型窗口动态分配预算，并让消息前缀尽可能命中缓存。

职责
----
· token 估算（用真实存档校准，只服务预算分配，不追求精确）；
· 由配置 ctx_window（模型窗口）动态决定：本回合 replay 深度、归档配额、下滑水位；
· 按「稳定性从高到低」组装消息，最大化 DeepSeek 前缀缓存命中率。

缓存布局（稳定性从高到低）
--------------------------
    [system 静态提示词] → [历史归档] → [replay 完整回合记录] → [本回合状态]

- system 必须逐字节稳定：任何随回合变化的内容（如"其余国家"名单）都不许放进来，
  否则整段前缀连同其后所有 replay 一起失效。
- 历史归档只在下滑（replay 裁掉旧回合）那一回合批量更新，两次下滑之间字节不变；
  它收录所有已滑出 replay 的回合总结（一行小结 / LLM 块总结），故长程记忆不再随
  窗口滑动丢失，且不再像旧实现那样每回合都作为"前情回顾"重传（那是纯 miss）。
- replay 是唯一会整体位移的段：裁掉最旧回合 → 其后整段前缀失效。故下滑按
  「高/低水位 + 分块」进行，把一次全段失效摊薄到很多回合（每回合摊薄 ≈ 2 个回合
  的 token，与窗口大小无关）。
- 本回合状态永远是新内容，天然 miss，放最后。

DeepSeek 文档依据（未写的一律不假设）
------------------------------------
· guides/multi_round_chat：服务端不存上下文，需客户端拼接全部历史；
· guides/thinking_mode：带 tools 时必须完整回传 reasoning_content（否则 400），
  故 replay 一律不剥思考；不带 tools 时该字段被忽略、不占上下文；
· guides/kv_cache：缓存按「完整前缀单元」匹配，命中统计在 usage 的
  prompt_cache_hit_tokens / prompt_cache_miss_tokens。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# token 估算
# ---------------------------------------------------------------------------
# 用真实存档（298 回合档 × 1023 条 journal 用量）最小二乘校准：
#   tokens ≈ 1.075×CJK字 + 0.317×其余字符 + 截距   （中位相对误差 4%，p90 12%）
# 取整为 1.05 / 0.32，宁高勿低（预算留安全余量）。仅用于分配，不做计费。
_CJK = re.compile(r"[一-鿿]")
_CJK_W = 1.05
_OTHER_W = 0.32
_MSG_OVERHEAD = 6      # 每条消息的角色/分隔结构开销
SAFETY_TOKENS = 3072   # 估算误差 + 端点模板差异的兜底余量


def est_tokens(text: str | None) -> int:
    """估算一段文本的 token 数（CJK 1.05/字，其余 0.32/字）。"""
    if not text:
        return 0
    c = len(_CJK.findall(text))
    return int(c * _CJK_W + (len(text) - c) * _OTHER_W) + 1


def msg_tokens(m: dict) -> int:
    """估算一条 OpenAI 消息（含工具调用参数）的 token 数。"""
    t = est_tokens(m.get("content")) + est_tokens(m.get("reasoning_content"))
    for tc in (m.get("tool_calls") or []):
        fn = tc.get("function") or {}
        t += est_tokens(fn.get("name")) + est_tokens(fn.get("arguments")) + 8
    return t + _MSG_OVERHEAD


def messages_tokens(msgs: list[dict]) -> int:
    return sum(msg_tokens(m) for m in msgs)


def record_tokens(rec: dict) -> int:
    """一个完整回合记录（含首条回合标记）的 token 数。"""
    return messages_tokens(rec.get("messages") or [])


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------
DEFAULT_FILL = 0.8          # 输入预算占窗口比例
DEFAULT_SLIDE_KEEP = 0.6    # 超预算时下滑到预算的该比例（余下留作增长空间）
DEFAULT_ARCHIVE_FILL = 0.06  # 历史归档上限占窗口比例
DEFAULT_MIN_TURNS = 4       # 无论预算多紧都保留的完整回合数
DEFAULT_FIXED_TURNS = 20    # 未配 ctx_window 时的旧行为：固定回合窗口


def _fnum(v, default: float, lo: float, hi: float) -> float:
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


CTX_KEYS = ("ctx_window", "ctx_fill", "ctx_slide_keep", "ctx_archive_fill",
            "ctx_archive_max", "ctx_min_turns", "ctx_compact", "ctx_compact_tokens",
            "ctx_full_turns", "ctx_reserve_out", "small_ctx")


def apply_defaults(cfg: dict) -> dict:
    """把配置顶层的 ctx_* 键下沉为每国默认值（国家条目里的同名键优先）。"""
    defaults = {k: cfg[k] for k in CTX_KEYS if k in cfg}
    for n in cfg.get("nations", []) or []:
        if isinstance(n, dict):
            for k, v in defaults.items():
                n.setdefault(k, v)
    return cfg


class Plan:
    """一次请求的上下文预算方案（每回合按当前配置与实测尺寸重算）。"""

    __slots__ = ("mode", "window", "budget", "replay_budget", "archive_cap",
                 "slide_keep", "min_turns", "fixed_turns", "compact",
                 "sys_tokens", "tail_tokens", "archive_tokens", "replay_tokens",
                 "replay_turns", "before_turn", "stable_tokens")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def total_tokens(self) -> int:
        return int((self.sys_tokens or 0) + (self.archive_tokens or 0)
                   + (self.replay_tokens or 0) + (self.tail_tokens or 0))

    def describe(self) -> str:
        """给日志用的一行摘要。"""
        if self.mode == "fixed":
            head = f"固定窗口{self.fixed_turns}回合"
        else:
            head = (f"窗口{_k(self.window)}·预算{_k(self.budget)}"
                    f"（{self.window and self.budget / self.window * 100:.0f}%）")
        total = self.total_tokens or 1
        hit = f"｜可命中前缀≈{self.stable_tokens / total * 100:.0f}%" if self.stable_tokens else ""
        return (f"{head}｜replay {self.replay_turns}回合{_k(self.replay_tokens)}"
                f"｜归档{_k(self.archive_tokens)}｜状态{_k(self.tail_tokens)}"
                f"｜system{_k(self.sys_tokens)}｜合计{_k(total)}{hit}")


def _k(n) -> str:
    n = int(n or 0)
    return f"{n / 1000:.1f}k" if n < 1_000_000 else f"{n / 1_000_000:.2f}M"


def make_plan(cfg: dict, *, fallback_turns: int | None = None) -> Plan:
    """从配置解析预算方案。

    ctx_window（token）是总闸：配了就走预算动态分配；只配 ctx_full_turns 或都没配
    则退回固定回合窗口（旧行为），但仍享受"归档前置 + 分块下滑"的缓存布局。
    """
    compact = str(cfg.get("ctx_compact", "llm")).lower() != "off"
    fixed = cfg.get("ctx_full_turns")
    window = cfg.get("ctx_window")
    if fixed:
        n = max(1, int(fixed))
        return Plan(mode="fixed", window=None, budget=0, replay_budget=0,
                    archive_cap=int(cfg.get("ctx_archive_max") or 60000),
                    slide_keep=DEFAULT_SLIDE_KEEP, min_turns=1, fixed_turns=n,
                    compact=compact)
    if not window:
        n = max(1, int(fallback_turns or DEFAULT_FIXED_TURNS))
        return Plan(mode="fixed", window=None, budget=0, replay_budget=0,
                    archive_cap=int(cfg.get("ctx_archive_max") or 60000),
                    slide_keep=DEFAULT_SLIDE_KEEP, min_turns=1, fixed_turns=n,
                    compact=compact)

    window = max(8192, int(window))
    fill = _fnum(cfg.get("ctx_fill"), DEFAULT_FILL, 0.05, 0.95)
    reserve = int(cfg.get("ctx_reserve_out") or (int(cfg.get("max_tokens", 4000)) + 2048))
    budget = max(8192, int(min(window * fill, window - reserve)))
    archive_cap = int(cfg.get("ctx_archive_max")
                      or max(20000, window * _fnum(cfg.get("ctx_archive_fill"),
                                                   DEFAULT_ARCHIVE_FILL, 0.0, 0.5)))
    return Plan(mode="budget", window=window, budget=budget, replay_budget=budget,
                archive_cap=archive_cap,
                slide_keep=_fnum(cfg.get("ctx_slide_keep"), DEFAULT_SLIDE_KEEP, 0.1, 0.95),
                min_turns=max(1, int(cfg.get("ctx_min_turns", DEFAULT_MIN_TURNS))),
                fixed_turns=None, compact=compact)


# ---------------------------------------------------------------------------
# 选择 replay 深度 / 裁剪存储
# ---------------------------------------------------------------------------
def select_replay(mem: list[dict], budget: int, min_turns: int) -> tuple[list[dict], int]:
    """从最新往回取整回合，直到再加一回合就超预算。返回 (保留的记录, 其 token 数)。"""
    kept: list[dict] = []
    used = 0
    for rec in reversed(mem):
        t = record_tokens(rec)
        if kept and used + t > budget:
            break
        kept.append(rec)
        used += t
    kept.reverse()
    if len(kept) < min_turns and len(mem) > len(kept):
        kept = list(mem[-min_turns:])   # 预算再紧也保底，宁可超一点
        used = sum(record_tokens(r) for r in kept)
    return kept, used


def slide(world, name: str, plan: Plan) -> list[dict]:
    """按高/低水位裁剪该国 turn_memory，返回被裁掉的记录（可能为空）。

    分块下滑：只在总量超过高水位时才裁，且一次裁到低水位——把"整段前缀失效"
    从每回合一次摊薄到很多回合一次。固定窗口模式下高水位=窗口+半窗。
    """
    mem = world.turn_memory.get(name)
    if not mem:
        return []
    if plan.mode == "fixed":
        limit = plan.fixed_turns
        chunk = max(4, limit // 2)
        if len(mem) <= limit + chunk:
            return []
        drop = len(mem) - limit
    else:
        total = sum(record_tokens(r) for r in mem)
        if total <= plan.replay_budget:
            return []
        low = int(plan.replay_budget * plan.slide_keep)
        drop, acc = 0, 0
        while drop < len(mem) - plan.min_turns and total - acc > low:
            acc += record_tokens(mem[drop])
            drop += 1
        if drop <= 0:
            return []
    dropped = mem[:drop]
    del mem[:drop]
    return dropped


# ---------------------------------------------------------------------------
# 历史归档（已滑出 replay 的回合总结）
# ---------------------------------------------------------------------------
ARCHIVE_HEAD = ("【历史归档 · 已滑出完整记录的旧回合（一行=该回合小结，"
                "「阶段总结」=多回合压缩）】")


def render_archive(sums: list[dict], blocks: list[dict], before_turn: int,
                   cap: int) -> str:
    """渲染第 <before_turn 回合的总结归档；超配额从最旧截断并标注。

    只依赖 (sums, blocks, before_turn, cap) 四者，两次下滑之间输入不变 → 输出字节
    不变 → 整段可命中前缀缓存。
    """
    if before_turn <= 1:
        return ""
    blocks = [b for b in (blocks or []) if int(b.get("to", 0)) < before_turn]
    sums = [s for s in (sums or []) if int(s.get("turn", 0)) < before_turn]
    if not blocks and not sums:
        return ""
    covered: set[int] = set()
    for b in blocks:
        covered.update(range(int(b["from"]), int(b["to"]) + 1))
    items: list[tuple[int, str]] = []
    for b in blocks:
        items.append((int(b["from"]),
                      f"【第{b['from']}-{b['to']}回合 · 阶段总结】\n{str(b.get('text', '')).strip()}"))
    for s in sums:
        if int(s["turn"]) not in covered:
            items.append((int(s["turn"]), f"  第{s['turn']}回合：{s['text']}"))
    items.sort(key=lambda x: x[0])
    kept: list[tuple[int, str]] = []
    used = 0
    omitted = 0
    for turn, text in reversed(items):
        t = est_tokens(text)
        if kept and used + t > cap:
            omitted = len(items) - len(kept)
            break
        kept.append((turn, text))
        used += t
    kept.reverse()
    # 抬头写死、回合区间与省略说明压到尾部：归档只在下滑时"追加"，这样旧归档的字节
    # 是新归档的前缀 → 下滑那一回合归档本身仍可命中（区间行是唯一失效的几行）。
    lines = [ARCHIVE_HEAD]
    lines.extend(t for _, t in kept)
    tail = f"（归档截至第 {before_turn - 1} 回合"
    if omitted:
        tail += f"；更早的 {omitted} 条总结因配额已省略"
    lines.append(tail + "）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 消息组装
# ---------------------------------------------------------------------------
def merge_same_role(msgs: list[dict]) -> list[dict]:
    """合并相邻同角色消息（不同 OpenAI 兼容端点对严格角色交替要求不一）。

    - user×user 合并（join content）；
    - 无 tool_calls 的 assistant×assistant 合并（content 与 reasoning_content 各自 join）；
    - 绝不合并 tool 消息（每条绑定唯一 tool_call_id）；
    - 绝不合并带 tool_calls 的 assistant（其 tool 响应必须紧随其后）。
    """
    out: list[dict] = []

    def _join(x, y):
        a = str(x or "").strip()
        b = str(y or "").strip()
        return (a + "\n\n" + b) if a and b else (a or b)

    for m in msgs:
        role = m.get("role")
        if out and out[-1].get("role") == role:
            last = out[-1]
            if role == "tool":
                out.append(dict(m))
                continue
            if role == "user":
                last["content"] = _join(last.get("content"), m.get("content"))
                continue
            if role == "assistant" and not last.get("tool_calls") and not m.get("tool_calls"):
                last["content"] = _join(last.get("content"), m.get("content"))
                if last.get("reasoning_content") or m.get("reasoning_content"):
                    last["reasoning_content"] = _join(last.get("reasoning_content"),
                                                      m.get("reasoning_content"))
                continue
        out.append(dict(m))
    return out


def assemble(system_text: str, archive_text: str, records: list[dict],
             tail_text: str) -> list[dict]:
    """按缓存稳定性顺序拼消息：system → 归档 → replay → 本回合状态。"""
    msgs: list[dict] = [{"role": "system", "content": system_text}]
    if archive_text:
        msgs.append({"role": "user", "content": archive_text})
    for rec in records:
        msgs.extend(rec.get("messages") or [])
    if tail_text:
        msgs.append({"role": "user", "content": tail_text})
    return merge_same_role(msgs)


def build(*, cfg: dict, mem: list[dict], sums: list[dict], blocks: list[dict],
          system_text: str, tail_text: str, tool_tokens: int = 0,
          fallback_turns: int | None = None) -> tuple[list[dict], Plan]:
    """组装一国本回合的完整上下文，并回填预算/用量统计到 Plan。

    两遍选深度：先按归档占满配额估 replay，再按归档实际大小回补——归档通常远小于
    配额，回补后能多塞几个回合。
    """
    plan = make_plan(cfg, fallback_turns=fallback_turns)
    plan.sys_tokens = est_tokens(system_text) + _MSG_OVERHEAD
    plan.tail_tokens = est_tokens(tail_text) + _MSG_OVERHEAD
    overhead = plan.sys_tokens + plan.tail_tokens + int(tool_tokens or 0) + SAFETY_TOKENS

    last_turn = int(mem[-1]["turn"]) if mem else 0
    before = last_turn + 1

    if plan.mode == "fixed":
        records = list(mem[-plan.fixed_turns:]) if mem else []
        plan.replay_tokens = sum(record_tokens(r) for r in records)
        before = int(records[0]["turn"]) if records else last_turn + 1
        archive_text = render_archive(sums, blocks, before, plan.archive_cap)
        plan.archive_tokens = est_tokens(archive_text)
    else:
        avail = max(2048, plan.budget - overhead)
        for _ in range(2):   # 两遍收敛：先按归档占满配额估，再按归档实际大小回补
            arch = plan.archive_cap if plan.archive_tokens is None else plan.archive_tokens
            plan.replay_budget = max(2048, avail - arch)
            records, plan.replay_tokens = select_replay(mem, plan.replay_budget, plan.min_turns)
            before = int(records[0]["turn"]) if records else last_turn + 1
            archive_text = render_archive(sums, blocks, before, plan.archive_cap)
            plan.archive_tokens = est_tokens(archive_text)

    plan.replay_turns = len(records)
    plan.before_turn = before
    # 稳态可命中前缀 = system + 归档 + 除最后一回合外的 replay（下一回合这两段字节不变；
    # 下滑那一回合除外——那时 replay 整体位移，前缀全废）
    last = record_tokens(records[-1]) if records else 0
    plan.stable_tokens = (int(plan.sys_tokens + plan.archive_tokens
                              + max(0, plan.replay_tokens - last)))
    return assemble(system_text, archive_text, records, tail_text), plan
