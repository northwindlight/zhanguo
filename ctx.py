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
- ★ **replay 必须逐字节等于当时发出去的历史原文**：`mp_ai._store_turn_memory` 存的是
  `messages[base-1:]`（含本回合末尾那条状态消息），不是"重排/改写"过的副本。任何
  重写上一回合开头的存法（例如给记录塞一行自造的回合标记占位）都会让**上一回合的整条
  记录永远 miss**——2026-09-19 实测：262k 窗口/每回合 4 次调用下命中被压在 87.7%、
  每回合白付 ≈7k tok（分歧点每次都精确落在那一行占位符上）。存原文后，下回合请求就是
  上回合请求的**逐字节延长**；代价是 replay 每回合多一条状态（≈2.9k tok）。
- 本回合状态永远是新内容，天然 miss，放最后。

怎样把命中率从 ~85% 推到 95%+（2026-09-15 实测结论）
---------------------------------------------------
前缀缓存的唯一杠杆是：请求字节与上次请求尽量共用同一个前缀。实测命中≈85% 的
根因通常是**逐回合 replay 记录体积大**（完整 thinking + 大工具返回）⇒ 滑动频繁
（每 5~10 回合一次）⇒ 每次滑动都把整段后缀（replay+状态）打进 cache miss。

★ **另有两条与体积无关的根因，别只盯 replay 大小**（2026-09-19 实测补记）：
  (a) **replay 若不是历史原文，每回合都会白丢一整段**——见上面「缓存布局」那条 ★；
  (b) **每次压缩（超水位裁剪）那一回合，前缀整段重建**：裁剪让 `before_turn`（= replay
      起点）往前跳一格，归档随之在尾部插入新行、"截至第 N 回合"也变；归档压在 replay
      之前 ⇒ 它后面的一切（整个 replay + 状态）全 miss。实测（262k 窗口、每回合 12 次
      调用、每回合记录 24.2k）：压缩每 **4** 回合来一次（第 69/73/77/81/85/89/93 回合命中
      **7.6%**，夹在中间的回合 82~87%）——摊下来整轮少 ≈20 个百分点。
      **压缩间隔 ≈ (1 − ctx_slide_keep) × replay_budget ÷ 每回合记录体积**。所以"少压"
      的三个杠杆是：窗口 ↑、每回合体积 ↓、`ctx_slide_keep` **↓**（每压一次多丢一点）。
      ⚠ 反过来把 `ctx_slide_keep` 调高（想要"填满才压、压得少"）会让压缩变成**每回合一次**
      ——每个回合都全段重建，那是最坏的配置。

1. **别剥旧回合的 thinking——那是短视的元凶（教训，2026-09-15 重写）**。
   `ctx_old_reasoning="strip"` 会把旧回合的推理换占位，而推理里往往装着模型的
   长程计划；剥掉后模型会变得只看得到最近几步。**酒馆（SillyTavern）从不这么干**：
   它要么让旧消息完整退出上下文换成一条**递归累积的摘要**（"If a summary already
   exists, use that as a base and expand"），要么用向量库检索精确事实。
   正解对齐这里：**旧回合整体退出 replay + 递归长期记忆承接长程**——
   `ctx_roll="period"`（非滑动定点压缩）+ `world.long_memory`（压缩时以旧记忆
   为基础扩写，注入为归档最前的【长期记忆】稳定块）。`ctx_old_reasoning` 仅作
   极端瘦档备用，默认 `"full"` 不再推荐。
2. **诚实地看数**：`🧠` 行带 provider 实测滚动命中率（`record_hit`/`rolling_hit`），
   别被 `describe()` 的"可命中前缀"误导——那是不含滑动摊销的理论上限。
3. **滑动降频降本**：水位判定用瘦身后的体积（见 `slide`），`ctx_slide_keep` 可调
   到 0.4~0.5（更低=滑动更稀、代价更小，但近期上下文变薄）。
4. （可选）模型支持就把 `ctx_window` 开大：同样回合数容纳更多，滑动天然更稀。

可选的「非滑动 + 定点压缩」模式（`ctx_roll="period"`，2026-09-15 加入）
------------------------------------------------------------------
滑动是命中率的最大敌人：每次滑动都要重建整段后缀。`ctx_roll="period"` 提供一条
更激进的路线——**两次压缩之间 replay 纯追加，前缀一个字节不动**：

