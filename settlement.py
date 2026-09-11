#!/usr/bin/env python3
"""结算机制：解析 mp_save.json 存档，按 GDP/军队/领土/固定资产 打分排名，
然后把各国 AI（抽走全部工具）请进「结算厅」聊天室，自由总结与交换意见，只进行 5 轮。

用法（在含 mp_save.json / mp_config.json 的目录运行，或用 --save/--config 指定）：
    python3 settlement.py                     # 打分 + 结算厅 5 轮
    python3 settlement.py --no-chat           # 只打分，不进聊天室
    python3 settlement.py --table             # 只看表：打印成绩单即退出
    python3 settlement.py --save ../zhanguo/mp_save.json

产物：结算报告.md（分数明细 + 结算厅完整对话录）。

看海人寄语：聊天室开场，你（游戏作者/看海人）以一句评语致辞每位君主——公开挂载在
计分板下方、全程可见（计分板每轮展示）。来源：--remarks 结算寄语.json（{"国":"话"}），
缺省且终端可交互时逐国现场输入，回车跳过该国。

────────────────────────────────────────────────────────────────────────
评分口径（全部按基准价结算，不用市价——市价随回合波动，基准价才是恒定标尺）
基准价：粮食2 木头2 矿石4 石油6 装备8 补给5；黄金矿场每座每回合 +10 金。

一、GDP——生产法（增加值法），按存档快照推算「每回合」流量：
    1. 采集建筑：产出 × 基准价（林场/农场/矿场/石油厂）
    2. 黄金矿场：+10 金/座（直接计货币产出）
    3. 市政厅：5 金基础 + 本地块其他建筑 ×1 金（复现游戏内公式）
    4. 能源厂：增加值 = 发电量 × 影子电价(1金) − 燃料 × 基准价
       电不存储、无市价 → 按边际生产成本定价：木材厂 1木(2金)→2电 = **1金/电**，
       油电厂 1油(6金)→8电 = 0.75金/电。选 1 而非更高值的硬理由：
       ①电厂+工厂的总 VA 中电价 p 严格抵消（电厂: Ep−燃料；工厂: 产−料−Ep），p 只在
       「富余电」（不可存储，多发即弃）和「兵营/市政厅用电」两处漏出——p=1 使每度计入
       的电都有 ≥0.75金 真实燃料背书（木材厂恰 1金），浪费的电贡献趋近 0（浪费不计入
       GDP）；p 取整 1 从高不从低，属保守口径；
       ②p=1 下油电厂如实计 +2、木材厂 0——发电划不划算由燃料市价体现，
       不给浪费性转换发虚假增加值。
    5. 装备厂：增加值 = 装备×8 − 矿×4 − 油×6（中间投入按基准价扣除）
    6. 军费（政府最终消费，即「军队消费纳入消费」）：Σ军队补给耗(步1骑2民1)×补给价5，民兵驻自家军屯格的免补给部分不计（与引擎同口径）
       ——补给厂产出**不计**增加值，其价值在军费项体现，避免双算
    选生产法而弃消费法：存档是存量快照，生产法可从建筑表确定性推算一回合流量；
    消费法需要建造成本/征兵/市场买卖的每回合流水，快照里没有，解析 history 不可靠。

二、军队：军力 = Σ(当前HP/该兵种满血) × 兵种权重（步 1.0 / 骑 1.5 / 民 0.4，按攻击 50/50/20 定）。

三、领土：地块数（plain count，最透明可解释）。

四、固定资产：重置成本 = Σ(造价金 + 耗木 × 木基准价)；
    城堡按已升到的级数计累计投入（Σ 第1..L级造价）。

排名：**按「总消费」排名**（用户 2026-09-10 拍板）——不看加权总分。
    总消费 = 累计建造 + 征兵 + 军费（军队吃掉的补给），全部按**当时市价**折金。
    只计「被消耗掉的资源」：市场买卖与馈赠不计（买来的物资在真正被消耗时才入账），
    避免重复计数。它测的是「你动用了多少国力」，而不是「你现在有多少国力」——
    囤而不用的国家在榜上垫底。上面四维只列示现状，不参与排名。
    **已亡国同样上榜**（按累计消费排名，四维现状记 0）——活着不是及格线，参与到底也算数。
────────────────────────────────────────────────────────────────────────
"""
import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

from game import (BUILDINGS, MARKET, TOWN_HALL_GOLD, TOWN_HALL_PER_SLOT,
                  TRADEABLE, UNIT_TYPES)

