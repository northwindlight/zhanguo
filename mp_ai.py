"""模型会话与上下文接线；玩家内容由加载的 Mod 提供。"""
from __future__ import annotations
import copy
import json
import re
import threading
from pathlib import Path
import ctx as ctxlib
from ctx import est_tokens
from llm_provider import REPLAY_KEY, make_backend
import rule_ai as rule_ai_registry
from mp import runtime
from mods.native.presentation import *
_engine_lock = threading.RLock()
def engine_call(fn, *args, **kwargs):
    with _engine_lock:
        return fn(*args, **kwargs)


def execute(world, actor: str, tool: str, args: dict) -> str:
    """执行一个工具调用并返回结果文本（喂回给 LLM）。所有机制校验都在引擎内完成。

    顶层兜底：单次工具调用参数非法或内部异常只记为一次失败返回给模型，
    绝不让异常穿出回合循环炸掉整个看海进程（结算前的行动会全部丢失）。
    """
    if actor not in world.nations:
        return "（你已亡国/不存在）"
    try:
        return _exec(world, actor, tool, args)
    except Exception as e:
        return (f"⚠ 工具 {tool} 执行出错，本次调用未生效（参数可能非法）；"
                f"请检查后用合法参数重试）：{type(e).__name__}: {e}")

def _exec(world, actor: str, tool: str, args: dict) -> str:
    """兼容入口；工具由加载的 Mod 注册并自动转换。"""
    result = world.call_tool(actor, tool, args)
    return result[1] if isinstance(result, tuple) and len(result) == 2 else result

def log_tool(world, actor, tool, args, result):
    """把一次工具调用写进看海日志（Observer 能看到每个行动）。"""
    def _fmt(v):
        return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    a = args or {}
    a_str = " ".join(f"{k}={_fmt(v)}" for k, v in a.items())
    # ★ 坐标**成对**解析（2026-09-23 修）：move/attack/retreat 的 x/y 虽是 required，
    #   但函数调用参数由模型给，**不保证齐全**——只给 x 不给 y 时旧写法（各自独立判 None）
    #   会留下半个坐标下传，`_stamp_seen` → `visible_to` → `neighbors` 里 `None + int`
    #   直接崩掉（整个回合都跑不完）。缺一个就当没有坐标：日志文本原样留（给人看），
    #   只把坐标置空走"无坐标"通道。
    x = y = None
    if a.get("x") is not None and a.get("y") is not None:
        try:
            x, y = int(a["x"]) - 1, int(a["y"]) - 1
        except (TypeError, ValueError):
            x = y = None
    world.action(actor, tool, a_str, result[:180], x=x, y=y)

def system_prompt(world, name) -> str:
    rt = getattr(world, 'runtime', runtime)
    p = rt.actor_prompt(world, name, _default_system_prompt)

    _at_war = getattr(world, "at_war", None)
    if callable(_at_war) and _at_war(name):
        p += "\n\n【战争手册（你在交战，每回合必读；停战后可用 rules(战争手册) 取回）】\n" + war_manual()
    if getattr(world, "opening_guide", False) and world.turn < EXTRA_PROMPT_TURNS:
        p += (f"\n\n【基础指南（开局前 {EXTRA_PROMPT_TURNS} 回合常驻；"
              f"过期后仍可用 rules(基础指南) 取回）】\n" + opening_guide())
    ep = world.extra_prompt.get(name)
    if ep:
        if world.turn < ep.get("until", world.turn):
            p += f"\n\n【临时情报/密谕（{EXTRA_PROMPT_TURNS}回合后仅剩总结）】\n" + ep.get("text", "")
        elif ep.get("summary"):
            p += "\n\n【遗留总结（前情之鉴，常驻）】\n" + ep["summary"]
    # ★ 行动纪律（2026-09-20 用户「ok 听你的改」）：
    #   ① 逼它先推理——实测该模型对"例行"提示只想 ~100 字、对难题想 ~1k 字（网关无关）；
    #   ② 允许并行调用——不要求它就一次一个（12 个动作 = 12 次往返，又慢又贵），
    #      实测明确要求时会一次发多个（见 2026-09-20 探针）。
    p += ("\n\n【行动纪律】① 动手前先在思考里过一遍：**我掌握的事实 → 推断 → 本回合要做什么"
          "（照国策）**，再发动作。② **一轮回复里可以同时发多个工具调用**：互不依赖的动作"
          "（查面板、多城动工、多军调防）请并行发出，别一个一个来回；只有需要看上一步结果"
          "才能决定的事，才分下一轮。")
    p += "\n\n【过往回合记录（含你的思考过程）仅作参考背景】勿重放旧命令/旧工具调用，一切以最新当前回合状态为准，按本回合行动。"
    return p

