# -*- coding: utf-8 -*-
"""多国 agent 层：把全部玩家功能注册成 OpenAI function tools，按国家隔离执行。

- 每个 agent 只拿到「自己该知道」的状态（自己的面板/信箱/视野内事件），
  只能调用自己的合法工具（机制与单机玩家一致，无作弊入口）。
- execute(world, actor, tool, args)：执行一个工具调用并返回结果文本。
- run_openai_turn(...)：一个国家的「回合」——反复调 LLM 直到它 end_turn / 无工具。
- dummy_turn(...)：无 key 时的简单规则 AI，用于机制验证/看海 demo。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from game import (
    ARMY_ATTACK_DAMAGE,
    ARMY_HEAL_PER_TURN,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    CASTLE_DEFENSE_PER_LEVEL,
    MARKET,
    MAX_SLOTS,
    TERRAIN_STATS,
    TOWN_HALL_GOLD,
    TOWN_HALL_PER_SLOT,
)
from mp import DIPLO_COST, LETTER_COST, PLAN_MAX_TURNS, RES_KEYS, RES_LABEL

# README 原文（匈奴 rules 附加用；读不到则留空）
_README_TEXT = ""
try:
    _README_TEXT = Path(__file__).resolve().parent.joinpath("README.md").read_text(encoding="utf-8")
except Exception:
    pass

# 引擎级锁：多国 agent 并发跑时，所有对 world 的读写在此串行化（网络调用在锁外并行）。
_engine_lock = threading.RLock()


def engine_call(fn, *a, **k):
    with _engine_lock:
        return fn(*a, **k)

# ---------------------------------------------------------------------------
# 面板（各国只见自己的）
# ---------------------------------------------------------------------------

GOODS_DISPLAY = ["粮食", "木头", "矿石", "石油", "装备", "补给"]

# 匈奴被禁的外交工具（只可 勒索通信/宣战/逼降求和）
HUNS_BLOCKED = {
    "propose", "提议", "respond_proposal", "回应邀约",
    "break_alliance", "断盟", "break_defense", "解除共同防御",
    "guarantee", "保障独立", "cancel_guarantee", "撤回保障",
    "gift", "赠送", "赠予", "馈赠",
    "share_map", "交换地图", "送图", "发地图",
    "bloc_found", "结盟", "发起结盟", "bloc_join", "入盟", "申请入盟",
    "bloc_leave", "退盟", "退出联盟", "vote", "投票",
}

# 匈奴教义（喂给匈奴 AI，令其贯彻）
HUNS_DOCTRINE = (
    "· 靠勒索与抢地为生：盯别国国库/资源，先写信威吓勒索——**优先要补给**（你缺补给，6 骑每回合耗 12，"
    "200 开局撑不久），其次要金；不给就宣战抢地。\n"
    "· 虚张声势：你明明只有 6 骑兵，也要写信说成 10 骑甚至更多，夸大兵威逼人交钱。\n"
    "· 核心是运动战：骑兵动 2 格、集中决战、打完就撤（守方还能免费 mv 撤），不恋战。\n"
    "· 骑兵集中决战可以轻易战胜敌方总量多很多的军队——你总可以以少打多：\n"
    "   集中骑兵挑软柿子（敌方分散/兵力少/补给差），避免硬拼满员要塞。\n"
    "· 见缝插针，多抢无驻军之地；少打有损失之战。\n"
    "· 力求百战百胜；有胜果之后马上写信要求对方投降/赔款，别拖泥带水。"
)


def _res_line(world, name) -> str:
    r = world.nations[name].res
    et, mt, short = world.energy_report.get(name, (0, 0, False))
    parts = []
    for k in RES_KEYS:
        label = RES_LABEL.get(k, k)
        v = r.get(k, 0)
        parts.append(f"{label}{v}" if k == "黄金" else f"{k}{v}")
    grid = "正常" if not short else "⚠停摆"
    summ = world.econ_summary.get(name, "")
    return (
        f"{'  '.join(parts)}\n"
        f"电网: 产{et}/需{mt} {grid}（不足则补给厂/装备厂/兵营/市政厅全部停摆）"
        + (f"\n上一回合结算: {summ}" if summ else "")
    )


def _fmt_armies(world, name) -> str:
    mine = [a for a in world.armies if a["owner"] == name]
    if not mine:
        return "（无军队——先建兵营再征兵 r）"
    lines = []
    for a in sorted(mine, key=lambda x: x["id"]):
        t = world.tiles.get((a["x"], a["y"]))
        where = t["name"] if t else "野外"
        st = "交战中" if a.get("engaged") else ("已移动" if a.get("moved_turn") == world.turn else "可行动")
        lines.append(f"{a['name']}(#{a['id']}) | {a['hp']}HP | ({a['x']+1},{a['y']+1}) {where} | {st}")
    return "\n".join(lines)


def _fmt_land(world, name, cap=40) -> str:
    own = world.own_tiles(name)
    lines = [f"国土 {len(own)} 块（最多列 {cap}，按坐标排序）:"]
    shown = 0
    for (x, y) in own:
        if shown >= cap:
            lines.append("  …")
            break
        t = world.tiles[(x, y)]
        b = t["buildings"]
        used = sum(b.values()) + sum((t.get("pending") or {}).values())
        res = " ".join(f"{k}x{t['resources'][k]}" for k in ("矿石", "黄金", "耕地", "石油", "木头"))
        pend = " ".join(f"{bn}在建" for bn, n in (t.get("pending") or {}).items() if n)
        built = " ".join(f"{bn}×{n}" for bn, n in b.items() if n) or "无"
        free = "可建" if not t["built_this_turn"] else "本回合已下单"
        gar = " ".join(f"军{a['id']}({a['hp']})" for a in world.armies if a["owner"] == name and (a["x"], a["y"]) == (x, y))
        extra = f" 在建:{pend}" if pend else ""
        core = "♥" if t.get("core") == name else ""  # ♥=核心领土（同战线盟友夺回会自动归还）
        lines.append(
            f"  {core}{t['name']} ({x+1},{y+1}){t['terrain']} 城L{b['城堡']} 位{used}/{MAX_SLOTS} "
            f"[资源 {res}] 建筑:{built}{extra} {free}{(' 驻:'+gar) if gar else ''}"
        )
        shown += 1
    fr = sorted(world.frontier_of(name))
    lines.append(f"可拓荒地 {len(fr)} 块（[野人]=有守军需 atk 打赢；[空地]=无守军，atk 进驻即占）:")
    frs = []
    for (x, y) in fr:
        guard = any(a["owner"] == "野人" and (a["x"], a["y"]) == (x, y) for a in world.armies)
        tag = "[野人]" if guard else "[空地]"
        frs.append(f"({x+1},{y+1}){world.ter_char(x, y)}{tag}")
    if frs:
        for i in range(0, len(frs), 10):
            lines.append("  " + " ".join(frs[i:i + 10]))
    return "\n".join(lines)


def _fmt_market(world, name) -> str:
    r = world.nations[name].res
    lines = ["世界市场（黄金是货币；可交易：粮食 木头 矿石 石油 装备 补给）:"]
    for g in GOODS_DISPLAY:
        p = world.prices[g]
        base = MARKET[g]
        tag = "≈基准" if abs(p - base) <= 0.02 * base else ("贵" if p > base else "贱")
        lines.append(f"  {g} 现价{p:.1f}(基准{base}) {tag}  你持有 {r.get(g, 0)}")
    return "\n".join(lines)


def _fmt_mail(world, name) -> str:
    box = world.mailbox.get(name, [])
    if not box:
        return "（收件箱为空）"
    lines = [f"收件箱 {len(box)} 封（寄出后下回合到）:"]
    for m in reversed(box[-8:]):
        lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{m['text']}")
    return "\n".join(lines)


def _fmt_diplomacy(world, name) -> str:
    lines = [f"国家关系: {world.rel_desc(name)}"]
    bloc = world.bloc_of(name)
    if bloc is not None:
        chief = bloc["members"][0]
        lines.append(
            f"  🤝 你的联盟「{bloc['name']}」（盟主 {chief}）成员：{'、'.join(bloc['members'])}"
            " —— 盟内互通领土/互不攻击/共享视野/外交免费；进攻战争须投票（多数决）；"
            "议和由盟主出面并投票；同战线盟友会自动归还你的核心领土（land 面板 ♥ 标记）"
        )
    # 本人在内的交战战线（议和须找谈判代表：主导者；有联盟时=其盟主）
    wars_in = []
    for w in world.wars:
        sides = world._war_sides(w)
        if name in sides[0] or name in sides[1]:
            fls = list(w["followers"]) + list(w.get("atk_followers", []))
            wars_in.append(f"{w['atk']}↔{w['def']}" + (f"(跟随 {'、'.join(fls)})" if fls else ""))
    if wars_in:
        lines.append("  交战战线: " + "；".join(wars_in)
                     + "（议和只能由双方谈判代表提出/接受：主导者，或主导者的盟主）")
    # 联盟进行中的投票（本盟成员或入盟申请人可见）
    for v in world.votes:
        vb = world.bloc_by_name(v["bloc"])
        mine = (bloc is not None and vb is bloc)
        cand = v["kind"] == "入盟" and v["payload"].get("candidate") == name
        if not (mine or cand):
            continue
        members = [m for m in (vb["members"] if vb else []) if m in world.nations]
        yes = sum(1 for m in members if v["votes"].get(m) is True)
        no = sum(1 for m in members if v["votes"].get(m) is False)
        pl = v["payload"]
        if v["kind"] == "宣战":
            desc = f"对 {pl.get('target')} 宣战"
        elif v["kind"] == "入盟":
            desc = f"{pl.get('candidate')} 申请入盟"
        elif pl.get("type") == "offer":
            desc = f"向 {pl.get('to')} 求和（{pl.get('kind')}{(' ' + str(pl.get('gold')) + '金') if pl.get('gold') else ''}）"
        else:
            desc = f"接受议和#{pl.get('offer_id')}"
        voted = v["votes"].get(name)
        tail = ""
        if mine and v["kind"] != "入盟":
            if voted is None:
                tail = f" —— vote {v['id']} true/false 表态"
            else:
                tail = "（你已投" + ("赞成）" if voted else "反对）")
        lines.append(f"  🗳 投票#{v['id']}（{v['bloc']}·{v['kind']}，发起 {v['proposer']}）{desc}"
                     f" 赞成{yes}/反对{no}/共{len(members)}{tail}")
    incoming = [p for p in world.proposals
                if p.get("b") == name or (p["kind"] == "联盟" and name in p.get("invitees", []))]
    if incoming:
        for p in incoming:
            if p["kind"] == "联盟":
                lines.append(f"  📨 邀约#{p['id']}: {p['a']} 提议结盟「{p['name']}」"
                             f"（创始成员：{'、'.join(p['invitees'])}；respond_proposal {p['id']} true/false）")
            else:
                lines.append(f"  📨 邀约#{p['id']}: {p['a']} 提议 {p['kind']}（respond_proposal {p['id']} true/false）")
    offers = [p for p in world.peace_offers if p["b"] == name]
    if offers:
        for p in offers:
            k = {"pay": f"{p['a']}愿赔{p['gold']}金", "demand": f"{p['a']}要你赔{p['gold']}金",
                 "white": "白和"}[p["kind"]]
            if p.get("truce"):
                k += f"（休战{p['truce']}回合）"
            lines.append(f"  🕊 求和#{p['id']}（{p['a']}→你）: {k} —— {p.get('note','')}（accept_peace/reject_peace {p['id']}）")
    if world.guarantees.get(name):
        lines.append(f"  你保障: {'、'.join(sorted(world.guarantees[name]))}")
    own_offers = [p for p in world.peace_offers if p["a"] == name]
    for p in own_offers:
        k = {"pay": f"你愿赔{p['gold']}金", "demand": f"你索{p['gold']}金", "white": "白和"}[p["kind"]]
        if p.get("truce"):
            k += f"（休战{p['truce']}回合）"
        lines.append(f"  你提的求和#{p['id']}（给{p['b']}）: {k}")
    return "\n".join(lines)


def _fmt_countries(world, name) -> str:
    """外交对象面板：先把『不是自己』的国家列出来选目标。所有 to= 参数只能填这里面的国名。"""
    lines = [f"可选外交对象（其余国家；外交/通信的 to= 必须是其中之一，不能是自己）:"]
    for n in world.alive():
        if n == name:
            continue
        tags = []
        if world.allied_between(name, n):
            tags.append(f"联盟({world.bloc_of(name)['name']})")
        if world.dp_between(name, n):
            tags.append("共同防御")
        if world.war_between(name, n):
            tags.append("交战")
        if not tags:
            tags.append("中立")
        if n in world.guarantee_of(name):
            tags.append("保障我")
        if world.guarantees.get(name) and n in world.guarantees[name]:
            tags.append("我保障")
        # 是否接壤
        border = False
        for (x, y) in world.own_tiles(name):
            if any(world.owned_by(*p) == n for p in world.neighbors(x, y)):
                border = True
                break
        letters = sum(1 for m in world.mailbox.get(name, []) if m["from"] == n)
        vis = [a for a in world.armies if a["owner"] == n and world.visible_to(name, a["x"], a["y"])]
        lines.append(
            f"  {n}：{'、'.join(tags)}{'，接壤' if border else '，不接壤'}"
            f" 你收其来信 {letters} 封" + (f"，视野内有其军队 {len(vis)} 支" if vis else "")
        )
    return "\n".join(lines)


def observer_board(world) -> str:
    """Observer 全景大面板：各国国库/储备/国土/军队/关系/近期通信，一屏尽览。"""
    alive = world.alive()
    L = [f"══════ 世界全景 · 第 {world.turn} 回合 · 现存 {'、'.join(alive)} ══════"]
    L.append("世界市场: " + "  ".join(f"{g}{world.market_price(g)}" for g in GOODS_DISPLAY))
    if world.blocs:
        L.append("联盟: " + world.bloc_desc())
    for n in alive:
        r = world.nations[n].res
        L.append(f"◆ {n}：国库{r['黄金']} 粮{r['粮食']} 木{r['木头']} 矿{r['矿石']} "
                 f"油{r['石油']} 装{r['装备']} 补给仓{r['补给']} | {world.econ_summary.get(n,'')}")
        L.append(f"   国土 {len(world.own_tiles(n))} 块 | 军队 {len(world.nation_armies(n))} 支 | {world.rel_desc(n)}")
        msgs = []
        # 只提示"本回合新到"的信（避免旧信内容在全景头每回合重打）
        for m in [m for m in (world.mailbox.get(n) or []) if m.get("turn") == world.turn][-1:]:
            msgs.append(f"收[{m['from']}]「{m['text'][:50]}」")
        for m in [m for m in world.mail_pending if m["from"] == n and m["arrive"] == world.turn + 1][-1:]:
            msgs.append(f"寄[{m['to']}]「{m['text'][:50]}」")
        if msgs:
            L.append("   " + " ⏐ ".join(msgs))
    dead = [n for n in world.order if n not in world.nations]
    if dead:
        L.append("亡国: " + "、".join(dead))
    return "\n".join(L)


def observer_map(world) -> str:
    """Observer 整张世界地图：大写字母=该国占领并**按国别着色**，小写=无人地形（不着色）。

    颜色只落在"国家占领区"，好一眼看出势力版图；荒野/野人维持字母不着色。
    纯观感（各国 agent 看不到这张图），用支持 ANSI 的终端看 mp_map.txt 即为彩色。
    """
    palette = ["91", "32", "94", "35", "96", "33", "92", "34", "95", "93", "36", "97"]
    ch = {}
    col = {}
    j = 0
    for i, n in enumerate(world.order):
        if n in world.nations:
            ch[n] = chr(65 + i) if i < 26 else str(i - 25)
            col[n] = palette[j % len(palette)]
            j += 1

    def cell(x: int, y: int) -> str:
        o = world.owned_by(x, y)
        if o:
            return f"\033[{col[o]}m{ch[o]}\033[0m"  # 占领区：着色大写字母
        return world.ter_char(x, y).lower()          # 荒野：不着色小写

    rows = ["".join(cell(x, y) for x in range(world.size)) for y in range(world.size)]
    legend = " ".join(f"\033[{col[n]}m{ch[n]}\033[0m={n}" for n in world.order if n in ch)
    return "\n".join(rows) + f"\n地图例：{legend} | 小写p/f/h/m/d=平原/森林/丘陵/山地/沙漠（无人）野人亦在其上"


def _help_sections() -> list[tuple[str, str]]:
    """规则全文（=README 的游戏规则），按主题分节，供 rules 查询按需返回。"""
    ter = "\n".join(
        f"  {k}：防御{v['defense']:+d}% · 建设惩罚{v['build_penalty']:+d}%"
        for k, v in TERRAIN_STATS.items()
    )
    bld = []
    for nm, info in BUILDINGS.items():
        cost = "、".join(map(str, info["cost"])) if isinstance(info["cost"], list) else info["cost"]
        cap = ("上限=本地" + info["cap_resource"]) if info["cap_resource"] else "任地可建"
        k = info["kind"]
        if k == "castle":
            note = f"城堡每级 +{CASTLE_DEFENSE_PER_LEVEL}% 防御，最多 L{info['max_level']}"
        elif k == "extract":
            note = "每回合产出 " + "、".join(f"{g}x{a}" for g, a in info["outputs"].items())
        elif k == "gold":
            note = "每回合 +" + str(info["outputs"].get("黄金", 0) * MARKET["黄金"]) + " 金入国库"
        elif k == "energy":
            note = "耗 " + "、".join(f"{f}x{a}" for f, a in info["fuel"].items()) + \
                   f" → 发 {info['energy_out']} 电（电不存储）"
        elif k == "factory":
            note = "维持1电；投 " + "、".join(f"{f}x{a}" for f, a in info["inputs"].items()) + \
                   " 产 " + "、".join(f"{g}x{a}" for g, a in info["outputs"].items())
        elif k == "townhall":
            note = ("维持1电；每座每回合 = 5金基础 + 该地块每座建筑×1金（不含自身，地越盖越值）"
                    "入国库；需本地已用建筑位≥6、每地块限1座")
        else:  # barracks
            note = "维持1电；每兵营每回合可征 1 支军队（耗 10粮 + 5装）；需本地已用建筑位≥3（含在建）"
        bld.append(f"  {nm}：造价 {cost}金 + {info['wood']}木 · {cap} · {note}")
    sections = [
        ("总览", (
            "EU4式 大地图国战：每人从 5 块地起家，拓荒/建设/生产/建军，可对他国结盟或开战。"
            "回合制：每回合你行动（可做多件事）→ 过回合统一结算（产出/电网/战斗/补给/市场回归）。"
            "地皮名字=ID，坐标 1-based。你能看的是自己地盘+相邻一圈（有联盟则连盟友的地盘也看得到）；他国国力只能推测。"
            "想细看任何机制就带主题调 rules，例如 rules(建筑) rules(联盟) rules(战斗)。"
        )),
        ("地形", ter + "\n  占地一律走 atk：派军队进格——有守军打赢即占，敌人=0 进驻即占；"
                        "mv 只挪位不占地；没有『凭空拓荒』命令。"),
        ("建筑与造价", "\n".join(bld) + "\n  每地块 20 建筑位；每地块每回合限建 1 座；"
                                        "建好后下一回合才生效（在建中）。"
                                        "\n  表中造价为平原基准价；实际金价按地块地形建设惩罚上浮"
                                        "（如山地 ×1.5，只加金不加木），匈奴再乘 1.3。"),
        ("经济与能源", (
            "全国制：国库/木材/粮矿油装补给都在你账上（res 面板）。"
            "电网全国且不存储：能源厂发电；补给厂/装备厂/兵营/市政厅都要耗电维持，"
            "发电 < 维持则这些高级建筑全部停摆（能源厂除外）。"
            "补给厂(粮1+矿1→补给2)；装备厂(矿1+油1→装备2)；补给仓每军每回合耗 1，空则军队挨饿。"
            "黄金矿场是稳定产金；市政厅(需本地已用位≥6·限1座·耗1电)每座每回合 = 5金基础"
            " + 该地块每座建筑×1金（不含自身，城越满越值）；"
            "也可在 world market 卖物资换金（卖得越多价压越低）。"
        )),
        ("军队与战斗", (
            "每军 100HP；兵营征召，每兵营每回合 1 支。兵种：步兵(耗10粮+5装，动1格/回合，耗补给1/回合)、"
            "骑兵(耗12粮+12装，动2格/回合，耗补给2/回合)。"
            "军队 id **各国独立编号、从 1 递增且阵亡不回收**：历史上的 #n 永远指同一支军队，"
            "引用一律以最近一次 query army 面板为准。"
            f"交战 = atk 冲入；每回合掷骰结算一轮；每军基础伤害 {ARMY_ATTACK_DAMAGE}，"
            "受守方地形+城堡防御%修正（地形与城堡为**相乘**叠加，山地+城堡L5≈75%而非100%）、总伤害分摊；攻方在敌地无加成。"
            "**多势力交战（进攻方不纯联合）**：同格多方各打各的敌人（互相宣战才互打）、每方掷自己的骰、"
            "伤害均分给各敌人；地形减伤只给格主/野人；野人只守无主格、只打进攻方；唯一幸存且野人已清的一方占地，多方都活则混战继续。"
            "撤出攻守对等：交战中的军队（含防守方守军）要离开战场一律用 retreat——挨敌方一击（约半回合战损）+ 耗移动；"
            "撤退固定只能退相邻 1 格，四周无合法撤退点（己方/同盟/无人荒地）则无法撤退；"
            "mv 不能从交战地撤离（会被拦）。守军全撤走/全灭时，进攻方自动占领该地（守军弃城即陷）。"
            "交战中双方（含守军）一律不回血。"
            "打赢守军→该地归你；无守军的空地/敌空城用 atk 直接进驻占领（mv 不占地）。"
            f"非交战且补给够时每回合回血 +{ARMY_HEAL_PER_TURN}HP；断粮则 -{ARMY_STARVE_DAMAGE}HP 可能饿毙。"
            "野人=无人荒地守军（100HP、自给自足、不主动打）。"
        )),
        ("联盟与核心领土", (
            "联盟（多边实体，取代旧双边同盟）：bloc_found(name=联盟名, tos=[创始成员…]) 发起，"
            "全体创始成员 respond_proposal 接受后才成立（任一拒绝即流产），发起者自动成为盟主（联盟主体）。"
            "入盟：bloc_join(name=联盟名) 申请，现成员多数决投票通过即加入；一国同时只属一个联盟。"
            "退盟：bloc_leave 单方面立即退出、无须任何人同意（已参战的战线不因此退出；滞留在前盟友领土的"
            "军队回合末自动遣返）。盟主退盟/亡国时由加入最早的剩余成员继承盟主之位。"
            "盟内效果：互通领土（自由通行、合法撤退地）、互不攻击、共享视野（盟友地盘及其相邻一圈你都看得见）、"
            "成员之间的外交动作（写信/馈赠/换图/投票/回应邀约）全部免费。"
            "战争：联盟成员不能擅自开战——declare_war 自动转为宣战投票，赞成过半（多数决）即全盟对目标宣战"
            "（盟主为进攻主导、全体成员为进攻跟随方）；防守不需要投票：任一成员被打，全盟自动参战。"
            "传导无限跳：宣战时守侧按 保障/共同防御/联盟 的传递闭包自动参战（A 保 B、B 盟 C → 打 B 时 C 也上）；"
            "防守义务优先：与进攻方的保障/共同防御自动解除后参战；联盟成员永不会被拖去打自家盟友。"
            "议和：每侧谈判代表 = 主导者（宣战方/被宣战方），主导者有联盟时 = 其盟主（联盟主体身份）——"
            "普通成员与跟随方不能单独议和；盟主提出或接受议和都须经联盟投票（多数决）；主导者议和则整条战线停战。"
            "投票：vote(投票id, true/false) 表态；发起者默认投赞成；逾期未决（下一回合结算前）即作废。"
            "核心领土：每块地有核心归属（land 面板 ♥ 标记=本国核心）；每次战争结束按参战各国实际持有重算核心"
            "——议和时的版图即新核心（战时丢的地，只要同战线盟友夺回就还是你的；议和割出去的地则归对方核心）。"
            "自动归还：同联盟且同战线（同一场战争同一侧）的盟友占领了你的核心领土时，立即自动归还给你，"
            "其驻军原地不动（盟国领土合法停留）；不同战线时（如盟友自己单独打的）占领者可以留下。"
        )),
        ("外交", (
            "国家关系：中立=不能入境也不能攻击对方；联盟=互通领土+互不攻击（详见【联盟与核心领土】）；"
            "宣战：对方必须应战，即刻生效；被宣战方的『保障独立/共同防御/联盟』关系按传递闭包自动参战打你"
            "（无限传导：A 保 B、B 盟 C，你打 B 则 C 也上）。"
            "共同防御=遭攻自动并肩；保障独立=你保它，别人打它你参战。"
            "战争分主导者：宣战方=进攻主导、被宣战方=防御主导，因保障/共同防御/联盟自动参战的是跟随方；"
            "议和只能由双方谈判代表提出/接受（主导者，或主导者的盟主），主导者议和则整条战线（含跟随方）停战。"
            "求和(offer_peace)：pay=你赔钱、demand=你索款、white=白和；接受即整条战线停战。"
            "休战时长由求和双方自行约定（offer_peace 的 truce 参数，0=不休战）；接受后 N 回合内"
            "双方（含跟随方）不得再互相宣战。一方灭亡后强制全天下休战 10 回合（防连环征服）。"
            "断盟/停战后滞留在对方领土的军队会自动遣返（每回合按兵种速度往家走：步 1 格/骑 2 格）。"
            "外交不一定要等到被打：先 countries 看清对象，主动发信、提结盟、换情报，都是合法手段。"
            "也可用 gift 把本国资源馈赠对方（粮木矿油装补给或黄金，本回合垫支、下回合到账）——示好、资助盟国、买通都行。"
            "还能用 share_map 把你的整张已知地图（全部坐标）发给对方，对方下回合在 query panel=intel 收到——换情报、亮家底、协调攻守都用得上。"
            "外交是有成本的：每成功一次外交动作（提议/回应/断盟/保障/宣战/求和/换图/馈赠）扣基础 10 金，"
            "写信(send_letter)单独 20 金。"
            "情报战：如果你不想开口问（懒得谈、不想欠人情）、又钱多，可用 spy(间谍) 花100金刺探别国，"
            "3回合后盗回其国库/收入/全部建设底细、粗略军情（仅各兵种数量，位置未知）、整张已知地图（地图进 query panel=intel 看）；"
            "⚠ 间谍**不含军队信息**——敌军的数量/兵种/位置侦察不到，只能靠换图、边地观察或正面交战得知——"
            "打谁、敲谁、开战时机都心中有数。"
        )),
        ("信箱", (
            "send_letter 可给任何别国写信（结盟邀约/和谈/威胁/情报交换），**每次 20 金、成功即扣、下回合送达**"
            "——这是最贵的外交动作（是普通外交 10 金的两倍）。**每封信寄出前先算账：这 20 金值不值？**"
            "预期收益（勒索要到的贡品/结盟带来的安全/关键情报/逼降止损）明显 > 20 金才写；说不出收益的信不要写。"
            "写信前再 query panel=res 看国库：**国库 <100 金别写信**；"
            "能并进一次正式外交提议（10 金）的话就别单独写信，更别拿写信闲聊。"
            "收到信要在 diplomacy/mail 面板回应——不回信，对方可能以为你拒绝。"
        )),
        ("市场", (
            "世界市场 buy/sell：黄金是货币；可交易粮/木/矿/油/装/补给。"
            "价格受供需影响：买→推高、卖→压低，整笔按成交后价格结算；每回合向基准价回归。"
            "市场深度随现存国家数缩放：国家越多，单笔买卖对市价的冲击越小。"
            "基准价：" + "  ".join(f"{g}{MARKET[g]}" for g in GOODS_DISPLAY) + "。分批慢慢卖比一次砸盘划算。"
        )),
        ("回合与存档", (
            "end_turn 结束你的本回合。每回合结算会：落地在建建筑→产出/电网→战争→补给/回血→"
            "遣返→市场回归。存档每回合自动写 mp_save.json，随时可中断续局。"
            "国策规划：用 plan 制定/修订（常驻上下文【国策规划】）；没有国策、或每 10 回合"
            f"未修订（超过 {PLAN_MAX_TURNS} 回合）时，end_turn 会被拦下，先 plan 再结束。"
            "计划建议涵盖 经济发展/军事规划/情报管理/外交方向 四方面。"
        )),
    ]
    return sections


def rules_text(world, topic: str = "") -> str:
    """rules tool：按主题返回规则段落；主题识别不了就返回全文（不设限）。"""
    t = (topic or "").strip()
    secs = _help_sections()
    labels = {
        "建筑": "建筑", "建造": "建筑", "兵营": "建筑", "农场": "建筑", "城堡": "建筑",
        "工厂": "建筑", "能源": "建筑", "电厂": "建筑", "造价": "建筑",
        "地形": "地形", "资源": "地形", "拓荒": "地形", "领土": "地形",
        "经济": "经济与能源", "电": "经济与能源", "能源": "经济与能源", "补给": "经济与能源",
        "装备": "经济与能源",
        "军队": "军队与战斗", "战斗": "军队与战斗", "战争": "军队与战斗", "征兵": "军队与战斗",
        "军队移动": "军队与战斗", "攻击": "军队与战斗", "野人": "军队与战斗",
        "外交": "外交", "保障": "外交", "宣战": "外交", "求和": "外交",
        "共同防御": "外交", "休战": "外交",
        "联盟": "联盟与核心领土", "同盟": "联盟与核心领土", "入盟": "联盟与核心领土",
        "退盟": "联盟与核心领土", "盟主": "联盟与核心领土", "投票": "联盟与核心领土",
        "核心": "联盟与核心领土", "归还": "联盟与核心领土", "视野": "联盟与核心领土",
        "信箱": "信箱", "信": "信箱", "邮件": "信箱",
        "市场": "市场", "买卖": "市场", "价格": "市场", "交易": "市场",
        "回合": "回合与存档", "存档": "回合与存档", "结算": "回合与存档",
    }
    picks = []
    for key, label in labels.items():
        if key in topic:
            if label not in picks:
                picks.append(label)
    if picks:
        canon = {"建筑": "建筑与造价"}  # 关键词 → 章节真实标题
        picks = [canon.get(p, p) for p in picks]
        return "\n\n".join(f"【{label}】\n{text}" for label, text in secs if label in picks)
    return "\n\n".join(f"【{label}】\n{text}" for label, text in secs)


def _fmt_threats(world, name) -> str:
    """视野内（自家地盘+相邻一圈）的他国/野人军队。"""
    rows = []
    for a in world.armies:
        if a["owner"] == name:
            continue
        if not world.visible_to(name, a["x"], a["y"]):
            continue
        rows.append(f"{a['name']}({a['owner']}) {a['hp']}HP @({a['x']+1},{a['y']+1})")
    return ("视野内的敌军/守军:\n  " + "\n  ".join(rows)) if rows else "视野内没有他国军队"


def _fmt_news(world, name) -> str:
    ev = world.events_for(name, limit=10)
    return ("近讯:\n  " + "\n  ".join(ev)) if ev else "近讯: 暂无"


def _fmt_memory(world, name) -> str:
    """本国近 10 回合小结纪事（私有记忆，别国不可见）。"""
    mem = world.summaries.get(name, [])
    if not mem:
        return "（尚无往回合小结）"
    return "\n".join(f"  [第{m['turn']}回合] {m['text']}" for m in mem[-10:])


def _fmt_plan(world, name) -> str:
    """国策规划（常驻上下文）：无 plan 不能结束回合；每 PLAN_MAX_TURNS 回合须修订。"""
    pl = world.plans.get(name)
    if not pl or not str(pl.get("text", "")).strip():
        return ("（尚未制定）——结束回合(end_turn)前必须先 plan(content=…) 制定国策；"
                "可从 经济发展 / 军事规划 / 情报管理 / 外交方向 四方面写明目标")
    since = world.turn - pl.get("turn", world.turn)
    flag = ""
    if since >= PLAN_MAX_TURNS:
        flag = f" ⚠ 已 {since} 回合未修订（每 {PLAN_MAX_TURNS} 回合必须修订一次才能结束）"
    elif PLAN_MAX_TURNS - since == 1:
        flag = f" ⚠ 本回合必须修订（下回合即满 {PLAN_MAX_TURNS} 回合）"
    return f"[第{pl.get('turn')}回合制定/修订]{flag}\n  {pl['text']}"


def _fmt_intel_hint(world, name) -> str:
    """收到的地图情报摘要（简短提示，完整见 query panel=intel）。"""
    ms = world.maps.get(name, [])
    if not ms:
        return "无（可用 share_map 与别国互发地图，或 spy 间谍偷地图，下回合到账）"
    last = ms[-1]
    return f"收到 {len(ms)} 张，最新来自 {last['from']}（第{last['turn']}回合）；完整内容见 query panel=intel"


def _fmt_intel(world, name) -> str:
    """完整地图情报：最近收到的别国地图。"""
    ms = world.maps.get(name, [])
    if not ms:
        return "（你尚未收到别国的地图情报）"
    out = [f"收到 {len(ms)} 张地图情报（按到达先后）:"]
    for m in ms[-3:]:
        out.append(f"── 第{m['turn']}回合 {m['from']} 的地图 ──")
        out.append(m["text"])
    return "\n".join(out)


def _fmt_spy_hint(world, name) -> str:
    """收到的经济情报摘要（完整见 query panel=spy）。"""
    es = world.econ_intel.get(name, [])
    if not es:
        return "无（可用 spy 花100金刺探别国，3回合后到手经济+粗略军情数量+地图）"
    last = es[-1]
    return f"{len(es)} 份，最新 {last['from']}（第{last['turn']}回合）；完整见 query panel=spy"


def _fmt_spy(world, name) -> str:
    """完整经济情报：最近拿到的别国经济底细。"""
    es = world.econ_intel.get(name, [])
    if not es:
        return "（你尚未拿到任何经济情报）"
    return "\n".join(m["text"] for m in es[-2:])


def _gval(world, good: str, amt: int) -> float:
    """按当前市价把 amt 单位 good 折成金（黄金固定按 MARKET 兑换额）。"""
    if amt <= 0:
        return 0.0
    if good == "黄金":
        return amt * MARKET["黄金"]
    p = world.prices.get(good)
    return amt * (p if p is not None else float(MARKET.get(good, 0)))


def _econ_building(world, building: str) -> str:
    """单个建筑的经济核算：按当前市价给 造价(折金)/每回合毛利/回本时间。"""
    info = BUILDINGS[building]
    wp = world.prices.get("木头", float(MARKET["木头"]))
    cost = info["cost"] if isinstance(info["cost"], int) else info["cost"][0]
    capex = cost + info["wood"] * wp
    k = info["kind"]
    if k == "castle":
        return (f"{building}: L1造价 {cost}金+{info['wood']}木(折{capex:.0f}金) · "
                f"每级+{CASTLE_DEFENSE_PER_LEVEL}%防御，不产金")
    if k in ("extract", "gold"):
        net = sum(_gval(world, g, a) for g, a in info["outputs"].items())
        pb = f"{capex / net:.0f}回合" if net > 0 else "—"
        tag = "固定+金" if k == "gold" else "折金"
        return f"{building}: 造价折{capex:.0f}金 · 每回合产出{tag}≈{net:.0f}金 · 回本≈{pb}"
    if k == "energy":
        fuel = sum(_gval(world, f, a) for f, a in info["fuel"].items())
        return (f"{building}: 造价折{capex:.0f}金 · 每回合烧燃料现值≈{fuel:.0f}金 "
                f"→ 产{info['energy_out']}电（电不交易，供高级建筑维持）")
    if k == "factory":
        inv = sum(_gval(world, f, a) for f, a in info["inputs"].items())
        outv = sum(_gval(world, g, a) for g, a in info["outputs"].items())
        net = outv - inv
        ec = info.get("energy", 0) * wp / 2  # 电按"1木发2电"的燃料成本估
        pb = f"{capex / net:.0f}回合" if net > 0 else "—"
        return (f"{building}: 造价折{capex:.0f}金 · 每回合投{inv:.0f}金现价料→产{outv:.0f}金现价货"
                f"（毛利{net:+.0f}金；另耗{info.get('energy', 0)}电≈{ec:.0f}金） · 回本≈{pb}")
    if k == "barracks":
        return (f"{building}: 造价折{capex:.0f}金 · 不自动产金，每兵营每回合可征1军"
                f"（步10粮5装 / 骑12粮12装，耗兵料另计）")
    if k == "townhall":
        return (f"{building}: 造价折{capex:.0f}金 · 每回合 = {TOWN_HALL_GOLD}金基础"
                f" + 该地块每座建筑×{TOWN_HALL_PER_SLOT}金（不含自身；10建筑城≈"
                f"{TOWN_HALL_GOLD + 10 * TOWN_HALL_PER_SLOT}金/回合）"
                f" · 需本地已用位≥6、每地块限1座、耗1电")
    return f"{building}: 无核算"


def _fmt_econ(world) -> str:
    """当前市价经济表：各建筑造价(折金)/毛利/回本，供建设决策。"""
    L = ["【经济核算 · 当前市价】单位建筑投入产出（木头按现价折金入造价，市政厅=5金基础+本地建筑×1金/回合）："]
    L.append("现价: " + "  ".join(f"{g}={world.prices.get(g):.1f}" for g in GOODS_DISPLAY))
    for b in BUILDINGS:
        L.append("  " + _econ_building(world, b))
    return "\n".join(L)


def full_state(world, name) -> str:
    return "\n".join([
        f"你（{name}）现在进行第 {world.turn} 回合的行动。",
        f"【国力】\n{_res_line(world, name)}",
        f"【国策规划】\n{_fmt_plan(world, name)}",
        f"【国土/视野】\n{_fmt_land(world, name)}",
        f"【军队】\n{_fmt_armies(world, name)}",
        f"【威胁】\n{_fmt_threats(world, name)}",
        f"【市场】\n{_fmt_market(world, name)}",
        f"【纪事(近10回合)】\n{_fmt_memory(world, name)}",
        f"【地图情报】\n{_fmt_intel_hint(world, name)}",
        f"【经济情报】\n{_fmt_spy_hint(world, name)}",
        f"【信箱】\n{_fmt_mail(world, name)}",
        f"【外交】\n{_fmt_diplomacy(world, name)}",
        f"【近讯】\n{_fmt_news(world, name)}",
    ])


def compact_state(world, name) -> str:
    r = world.nations[name].res
    n_army = len([a for a in world.armies if a["owner"] == name])
    box = world.mailbox.get(name, [])
    return (
        f"【刷新】你{name} 国库{r['黄金']} 粮{r['粮食']} 木{r['木头']} 矿{r['矿石']} "
        f"油{r['石油']} 装{r['装备']} 补给仓{r['补给']} | 军队{n_army} | 收信{len(box)} | "
        f"关系:{world.rel_desc(name)} | 近讯见 events。继续你的行动，做完了调 end_turn。"
    )


# ---------------------------------------------------------------------------
# 坐标/物品解析
# ---------------------------------------------------------------------------

def _tile_xy(world, actor, ref: str) -> tuple[int, int] | None:
    """ref 可形如 '5 6'（1-based 坐标）、地块名、或『名字 (x,y)』混合串。返回 0-based 坐标或 None。"""
    s = ref.strip()
    s = " ".join(s.replace("（", " ").replace("）", " ").replace("(", " ").replace(")", " ")
                  .replace(",", " ").split())
    toks = s.split()
    if len(toks) >= 2 and toks[0].isdigit() and toks[1].isdigit():
        return int(toks[0]) - 1, int(toks[1]) - 1
    # 名字夹带坐标注释时：直接按地块名查（取命中最长者）
    best, bl = None, -1
    for (x, y), t in world.tiles.items():
        nm = t.get("name")
        if nm and nm in s and len(nm) > bl:
            best, bl = (x, y), len(nm)
    return best


BUILD_NAMES = list(BUILDINGS)
GOOD_ALIAS = {
    "粮食": "粮食", "粮": "粮食", "food": "粮食", "grain": "粮食",
    "木头": "木头", "木": "木头", "wood": "木头",
    "矿石": "矿石", "矿": "矿石", "ore": "矿石",
    "石油": "石油", "油": "石油", "oil": "石油",
    "装备": "装备", "装": "装备", "equip": "装备", "weapon": "装备",
    "补给": "补给", "补": "补给", "supply": "补给",
}
KIND_MAP = {"pay": "pay", "赔款": "pay", "我方赔款": "pay",
            "demand": "demand", "索款": "demand", "要求赔款": "demand",
            "white": "white", "白和": "white"}
PACT_MAP = {"共同防御": "共同防御", "defense": "共同防御", "defensive": "共同防御"}


def _diplo_cost(world, actor: str, to: str | None) -> int:
    """外交基础费：对象是联盟成员 → 免费（盟内外交免费）；否则 10 金。"""
    if to and world.allied_between(actor, to):
        return 0
    return DIPLO_COST


# ---------------------------------------------------------------------------
# 工具执行（隔离：所有动作都以 actor=本国身份执行，引擎自会校验合法）
# ---------------------------------------------------------------------------

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


def _charge(world, actor: str, cost: int, fn, *a, **k) -> str:
    """外交/换图/馈赠/信件统一收费：先验国库，动作成功后扣费。

    动作合法失败（已同盟/已交战/物资不足等）不烧金，避免模型空试浪费；
    国库 < 费用时动作直接不发。
    """
    if world.res(actor, "黄金") < cost:
        return f"国库不足：此操作需 {cost} 金（你现 {world.res(actor, '黄金')}）"
    ok, msg = fn(*a, **k)
    if ok:
        world.add_res(actor, "黄金", -cost)
    return msg


def _exec(world, actor: str, tool: str, args: dict) -> str:
    """execute 的实质分发（兜底由 execute 负责）。"""
    if world.polity.get(actor) == "huns" and tool in HUNS_BLOCKED:
        return ("匈奴不搞这套外交：你只能用 通信勒索贡品(send_letter) / 宣战(declare_war) / "
                "要求投降或赔款求和(offer_peace / accept_peace / reject_peace)。「"
                + tool + "」被禁。")
    # ---- 面板（查询接口）
    if tool in ("query", "view", "panel", "查", "查询", "看", "面板"):
        which = str(args.get("panel", "all")).lower()
        return {
            "all": full_state(world, actor),
            "res": _res_line(world, actor),
            "land": _fmt_land(world, actor),
            "army": _fmt_armies(world, actor),
            "market": _fmt_market(world, actor),
            "mail": _fmt_mail(world, actor),
            "diplomacy": _fmt_diplomacy(world, actor),
            "countries": _fmt_countries(world, actor),
            "news": _fmt_news(world, actor),
            "threats": _fmt_threats(world, actor),
            "econ": _fmt_econ(world),
            "intel": _fmt_intel(world, actor),
            "spy": _fmt_spy(world, actor),
            "plan": _fmt_plan(world, actor),
        }.get(which, full_state(world, actor))

    # ---- 规则查询（= README 的游戏规则；匈奴另附 README 原文全文）
    if tool in ("rules", "规则", "help", "帮助"):
        text = rules_text(world, str(args.get("topic", "") or ""))
        if world.polity.get(actor) == "huns":
            text = ("【匈奴教义（必须贯彻）】\n" + HUNS_DOCTRINE + "\n\n" + text
                    + "\n\n【README 原文（完整游戏文档，供检索细节）】\n" + _README_TEXT)
        return text

    # ---- 外交对象（先选一个非自己的国家）
    if tool in ("countries", "外交对象", "国家列表", "对手"):
        return _fmt_countries(world, actor)

    # ---- 经济核算（建设回报，按当前市价）
    if tool in ("econ", "核算", "经济核算", "预算", "回本"):
        b = str(args.get("building", "") or "")
        if b in BUILDINGS:
            return _econ_building(world, b)
        return _fmt_econ(world)

    # ---- 领土（无"凭空占"：只有军队 mv 移入"敌人=0"的地格才占地）
    if tool in ("expand", "拓荒", "activate"):
        return "没有单独占地命令：占地一律走 atk——派军队 attack 目标格，若那格没有守军/敌军（敌人=0）军队直接进驻占领；有野人/敌军则打赢后自动占地。mv 只挪位置、不占地。"

    # ---- 建设 / 征兵
    if tool in ("build", "建", "建造"):
        ref = str(args.get("tile", ""))
        xy = _tile_xy(world, actor, ref)
        if xy is None:
            return f"地块引用无效：{ref}（用坐标如 '5 6' 或自家地块名）"
        ok, msg = world.build(actor, xy[0], xy[1], str(args.get("building", "")))
        return msg
    if tool in ("recruit", "征兵", "r"):
        ref = str(args.get("tile", ""))
        xy = _tile_xy(world, actor, ref)
        if xy is None:
            return f"地块引用无效：{ref}"
        ok, msg = world.recruit(actor, xy[0], xy[1], int(args.get("n", 1)),
                                str(args.get("kind", "步") or "步"))
        return msg

    # ---- 军队
    if tool in ("move", "mv", "移动"):
        aid = int(args.get("army_id", 0))
        try:
            x, y = int(args["x"]) - 1, int(args["y"]) - 1
        except (KeyError, TypeError, ValueError):
            return "需要目标坐标 x y（1-based）"
        ok, msg = world.move(actor, aid, x, y)
        return msg
    if tool in ("attack", "atk", "进攻"):
        aids = args.get("army_ids", args.get("army_id"))
        if isinstance(aids, int):
            aids = [aids]
        aids = [int(a) for a in aids] if isinstance(aids, list) else []
        try:
            x, y = int(args["x"]) - 1, int(args["y"]) - 1
        except (KeyError, TypeError, ValueError):
            return "需要目标坐标 x y"
        ok, msg = world.attack(actor, aids, x, y)
        return msg
    if tool in ("retreat", "撤", "撤出"):
        aid = int(args.get("army_id", 0))
        try:
            x, y = int(args["x"]) - 1, int(args["y"]) - 1
        except (KeyError, TypeError, ValueError):
            return "需要撤退目标坐标 x y"
        ok, msg = world.retreat(actor, aid, x, y)
        return msg

    # ---- 市场
    if tool in ("buy", "买"):
        g = GOOD_ALIAS.get(str(args.get("good", "")).lower())
        if g is None:
            return "物资名无效：" + "、".join(GOODS_DISPLAY)
        ok, msg = world.buy(actor, g, int(args.get("qty", args.get("amount", 0))))
        return msg
    if tool in ("sell", "卖"):
        g = GOOD_ALIAS.get(str(args.get("good", "")).lower())
        if g is None:
            return "物资名无效：" + "、".join(GOODS_DISPLAY)
        ok, msg = world.sell(actor, g, int(args.get("qty", args.get("amount", 0))))
        return msg

    # ---- 信箱（信件单独 20 金，成功才扣；收信人为联盟成员 → 免费）
    if tool in ("send_letter", "写信", "letter"):
        to = str(args.get("to", ""))
        text = str(args.get("content", ""))
        return _charge(world, actor, 0 if world.allied_between(actor, to) else LETTER_COST,
                       world.send_mail, actor, to, text)

    # ---- 外交馈赠（本国储备垫支赠他国，下回合到账；另扣 10 金手续费）
    if tool in ("gift", "赠送", "赠予", "馈赠"):
        to = str(args.get("to", ""))
        g = GOOD_ALIAS.get(str(args.get("good", "")).lower())
        if g is None:
            g = {"黄金": "黄金", "金": "黄金", "gold": "黄金"}.get(str(args.get("good", "")).lower())
        if g is None:
            return "物资名无效（可赠：" + "、".join(GOODS_DISPLAY) + " 或 黄金）"
        try:
            n = int(args.get("qty", args.get("amount", 0)))
        except (TypeError, ValueError):
            return "数量需为整数"
        return _charge(world, actor, _diplo_cost(world, actor, to), world.gift, actor, to, g, n)

    # ---- 交换地图（把你的整张已知地图发给对方，下回合到账对方 intel；对象为盟友时免费）
    if tool in ("share_map", "交换地图", "送图", "发地图"):
        to = str(args.get("to", ""))
        return _charge(world, actor, _diplo_cost(world, actor, to), world.share_map, actor, to)

    # ---- 经济间谍（100金，3回合后盗回目标经济情报+粗略军情+地图进 intel；不能对自己用）
    if tool in ("spy", "经济间谍", "间谍", "刺探"):
        return world.spy(actor, str(args.get("to", "")))[1]

    # ---- 外交（每成功一次扣基础 10 金）
    if tool in ("propose", "提议"):
        to = str(args.get("to", ""))
        kind = PACT_MAP.get(str(args.get("kind", "")).lower(), args.get("kind"))
        return _charge(world, actor, DIPLO_COST, world.propose_pact, kind, actor, to)
    if tool in ("respond_proposal", "回应邀约"):
        pid = int(args.get("proposal_id", args.get("id", 0)))
        accept = str(args.get("accept", "")).lower() in ("true", "yes", "1", "接受", "是")
        p = next((x for x in world.proposals if x["id"] == pid), None)
        to = p["a"] if p else None
        cost = _diplo_cost(world, actor, to)
        if accept:
            return _charge(world, actor, cost, world.accept_pact, actor, pid)
        return _charge(world, actor, cost, world.reject_pact, actor, pid)
    if tool in ("break_alliance", "断盟"):
        to = str(args.get("to", ""))
        return _charge(world, actor, DIPLO_COST, world.break_pact, "同盟", actor, to)

    # ---- 联盟（多边实体：起名结盟 / 申请入盟 / 单方面退盟 / 联盟投票）
    if tool in ("bloc_found", "结盟", "发起结盟"):
        tos = args.get("tos", args.get("to", []))
        if isinstance(tos, str):
            tos = [tos]
        tos = [str(t).strip() for t in (tos or []) if str(t).strip()]
        return _charge(world, actor, DIPLO_COST, world.propose_bloc, actor,
                       str(args.get("name", "")), tos)
    if tool in ("bloc_join", "入盟", "申请入盟"):
        return _charge(world, actor, DIPLO_COST, world.bloc_join, actor,
                       str(args.get("name", args.get("bloc", ""))))
    if tool in ("bloc_leave", "退盟", "退出联盟"):
        return _charge(world, actor, DIPLO_COST, world.bloc_leave, actor)
    if tool in ("vote", "投票"):
        vid = int(args.get("vote_id", args.get("id", 0)))
        approve = str(args.get("approve", args.get("accept", ""))).lower() in (
            "true", "yes", "1", "赞成", "同意", "接受", "是")
        return _charge(world, actor, 0, world.cast_vote, actor, vid, approve)  # 盟内投票免费
    if tool in ("break_defense", "解除共同防御"):
        to = str(args.get("to", ""))
        return _charge(world, actor, DIPLO_COST, world.break_pact, "共同防御", actor, to)
    if tool in ("guarantee", "保障独立"):
        to = str(args.get("to", ""))
        return _charge(world, actor, DIPLO_COST, world.declare_guarantee, actor, to)
    if tool in ("cancel_guarantee", "撤回保障"):
        to = str(args.get("to", ""))
        return _charge(world, actor, DIPLO_COST, world.cancel_guarantee, actor, to)
    if tool in ("declare_war", "宣战"):
        to = str(args.get("to", ""))
        return _charge(world, actor, DIPLO_COST, world.declare_war, actor, to)
    if tool in ("offer_peace", "求和"):
        to = str(args.get("to", ""))
        kind = KIND_MAP.get(str(args.get("kind", "")).lower(), args.get("kind"))
        gold = int(args.get("gold", 0) or 0)
        note = str(args.get("note", "") or "")
        truce = int(args.get("truce", 0) or 0)
        return _charge(world, actor, DIPLO_COST, world.offer_peace, actor, to, kind, gold, note, truce)
    if tool in ("accept_peace", "接受议和"):
        return _charge(world, actor, DIPLO_COST, world.accept_peace, actor,
                       int(args.get("offer_id", 0)))
    if tool in ("reject_peace", "拒绝议和"):
        return _charge(world, actor, DIPLO_COST, world.reject_peace, actor,
                       int(args.get("offer_id", 0)))

    # ---- 国策规划（常驻上下文；无 plan 或每 PLAN_MAX_TURNS 回合未修订都不能结束）
    if tool in ("plan", "国策", "国策规划"):
        text = str(args.get("content", args.get("text", ""))).strip()
        if not text:
            return "plan 需要 content=你的国策一句话（常驻上下文作为长期目标，可随时修订）"
        is_new = actor not in world.plans
        world.plans[actor] = {"text": text, "turn": world.turn}
        act = "制定" if is_new else "修订"
        return (f"✅ 国策已{act}（第{world.turn}回合），常驻你的上下文。"
                f"每 {PLAN_MAX_TURNS} 回合须再修订一次，否则无法结束回合。"
                f"建议从 经济发展/军事规划/情报管理/外交方向 四方面写（可后续 plan 随时修订）。")

    # ---- 结束回合（必须带一句话小结 + 有效国策）
    if tool in ("end_turn", "结束回合", "done"):
        pl = world.plans.get(actor)
        if not pl or not str(pl.get("text", "")).strip():
            return ("本回合还不能结束：还没有国策规划。请先 plan(content=…) 制定国策"
                    "（常驻上下文作为长期目标；建议涵盖 经济发展/军事规划/情报管理/外交方向）。")
        since = world.turn - pl.get("turn", world.turn)
        if since >= PLAN_MAX_TURNS:
            return (f"本回合还不能结束：国策已 {since} 回合未修订（每 {PLAN_MAX_TURNS} 回合必须修订一次），"
                    f"请先用 plan(content=…) 更新国策。")
        summary = str(args.get("summary", "")).strip()
        if len(summary) < 4:
            return "本回合还没收尾：end_turn 必须带 summary=一句话，总结你这回合做了什么/立场（例如 summary=这回合建了两座农场并继续拓荒）。"
        world.log(f"{actor} 回合小结：{summary}", phase="行动", nation=actor)
        mem = world.summaries.setdefault(actor, [])
        mem.append({"turn": world.turn, "text": summary})  # 全留（每行很短），供旧回合前情回顾汇总
        return f"✅ 本回合结束（小结已记录：{summary}）"
    return f"未知工具 {tool}"


def log_tool(world, actor, tool, args, result):
    """把一次工具调用写进看海日志（Observer 能看到每个行动）。"""
    def _fmt(v):
        return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    a = args or {}
    a_str = " ".join(f"{k}={_fmt(v)}" for k, v in a.items())
    x = a.get("x")
    y = a.get("y")
    try:
        x = int(x) - 1 if x is not None else None
        y = int(y) - 1 if y is not None else None
    except (TypeError, ValueError):
        x = y = None
    world.action(actor, tool, a_str, result[:180], x=x, y=y)


# ---------------------------------------------------------------------------
# 工具 schema（OpenAI function calling）
# ---------------------------------------------------------------------------

def _props(schema: dict) -> dict:
    """把每个属性里的 `required: True` 提到顶层（属性内不允许出现 required）。"""
    props, req = {}, []
    for k, v in schema.items():
        pv = dict(v)
        if pv.pop("required", False):
            req.append(k)
        props[k] = pv
    d = {"type": "object", "properties": props}
    if req:
        d["required"] = req
    return d


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "query", "description": "查询接口：随时获取你的各面板。res=国库与储备 / plan=国策规划 / land=地皮(国土+可拓荒地) / army=军队 / market=世界市场(现价+持有) / econ=经济核算(各建筑造价毛利回本) / intel=收到的地图情报(全部坐标) / spy=经济间谍情报(别国经济底细) / mail=信箱 / countries=可选外交对象 / diplomacy=外交 / news=近讯 / threats=视野内敌军 / all=全部。每个行动后状态会变，拿不准就再查一次。",
        "parameters": _props({"panel": {"type": "string", "enum": ["all", "res", "plan", "land", "army", "market", "econ", "intel", "spy", "mail", "countries", "diplomacy", "news", "threats"], "description": "要查询的面板", "required": True}})}},
    {"type": "function", "function": {
        "name": "countries", "description": "列出所有可选外交对象（除你之外的每个国家：关系/是否接壤/有无来信）。外交动作前先用它选一个目标，再以 to=该国家 行动；绝不能对自己用外交工具。",
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "rules", "description": "查询完整游戏规则（相当于 README）：建筑造价与上限、地形、电网经济、军队战斗、外交、信箱、市场、回合存档。可带 topic 只取相关段（如 '兵营'、'外交'、'宣战'）；不带则返回全文。",
        "parameters": _props({"topic": {"type": "string", "description": "想查的主题（可选）"}})}},
    {"type": "function", "function": {
        "name": "econ", "description": "按当前市价核算建设回报：某建筑的 造价(折金)/每回合毛利/回本时间；不带 building 则输出全部建筑经济表。做建设/买卖决策前先算再定。",
        "parameters": _props({"building": {"type": "string", "enum": BUILD_NAMES, "description": "要核算的建筑名（可选；省则输出全部）"}})}},
    {"type": "function", "function": {
        "name": "build", "description": "在自己的一块地上建一座建筑。每地块每回合限建1座。建筑: 城堡/林场/农场/矿场/黄金矿场/石油厂/木材能源厂/石油能源厂/补给厂/装备厂/兵营/市政厅。采集类上限=本地资源量；补给厂/装备厂/能源厂任地可建（工业不挑地）；兵营需本地已用建筑位≥3；市政厅需本地已用位≥6且每地块限1。",
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标如 '5 6' 或自家地块名（land 面板有）", "required": True},
                              "building": {"type": "string", "enum": BUILD_NAMES, "description": "建筑名", "required": True}})}},
    {"type": "function", "function": {
        "name": "recruit", "description": "在自己有兵营且电网正常的地块征召军队，每兵营每回合1支。兵种 kind：步=步兵(10粮+5装，动1格/回合、耗补给1)；骑=骑兵(12粮+12装，动2格/回合、耗补给2)。",
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标 '5 6' 或名字", "required": True},
                              "n": {"type": "integer", "description": "征召数量（默认1）"},
                              "kind": {"type": "string", "enum": ["步", "骑"], "description": "兵种（默认 步）"}})}},
    {"type": "function", "function": {
        "name": "move", "description": "把一支自己的军队以自身为中心按兵种速度移动（步兵 1 格=3×3、骑兵 2 格=5×5），纯移动不占地。每回合每支限1次。**野地（无人荒地）是合法移动目标**：mv 可任意在野地移动，不会被守军/野人攻击（野人不主动攻击、路过不打）。中立国地盘不能进（先结盟/宣战）；交战中不能移动，须先 retreat 撤出。要占无守军的空地/敌空城，请用 attack（atk 会直接进驻占领）。",
        "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "attack", "description": "军队(按兵种速度可及：步1格/骑2格)冲入目标地块并交战——打赢该地守军自动占地；格上**无任何军队**则直接进驻占领；有他国军队但非你敌人（中立/第三方）不能进驻。同格多方同时开战则各打各的敌人（互相宣战才互打）。与别国开打需已宣战。",
        "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"}, "description": "参战本国军队id数组（各国独立从1编号）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "retreat", "description": "交战中的军队（含防守方守军）撤出——**固定只能退相邻 1 格**（所有人，不按兵种速度），当回合挨敌方一击（约半回合战损）。目标限 己方/同盟/无人荒地；四周无合法撤退点则无法撤退。mv 不能从交战地撤离；想脱离战场一律用 retreat。",
        "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "buy", "description": "从世界市场买物资花黄金。买=推高市价，整笔按推高后清仓价结算，越急买越贵。",
        "parameters": _props({"good": {"type": "string", "description": "物资：粮食/木头/矿石/石油/装备/补给", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "sell", "description": "向世界市场卖物资赚黄金。卖=压低市价，整笔按压低后清仓价结算；分批慢慢卖更划算。",
        "parameters": _props({"good": {"type": "string", "description": "物资", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "send_letter", "description": "给别国写信。**每次单独花 20 金（最贵的外交动作，普通外交只要 10 金）、成功即扣**；信件下回合才送达对方信箱。写信前先算账：这 20 金值不值？预期收益（贡品/结盟/情报/逼降）明显大于 20 金才写，说不出收益就别写；国库 <100 金不要写信；诉求能并进一次正式外交提议（10 金）就别单独写信。to 必须用 countries 选出的别国，不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "收信国名", "required": True},
                              "content": {"type": "string", "description": "信件正文", "required": True}})}},
    {"type": "function", "function": {
        "name": "gift", "description": "把本国储备赠给别国（to=countries 里的别国，不能是自己）：good=粮食/木头/矿石/石油/装备/补给 或 黄金，qty=数量。本回合垫支扣出、下回合到账；另扣 10 金外交手续费。示好/资助盟国/买通可用。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                              "good": {"type": "string", "description": "物资名", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "share_map", "description": "把你的整张已知地图（全部国土块+边界外可见块，含坐标）发给别国，对方下一回合在 query panel=intel 收到（外交基础费 10 金，成功才扣）。换情报/亮家底/协同步调可用。to=countries 里的别国，不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "spy", "description": "不想开口问（懒得谈、钱多）时派间谍刺探别国：花 100 金（国库不足会被拒），3 回合后拿回该国全部经济情报（query panel=spy 看——国库/储备、上回合收入、每一块地的建筑与在建）**、粗略军情（仅各兵种数量，军队位置/血量/番号不外泄）**，以及它的整张已知地图（进 query panel=intel）。目标不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "刺探对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "plan", "description": "制定或修订你的国策（长期战略目标），会永久常驻你的上下文（【国策规划】标记），直到你再次修订。⚠ 结束回合(end_turn)前必须已有国策；且每 10 回合必须修订一次，否则 end_turn 会被拦。建议按四方面写：经济发展（粮木矿油/建设/卖买）、军事规划（扩军/攻防/结盟）、情报管理（间谍/换图/来信研判）、外交方向（结盟/宣战/求和/馈赠立场）。",
        "parameters": _props({"content": {"type": "string", "description": "国策内容", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_found", "description": "发起结盟（多边联盟，取代旧双边同盟）：给联盟起名并邀请创始成员。全体创始成员 respond_proposal 接受后联盟成立（任一拒绝即流产），你自动成为盟主（联盟主体）。盟内效果：互通领土/互不攻击/共享视野/成员间外交免费；进攻战争须投票；议和由盟主出面投票；同战线自动归还核心领土。发起扣 10 金。",
        "parameters": _props({"name": {"type": "string", "description": "联盟名（1~12字，全局唯一）", "required": True},
                              "tos": {"type": "array", "items": {"type": "string"}, "description": "创始成员国名数组（至少1个，须为 countries 里的别国）", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_join", "description": "申请加入指定联盟：现成员多数决投票，通过即入盟（一国同时只属一个联盟；与该联盟成员交战中不能申请）。扣 10 金。",
        "parameters": _props({"name": {"type": "string", "description": "联盟名", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_leave", "description": "单方面退出所在联盟：立即生效、无须任何人同意（已参战的战线不因此退出；滞留在前盟友领土的军队回合末自动遣返）。扣 10 金。",
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "vote", "description": "对所在联盟进行中的投票表态（宣战/议和/入盟；多数决：赞成>半数即通过并立即执行；发起者默认已投赞成）。联盟成员投票免费。",
        "parameters": _props({"vote_id": {"type": "integer", "description": "投票id（diplomacy 面板有）", "required": True},
                              "approve": {"type": "boolean", "description": "赞成? true/false", "required": True}})}},
    {"type": "function", "function": {
        "name": "propose", "description": "向别国提议『共同防御』（仅守：它被打才自动并肩参战，你主动开战它不上）。全面结盟请用 bloc_found（起名的多边联盟）。to 必须用 countries 选出的别国，不能是自己；对方 respond_proposal 接受才生效。外交基础费 10 金（对象为盟友则免费），成功才扣。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                              "kind": {"type": "string", "enum": ["共同防御"], "description": "类型", "required": True}})}},
    {"type": "function", "function": {
        "name": "respond_proposal", "description": "回应收到的邀约（共同防御/结盟：accept=true 接受 / false 拒绝；联盟创始须全体接受才成立；对方是盟友时免费）。",
        "parameters": _props({"proposal_id": {"type": "integer", "description": "邀约id（diplomacy面板有）", "required": True},
                              "accept": {"type": "boolean", "description": "接受? true/false", "required": True}})}},
    {"type": "function", "function": {
        "name": "break_defense", "description": "单方面解除共同防御（成功扣 10 金）。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "guarantee", "description": "宣布保障别国独立：任何国家攻击它，你将自动参战（仅此单向、一跳）。它若与别国结成同盟/共同防御，你的保障会被自动升级解除；已有更高档时无须保障。不想履行参战义务时可解除保障（或断盟）退出。to=别国（不能自己）。成功扣 10 金。",
        "parameters": _props({"to": {"type": "string", "description": "被保障国", "required": True}})}},
    {"type": "function", "function": {
        "name": "cancel_guarantee", "description": "撤回独立保障（成功扣 10 金）。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "declare_war", "description": "对别国宣战（对方必须应战，即刻生效；成功扣 10 金外交费）。先 countries 选目标，to=别国（不能自己）。⚠ 联盟成员不能擅自开战：调用即自动转为**联盟宣战投票**（多数决通过后全盟参战、盟主为进攻主导）。战争传导（无限跳）：对方的保障/共同防御/联盟全体按传递闭包自动参战打你（A 保 B、B 盟 C → 打 B 则 C 也上）。若目标正与你方成员/共同防御对象交战，宣战会**并入其现有战线**当跟随方（跟随方不能单独议和，主导者议和整条战线停战）。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "offer_peace", "description": "向对方谈判代表求和（每侧代表=主导者；主导者有联盟时=其盟主；普通成员/跟随方不能谈，to=diplomacy 面板所示对方代表；成功扣 10 金外交费）。⚠ 盟主求和会先发起联盟投票，多数同意才正式提出。pay=我方向对方赔X金；demand=要求对方赔X金；white=白和。接受后整条战线（含跟随方）停战，且各方实际持有地块重算为核心领土。truce=休战回合数（0=不休战）。",
        "parameters": _props({"to": {"type": "string", "description": "对方谈判代表国", "required": True},
                              "kind": {"type": "string", "enum": ["pay", "demand", "white"], "description": "pay=我方赔款 / demand=要求对方赔款 / white=白和", "required": True},
                              "gold": {"type": "integer", "description": "赔款量（pay/demand 必填>0）"},
                              "truce": {"type": "integer", "description": "休战回合数（自行约定，0=不休战）"},
                              "note": {"type": "string", "description": "附加条件/说明（可选）"}})}},
    {"type": "function", "function": {
        "name": "accept_peace", "description": "接受对方求和（diplomacy 面板可看提议编号；只有被点名的一方=谈判代表能接受；成功扣 10 金外交费）。⚠ 若你是盟主，接受会先发起联盟投票，多数同意才正式生效。",
        "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}},
    {"type": "function", "function": {
        "name": "reject_peace", "description": "拒绝对方求和，战争继续（成功扣 10 金外交费）。",
        "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}},
    {"type": "function", "function": {
        "name": "end_turn", "description": "结束本国本回合的行动。⚠ 必填 summary：用一句话总结你这回合做了什么/当前立场（例如：summary=这回合建了两座农场并继续拓荒）。没有这句小结就不算结束本回合。",
        "parameters": _props({"summary": {"type": "string", "description": "一句话回合小结（必填，>=4字）", "required": True}})}},
]


def _huns_prompt(world, name) -> str:
    others = "、".join(n for n in world.alive() if n != name)
    return (
        "你是草原游牧帝国【" + name + "】（匈奴）的可汗，以劫掠、勒索、虚张声势维生。其余国家：" + (others or "（只剩你）") + "。\n\n"
        "【政体约束（硬性）】你不搞结盟/共同防御/保障/馈赠/交换地图那套外交。你能用的只有："
        "send_letter（写信威吓勒索贡品）、declare_war（宣战）、offer_peace（要求投降/赔款求和）、"
        "accept_peace / reject_peace（议和/拒绝）。\n"
        "【开局（事实）】你只有 6 骑兵、金 1000、补给 200（每骑每回合耗 2 补给，别饿空，否则 -10HP/回合）。"
        "你建建筑有 +30% 惩罚（别走种田流），但你的骑兵征召只要 8 粮+8 装（比别人便宜）。\n"
        "【生存（事实）】你不靠种田建厂活：靠勒索别国贡金与**补给**（你缺补给，6 骑每回合耗 12、"
        "200 开局撑不久，勒索要优先点名要补给）、抢无驻军之地、打完胜仗索要赔款，缺什么就从市场买卖补。"
        "补给仓空了会 -10HP/回合饿死。\n"
        "【教义（必须贯彻）】\n" + HUNS_DOCTRINE + "\n"
        "【信息】情报有迷雾，你只看得见自己地盘与相邻一圈；写信对象随时可用 countries 选。"
        "想细看机制就 rules 查。"
    )


def system_prompt(world, name) -> str:
    if world.polity.get(name) == "huns":
        p = _huns_prompt(world, name)
    else:
        p = _default_system_prompt(world, name)
    ep = world.extra_prompt.get(name)
    if ep:
        if world.turn < ep.get("until", world.turn):
            p += "\n\n【临时情报/密谕（20回合后仅剩总结）】\n" + ep.get("text", "")
        elif ep.get("summary"):
            p += "\n\n【遗留总结（前情之鉴，常驻）】\n" + ep["summary"]
    p += "\n\n【过往回合记录（含你的思考过程）仅作参考背景】勿重放旧命令/旧工具调用，一切以最新当前回合状态为准，按本回合行动。"
    return p


def _default_system_prompt(world, name) -> str:
    others = "、".join(n for n in world.alive() if n != name)
    return (
        "你是国家元首【" + name + "】，在一个 EU4 式大地图战略游戏里治国。其余国家：" + (others or "（只剩你）") + "。\n\n"
        "这局没有预设目标：富国、拓荒、称霸、报复、苟和都行，由你自己判断；每种选择都有后果，后果也由你承担。\n"
        "【回合】每回合你可用工具做很多事：建设/拓荒/征兵/调兵/打仗/买卖/外交/写信。你有一个常驻的【国策规划】"
        "：结束回合前必须先 plan 制定，且每 10 回合必须修订一次（建议涵盖 经济发展/军事规划/情报管理/外交方向）。"
        "做完用 end_turn 结束本回合，"
        "并在 summary 用一句话小结你这回合的作为。地理：每块地=1格，军队每回合限移动一次、"
        "按兵种速度以自身为中心移动（步兵 1 格=3×3、骑兵 2 格=5×5）；撤退( retreat )是例外——"
        "固定只能退相邻 1 格（不按兵种速度），交战中挨一击脱身，无合法撤退点（己方/同盟/荒地）则不能退。\n"
        "【资源用途（事实）】木头=建一切建筑+木材电厂燃料；粮=征兵(10/军)+补给厂原料；矿=装备厂+补给厂原料；"
        "油=装备厂原料+油电厂燃料；装=征兵(5/军)；补给=每军每回合耗1，仓空军队挨饿。\n"
        "【生产链（事实）】林场/农场/矿场/石油厂/黄金矿场=采集；木材厂(耗1木→2电)/油电厂(耗1油→5电)=发电，"
        "电不存储，电网不足则补给厂/装备厂/兵营/市政厅全停摆；补给厂(粮1+矿1→补给2)；装备厂(矿1+油1→装备2)；"
        "兵营(耗1电，需本地建筑位≥3)每回合可征1军(10粮5装)；黄金矿场+10金/回合；也可世界市场 buy/sell 换黄金。\n"
        "【扩张与战争（事实）】占地一律走 atk：派军进目标格，有守军(野人/敌军)就打赢再占、"
        "敌人=0 就直接进驻占领；mv 只是挪位置，不占地。荒地/敌空城都这样占。"
        "撤出攻守对等：交战中的军队（含防守方）离开战场一律用 retreat（挨敌方一击约半回合战损、固定只能退相邻1格）；mv 不能从交战地撤离；"
        "守军撤光时进攻方自动占领（弃城即陷）；交战中双方（含守军）一律不回血。"
        "军队非交战且补给够时每回合回25HP。中立(不结盟不交战)时你的军队进不了别国、也打不了别国；"
        "联盟=互通+互不攻击+共享视野；宣战对方必须应战；被宣战方的『保障独立/共同防御/联盟』关系按传递闭包自动参战打你（无限传导）。"
        "联盟成员开战必须先经联盟投票（多数决）；议和由各方谈判代表（主导者或其盟主）出面，盟主议和还须联盟投票；"
        "主导者议和则整条战线（含跟随方）停。同联盟且同战线的盟友夺回你的核心领土（land 面板 ♥ 标记）会自动归还给你。\n"
        "【外交（事实）】你与每个别国的关系独立：可保持中立、可提议共同防御或发起/申请加入联盟（对方可能接受或拒绝）、"
        "可单方保障它或撤回、可退盟（单方面、无须同意）、可宣战、战中可求和。联盟成员间外交（写信/馈赠/换图/投票）免费。"
        "来信可回应也可不回；邀约可接受可拒绝可冷处理；承诺可以兑现也可以背弃。这些都由你权衡。\n"
        "【信息】情报有迷雾：你只看得见自己地盘与相邻一圈（有联盟则连盟友的地盘也看得到）；"
        "他国国库/储备/全部军队你看不到，只能从来信、边界动静与其言行推断；"
        "他国来信未必可信，你也可说谎。\n"
        "【规则查询】完整玩法（造价/地形/战斗/外交/市场细则）随时可查：调用 rules，可带主题如 rules(外交)、rules(建筑)。\n"
        "【行动建议（自由）】动手前可用 query 看面板（res/land/army/market/countries 随时可查）；"
        "想清楚再调用工具。你可以边想边做，也可以只做一两件事。没有『应该』怎么做，只有你想要什么后果。"
    )


def _merge_same_role(msgs: list[dict]) -> list[dict]:
    """合并相邻同角色消息（不同 OpenAI 兼容端点对严格角色交替要求不一）。

    - user×user 合并（join content）；
    - 无 tool_calls 的 assistant×assistant 合并（content 与 reasoning_content 各自 join）；
    - 绝不合并 tool 消息（每条绑定唯一 tool_call_id）；
    - 绝不合并带 tool_calls 的 assistant（其 tool 响应必须紧随其后）。
    """
    out: list[dict] = []
    for m in msgs:
        role = m.get("role")
        if out and out[-1].get("role") == role:
            last = out[-1]

            def _join(x, y):
                a = str(x or "").strip()
                b = str(y or "").strip()
                return (a + "\n\n" + b) if a and b else (a or b)

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


def _store_turn_memory(world, name, messages, base: int, window: int) -> None:
    """把本回合新增的消息（messages[base:]）存入该国 turn_memory，只留最近 window 回合。

    只存本回合 append 的部分（base 之前是历史 replay），避免把整个历史嵌进每条记录、
    记录间二次方膨胀。首条加 user 回合标记，回放时分隔回合边界。
    """
    if name not in world.nations:
        return
    body = messages[base:]
    rec = [{"role": "user", "content": f"【第{world.turn}回合 行动记录】"}] + [dict(m) for m in body]
    mem = world.turn_memory.setdefault(name, [])
    mem.append({"turn": world.turn, "messages": rec})
    if len(mem) > window:
        del mem[:-window]


def build_context(world, name, window: int) -> list[dict]:
    """构造一国本回合的完整 LLM 上下文：
    [system] + [前情回顾: 窗口外回合的 end_turn 小结，至多 window 条] +
    [窗口内各回合完整记录 replay（含思考 reasoning_content）] + [本回合 fresh full_state]。
    """
    msgs: list[dict] = [{"role": "system", "content": engine_call(system_prompt, world, name)}]
    mem = world.summaries.get(name, [])
    old = [m for m in mem if m["turn"] < world.turn - window]
    if old:
        head = f"【前情回顾（第1~{old[-1]['turn']}回合 总结）】"
        lines = "\n".join(f"  第{m['turn']}回合：{m['text']}" for m in old[-window:])
        msgs.append({"role": "user", "content": head + "\n" + lines})
    for rec in world.turn_memory.get(name, []):  # 窗口内完整 replay（含思考/工具/结果）
        msgs.extend(rec["messages"])
    msgs.append({"role": "user", "content":
                 f"以上为过往回合记录，现在开始第 {world.turn} 回合行动。\n"
                 + engine_call(full_state, world, name)})
    return _merge_same_role(msgs)


# ---------------------------------------------------------------------------
# OpenAI 回合循环
# ---------------------------------------------------------------------------

def run_openai_turn(world, name, cfg, max_steps: int = 16, emit=None) -> int:
    """跑一国一回合：反复调 LLM 用工具，直到 end_turn/无工具/步数上限。返回执行次数。

    deepseek-v4-flash 这类推理模型把思考放在 reasoning_content（独立于 content），
    且可能连续多轮纯思考后才调用工具：这里把每轮思考回显给下一轮、并给足 token 预算，
    直到它真正用工具或宣告结束。
    """
    from openai import OpenAI
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"],
                    timeout=float(cfg.get("api_timeout", 180)))
    # SIGALRM 硬超时兜底：即使 SDK 因网络黑洞/服务端不回应而不抛，超时也会强制抛
    # （TimeoutError 会被下方 except 接住，整局看海不会因此挂死）
    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError("API 调用超时（> %ds）" % int(cfg.get("api_timeout", 180)))

    signal.signal(signal.SIGALRM, _timeout_handler)
    window = int(cfg.get("ctx_full_turns", 20))  # 跨回合持久上下文窗口：最近 N 回合完整保留（含思考），更早用回合小结
    messages = build_context(world, name, window)
    base = len(messages)  # 本回合新增消息的起点（base 之前是历史 replay，存储时不再重复）
    done = 0
    stall = 0  # 连续"只思考/空转"轮数
    agg: dict = {"calls": 0, "wall": 0.0, "stream": 0.0, "first": 0.0,
                 "maxgap": 0.0, "out_tokens": 0, "reason_tokens": 0}

    def _finish(d: int) -> int:
        if agg.get("calls"):
            speed = (agg["out_tokens"] / agg["stream"]) if agg["stream"] > 0 else 0.0
            world.log(
                f"📊 {name} 本回合: {agg['calls']}次调用 {agg['wall']:.0f}s｜"
                f"输出{agg['out_tokens']}tok(思考{agg['reason_tokens']})｜"
                f"首token均{agg['first'] / agg['calls']:.0f}s｜最长无输出{agg['maxgap']:.0f}s｜"
                f"真正输出{agg['stream']:.0f}s｜速度{speed:.1f}tok/s",
                phase="事件")
        _store_turn_memory(world, name, messages, base, window)
        return d

    for step in range(max_steps):
        if name not in world.nations:
            return _finish(done)
        try:
            extra = {}
            # deepseek-v4：thinking 开关 + reasoning_effort（low/medium/high）
            if "thinking" in cfg:
                extra["thinking"] = {"type": cfg["thinking"]}  # "enabled"/"disabled"
            if cfg.get("reasoning_effort"):
                extra["reasoning_effort"] = cfg["reasoning_effort"]
            api_timeout = int(cfg.get("api_timeout", 180))
            retries = int(cfg.get("api_retries", 3))
            backoff = float(cfg.get("api_retry_wait", 2.0))
            stream_stats = {}
            for attempt in range(1, retries + 1):
                try:
                    wall0 = time.time()
                    signal.setitimer(signal.ITIMER_REAL, api_timeout)
                    try:
                        stream = client.chat.completions.create(
                            model=cfg["model"], messages=messages,
                            tools=TOOL_SCHEMAS, tool_choice="auto",
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
                    finally:
                        signal.setitimer(signal.ITIMER_REAL, 0)
                    stream_stats["wall"] = time.time() - wall0
                    if first_t:
                        stream_stats["first"] = first_t - wall0
                        stream_stats["stream"] = max(0.0, last_t - first_t)
                    msg = {"content": c_s, "reasoning_content": r_s}
                    if tool_acc:
                        msg["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
                    break
                except Exception as e:
                    from openai import (APIStatusError, APIConnectionError,
                                        APITimeoutError, RateLimitError)
                    transient = (isinstance(e, (APIConnectionError, APITimeoutError, RateLimitError))
                                 or isinstance(e, TimeoutError)
                                 or (isinstance(e, APIStatusError) and 500 <= e.status_code < 600))
                    if attempt < retries and transient:
                        if emit:
                            emit(f"⚠ {name} 第{attempt}次调用失败({type(e).__name__})，"
                                 f"{backoff * attempt:.0f}s 后重试（共 {retries} 次）")
                        time.sleep(backoff * attempt)
                        continue
                    raise
        except Exception as e:
            messages.append({"role": "user", "content": f"（API 错误，若可继续请继续，否则 end_turn）: {e}"})
            if emit:
                tag = "超时" if isinstance(e, TimeoutError) else type(e).__name__
                emit(f"⚠ {name} API错误({tag}): {str(e)[:120]}")
            continue
        reasoning = (msg.get("reasoning_content") or "").strip()
        content = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []
        for _k in ("wall", "stream", "first", "maxgap", "out_tokens", "reason_tokens"):
            agg[_k] = agg.get(_k, 0.0) + (stream_stats.get(_k) or 0.0)
        agg["calls"] = agg.get("calls", 0) + 1
        if emit and reasoning:
            emit(f"💭 {name} 思考：{reasoning[:160].replace(chr(10),' ')}")
        if tool_calls:
            stall = 0
            asst: dict = {"role": "assistant", "content": msg.get("content")}
            if reasoning:
                asst["reasoning_content"] = reasoning
            asst["tool_calls"] = [
                {"id": tc["id"], "type": tc["type"],
                 "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}
                for tc in tool_calls
            ]
            messages.append(asst)
            acted_this = False
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
                    if emit:
                        a_s = " ".join(f"{k}={v}" for k, v in (args or {}).items())
                        emit(f"{world.turn}回合·{name} ◇ {fn} {a_s}")
                        emit(f"      ↳ {result}")
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                done += 1
                acted_this = True
                if name not in world.nations:
                    return _finish(done)
                if is_end:
                    # 只有带上有效的小结才算真结束；没带会被 execute 拦下，继续逼它补
                    if (str(args.get("summary", "")).strip()):
                        return _finish(done)
            if name in world.nations:  # 每次行动后都回填一次最新状态（默认塞查询）
                messages.append({"role": "user", "content": engine_call(compact_state, world, name)})
            continue
        # 没有工具调用：
        if content:
            # 有正文——当作宣告/收尾（把宣告也写进记录，跨回合记忆能回放这次收尾）
            asst = {"role": "assistant", "content": msg.get("content")}
            if reasoning:
                asst["reasoning_content"] = reasoning
            messages.append(asst)
            engine_call(world.log, f"{name} 宣告:「{content}」", phase="行动", nation=name)
            if emit:
                emit(f"🗣 {name} 宣告：「{content}」")
            return _finish(done)
        if reasoning:
            # 纯思考轮（无正文无工具）：把思考原文回喂，让模型接着想而不是每次从零大思考
            # （否则每轮重想一遍，又慢又贵——百万上下文模型输出 token 价高且不缓存）。
            messages.append({"role": "assistant", "content": None, "reasoning_content": reasoning})
            stall += 1
            if stall >= 8:
                messages.append({"role": "user",
                                 "content": "（请继续完成本回合：想好了就调用工具；若确实无事可做就 end_turn。）"})
                stall = 0
            continue
        # 空回复：催一次，若再空就结束
        messages.append({"role": "user", "content": "请决策并调用工具；若本回合无事可做，请 end_turn。"})
        stall += 1
        if stall >= 3:
            return _finish(done)
    return _finish(done)


# ---------------------------------------------------------------------------
# 无 key 的规则 AI（验证机制 + 看海 demo）
# ---------------------------------------------------------------------------

def dummy_turn(world, name, rng, max_actions: int = 12) -> int:
    """无 key 的规则 AI（走 execute + log_tool，因此每个行动都会进看海日志）。

    策略：补能源→屯田→兵营→征兵→集火最近野人拓疆；缺钱卖木头/矿石，缺装备买一点。
    不搞国家间外交/战争（看海 demo 与机制验证用）。
    """
    import random
    rng = random.Random(rng.randrange(1 << 30))
    acted = 0

    def do(tool: str, args: dict):
        nonlocal acted
        if acted >= max_actions:
            return
        res = execute(world, name, tool, args)
        log_tool(world, name, tool, args, res)
        acted += 1

    # 决定用：需要多少电（补给厂+装备厂+兵营数）
    need_energy = sum(
        (1 if BUILDINGS[bn]["kind"] in ("factory", "barracks", "townhall") else 0) * cnt
        for t in world.tiles.values() if t["owner"] == name
        for bn, cnt in t["buildings"].items()
    )
    have_energy_plants = any(
        cnt and BUILDINGS[bn]["kind"] == "energy"
        for t in world.tiles.values() if t["owner"] == name
        for bn, cnt in t["buildings"].items()
    )
    r = world.nations[name].res
    own = world.own_tiles(name)

    lumber = sum(t["buildings"]["林场"] for t in world.tiles.values() if t["owner"] == name)
    r = world.nations[name].res
    plant_cnt = sum(t["buildings"]["木材能源厂"] + t["buildings"]["石油能源厂"]
                    for t in world.tiles.values() if t["owner"] == name)

    def _cand(bn: str) -> list:
        out = []
        for (x, y) in own:
            t = world.tiles[(x, y)]
            if t["built_this_turn"]:
                continue
            cr = BUILDINGS[bn]["cap_resource"]
            cnt = t["buildings"][bn]
            if cr is None:
                ms = BUILDINGS[bn].get("min_slots", 0)
                if ms and sum(t["buildings"].values()) < ms:
                    continue  # 兵营/市政厅等：需本地建筑位达标
                if cnt < 3:  # 不限资源的地（如能源厂/工厂）：任地可建，留点节制
                    out.append((x, y))
            elif t["resources"].get(cr, 0) > cnt:
                out.append((x, y))
        return out

    # 1) 木头是自用命脉：先保证至少 1 座林场，再谈烧木头发电
    if (lumber == 0 or r["木头"] < 45) and _cand("林场"):
        do("build", {"tile": f"{_cand('林场')[0][0]+1} {_cand('林场')[0][1]+1}", "building": "林场"})
        r = world.nations[name].res

    # 2) 电网：需要电且没电/停摆时，优先造木头电厂（有林场管线），油电厂其次
    if need_energy > 0 and (plant_cnt == 0 or world.grid_short.get(name)):
        for bn, cond in (("木材能源厂", lumber >= plant_cnt + 1 or r["木头"] >= 20),
                         ("石油能源厂", True)):
            sites = _cand(bn)
            if sites and cond:
                do("build", {"tile": f"{sites[0][0]+1} {sites[0][1]+1}", "building": bn})
                break

    # 3) 采集屯田：按需补 农场/矿场/黄金矿场（林场已在上面照顾）
    r = world.nations[name].res
    order = []
    if r["粮食"] < 25:
        order += ["农场"] * 3
    if r["矿石"] < 20:
        order += ["矿场"] * 2
    if r["黄金"] < 350:
        order += ["黄金矿场"] * 2
    for bn in order:
        if acted >= max_actions:
            break
        sites = _cand(bn)
        if sites:
            do("build", {"tile": f"{sites[0][0]+1} {sites[0][1]+1}", "building": bn})
            r = world.nations[name].res
    # 兵营：等粮食和木头都稳了再造（每兵营每回合可征 1 军）
    if r["粮食"] >= 15 and r["黄金"] >= 500:
        sites = _cand("兵营")
        if sites:
            do("build", {"tile": f"{sites[0][0]+1} {sites[0][1]+1}", "building": "兵营"})

    # 3) 征兵（兵营空位 + 粮装够 + 电网正常）
    for (x, y) in own:
        t = world.tiles[(x, y)]
        if t["buildings"]["兵营"] > t["recruited_this_turn"] \
                and r["粮食"] >= 12 and r["装备"] >= 6 and not world.grid_short.get(name):
            do("recruit", {"tile": f"{x+1} {y+1}", "n": 1})
            r = world.nations[name].res
            if acted >= max_actions:
                break

    # 4) 军事：若某军旁 1 格有野人守军且够兵，就打；否则朝最近的野人荒地挪一步
    my_armies = [a for a in world.armies if a["owner"] == name]
    if my_armies:
        guardians = [a for a in world.armies if a["owner"] == "野人"]
        # 够近就打
        for g in list(guardians)[:6]:
            nearby = [a for a in my_armies if not a.get("engaged")
                      and max(abs(a["x"] - g["x"]), abs(a["y"] - g["y"])) <= 1]
            if len(nearby) >= 3:
                do("attack", {"army_ids": [a["id"] for a in nearby[:4]],
                              "x": g["x"] + 1, "y": g["y"] + 1})
                break
        else:
            # 朝最近的野人荒地挪动（只走荒地/自家）
            for a in my_armies:
                if a.get("engaged") or a.get("moved_turn") == world.turn:
                    continue
                if not guardians:
                    break
                gx, gy = min(((g["x"], g["y"]) for g in guardians),
                             key=lambda p: max(abs(p[0] - a["x"]), abs(p[1] - a["y"])))
                cands = [p for p in world.neighbors(a["x"], a["y"])
                         if world.owned_by(*p) is None]
                if not cands:
                    continue
                step = min(cands, key=lambda p: max(abs(p[0] - gx), abs(p[1] - gy)))
                if max(abs(step[0] - gx), abs(step[1] - gy)) < max(abs(a["x"] - gx), abs(a["y"] - gy)):
                    do("move", {"army_id": a["id"], "x": step[0] + 1, "y": step[1] + 1})
                    break

    # 5) 市场调剂（此 AI 不卖木头——木头要用来建设和发电，留着自用）
    if acted < max_actions and r["黄金"] < 350:
        if r["矿石"] >= 15:
            do("sell", {"good": "矿石", "qty": 10})
        elif r["粮食"] >= 25:
            do("sell", {"good": "粮食", "qty": 10})
    if acted < max_actions and r["装备"] < 6 and r["黄金"] >= 300:
        do("buy", {"good": "装备", "qty": 2})
    return acted