# ───────────────────────── 常量（一律取自 game.py，避免两处漂移） ─────────────────────────
BASE_PRICE = {g: MARKET[g] for g in TRADEABLE}   # 基准价（黄金是货币，不在市场内）
GOLD_MINE_PER_TURN = BUILDINGS["黄金矿场"]["outputs"]["黄金"] * MARKET["黄金"]
TOWN_HALL_BASE = TOWN_HALL_GOLD                  # 市政厅基础金
ENERGY_PRICE = 1                 # 影子电价 = 边际生产成本（结算口径，非游戏规则）
UNIT_SUPPLY = {k: v["supply"] for k, v in UNIT_TYPES.items()}   # 每军每回合补给耗量
UNIT_WEIGHT = {"步": 1.0, "骑": 1.5, "民": 0.4}   # 结算口径：军力权重（按攻击 50/50/20 定，非游戏规则）
UNIT_MAX_HP = {k: v.get("hp", 100) for k, v in UNIT_TYPES.items()}   # 各兵种满血（民 80）
CASTLE_COST = BUILDINGS["城堡"]["cost"]          # 城堡逐级造价（累计投入求和用）
# 建筑造价/耗木（重置成本用）；城堡造价是逐级列表，单独按级累计
BUILD_COST = {name: (info["cost"], info["wood"]) for name, info in BUILDINGS.items()
              if isinstance(info["cost"], int)}

# 总消费的三条去向（键须与 mp.py 的 SPEND_FIELDS 一致）；排名只看它们的合计
SPEND_LABELS = {"build": "建造", "recruit": "征兵", "supply": "军费"}
CHAT_ROUNDS = 5


# ───────────────────────── 存档解析与打分 ─────────────────────────
def load_save(path: str) -> dict:
    from mp import SAVE_VERSION
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # 与 mp.World.load 同一契约：版本不符直接拒算——旧档字段形状不同，
    # 硬打分只会得出一本正经的错误账（结算器自己 load 原始 dict，不走 World.load）。
    if data.get("version") != SAVE_VERSION:
        sys.exit(f"存档版本不符（档内 {data.get('version', '无版本号＝旧档')} ≠ 当前 "
                 f"{SAVE_VERSION}）：结算拒绝在旧档上打分，请用当前代码重跑一局。")
    return data


def _roster(save: dict) -> list[str]:
    """全部国家（**含已亡国**）：save["order"] 记着出现过的所有国名，nations 只剩存活者。"""
    names = [n for n in (save.get("order") or []) if isinstance(n, str)]
    for n in save.get("nations", {}):
        if n not in names:
            names.append(n)
    return names


def army_type(a: dict) -> str:
    t = a.get("type")
    if t in UNIT_WEIGHT:
        return t
    return "骑" if "骑" in a.get("name", "") else "步"  # 旧档无 type 时从番号推断