def _default_system_prompt(world, name) -> str:
    return (
        "你是国家元首【" + name + "】，在一个 EU4 式大地图战略游戏里治国。\n\n"
        "这局没有预设目标：富国、拓荒、称霸、报复、苟和都行，由你自己判断；每种选择都有后果，后果也由你承担。\n"
        "【怎么玩】每回合你可用工具行动：建设/拓荒/征兵/调兵/打仗/买卖/外交/写信/结盟。"
        "开始前定一份【国策规划】（plan），行动完用 end_turn 结束本回合并附一句回合小结（国策需定期修订）。\n"
        "【规则去哪查】所有玩法细节（资源/造价/战斗/市场/外交/联盟）都不必记住：调用 rules 随时可查权威说明"
        "（可带主题，如 rules(建筑)、rules(战争)）；本国现状用 query 看面板（res/land/army/market/countries…）。\n"
        "【信息】情报有迷雾：你只看得见自己地盘与相邻一圈（有联盟则连盟友的地盘也看得到）；"
        "他国国情你看不到，只能从来信、边界动静与其言行推断；他国来信未必可信，你也可说谎。\n"
        "【建议】动手前先 query 看面板、rules 查规则，想清楚再做。没有『应该』怎么做，只有你想要什么后果。"
    )

def normalize_cfg(cfg: dict) -> dict:
    """补全上下文配置（就地）。small_ctx 是旧键：等价于按 256k 窗口标定。"""
    if cfg.get("small_ctx") and not cfg.get("ctx_window"):
        cfg["ctx_window"] = 262144
    return cfg

def _ctx_parts(world, name) -> tuple[list, list, list]:
    return (world.turn_memory.get(name) or [],
            world.summaries.get(name) or [],
            world.summary_blocks.get(name) or [])

TURN_STATE_HEAD = "以上为过往回合记录"

def turn_state(world, name, replay_since: int | None = None, map_text: str | None = None) -> str:
    """本回合的新鲜状态（消息尾部）。replay_since=已进 replay 的最早回合，
    用于把状态面板里重复的内容去掉（见 _fmt_memory/_fmt_news/_fmt_mail）。

    ★这条消息**原样进 replay**（见 `_store_turn_memory`）：它是本回合请求的最后一条，
    下回合必须按同一串字节把它摆在 replay 里，请求才是"上一次请求 + 新状态"的纯追加。"""
    return (f"{TURN_STATE_HEAD}，现在开始第 {world.turn} 回合行动。\n"
            + engine_call(full_state, world, name, replay_since, map_text))

def build_context(world, name, cfg, *, emit=None) -> tuple[list[dict], "ctxlib.Plan"]:
    """构造一国本回合的完整 LLM 上下文（预算分配与缓存布局见 ctx.py）。

    顺序：system → 历史归档（已滑出回合的总结）→ replay（窗口内完整回合，含思考）
    → 本回合状态。返回 (messages, plan)，plan 供日志与下滑裁剪使用。

    状态面板的去重边界依赖 replay 起点，而 replay 深度又依赖状态大小——先按不去重
    （更保守）估一遍拿到起点，再用真实边界重建一次。
    """
    normalize_cfg(cfg)
    system_text = engine_call(system_prompt, world, name)
    mem, sums, blocks = _ctx_parts(world, name)
    map_text = None
    tail_map = None
    # 开关默认关闭；只有确认当前图能画出来，才移除逐格 atlas。
    # 说明在游戏上下文直接注入，图里不塞文字图例。
    if cfg.get("vision") is True:
        import render as map_render
        try:
            facts = engine_call(map_render.snapshot, world, name)
            max_px = max(240, min(1800, int(cfg.get("map_max_px", 960))))
            if map_render.render_snapshot(facts, max_px=max_px, emit=emit) is not None:
                map_text = ("本回合空间地图以图片提供；看形状、前线、地形与军队位置。"
                            "精确地块/国别/番号/HP用 query panel=tile/land/army/battle。"
                            "图片是回合开始时的快照；本回合行动后用 query 查最新状态。")
                tail_map = {"facts": facts, "max_px": max_px,
                            "estimated_tokens": max(1, int(cfg.get("map_image_tokens", 2048))),
                            "fallback": engine_call(_fmt_atlas, world, name)}
                # 放在 system 的稳定后缀，不改变每回合色表、阵营语义。
                system_text += "\n【图片地图读法】\n" + map_render.legend_text(world, name)
        except Exception as exc:
            map_render._warn_once(emit, f"{type(exc).__name__}: {str(exc)[:160]}")

    def _build(tail: str):
        return ctxlib.build(cfg=cfg, mem=mem, sums=sums, blocks=blocks,
                            system_text=system_text, tail_text=tail,
                            tool_tokens=TOOL_SCHEMAS_TOKENS,
                            long_memory=world.long_memory.get(name, ""), tail_map=tail_map)

    tail1 = turn_state(world, name, None, map_text)
    msgs, plan = _build(tail1)
    tail2 = turn_state(world, name, plan.before_turn, map_text)
    if tail2 != tail1:
        msgs, plan = _build(tail2)
    return msgs, plan