- `ctx_period`：压缩周期，**不配 = 自动**（余量 ÷ 实测每回合体积倒推）；配数字 =
  手动固定。触发的唯一条件是「到点」或「超水位兜底」，其余回合 `slide()` 直接放行、
  绝不动 mem → 请求对上一次请求是纯 append，命中≈97%。
- `ctx_slice_keep`：**已弃用**（period 现与 slide 共用同一份预算分配，不再把 replay
  压缩成半片——半片会白白浪费稳定前缀）。自动周期 = 余量（预算−低位）÷ 实测每回合
  体积；手动 `ctx_period` 若过大导致周期内超水位，`slide` 会兜底压缩而非逐回合
  从请求头裁切。
- 压缩那一回合的前缀重建，等价于把冷回合成本摊到"每 period 回合一次"，
  比滑动模式的"每 8~15 回合一次"低一个数量级。
- ⚠ **实际间隔 ≠ `ctx_period`，而是 min(period, (1−`ctx_slide_keep`)×replay_budget ÷
  每回合体积)**：超水位那条路先触发时，period 只是个上限。实测（262k 窗口、每回合
  12 次调用、每回合记录 24.2k）：水位余量两回合就被填满 ⇒ **每 4 回合压一次**，
  压缩那回合命中 7.6%、其余 82~87% ⇒ 整轮被摊掉 ≈20 个百分点。"命中≈97%"只在
  **每回合体积远小于水位余量**的瘦回合档成立（想让压缩变稀：窗口 ↑ / 每回合体积 ↓ /
  `ctx_slide_keep` ↓；把 `ctx_slide_keep` 调高会让压缩变成每回合一次，最坏）。
- 升级版（②记忆组件）：把压缩周期间隔拉长、并让该国 AI 用自己的口吻写
  【长期记忆】（教训/盟约/目标），即"到点让国家自己压缩"——这里留给上层接线。

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


def _shrink_messages(msgs: list[dict], *, keep_reasoning: bool, tool_trim: int) -> list[dict]:
    """读路径瘦身单条记录的消息列表：只替换 reasoning 占位 / 截 tool 超长内容。"""
    out = []
    for m in msgs:
        nm = dict(m)
        if not keep_reasoning and nm.get("role") == "assistant" and nm.get("reasoning_content"):
            nm["reasoning_content"] = "（思考过程已并入历史归档，细节以 rules/面板为准）"
        if tool_trim and nm.get("role") == "tool":
            c = nm.get("content") or ""
            if len(c) > tool_trim:
                nm["content"] = c[:tool_trim] + f"…[已截断{len(c)}字]"
        out.append(nm)
    return out


def shrink_records(records: list[dict], *, newest_turn: int,
                   old_reasoning: str = "full", tool_trim: int = 0) -> list[dict]:
    """组装请求前的读路径瘦身：只保留最近 1 回合完整 thinking，更旧换占位；
    tool 超长内容截断。**只看不改存档**，切回 "full" 即恢复全量。"""
    if old_reasoning == "full" and not tool_trim:
        return list(records)
    if not records:
        return []
    out = []
    for rec in records:
        keep = (old_reasoning == "full" or int(rec.get("turn") or 0) >= newest_turn)
        ms = _shrink_messages(rec.get("messages") or [], keep_reasoning=keep,
                              tool_trim=tool_trim)
        out.append({**rec, "messages": ms})
    return out


def send_tokens(rec: dict, *, newest_turn: int,
                old_reasoning: str = "full", tool_trim: int = 0) -> int:
    """瘦身后的单条记录体积（slide 判水位、build 计量都用它）。"""
    sr = shrink_records([rec], newest_turn=newest_turn,
                        old_reasoning=old_reasoning, tool_trim=tool_trim)
    return record_tokens(sr[0]) if sr else 0


# provider 实测命中率的滚动平均（近 20 次请求），供 🧠 日志诚实展示
_HIT_HIST: dict[str, list[tuple[int, int]]] = {}


def record_hit(name: str, hit: int, miss: int) -> None:
    lst = _HIT_HIST.setdefault(name, [])
    lst.append((int(hit or 0), int(miss or 0)))
    del lst[:-20]


def rolling_hit(name: str) -> float | None:
    h = m = 0
    for a, b in _HIT_HIST.get(name, []):
        h += a
        m += b
    return (h / (h + m)) if (h + m) else None


def _last_compact_turn(world, name: str) -> int:
    """该国上一次压缩库的回合（读 summary_blocks 里最后一个块的记录时刻）。"""
    bl = world.summary_blocks.get(name) or []
    return int(bl[-1]["turn"]) if bl and bl[-1].get("turn") else 0