def score_gdp(save: dict, nation: str) -> tuple[float, list[str]]:
    """生产法 GDP：按快照推算每回合增加值（基准价）。返回 (gdp, 聚合明细行)。"""
    agg: dict[str, tuple[int, float]] = {}  # 名目 → (数量, 金额)
    gdp = 0.0

    def _add(item: str, cnt: int, v: float) -> None:
        c0, v0 = agg.get(item, (0, 0.0))
        agg[item] = (c0 + cnt, v0 + v)
        nonlocal gdp
        gdp += v

    for t in save["tiles"].values():
        if t.get("owner") != nation:
            continue
        b = t.get("buildings", {})
        for name, cnt in b.items():
            if not cnt:
                continue
            if name == "城堡":
                continue  # 城堡无产出，投入计固定资产
            elif name == "林场":
                _add("林场", cnt, cnt * BASE_PRICE["木头"])
            elif name == "农场":
                _add("农场", cnt, cnt * BASE_PRICE["粮食"])
            elif name == "矿场":
                _add("矿场", cnt, cnt * BASE_PRICE["矿石"])
            elif name == "石油厂":
                _add("石油厂", cnt, cnt * BASE_PRICE["石油"])
            elif name == "黄金矿场":
                _add("金矿", cnt, cnt * GOLD_MINE_PER_TURN)
            elif name == "市政厅":
                others = sum(b.values()) - cnt  # 与游戏内公式一致：不含市政厅自身
                _add(f"市政厅(每座+{others}位)", cnt,
                     cnt * (TOWN_HALL_BASE + others * TOWN_HALL_PER_SLOT))
            elif name == "木材能源厂":
                _add("木材能源厂", cnt, cnt * (2 * ENERGY_PRICE - BASE_PRICE["木头"]))
            elif name == "石油能源厂":
                _add("石油能源厂", cnt, cnt * (8 * ENERGY_PRICE - BASE_PRICE["石油"]))
            elif name == "装备厂":
                _add("装备厂", cnt,
                     cnt * (2 * BASE_PRICE["装备"] - BASE_PRICE["矿石"] - BASE_PRICE["石油"]))
            # 补给厂不计增加值：补给价值在军费（政府最终消费）中体现，防双算
    armies = [a for a in save["armies"] if a.get("owner") == nation]
    if armies:
        # 军屯驻屯免补给（与引擎 _supply_need 同口径：民兵驻自家军屯格，每座覆盖本格 1 支）
        camps: dict[tuple[int, int], int] = {}
        for k, t in save["tiles"].items():
            if t.get("owner") != nation:
                continue
            c = t.get("buildings", {}).get("军屯", 0)
            if c:
                xs, ys = k.split(",")
                camps[(int(xs), int(ys))] = c
        need = 0
        used: dict[tuple[int, int], int] = {}
        for a in armies:
            if army_type(a) == "民":
                p = (a.get("x", -1), a.get("y", -1))
                if p in camps and used.get(p, 0) < camps[p]:
                    used[p] = used.get(p, 0) + 1
                    continue  # 驻屯民兵不吃补给
            need += UNIT_SUPPLY[army_type(a)]
        mcost = need * BASE_PRICE["补给"]
        _add(f"军费({len(armies)}支)", len(armies), mcost)
    rows = [f"{k}×{c} {v:+.0f}" for k, (c, v) in agg.items()]
    return gdp, rows


def score_army(save: dict, nation: str) -> tuple[float, int]:
    armies = [a for a in save["armies"] if a.get("owner") == nation]
    power = sum(a.get("hp", 0) / UNIT_MAX_HP[army_type(a)] * UNIT_WEIGHT[army_type(a)]
                for a in armies)
    return power, len(armies)


def score_land(save: dict, nation: str) -> tuple[int, int]:
    tiles = [t for t in save["tiles"].values() if t.get("owner") == nation]
    return len(tiles), len(tiles)


def score_asset(save: dict, nation: str) -> tuple[float, list[str]]:
    agg: dict[str, tuple[int, float]] = {}
    total = 0.0

    def _add(item: str, cnt: int, inv: float) -> None:
        c0, v0 = agg.get(item, (0, 0.0))
        agg[item] = (c0 + cnt, v0 + inv)

    for t in save["tiles"].values():
        if t.get("owner") != nation:
            continue
        b = t.get("buildings", {})
        for name, cnt in b.items():
            if not cnt:
                continue
            if name == "城堡":
                _add(f"城堡L{cnt}", cnt, sum(CASTLE_COST[:cnt]))  # 逐级累计投入
            else:
                gold, wood = BUILD_COST[name]
                _add(name, cnt, cnt * (gold + wood * BASE_PRICE["木头"]))
    total = sum(v for _, v in agg.values())
    rows = [f"{k}×{c} {v:.0f}金" for k, (c, v) in sorted(agg.items(), key=lambda kv: -kv[1][1])]
    return total, rows


def settle(save: dict) -> dict:
    """返回 {nation: {gdp, army, land, asset, spend, spend_total, alive, *_rows}}。

    **含已亡国**：亡国照样按总消费上榜（四维现状记 0）——活着不是及格线，
    参与到底也算数。排名依据 = 总消费（累计建造+征兵+军费，按当时市价折金）。
    """
    alive_map = save.get("nations", {}) or {}
    spend_all = save.get("spend", {}) or {}
    out: dict[str, dict] = {}
    for n in _roster(save):
        gdp, gdp_rows = score_gdp(save, n)
        army, n_army = score_army(save, n)
        land, _ = score_land(save, n)
        asset, asset_rows = score_asset(save, n)
        sp = {k: float((spend_all.get(n) or {}).get(k, 0) or 0) for k in SPEND_LABELS}
        out[n] = {"gdp": gdp, "gdp_rows": gdp_rows, "army": army, "n_army": n_army,
                  "land": land, "asset": asset, "asset_rows": asset_rows,
                  "spend": sp, "spend_total": sum(sp.values()),
                  "alive": n in alive_map}
    return out