def _store_turn_memory(world, name, messages, base: int, plan) -> list[dict]:
    """把本回合**真正发给模型的那段原文**存入该国 turn_memory，并按预算下滑。

    存 `messages[base-1:]`：base 是 build_context 返回的长度，所以 `messages[base-1]`
    就是本回合那条状态消息（`turn_state`），其后是本回合的工具往返。base 之前是历史
    replay，不重复存（否则记录间二次方膨胀）。完整存，不截断。
    返回被裁掉的旧回合记录（调用方可据此生成阶段块总结）。

    ★★ 为什么不能用一行自造的回合标记当首条（2026-09-19 修，实测）：
    旧实现存 `[{"role":"user","content":"【第N回合 行动记录】"}] + messages[base:]`——
    等于**把上一回合的发文开头改写掉**。于是下一回合的请求不是上一回合请求的延长，
    而是在"上一回合开头"处就分叉：

        上一次请求 … | user: 以上为过往回合记录，现在开始第 87 回合行动… （真状态）
        本次请求   … | user: 【第87回合 行动记录】                    （占位符）→ 后面全 miss

    后果就是**上一回合的整条记录（思考 + 工具往返）永远进不了前缀缓存，每回合白付一遍**；
    60×60 两国、262k 窗口、每回合 4 次调用的实测（第 62→88 回合）：命中 46.8%→95.1%，
    分歧点每一次都精确落在占位符那一行，每回合固定 miss ≈ 上一回合记录 7.0k + 状态 2.8k。
    存原文后，下回合请求 = 上回合请求的**逐字节延长**，命中由"窗口多大"决定而非被自己砍掉。
    代价：replay 每回合多存一条状态（实测 ≈2.9k tok，窗口填得略快，压缩周期略短）。
    """
    if name not in world.nations:
        return []
    body = [dict(m) for m in messages[max(0, base - 1):]]
    mem = world.turn_memory.setdefault(name, [])
    mem.append({"turn": world.turn, "messages": body})
    return ctxlib.slide(world, name, plan)

COMPACT_INSTRUCTION = (
    "你是战略游戏 AI 的【长期记忆维护者】。上面是本国更早回合的记录（大多为原文，"
    "含当时的思考与工具往返；若是逐回合小结，会另有说明），"
    "其中【长期记忆】那一块是上一次维护出来的版本。"
    "请以【长期记忆】为基础，把这些更早经历合并扩写进去：保留所有仍然成立的"
    "旧事实（战略处境、盟约、承诺、威胁、未了结事务、目标、教训），补充新进展，"
    "删除已过期的条目。不要重写、不要丢旧事实、不要评价、不要虚构、不要写建议。"
    "输出为更新后的完整长期记忆（300~600 字，可略超以容纳关键细节）。"
    "只输出这段记忆正文：不要调用任何工具，不要写标题，不要复述上面的记录。"
)

COMPACT_INPUT_CHARS = 120_000

COMPACT_REPLAY_TOKENS = 120_000

def _compact_input(dropped: list[dict], sums: list[dict], from_turn: int) -> str:
    """拼压缩调用的**降级稿**：逐回合小结 + 行动记录（剥思考——不带 tools 时该字段被忽略）。

    2026-10-02 起它只用在**溢出**那条路上：逐字回放的前缀超了 `COMPACT_REPLAY_TOKENS`
    时，砍掉的那部分旧回合（前缀性质要求只能砍尾部）用这份小结补在指令之前。
    正常体量下根本不走这里——原文回放既准又（命中缓存）便宜。
    """
    lines: list[str] = []
    by_turn = {int(s["turn"]): str(s.get("text", "")) for s in sums}
    for rec in dropped:
        t = int(rec["turn"])
        lines.append(f"—— 第{t}回合 ——")
        if by_turn.get(t):
            lines.append("小结：" + by_turn[t])
        for m in rec.get("messages") or []:
            role = m.get("role")
            if role == "assistant":
                if m.get("content"):
                    lines.append("发言：" + str(m["content"]))
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    lines.append(f"行动：{fn.get('name')}({fn.get('arguments')})")
            elif role == "tool":
                txt = str(m.get("content") or "")
                lines.append("结果：" + (txt if len(txt) <= 800 else txt[:800] + "…"))
    text = "\n".join(lines)
    if len(text) > COMPACT_INPUT_CHARS:
        # 超长时保尾部（较新的回合信息更重要），头部留个说明
        head = f"（第{from_turn}回合起的更早细节因长度已省略）\n"
        text = text[-COMPACT_INPUT_CHARS:]
        text = head + text
    return text