def _effective_period(replay_budget: int, slide_keep: float, avg_turn: float) -> int:
    """自动压缩周期 = 把切片从"低位"回填到"高位"所需的回合数。

    高位=budget×slice_keep（period 模式的 replay_budget），低位=高位×slide_keep；
    avg_turn=实测的每回合记录体积（瘦身后）。由配置的窗口长度与实测回合密度共同决定。
    """
    low = int(replay_budget * slide_keep)
    head = max(1, replay_budget - low)
    return max(6, min(120, int(head / max(1.0, avg_turn))))


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
            "ctx_full_turns", "ctx_reserve_out", "small_ctx",
            "ctx_old_reasoning", "ctx_trim_tool_chars",
            "ctx_roll", "ctx_period", "ctx_slice_keep")


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
                 "replay_turns", "before_turn", "stable_tokens",
                 "old_reasoning", "tool_trim", "roll", "period", "slice_keep",
                 "effective_period")

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
        if self.roll == "period":
            head += (f"·period每{self.period}回合压缩"
                     if self.period else
                     f"·period自动≈每{self.effective_period or '?'}回合")
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
    old_reasoning = "strip" if str(cfg.get("ctx_old_reasoning", "full")).lower() != "full" else "full"
    tool_trim = max(0, int(cfg.get("ctx_trim_tool_chars") or 0))
    roll = "period" if str(cfg.get("ctx_roll", "slide")).lower() in ("period", "periodic", "none") else "slide"
    period = max(0, int(cfg.get("ctx_period") or 0))   # 0=自动（窗口几何+实测回合体积推导）；>0=手动固定周期
    slice_keep = _fnum(cfg.get("ctx_slice_keep"), 0.55, 0.2, 0.9)
    fixed = cfg.get("ctx_full_turns")
    window = cfg.get("ctx_window")
    if fixed:
        n = max(1, int(fixed))
        return Plan(mode="fixed", window=None, budget=0, replay_budget=0,
                    archive_cap=int(cfg.get("ctx_archive_max") or 60000),
                    slide_keep=DEFAULT_SLIDE_KEEP, min_turns=1, fixed_turns=n,
                    compact=compact, old_reasoning=old_reasoning, tool_trim=tool_trim,
                    roll=roll, period=period, slice_keep=slice_keep)
    if not window:
        n = max(1, int(fallback_turns or DEFAULT_FIXED_TURNS))
        return Plan(mode="fixed", window=None, budget=0, replay_budget=0,
                    archive_cap=int(cfg.get("ctx_archive_max") or 60000),
                    slide_keep=DEFAULT_SLIDE_KEEP, min_turns=1, fixed_turns=n,
                    compact=compact, old_reasoning=old_reasoning, tool_trim=tool_trim,
                    roll=roll, period=period, slice_keep=slice_keep)

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
                fixed_turns=None, compact=compact,
                old_reasoning=old_reasoning, tool_trim=tool_trim,
                roll=roll, period=period, slice_keep=slice_keep)