def _dw(s: str) -> int:
    """终端显示宽度：CJK/全角字符按 2 计（Python len 只数字符，中文一掺就对不齐）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s, width: int, align: str = "left") -> str:
    s = str(s)
    gap = max(0, width - _dw(s))
    return " " * gap + s if align == "right" else s + " " * gap


def scoreboard_text(save: dict, result: dict, remarks: dict[str, str] | None = None) -> str:
    turn = save.get("turn", "?")
    rank = sorted(result, key=lambda n: -result[n]["spend_total"])
    header = ["排名", "国家", "总消费", "建造", "征兵", "军费", "GDP", "军力", "领土", "资产"]
    aligns = ["right", "left"] + ["right"] * 8
    rows: list[list[str]] = [header]
    for i, n in enumerate(rank, 1):
        r = result[n]
        rows.append([
            str(i), n if r["alive"] else f"{n}（亡）", f"{r['spend_total']:.0f}",
            f"{r['spend']['build']:.0f}", f"{r['spend']['recruit']:.0f}",
            f"{r['spend']['supply']:.0f}",
            f"{r['gdp']:.0f}", f"{r['army']:.1f}", f"{r['land']}", f"{r['asset']:.0f}",
        ])
    widths = [max(_dw(row[c]) for row in rows) for c in range(len(header))]
    body = ["  ".join(_pad(row[c], widths[c], aligns[c]) for c in range(len(header)))
            for row in rows]
    body.insert(1, "-" * _dw(body[0]))
    lines = [f"《战国》第 {turn} 回合 · 终局结算（按总消费排名）", ""] + body
    lines.append("")
    lines.append("口径：总消费 = 累计建造 + 征兵 + 军费（军队吃掉的补给），全部按当时市价折金——"
                 "本质是支出法 GDP 的骨架（投资 + 政府消费，本游戏无居民部门故无 C）。")
    lines.append("　　　只算「真正花掉的」：市场买卖与馈赠不计（买来的物资在被消耗时才入账），"
                 "存货也不计（囤积不是消费）——所以囤而不用的国家在这张榜上垫底。")
    lines.append("　　　GDP=生产法每回合推算(基准价)；军力=ΣHP%×兵种权重(步1.0/骑1.5/民0.4)；"
                 "领土=地块数；资产=建筑重置成本(基准价)——后四列只列示现状，不参与排名。")
    if any(not result[n]["alive"] for n in rank):
        lines.append("　　　已亡国同样上榜（按累计消费排名，四维现状记 0）——活着不是及格线，参与到底也算数。")
    if not any(result[n]["spend_total"] for n in rank):
        lines.append("⚠ 本档没有消费记录（总消费从记账启用后开始累计）——请用新档结算。")
    if remarks:
        lines.append("")
        lines.append("【看海人寄语】（游戏作者致辞，公开）")
        for n in result:
            if remarks.get(n):
                lines.append(f"· 致{n}：{remarks[n]}")
    return "\n".join(lines)


# ───────────────────────── 结算厅（无工具自由聊天，5 轮） ─────────────────────────
# 君主不是被塞一份记忆摘要，而是「从游戏里直接走过来」：
# system 与逐字 replay 与游戏内 build_context 完全一致（含思考 reasoning_content），
# 结算厅规则作为附则挂在 system 尾部，终局宣告开新话头——对话流从未断过。

SETTLE_APPENDIX = """