def _compact_prefix(messages: list[dict], plan, dropped: list[dict],
                    budget: int | None = None) -> tuple[list[dict], list[dict]]:
    """切出「上一次请求的前缀」，返回 (逐字前缀消息, 没能逐字回放的旧回合)。

    两条纪律，缺一不可：

    ① **只有"本回合请求里真的带着"的旧回合才能逐字回放**。`slide` 是按低水位裁的，
       被它裁掉的回合里，只有 `turn >= plan.before_turn` 的那些当初真的进了这次请求
       （更老的早就被预算挡在请求之外了）；把不在请求里的回合硬塞进来，前缀性质当场
       就没了。不在请求里的那部分走小结（`_compact_input`）补在指令之前。
    ② **切点让 `ctxlib.replay_head` 数，字节取 `messages` 的切片**。`replay_head` 复刻了
       `assemble` 的同一套合并规则——归档(user) 与请求里第一条回合记录的首条(user) 被
       合成**一条**消息。合并只改变条数、不改变顺序，所以"从请求的第一条记录起、数到
       最后一条要回放的记录"的条数，就是真请求里的下标。自己重渲染成两条独立 user
       消息会让命中断在归档末尾（正好是被压区间的起点）。

    超预算时**只砍尾部**（砍掉的旧回合也进小结）：砍头会毁掉前缀性质，那等于把这次
    改造的意义整个丢掉。认不出布局（首条不是 system / 没有 plan）时退回"全走小结"。
    """
    if not messages or messages[0].get("role") != "system" or plan is None:
        return [], list(dropped)
    budget = COMPACT_REPLAY_TOKENS if budget is None else budget
    sys_text = messages[0].get("content") or ""
    arch = getattr(plan, "archive_text", None) or ""
    before = getattr(plan, "before_turn", None)
    start = 0
    if before is not None:
        while start < len(dropped) and int(dropped[start]["turn"]) < int(before):
            start += 1
    present = dropped[start:]
    keep = len(present)
    while True:
        cut = len(ctxlib.replay_head(sys_text, arch, present[:keep]))
        if keep == 0 or ctxlib.messages_tokens(messages[:cut]) <= budget:
            break
        keep -= 1
    return [dict(m) for m in messages[:cut]], dropped[:start] + present[keep:]

def _compact_messages(messages: list[dict], plan, dropped: list[dict],
                      sums: list[dict]) -> list[dict]:
    """拼压缩请求：**热前缀**（system + 归档 + 旧回合原文）+ 可选小结 + 尾随指令。"""
    prefix, over = _compact_prefix(messages, plan, dropped)
    out = list(prefix)
    if over:
        out.append({"role": "user", "content":
                    "【更早的旧回合（这些回合不在上一次请求里，只有逐回合小结）】\n"
                    + _compact_input(over, sums, int(over[0]["turn"]))})
    out.append({"role": "user", "content": COMPACT_INSTRUCTION})
    return out

def _compact_block(backend, cfg, world, name, dropped: list[dict], plan=None,
                   messages: list[dict] | None = None, tools=None,
                   emit=None) -> str | None:
    """把滑出 replay 的回合并入"递归累积的长期记忆"（酒馆式）。

    - 请求 = **上一次请求的前缀**（system + 归档 + 这些旧回合的原文）+ 尾随维护指令，
      所以这次调用自己几乎全部命中提供方前缀缓存（见本节顶部那段注释）；
    - 旧记忆压在归档里（就在前缀内），指令要求以它为基础递归扩写，长程不断层；
    - 结果写回 world.long_memory（稳定头块，只在压缩回合变字节），同时也记一段
      summary_blocks 保留块史。失败返回 None（调用方退回逐回合小结）。
    - `messages` 是本回合真正发出去的那份上下文（取前缀字节用）；`tools` 必须是主请求
      同一份工具声明（缺了就是零命中）。
    """
    sums = world.summaries.get(name) or []
    from_turn = int(dropped[0]["turn"])
    to_turn = int(dropped[-1]["turn"])
    prev = (world.long_memory.get(name) or "").strip()
    payload = _compact_messages(messages or [], plan, dropped, sums)
    if emit:
        emit(f"🧠 {name} 压缩记忆：第{from_turn}~{to_turn}回合 → 递归扩写长期记忆"
             + ("（有旧记忆为基础）" if prev else "（首次建立）"))
    text, stats = backend.complete_text(payload, cfg, tools=tools)
    if emit:
        # ★ 压缩调用自己的命中率（2026-10-02 起才统计得到）：改造的全部意义就在这个数上，
        #   看不见就等于没改。提供方没报用量时不打这行，别拿估算装准数。
        inp = int(stats.get("hit") or 0) + int(stats.get("miss") or 0)
        if stats.get("usage_reported") and inp:
            emit(f"🧠 {name} 压缩调用：输入{inp}tok 命中{stats['hit'] / inp * 100:.0f}%"
                 f"（{stats['hit']}/{inp}）｜{stats.get('wall', 0):.0f}s")
    text = str(text or "").strip()
    if len(text) < 20:
        return None
    with _engine_lock:
        world.long_memory[name] = text
        world.summary_blocks.setdefault(name, []).append(
            {"from": from_turn, "to": to_turn, "text": text, "turn": world.turn})
    return text