# ---------------------------------------------------------------------------
# 选择 replay 深度 / 裁剪存储
# ---------------------------------------------------------------------------
def select_replay(mem: list[dict], budget: int, min_turns: int,
                   size_fn=record_tokens) -> tuple[list[dict], int]:
    """从最新往回取整回合，直到再加一回合就超预算。返回 (保留的记录, 其 token 数)。

    size_fn：按`瘦身后体积`挑选（启用 ctx_old_reasoning/tool_trim 时把省出的预算
    再买更多回合 ⇒ replay 保持大而稳定、滑动更稀，是命中上 95% 的关键一步）。
    """
    kept: list[dict] = []
    used = 0
    for rec in reversed(mem):
        t = size_fn(rec)
        if kept and used + t > budget:
            break
        kept.append(rec)
        used += t
    kept.reverse()
    if len(kept) < min_turns and len(mem) > len(kept):
        kept = list(mem[-min_turns:])   # 预算再紧也保底，宁可超一点
        used = sum(size_fn(r) for r in kept)
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
        newest = mem[-1]["turn"] if mem else 0
        if plan.old_reasoning == "full" and not plan.tool_trim:
            sizes = [record_tokens(r) for r in mem]
        else:
            sizes = [send_tokens(r, newest_turn=newest,
                                 old_reasoning=plan.old_reasoning,
                                 tool_trim=plan.tool_trim) for r in mem]
        total = sum(sizes)
        if plan.roll == "period":
            # 非滑动周期模式：只在「到期」或「已超水位兜底」那个回合裁剪，
            # 其余回合绝不动前缀（纯追加，命中≈97%）
            eff = plan.effective_period or 6
            if plan.period:
                due = (world.turn % plan.period == 0)
            else:
                due = (world.turn - _last_compact_turn(world, name) >= eff)
            if not due and total <= plan.replay_budget:
                return []
        if total <= plan.replay_budget:
            return []
        low = int(plan.replay_budget * plan.slide_keep)
        drop, acc = 0, 0
        while drop < len(mem) - plan.min_turns and total - acc > low:
            acc += sizes[drop]
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
                   cap: int, long_memory: str = "") -> str:
    """渲染第 <before_turn 回合的总结归档；超配额从最旧截断并标注。

    long_memory：各国**递归累积的长期记忆**（酒馆式：压缩回合以旧记忆为基础
    扩写），永远置于归档**最前**——只在压缩回合变字节，两次之间整段命中缓存，
    并承接长程计划/盟约/教训（解决"丢旧回合后变短视"）。
    两次收缩之间 (sums,blocks,before_turn,cap,long_memory) 输入不变 → 输出字节不变。
    """
    if before_turn <= 1 and not long_memory:
        return ""
    blocks = [b for b in (blocks or []) if int(b.get("to", 0)) < before_turn]
    sums = [s for s in (sums or []) if int(s.get("turn", 0)) < before_turn]
    if not blocks and not sums and not long_memory:
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
    lines = []
    if long_memory:
        lines.append("【长期记忆 · 递归累积（压缩时以旧扩写：长程计划/盟约/教训）】\n" + long_memory)
    if blocks or sums:
        lines.append(ARCHIVE_HEAD)
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
          fallback_turns: int | None = None, long_memory: str = "") -> tuple[list[dict], Plan]:
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
        archive_text = render_archive(sums, blocks, before, plan.archive_cap, long_memory)
        plan.archive_tokens = est_tokens(archive_text)
    else:
        # 预算模式：slide 与 period **共用同一份预算分配**（replay 顶满请求，别让
        # 稳定前缀变小）。period 只在「何时修剪」上有别（固定周期/自动 vs 超水位）。
        avail = max(2048, plan.budget - overhead)
        if plan.old_reasoning != "full" or plan.tool_trim:
            size_fn = (lambda r: send_tokens(r, newest_turn=last_turn,
                                             old_reasoning=plan.old_reasoning,
                                             tool_trim=plan.tool_trim))
        else:
            size_fn = record_tokens
        for _ in range(2):   # 两遍收敛：先按归档占满配额估，再按归档实际大小回补
            arch = plan.archive_cap if plan.archive_tokens is None else plan.archive_tokens
            plan.replay_budget = max(2048, avail - arch)
            records, plan.replay_tokens = select_replay(mem, plan.replay_budget,
                                                        plan.min_turns, size_fn=size_fn)
            before = int(records[0]["turn"]) if records else last_turn + 1
            archive_text = render_archive(sums, blocks, before, plan.archive_cap, long_memory)
            plan.archive_tokens = est_tokens(archive_text)
        if plan.roll == "period" and not plan.period:   # 自动周期 = 余量 ÷ 实测回合体积
            k = min(4, len(mem))
            avg = (sum(size_fn(r) for r in mem[-k:]) / k) if k else 0.0
            plan.effective_period = _effective_period(plan.replay_budget, plan.slide_keep, avg)

    if plan.old_reasoning != "full" or plan.tool_trim:
        records = shrink_records(records, newest_turn=max(0, last_turn),
                                 old_reasoning=plan.old_reasoning, tool_trim=plan.tool_trim)
        plan.replay_tokens = sum(record_tokens(r) for r in records)

    plan.replay_turns = len(records)
    plan.before_turn = before
    # 稳态可命中前缀 = system + 归档 + 除最后一回合外的 replay（下一回合这两段字节不变；
    # 下滑那一回合除外——那时 replay 整体位移，前缀全废）
    last = record_tokens(records[-1]) if records else 0
    plan.stable_tokens = (int(plan.sys_tokens + plan.archive_tokens
                              + max(0, plan.replay_tokens - last)))
    return assemble(system_text, archive_text, records, tail_text), plan