【终局附则 · 结算厅】
游戏到此为止：你已离开王座，被请进「结算厅」。这里没有工具、不能行动，
只能以文字发言，共 {rounds} 轮、每轮一次。主持者是「看海人」——本游戏的作者，
全程旁观了你们的一切，他将宣读终局成绩单，并给每位君主留一句开场寄语（公开、全体可见）。
1. 第一轮发言先回应看海人的寄语；
2. 凭你自己的记忆总结这一局：哪些决策英明、哪些是败笔（诚实，给看海人一个交代）；
3. 对最终排名表态：服或不服，说清理由；
4. 与其他君主交换意见：可以致意、可以反驳、可以互认得失——这是全剧终前的最后对话；
5. 中文，200~500 字，不要客套空话，不要跳出角色。
"""


def _replay_msg(m: dict) -> dict:
    """replay 一条游戏消息，剥掉 reasoning_content（用户拍板：结算厅不需要思考内容）。

    结算厅请求不带 tools——按思考模式文档，输入中的 reasoning_content 本就会被
    API 忽略、不拼进上下文，显式剥掉省得白传。
    """
    m = dict(m)
    m.pop("reasoning_content", None)
    return m


def _game_context(save: dict, name: str, rounds: int, ncfg: dict | None = None) -> list[dict]:
    """重建君主进场时的对话流：system+附则 → 历史归档 → 窗口内逐字 replay。

    与游戏内 ctx.build 同一套预算/归档/缓存布局（窗口按该国配置的 ctx_window 分配），
    只是不带本回合状态面板，「本回合行动」由终局宣告代替——君主带着原样的记忆与
    上下文跳转过来，而非收到一份转述。
    """
    import ctx as ctxlib
    import mp_ai
    shim = SimpleNamespace(
        turn=save.get("turn", 0),
        polity=save.get("polity") or {},
        extra_prompt=save.get("extra_prompt") or {},
        alive=lambda: list(save["nations"].keys()),
    )
    system_text = (mp_ai.system_prompt(shim, name) + SETTLE_APPENDIX.format(rounds=rounds))
    recs = (save.get("turn_memory") or {}).get(name) or []
    # 结算厅不带 tools → 按思考模式文档 reasoning_content 会被忽略，直接剥掉省 token
    mem = [{"turn": r["turn"], "messages": [_replay_msg(m) for m in r.get("messages") or []]}
           for r in recs]
    msgs, _plan = ctxlib.build(
        cfg=ncfg or {}, mem=mem,
        sums=(save.get("summaries") or {}).get(name) or [],
        blocks=(save.get("summary_blocks") or {}).get(name) or [],
        system_text=system_text, tail_text="")
    return msgs


def _finale_text(save: dict, board: str) -> str:
    return (f"【终局】看海人宣布：游戏在第 {save.get('turn', '?')} 回合结束，天下大局已定。\n\n"
            f"{board}\n\n"
            "你摘下王冠，走进结算厅——system 尾部的【终局附则】即刻生效："
            "没有工具、不能行动，只能发言。落座吧，等看海人点名。")


def run_chat(save: dict, result: dict, cfg: dict, rounds: int, log,
             remarks: dict[str, str] | None = None) -> list[str]:
    nations = list(save["nations"].keys())
    if not nations:
        # 全员同归于尽的档：结算厅无人可坐（轮转取模会除零）——只出成绩单，聊天跳过。
        log("（诸国俱亡，结算厅无人到场——只出成绩单。）")
        return []
    from openai import OpenAI
    clients = {}
    for nc in cfg.get("nations", []):
        if nc.get("base_url") and nc.get("api_key") and nc["name"] in nations:
            extra = {}
            if "thinking" in nc:
                extra["thinking"] = {"type": nc["thinking"]}
            if nc.get("reasoning_effort"):
                extra["reasoning_effort"] = nc["reasoning_effort"]
            clients[nc["name"]] = (OpenAI(base_url=nc["base_url"], api_key=nc["api_key"]),
                                   nc.get("model"), nc.get("temperature", 0.7),
                                   nc.get("max_tokens", 8192), extra)
    missing = [n for n in nations if n not in clients]
    if missing:
        log(f"（缺 API 配置，{ '、'.join(missing) } 不参加结算厅）")

    board = scoreboard_text(save, result, remarks)
    if remarks:
        for n, txt in remarks.items():
            if txt:
                log(f"🕊 看海人 致{n}：{txt}")

    # 每位君主一条从游戏延续下来的对话流，发言以 assistant 消息追加，不断重建
    states: dict[str, dict] = {}
    ctx_cfg = {n["name"]: n for n in cfg.get("nations", []) if isinstance(n, dict)}
    for name in nations:
        if name not in clients:
            continue
        msgs = _game_context(save, name, rounds, ctx_cfg.get(name))
        msgs.append({"role": "user", "content": _finale_text(save, board)})
        states[name] = {"msgs": msgs, "client": clients[name]}
    transcript: list[str] = []  # [f"【第r轮·{name}】..."]
    seen: dict[str, int] = {n: 0 for n in states}  # 各君主已读到的 transcript 位置
    for r in range(1, rounds + 1):
        order = nations[(r - 1) % len(nations):] + nations[:(r - 1) % len(nations)]
        log(f"─── 结算厅 第 {r}/{rounds} 轮 ───")
        for name in order:
            if name not in states:
                continue
            st = states[name]
            new = transcript[seen[name]:]  # 上次发言之后厅里新说的话，由看海人转达
            hist = "\n\n".join(new) if new else "（还无人发言，你是第一位落座的）"
            st["msgs"].append({"role": "user", "content":
                               f"【第 {r}/{rounds} 轮】看海人环视一圈。\n\n"
                               f"【你上离席后厅里的发言】\n{hist}\n\n轮到你（{name} 的君主）发言。"})
            client, model, temp, max_tok, extra = st["client"]
            for attempt in (1, 2, 3):
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=st["msgs"], temperature=temp,
                        max_tokens=max_tok, extra_body=extra or None)
                    text = (resp.choices[0].message.content or "").strip()
                    break
                except Exception as e:
                    log(f"（{name} 第{attempt}次发言失败：{e}）")
                    text = ""
                    time.sleep(3 * attempt)
            if not text:
                transcript.append(f"【第{r}轮·{name}】（发言失败，缺席）")
                seen[name] = len(transcript)
                continue
            st["msgs"].append({"role": "assistant", "content": text})
            transcript.append(f"【第{r}轮·{name}】{text}")
            seen[name] = len(transcript)
            log(f"◆ {name}：{text}")
    return transcript


def load_remarks(path: str | None, save: dict, log) -> dict[str, str]:
    """看海人寄语：JSON 文件或终端逐国输入。"""
    if path:
        data = json.load(open(path, encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items() if str(v).strip()}
    if not sys.stdin.isatty():
        return {}
    remarks: dict[str, str] = {}
    log("—— 看海人寄语（公开挂载计分板下，直接回车跳过该国）——")
    for n in save["nations"]:
        try:
            txt = input(f"  致{n}：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if txt:
            remarks[n] = txt
    return remarks


# ───────────────────────── 主流程 ─────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="战国终局结算：打分 + 结算厅")
    ap.add_argument("--save", default="mp_save.json")
    ap.add_argument("--config", default="mp_config.json")
    ap.add_argument("--rounds", type=int, default=CHAT_ROUNDS)
    ap.add_argument("--no-chat", action="store_true", help="只打分，不进结算厅")
    ap.add_argument("--table", action="store_true",
                    help="只看表：打印成绩单即退出，不进结算厅、不写报告、不要寄语")
    ap.add_argument("--out", default="结算报告.md")
    ap.add_argument("--remarks", default=None,
                    help="看海人寄语 JSON 文件（键=国名 值=一句话）；缺省且终端可交互时现场输入")
    args = ap.parse_args()

    if not Path(args.save).exists():
        sys.exit(f"找不到存档 {args.save}（用 --save 指定）")
    save = load_save(args.save)
    result = settle(save)

    def log(s: str) -> None:
        print(s, flush=True)

    board = scoreboard_text(save, result)
    log(board)
    if args.table:
        return

    transcript: list[str] = []
    if not args.no_chat:
        cfg = {}
        if Path(args.config).exists():
            cfg = json.load(open(args.config, encoding="utf-8"))
            import ctx as ctxlib
            ctxlib.apply_defaults(cfg)   # 顶层 ctx_* 下沉到各国，与游戏内一致
        else:
            log(f"（找不到 {args.config}，跳过结算厅）")
        if cfg:
            remarks = load_remarks(args.remarks, save, log)
            board = scoreboard_text(save, result, remarks)  # 报告版含寄语
            log("")
            transcript = run_chat(save, result, cfg, args.rounds, log, remarks)

    report = [f"# 《战国》终局结算报告\n", f"生成于第 {save.get('turn','?')} 回合存档。\n",
              "## 成绩单\n", f"```\n{board}\n```\n"]
    for n, r in sorted(result.items(), key=lambda kv: -kv[1]["spend_total"]):
        tag = "" if r["alive"] else " · 已亡国"
        report.append(f"### {n}{tag}（总消费 {r['spend_total']:.0f}）\n")
        report.append("- 总消费明细：" + "；".join(
            f"{SPEND_LABELS[k]} {r['spend'][k]:.0f}" for k in SPEND_LABELS))
        report.append(f"- GDP 原始值 {r['gdp']:.0f}：{'；'.join(r['gdp_rows']) or '无经济产出'}")
        report.append(f"- 固定资产 {r['asset']:.0f}：{'；'.join(r['asset_rows']) or '无建筑'}\n")
    if transcript:
        report.append("## 结算厅对话录\n")
        report.append("\n\n".join(transcript))
    Path(args.out).write_text("\n".join(report), encoding="utf-8")
    log(f"\n📄 报告已写入 {args.out}")


if __name__ == "__main__":
    main()