def _pair_tool_calls(msgs: list[dict]) -> int:
    """给悬空的 tool_calls 补桩 tool 响应，返回补的数量（原地修复）。
    半途而废的批次（批中亡国 / 日志层抛错）会留下"assistant 声明了 N 个调用、
    只回了 k 个"的残缺对话——进 replay 后多数 OpenAI 兼容端点此后每回合 400，
    该国永久卡死在烧 max_steps 的空转里。入库/续跑前先修平。"""
    have = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    out, n = [], 0
    for m in msgs:
        out.append(m)
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                tid = tc.get("id")
                if tid and tid not in have:
                    out.append({"role": "tool", "tool_call_id": tid,
                                "content": "（该行动未执行：本回合提前结束，此令作废）"})
                    have.add(tid)
                    n += 1
    if n:
        msgs[:] = out
    return n

def _asst_msg(msg: dict, content, reasoning: str) -> dict:
    """把提供方返回的 assistant 消息收进 replay（只挑该存的字段，别把整个 msg 抄进去）。

    ★ `llm_provider.REPLAY_KEY`（Anthropic 的 **thinking 签名**）必须跟着走：它不给模型看，
      是留给 Anthropic 那一路**下一回合**把 thinking block 连签名一起回放用的（官方 API
      缺签名/签名对不上就 400）。存进消息 ⇒ 随 turn_memory 落盘 ⇒ 签名里带 model 字段，
      换模型继续玩时自动作废（见 `llm_provider` 里 REPLAY_KEY 的说明）。
    ★ 不回显、不估算：思考原文照旧只进 replay，不上看海台（2026-09-19 口径）。
    """
    out: dict = {"role": "assistant", "content": content}
    if reasoning:
        out["reasoning_content"] = reasoning
    if msg.get(REPLAY_KEY):
        out[REPLAY_KEY] = msg[REPLAY_KEY]
    return out

def _usage_running_line(world) -> str:
    """📊 行尾追加的**全期累计 token**（`world.token_usage` 的总账）。

    **满 100 万才显示**：八国 × 几百回合，每条状态行都挂一长串数字会把真正的信息淹掉。
    掺了本地估算就整行打 `≈`（见 `World.usage_totals` 的 `estimated`）——
    绝不把估数当准数报（2026-09-20 那条口径：「报错误的会导致估价错误」）。

    ★ `≈` 的**确切含义**是「这行里掺了**端点没报用量**的调用」（那种调用命中/输出全是本地估的）。
      本行显示的三样（累计 / 命中% / 输出）在 Anthropic 那路**都是端点真报的**，故不打 ≈；
      那条路上唯一的估数是**思考 token**（端点不单报），而思考不在这行里 ——
      逐国回合那行会给它单独打 `≈`（`agg["reason_estimated"]`）。
    """
    try:
        total = world.usage_totals()
    except Exception:                      # 统计永远不该把跑局搞崩
        return ""
    if total["total"] < 1_000_000:
        return ""
    mark = "≈" if total["estimated"] else ""
    parts = [f"累计 {mark}{total['total'] / 1e6:.1f}M tok"]
    if total.get("hit_rate") is not None:
        parts.append(f"命中{total['hit_rate'] * 100:.0f}%")
    if total["out"]:
        parts.append(f"输出{total['out'] / 1e6:.2f}M")
    if total["est_calls"]:
        parts.append(f"其中{total['est_calls']}/{total['calls']}次为估算")
    return "｜" + " ".join(parts)

def run_openai_turn(world, name, cfg, max_steps: int = 16, emit=None, on_call=None,
                    head=None) -> int:
    """跑一国一回合：反复调 LLM 用工具，直到 end_turn **被引擎回执认可** / 步数上限。
    返回执行次数。

    **三条播报通道**（分工见下，别再往 `emit` 里塞动作/思考——那边只发通知）：

      `emit(s)`    —— **通知行**：🧠 下滑/压缩记忆、⚠ 重试/故障。看海端**逐行即时**刷出
                      （`mp_run` 把它们钉进终端底部固定状态区）。
      `head(s)`    —— **本回合的常驻状态行**（只有一条：🧠 上下文计划），状态区第 1 行钉着它，
                      整回合不滚走。给了 `head` 就走 `head`、不再走 `emit`（调用方自己决定
                      落哪里：真终端给状态区、管道退回日志流，见 `mp_run.panel_note`）。
      `on_call(k)` —— **每次 LLM 调用回显一次**（用户 2026-09-20：「能不能每次调用回显一次，
                      而不是全部操作完毕后一次性回显」）：第 k 次（**0-based**）调用**把它的
                      工具跑完之后**回调，让看海端当场把这次调用的动作从纪事里刷出来。
                      此前攒到**整国回合结束**才 flush ⇒ 一次 5 次调用的回合（实测 426s）
                      期间看海台一个字都不出，结束时一次性砸下来。
                      **最后一次调用不回调**——它的尾巴由调用方在回合末兜（`mp_run` 那句
                      `◈ … 行动完毕` 块正是干这个的）⇒ 既不重复回显，也不丢动作。

    提供方差异（OpenAI 兼容 / Anthropic Messages）收口在 llm_provider，循环只见
    OpenAI 形态消息。三条纪律：
    ① 结束只认 execute 的回执（✅）——模型递个非空 summary 不算收尾（拒绝文案
       会作为 tool 响应回喂，继续逼它补）；
    ② 任何 return / 异常续跑前都过 _pair_tool_calls——没配对的命令不许进记忆；
    ③ **只回正文、没调工具也不算收尾**——正文照常入 messages（与带 tool_calls 时
       同等待遇），然后回喂催它继续。模型在动手前先陈述一句是常态，不是收尾信号。

    deepseek-v4-flash 这类推理模型把思考放在 reasoning_content（独立于 content），
    且可能连续多轮纯思考后才调用工具：每轮思考回显给下一轮，直到它真正行动。
    """
    backend = make_backend(cfg)
    # 上下文：窗口大小由配置 ctx_window 定义，深度/归档/下滑水位由 ctx.py 按预算动态分配
    messages, plan = build_context(world, name, cfg, emit=emit)
    if emit or head:
        _rl = ctxlib.rolling_hit(name)
        # ★ 这一行是**常驻状态行**（给了 head 就走 head）：它得整回合钉在状态区第 1 行，
        #   跟"下滑/压缩/重试"那种一闪而过的通知不是一回事。
        _ctx_line = (f"🧠 {name} 上下文: {plan.describe()}"
                     + (f"｜实测命中≈{_rl * 100:.0f}%（近20次滚动）" if _rl is not None else ""))
        (head or emit)(_ctx_line)
    base = len(messages)  # 本回合新增消息的起点（base 之前是历史 replay，存储时不再重复）
    done = 0
    stall = 0  # 连续"只思考/空转"轮数
    agg: dict = {"calls": 0, "wall": 0.0, "stream": 0.0, "first": 0.0,
                 "maxgap": 0.0, "out_tokens": 0, "reason_tokens": 0,
                 "hit": 0, "miss": 0,
                 # ★ 账本（`World.add_usage`）按这个键分"真数/估算"，**必须显式带上**：
                 #   以前只累加 hit/miss/out 与 estimated/reason_estimated，从不带它 ⇒
                 #   `stats.get("usage_reported")` 恒为 None ⇒ 整局每次都记成 est_calls，
                 #   全期累计那行于是永远打 `≈` 并显示「N/N次为估算」——**把端点真报的数
                 #   说成估的**（2026-10-06 从存档实据发现并修正）。
                 "usage_reported": True}

    def _auto_summary(text) -> None:
        """收尾没带 summary 时，从正文里取一句补进回合小结——否则这一回合在归档里凭空消失。"""
        line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
        line = line or "（本回合无小结）"
        line = (line[:80] + "…") if len(line) > 80 else line
        engine_call(world.summaries.setdefault(name, []).append,
                    {"turn": world.turn, "text": line})
        engine_call(world.log, f"{name} 回合小结（自动归纳）：{line}", phase="行动", nation=name)

    def _finish(d: int) -> int:
        _pair_tool_calls(messages)      # 终闸：任何提前 return 的残批都在此处修平后才入库
        if agg.get("calls"):
            speed = (agg["out_tokens"] / agg["stream"]) if agg["stream"] > 0 else 0.0
            inp = agg["hit"] + agg["miss"]  # 缓存按输入前缀算：hit+miss=prompt tokens
            cache = (f"｜缓存命中{agg['hit'] / inp * 100:.0f}%({agg['hit']}/{inp}tok)"
                     if inp else "")
            if inp:
                ctxlib.record_hit(name, agg["hit"], agg["miss"])
            # ★ 提供方没报用量时，数字是本地估算 ⇒ **打上 ≈**，别让它看起来像真数
            #   （用户 2026-09-20：「报错误的会导致估价错误」——宁标"估"，不装"准"）
            #   思考那一格单独判：Anthropic 那路**报了真用量但思考 token 分不出来**（它把
            #   思考并进 output_tokens），这格是本地估的 ⇒ 也得打 ≈（`reason_estimated`）。
            #   ★ 2026-10-02：本机 qoder-flash 网关已改为**透出上游真数**（上游一直在报
            #   prompt/completion/cached_tokens，是网关 `_parse_chunk` 把它丢了），
            #   所以走那条路时这里不再出现"未报用量"。
            eq = "≈" if agg.get("estimated") else ""
            rq = "≈" if (agg.get("estimated") or agg.get("reason_estimated")) else ""
            tok = (f"输出{eq}{agg['out_tokens']}tok(思考{rq}{agg['reason_tokens']})"
                   + ("（本网关未报用量，此为本地估算）" if agg.get("estimated") else ""))
            # ★ 累计进世界总账（`world.token_usage`）：看海台要能回答"这一局到底烧了多少"。
            #   估的与报的分开数（见 `World.add_usage`），所以把 agg 原样交给它。
            world.add_usage(name, agg)
            world.log(
                f"📊 {name} 本回合: {agg['calls']}次调用 {agg['wall']:.0f}s｜"
                f"{tok}{cache}｜"
                f"首token均{agg['first'] / agg['calls']:.0f}s｜最长无输出{agg['maxgap']:.0f}s｜"
                f"真正输出{agg['stream']:.0f}s｜速度{eq}{speed:.1f}tok/s"
                + _usage_running_line(world),
                phase="事件")
        dropped = _store_turn_memory(world, name, messages, base, plan)
        if dropped:
            if emit:
                emit(f"🧠 {name} 下滑：裁掉第{dropped[0]['turn']}~{dropped[-1]['turn']}回合"
                     f"（{len(dropped)} 回合）——本回合前缀缓存全段重建")
            if plan.compact and name in world.nations:
                try:
                    # ★ messages/plan/tools 都传进去：压缩调用要接在本回合请求的前缀后面
                    #   （前缀缓存），tools 必须与主请求同一份（见 `_compact_block`）
                    _compact_block(backend, cfg, world, name, dropped,
                                   plan=plan, messages=messages,
                                   tools=tool_schemas(world, name), emit=emit)
                except Exception as e:   # 压缩失败不影响主流程：归档退回一行小结
                    if emit:
                        emit(f"⚠ {name} 记忆压缩失败({type(e).__name__})，归档仍用逐回合小结")
        return d

    for step in range(max_steps):
        # ★ 每次 LLM 调用回显一次（用户 2026-09-20）：**上一轮**调用（step-1）的动作已经执行
        #   完了，在这里就把它刷给看海端——而不是等整国回合结束。`on_call` 收的是**已完成**
        #   那次调用的序号（0-based）；最后一次调用不回显（尾巴归调用方在回合末兜，
        #   见函数 docstring 的两条通道分工）。
        if step and on_call:
            on_call(step - 1)
        if name not in world.nations:
            return _finish(done)
        try:
            # 调用+流式聚合+重试分类全在 llm_provider（POSIX 硬超时/Windows 降级也在彼处），
            # 循环只见 (msg, stats)。on_retry 把重试进度回显给看海终端。
            msg, stream_stats = backend.chat_turn(
                messages, tool_schemas(world, name), cfg,
                on_retry=lambda a, tag, wait, total: emit and emit(
                    f"⚠ {name} 第{a}次调用失败({tag})，{wait:.0f}s 后重试（共 {total} 次）"))
        except Exception as e:
            _pair_tool_calls(messages)   # 防御：异常路径若留下未配对残批，续跑前先修平
            # ★★ 致命：**直接终止整局**，不兜、不代打、不接着烧步数。
            #   用户 2026-09-20：「任何错误都应该直接终止游戏」。
            #   这里曾经是把错误写成一条 user 消息再 `continue`——API 一挂（或配额耗尽）就
            #   每回合空烧 max_steps 次调用、每次还叠 3 次重试与退避：2026-09-19 夜里 5 国
            #   同时撞上 token-plan 周配额墙，白跑 14 个回合、白烧一整周额度，还把 83 万条
            #   报错灌进 replay 让存档从 5 MB 涨到 1.1 GB。**进度不会丢**：每回合结算后
            #   存档已原子落盘，重启即从上一回合续。
            if emit:
                tag = "超时" if isinstance(e, TimeoutError) else type(e).__name__
                emit(f"🛑 {name} LLM 调用失败（{tag}），本局终止：{str(e)[:200]}")
            raise
        reasoning = (msg.get("reasoning_content") or "").strip()
        content = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []
        for _k in ("wall", "stream", "first", "maxgap", "out_tokens", "reason_tokens",
                   "hit", "miss"):
            agg[_k] = agg.get(_k, 0.0) + (stream_stats.get(_k) or 0.0)
        agg["calls"] = agg.get("calls", 0) + 1
        if stream_stats.get("estimated"):
            agg["estimated"] = True        # 提供方没报用量 ⇒ 这组数是本地估算，显示时要打 ≈
        if stream_stats.get("reason_estimated"):
            agg["reason_estimated"] = True  # 报了用量、但思考那一格是本地估的（Anthropic 那路）
        if not stream_stats.get("usage_reported"):
            # 账本按**国回合**记账：这一次调用没报用量，整国回合的合并数就掺了估算
            # （只要有一次真报，后来的 False 也翻不回来——宁可标估，不装准）
            agg["usage_reported"] = False
        # ★ 不再回显 💭 思考（2026-09-19 用户口径：「我不想知道他们怎么想的」）。思考原文
        #   照样进 replay/记录（那是模型自己的上下文），只是不上看海台。
        if tool_calls:
            stall = 0
            asst = _asst_msg(msg, msg.get("content"), reasoning)
            asst["tool_calls"] = [
                {"id": tc["id"], "type": tc["type"],
                 "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}
                for tc in tool_calls
            ]
            messages.append(asst)
            for tc in tool_calls:
                fn = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = engine_call(execute, world, name, fn, args)
                is_end = fn in ("end_turn", "结束回合", "done")
                if not is_end:
                    # end_turn 的小结由 execute 自己记一条即可，避免在 feed 里重复
                    engine_call(log_tool, world, name, fn, args, result)
                    # ★ 不再实时回显动作与 ↳ 结果（曾 emit `{N}回合·{名} ◇ {工具} …`）：
                    #   看海口径（用户 2026-09-19）是**只要压缩**——动作几秒后本来就会由
                    #   纪事块（`[N·行动]…`，observer 刷 world.history）原样打出来，重复两遍纯噪音。
                    #   ⇒ 运行期只播报 **记忆压缩**（🧠 上下文/下滑/压缩记忆）与 **故障**（⚠）。
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                done += 1
                if name not in world.nations:
                    return _finish(done)      # 批中亡国：剩余调用由 _finish 的配对闸补桩后入库
                if is_end:
                    # ★ 只认 execute 的回执：✅ 才算真结束。递了非空 summary 但没国策/国策过期/
                    #   小结太短，都被 execute 拒绝（拒绝文案已回喂），继续逼它补。
                    if str(result).startswith("✅"):
                        return _finish(done)
            if name in world.nations:  # 每次行动后都回填一次最新状态（默认塞查询）
                messages.append({"role": "user", "content": engine_call(compact_state, world, name)})
            continue
        # 没有工具调用：**不构成收尾**——收回合只认 end_turn 的 ✅ 回执或步数上限。
        # 正文照常入 messages（和带 tool_calls 时一个待遇，跨回合记忆靠它），然后催它继续。
        if content:
            asst = _asst_msg(msg, msg.get("content"), reasoning)
            messages.append(asst)
            stall += 1
            pl = world.plans.get(name)
            if (not pl or not str(pl.get("text", "")).strip()
                    or world.turn - pl.get("turn", world.turn) >= PLAN_MAX_TURNS):
                messages.append({"role": "user", "content":
                                 "本回合还不能结束：还没有有效国策。请先 plan(content=…) "
                                 "制定/修订国策，再用工具行动，最后用 end_turn(summary=…) 收尾。"})
            else:
                # 点名它自己刚说的话：讲了一套计划 ≠ 执行了这套计划
                said = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
                said = (said[:80] + "…") if len(said) > 80 else said
                messages.append({"role": "user", "content":
                                 f"你没有调用任何工具，本回合尚未结束。你上面说：「{said}」——"
                                 "请实际调用工具把它执行掉；若确实无事可做，请 end_turn(summary=…)。"})
            continue
        if reasoning:
            # 纯思考轮（无正文无工具）：把思考原文回喂，让模型接着想而不是每次从零大思考
            # （否则每轮重想一遍，又慢又贵——百万上下文模型输出 token 价高且不缓存）。
            messages.append(_asst_msg(msg, None, reasoning))
            stall += 1
            if stall >= 8:
                messages.append({"role": "user",
                                 "content": "（请继续完成本回合：想好了就调用工具；若确实无事可做就 end_turn。）"})
                stall = 0
            continue
        # 空回复：催它继续（同样不收尾）
        messages.append({"role": "user", "content": "请决策并调用工具；若本回合无事可做，请 end_turn。"})
        stall += 1
    _auto_summary("（达到本回合行动轮数上限，提前收尾）")
    return _finish(done)

def dummy_turn(world, name, rng,
               max_actions: int = rule_ai_registry.UNLIMITED_ACTIONS,
               rule_ai: str | None = None) -> int:
    """无 key 的规则 AI：**按版本名**从注册表取一版扩张流（`rule_ai.py`），
    并把每个动作写进看海日志（Observer 因此能看到它的每个行动）。

    `rule_ai` = 版本名，如 `"v10"`（配置顶层或逐国可覆盖，见 README 配置表）；
    不传则用 `rule_ai.DEFAULT_RULE_AI`。**本函数不认识任何具体版本** ——
    换基线只改配置，不动这里（`tests/test_rule_ai.py` 盯着这条）。

    ★`max_actions` 缺省**无上限**（用户 2026-09-15：「看海口径的动作上限……全删了」）——
    原缺省是 12，看海时超了直接截断（实测 v10 有 8.6% 的回合被截顶）。
    口径与理由见 `rule_ai.UNLIMITED_ACTIONS`；配置里仍可显式写 `max_actions` 卡住。

    策略本身在 `ruleai/v*.py`——那是游戏层，不依赖本 LLM 层的工具 schema /
    文本面板 / 国策。各版差异与历代表见 `rule_ai.py` 的模块 docstring。
    """
    _, fn = rule_ai_registry.resolve(rule_ai)
    acts = fn(world, name, rng, max_actions=max_actions)
    for tool, args, ok, msg in acts:
        log_tool(world, name, tool, args, msg)
    return len(acts)
