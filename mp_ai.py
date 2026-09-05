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
)
from mp import RES_KEYS, RES_LABEL

# 引擎级锁：多国 agent 并发跑时，所有对 world 的读写在此串行化（网络调用在锁外并行）。
_engine_lock = threading.RLock()


def engine_call(fn, *a, **k):
    with _engine_lock:
        return fn(*a, **k)

# ---------------------------------------------------------------------------
# 面板（各国只见自己的）
# ---------------------------------------------------------------------------

GOODS_DISPLAY = ["粮食", "木头", "矿石", "石油", "装备", "补给"]


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
        lines.append(
            f"  {t['name']} ({x+1},{y+1}){t['terrain']} 城L{b['城堡']} 位{used}/{MAX_SLOTS} "
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
    incoming = [p for p in world.proposals if p["b"] == name]
    if incoming:
        for p in incoming:
            lines.append(f"  📨 邀约#{p['id']}: {p['a']} 提议 {p['kind']}（respond_proposal {p['id']} true/false）")
    offers = [p for p in world.peace_offers if p["b"] == name]
    if offers:
        for p in offers:
            k = {"pay": f"{p['a']}愿赔{p['gold']}金", "demand": f"{p['a']}要你赔{p['gold']}金",
                 "white": "白和"}[p["kind"]]
            lines.append(f"  🕊 求和#{p['id']}（{p['a']}→你）: {k} —— {p.get('note','')}（accept_peace/reject_peace {p['id']}）")
    if world.guarantees.get(name):
        lines.append(f"  你保障: {'、'.join(sorted(world.guarantees[name]))}")
    own_offers = [p for p in world.peace_offers if p["a"] == name]
    for p in own_offers:
        k = {"pay": f"你愿赔{p['gold']}金", "demand": f"你索{p['gold']}金", "white": "白和"}[p["kind"]]
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
            tags.append("同盟")
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
            note = "维持1电；每兵营每回合可征 1 支军队（耗 10粮 + 5装）"
        bld.append(f"  {nm}：造价 {cost}金 + {info['wood']}木 · {cap} · {note}")
    sections = [
        ("总览", (
            "EU4式 大地图国战：每人从 5 块地起家，拓荒/建设/生产/建军，可对他国结盟或开战。"
            "回合制：每回合你行动（可做多件事）→ 过回合统一结算（产出/电网/战斗/补给/市场回归）。"
            "地皮名字=ID，坐标 1-based。你能看的只有自己地盘+相邻一圈；他国国力只能推测。"
            "想细看任何机制就带主题调 rules，例如 rules(建筑) rules(外交) rules(战斗)。"
        )),
        ("地形", ter + "\n  占地一律走 atk：派军队进格——有守军打赢即占，敌人=0 进驻即占；"
                        "mv 只挪位不占地；没有『凭空拓荒』命令。"),
        ("建筑与造价", "\n".join(bld) + "\n  每地块 20 建筑位；每地块每回合限建 1 座；"
                                        "建好后下一回合才生效（在建中）。"),
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
            f"交战 = atk 冲入；每回合掷骰结算一轮；每军基础伤害 {ARMY_ATTACK_DAMAGE}，"
            "受守方地形+城堡防御%修正、总伤害分摊；攻方在敌地无加成。"
            "打赢守军→该地归你；无守军的空地/敌空城用 atk 直接进驻占领（mv 不占地）。"
            f"非交战且补给够时每回合回血 +{ARMY_HEAL_PER_TURN}HP；断粮则 -{ARMY_STARVE_DAMAGE}HP 可能饿毙。"
            "野人=无人荒地守军（100HP、自给自足、不主动打）。"
        )),
        ("外交", (
            "国家关系：中立=不能入境也不能攻击对方；同盟=互通领土+互不攻击；"
            "宣战：对方必须应战，即刻生效；若被宣战方有『保障独立/共同防御』的盟国会自动参战打你。"
            "共同防御=遭攻自动并肩；保障独立=你保它，别人打它你参战。"
            "求和(offer_peace)：pay=你赔钱、demand=你索款、white=白和；对方 accept_peace 即停战。"
            "断盟/停战后滞留在对方领土的军队会自动遣返（每回合往家走 1 格）。"
            "外交不一定要等到被打：先 countries 看清对象，主动发信、提结盟、换情报，都是合法手段。"
            "也可用 gift 把本国资源馈赠对方（粮木矿油装补给或黄金，本回合垫支、下回合到账）——示好、资助盟国、买通都行。"
            "还能用 share_map 把你的整张已知地图（全部坐标）发给对方，对方下回合在 query panel=intel 收到——换情报、亮家底、协调攻守都用得上。"
            "情报战：如果你不想开口问（懒得谈、不想欠人情）、又钱多，可用 spy(经济间谍) 花20金刺探别国，"
            "2回合后盗回其国库/收入/全部建设底细（query panel=spy 看）——打谁、敲谁、开战时机都心中有数。"
        )),
        ("信箱", (
            "send_letter 可给任何别国写信（内容任意：结盟邀约/和谈/威胁/闲聊），下回合送达。"
            "收到信要在 diplomacy/mail 面板回应——不回信，对方可能以为你拒绝。"
        )),
        ("市场", (
            "世界市场 buy/sell：黄金是货币；可交易粮/木/矿/油/装/补给。"
            "价格受供需影响：买→推高、卖→压低，整笔按成交后价格结算；每回合向基准价回归。"
            "基准价：" + "  ".join(f"{g}{MARKET[g]}" for g in GOODS_DISPLAY) + "。分批慢慢卖比一次砸盘划算。"
        )),
        ("回合与存档", (
            "end_turn 结束你的本回合。每回合结算会：落地在建建筑→产出/电网→战争→补给/回血→"
            "遣返→市场回归。存档每回合自动写 mp_save.json，随时可中断续局。"
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
        "外交": "外交", "同盟": "外交", "保障": "外交", "宣战": "外交", "求和": "外交",
        "共同防御": "外交",
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
    return "\n".join("  " + s for s in mem[-10:])


def _fmt_intel_hint(world, name) -> str:
    """收到的地图情报摘要（简短提示，完整见 query panel=intel）。"""
    ms = world.maps.get(name, [])
    if not ms:
        return "无（可用 share_map 与别国互发地图，下回合到账）"
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
        return "无（可用 spy 花20金刺探别国，2回合后到手）"
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
PACT_MAP = {"同盟": "同盟", "alliance": "同盟", "结盟": "同盟",
            "共同防御": "共同防御", "defense": "共同防御", "defensive": "共同防御"}


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


def _exec(world, actor: str, tool: str, args: dict) -> str:
    """execute 的实质分发（兜底由 execute 负责）。"""
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
        }.get(which, full_state(world, actor))

    # ---- 规则查询（= README 的游戏规则）
    if tool in ("rules", "规则", "help", "帮助"):
        return rules_text(world, str(args.get("topic", "") or ""))

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

    # ---- 信箱
    if tool in ("send_letter", "写信", "letter"):
        to = str(args.get("to", ""))
        text = str(args.get("content", ""))
        ok, msg = world.send_mail(actor, to, text)
        return msg

    # ---- 外交馈赠（本国储备垫支赠他国，下回合到账）
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
        return world.gift(actor, to, g, n)[1]

    # ---- 交换地图（把你的整张已知地图发给对方，下回合到账对方 intel）
    if tool in ("share_map", "交换地图", "送图", "发地图"):
        to = str(args.get("to", ""))
        return world.share_map(actor, to)[1]

    # ---- 经济间谍（20金，2回合后盗回目标全部经济情报；不能对自己用）
    if tool in ("spy", "经济间谍", "间谍", "刺探"):
        return world.spy(actor, str(args.get("to", "")))[1]

    # ---- 外交
    if tool in ("propose", "提议"):
        to = str(args.get("to", ""))
        kind = PACT_MAP.get(str(args.get("kind", "")).lower(), args.get("kind"))
        ok, msg = world.propose_pact(kind, actor, to)
        return msg
    if tool in ("respond_proposal", "回应邀约"):
        pid = int(args.get("proposal_id", args.get("id", 0)))
        accept = str(args.get("accept", "")).lower() in ("true", "yes", "1", "接受", "是")
        return world.accept_pact(actor, pid)[1] if accept else world.reject_pact(actor, pid)[1]
    if tool in ("break_alliance", "断盟"):
        to = str(args.get("to", ""))
        return world.break_pact("同盟", actor, to)[1]
    if tool in ("break_defense", "解除共同防御"):
        to = str(args.get("to", ""))
        return world.break_pact("共同防御", actor, to)[1]
    if tool in ("guarantee", "保障独立"):
        to = str(args.get("to", ""))
        return world.declare_guarantee(actor, to)[1]
    if tool in ("cancel_guarantee", "撤回保障"):
        to = str(args.get("to", ""))
        return world.cancel_guarantee(actor, to)[1]
    if tool in ("declare_war", "宣战"):
        to = str(args.get("to", ""))
        return world.declare_war(actor, to)[1]
    if tool in ("offer_peace", "求和"):
        to = str(args.get("to", ""))
        kind = KIND_MAP.get(str(args.get("kind", "")).lower(), args.get("kind"))
        gold = int(args.get("gold", 0) or 0)
        note = str(args.get("note", "") or "")
        return world.offer_peace(actor, to, kind, gold, note)[1]
    if tool in ("accept_peace", "接受议和"):
        return world.accept_peace(actor, int(args.get("offer_id", 0)))[1]
    if tool in ("reject_peace", "拒绝议和"):
        return world.reject_peace(actor, int(args.get("offer_id", 0)))[1]

    # ---- 结束回合（必须带一句话小结）
    if tool in ("end_turn", "结束回合", "done"):
        summary = str(args.get("summary", "")).strip()
        if len(summary) < 4:
            return "本回合还没收尾：end_turn 必须带 summary=一句话，总结你这回合做了什么/立场（例如 summary=这回合建了两座农场并继续拓荒）。"
        world.log(f"{actor} 回合小结：{summary}", phase="行动", nation=actor)
        mem = world.summaries.setdefault(actor, [])
        mem.append(f"第{world.turn}回合：{summary}")
        if len(mem) > 10:
            del mem[:-10]  # 只留最近 10 回合
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
        "name": "query", "description": "查询接口：随时获取你的各面板。res=国库与储备 / land=地皮(国土+可拓荒地) / army=军队 / market=世界市场(现价+持有) / econ=经济核算(各建筑造价毛利回本) / intel=收到的地图情报(全部坐标) / spy=经济间谍情报(别国经济底细) / mail=信箱 / countries=可选外交对象 / diplomacy=外交 / news=近讯 / threats=视野内敌军 / all=全部。每个行动后状态会变，拿不准就再查一次。",
        "parameters": _props({"panel": {"type": "string", "enum": ["all", "res", "land", "army", "market", "econ", "intel", "spy", "mail", "countries", "diplomacy", "news", "threats"], "description": "要查询的面板", "required": True}})}},
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
        "name": "build", "description": "在自己的一块地上建一座建筑。每地块每回合限建1座。建筑: 城堡/林场/农场/矿场/黄金矿场/石油厂/木材能源厂/石油能源厂/补给厂/装备厂/兵营/市政厅。建造上限受该地资源量限制(市政厅另需本地已用位≥6且每地块限1)。",
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标如 '5 6' 或自家地块名（land 面板有）", "required": True},
                              "building": {"type": "string", "enum": BUILD_NAMES, "description": "建筑名", "required": True}})}},
    {"type": "function", "function": {
        "name": "recruit", "description": "在自己有兵营且电网正常的地块征召军队，每兵营每回合1支。兵种 kind：步=步兵(10粮+5装，动1格/回合、耗补给1)；骑=骑兵(12粮+12装，动2格/回合、耗补给2)。",
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标 '5 6' 或名字", "required": True},
                              "n": {"type": "integer", "description": "征召数量（默认1）"},
                              "kind": {"type": "string", "enum": ["步", "骑"], "description": "兵种（默认 步）"}})}},
    {"type": "function", "function": {
        "name": "move", "description": "把一支自己的军队调到相邻一格(含对角)，纯移动不占地。每回合每支限1次。中立国地盘不能进（先结盟/宣战）；交战中不能移动，须先 retreat 撤出。要占无守军的空地/敌空城，请用 attack（atk 会直接进驻占领）。",
        "parameters": _props({"army_id": {"type": "integer", "description": "军队id", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "attack", "description": "军队(1格内)冲入目标地块并交战（打赢该地守军→自动夺地；荒地守军=野人）。与别国开打需已宣战。",
        "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"}, "description": "参战军队id数组", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "retreat", "description": "交战中撤出到相邻一格，当回合挨守军一击。",
        "parameters": _props({"army_id": {"type": "integer", "description": "军队id", "required": True},
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
        "name": "send_letter", "description": "给别国写信（内容任意）。信件下回合才送达对方信箱。to 必须用 countries 选出的别国，不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "收信国名", "required": True},
                              "content": {"type": "string", "description": "信件正文", "required": True}})}},
    {"type": "function", "function": {
        "name": "gift", "description": "把本国储备赠给别国（to=countries 里的别国，不能是自己）：good=粮食/木头/矿石/石油/装备/补给 或 黄金，qty=数量。本回合垫支扣出、下回合到账。示好/资助盟国/买通可用。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                              "good": {"type": "string", "description": "物资名", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "share_map", "description": "把你的整张已知地图（全部国土块+边界外可见块，含坐标）发给别国，对方下一回合在 query panel=intel 收到。换情报/亮家底/协同步调可用。to=countries 里的别国，不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "spy", "description": "不想开口问（懒得谈、钱多）时派经济间谍刺探别国：花 20 金（国库不足会被拒），2 回合后在 query panel=spy 拿回该国全部经济情报——国库/储备、上回合收入、每一块地的建筑与在建。目标不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "刺探对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "propose", "description": "向别国提议『同盟』（互通领土、互不攻击）或『共同防御』（遭攻自动并肩，平时互不攻击）。to 必须用 countries 选出的别国，不能是自己；对方 respond_proposal 接受才生效。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                              "kind": {"type": "string", "enum": ["同盟", "共同防御"], "description": "类型", "required": True}})}},
    {"type": "function", "function": {
        "name": "respond_proposal", "description": "回应收到的同盟/共同防御邀约。",
        "parameters": _props({"proposal_id": {"type": "integer", "description": "邀约id（diplomacy面板有）", "required": True},
                              "accept": {"type": "boolean", "description": "接受? true/false", "required": True}})}},
    {"type": "function", "function": {
        "name": "break_alliance", "description": "单方面解除同盟（to=别国）。对方境内的你方军队将自回合末起自动全部撤出。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "break_defense", "description": "单方面解除共同防御。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "guarantee", "description": "宣布保障别国独立：任何国家攻击它，你将自动参战。to=别国（不能自己）。",
        "parameters": _props({"to": {"type": "string", "description": "被保障国", "required": True}})}},
    {"type": "function", "function": {
        "name": "cancel_guarantee", "description": "撤回独立保障。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "declare_war", "description": "对别国宣战（对方必须应战，即刻生效）。先 countries 选目标，to=别国（不能自己）。若对方有保障独立/共同防御者会自动参战打你；与同盟/共同防御对象开战会先破裂关系。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "offer_peace", "description": "向交战国求和（to=你的敌国）：pay=我方向对方赔X金；demand=要求对方赔X金；white=白和。对方 accept_peace 即停战，索款不能超过对方国库。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                              "kind": {"type": "string", "enum": ["pay", "demand", "white"], "description": "pay=我方赔款 / demand=要求对方赔款 / white=白和", "required": True},
                              "gold": {"type": "integer", "description": "赔款量（pay/demand 必填>0）"},
                              "note": {"type": "string", "description": "附加条件/说明（可选）"}})}},
    {"type": "function", "function": {
        "name": "accept_peace", "description": "接受对方求和（diplomacy 面板可看提议编号）。",
        "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}},
    {"type": "function", "function": {
        "name": "reject_peace", "description": "拒绝对方求和，战争继续。",
        "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}},
    {"type": "function", "function": {
        "name": "end_turn", "description": "结束本国本回合的行动。⚠ 必填 summary：用一句话总结你这回合做了什么/当前立场（例如：summary=这回合建了两座农场并继续拓荒）。没有这句小结就不算结束本回合。",
        "parameters": _props({"summary": {"type": "string", "description": "一句话回合小结（必填，>=4字）", "required": True}})}},
]


def system_prompt(world, name) -> str:
    others = "、".join(n for n in world.alive() if n != name)
    return (
        "你是国家元首【" + name + "】，在一个 EU4 式大地图战略游戏里治国。其余国家：" + (others or "（只剩你）") + "。\n\n"
        "这局没有预设目标：富国、拓荒、称霸、报复、苟和都行，由你自己判断；每种选择都有后果，后果也由你承担。\n"
        "【回合】每回合你可用工具做很多事：建设/拓荒/征兵/调兵/打仗/买卖/外交/写信。做完用 end_turn 结束本回合，"
        "并在 summary 用一句话小结你这回合的作为。地理：每块地=1格，军队每回合只能移动相邻1格。\n"
        "【资源用途（事实）】木头=建一切建筑+木材电厂燃料；粮=征兵(10/军)+补给厂原料；矿=装备厂+补给厂原料；"
        "油=装备厂原料+油电厂燃料；装=征兵(5/军)；补给=每军每回合耗1，仓空军队挨饿。\n"
        "【生产链（事实）】林场/农场/矿场/石油厂/黄金矿场=采集；木材厂(耗1木→2电)/油电厂(耗1油→5电)=发电，"
        "电不存储，电网不足则补给厂/装备厂/兵营/市政厅全停摆；补给厂(粮1+矿1→补给2)；装备厂(矿1+油1→装备2)；"
        "兵营(耗1电)每回合可征1军(10粮5装)；黄金矿场+10金/回合；也可世界市场 buy/sell 换黄金。\n"
        "【扩张与战争（事实）】占地一律走 atk：派军进目标格，有守军(野人/敌军)就打赢再占、"
        "敌人=0 就直接进驻占领；mv 只是挪位置，不占地。荒地/敌空城都这样占。"
        "军队非交战且补给够时每回合回25HP。中立(不结盟不交战)时你的军队进不了别国、也打不了别国；"
        "结盟=互通+互不攻击；宣战对方必须应战；被宣战方若有『保障独立/共同防御』的盟国会自动参战打你。"
        "求和 pay=你赔钱 / demand=索对方赔款 / white=白和。\n"
        "【外交（事实）】你与每个别国的关系独立：可保持中立、可提结盟/共同防御（对方可能接受或拒绝）、可单方保障它或撤回、"
        "可宣战、战中可求和。来信可回应也可不回；邀约可接受可拒绝可冷处理；承诺可以兑现也可以背弃。这些都由你权衡。\n"
        "【信息】情报有迷雾：你只看得见自己地盘与相邻一圈；他国国库/储备/全部军队你看不到，只能从来信、边界动静与其言行推断；"
        "他国来信未必可信，你也可说谎。\n"
        "【规则查询】完整玩法（造价/地形/战斗/外交/市场细则）随时可查：调用 rules，可带主题如 rules(外交)、rules(建筑)。\n"
        "【行动建议（自由）】动手前可用 query 看面板（res/land/army/market/countries 随时可查）；"
        "想清楚再调用工具。你可以边想边做，也可以只做一两件事。没有『应该』怎么做，只有你想要什么后果。"
    )


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
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=240)
    messages = [
        {"role": "system", "content": engine_call(system_prompt, world, name)},
        {"role": "user", "content": engine_call(full_state, world, name)},
    ]
    done = 0
    stall = 0  # 连续"只思考/空转"轮数
    for step in range(max_steps):
        if name not in world.nations:
            return done
        try:
            extra = {}
            # deepseek-v4：thinking 开关 + reasoning_effort（low/medium/high）
            if "thinking" in cfg:
                extra["thinking"] = {"type": cfg["thinking"]}  # "enabled"/"disabled"
            if cfg.get("reasoning_effort"):
                extra["reasoning_effort"] = cfg["reasoning_effort"]
            resp = client.chat.completions.create(
                model=cfg["model"], messages=messages,
                tools=TOOL_SCHEMAS, tool_choice="auto",
                temperature=cfg.get("temperature", 0.3),
                max_tokens=cfg.get("max_tokens", 4000),
                extra_body=extra or None,
            )
        except Exception as e:
            messages.append({"role": "user", "content": f"（API 错误，若可继续请继续，否则 end_turn）: {e}"})
            if emit:
                emit(f"⚠ {name} API错误: {type(e).__name__}: {e}")
            continue
        msg = resp.choices[0].message
        reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
        content = (getattr(msg, "content", None) or "").strip()
        tool_calls = getattr(msg, "tool_calls", None) or []
        if emit and reasoning:
            emit(f"💭 {name} 思考：{reasoning[:160].replace(chr(10),' ')}")
        if tool_calls:
            stall = 0
            asst: dict = {"role": "assistant", "content": getattr(msg, "content", None)}
            if reasoning:
                asst["reasoning_content"] = reasoning
            asst["tool_calls"] = [
                {"id": tc.id, "type": tc.type,
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ]
            messages.append(asst)
            acted_this = False
            for tc in tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
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
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                done += 1
                acted_this = True
                if name not in world.nations:
                    return done
                if is_end:
                    # 只有带上有效的小结才算真结束；没带会被 execute 拦下，继续逼它补
                    if (str(args.get("summary", "")).strip()):
                        return done
            if name in world.nations:  # 每次行动后都回填一次最新状态（默认塞查询）
                messages.append({"role": "user", "content": engine_call(compact_state, world, name)})
            continue
        # 没有工具调用：
        if content:
            # 有正文——当作宣告/收尾
            engine_call(world.log, f"{name} 宣告:「{content}」", phase="行动", nation=name)
            if emit:
                emit(f"🗣 {name} 宣告：「{content}」")
            return done
        if reasoning:
            # 纯思考轮（无正文无工具）：预算已很大仍被思考吃光时，不追加 reasoning（API 不收），
            # 用中性话让它把回合续完；不打断它的思考风格。
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
            return done
    return done


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
                if cnt < 3:  # 不限资源的地（如兵营）：任地可建，留点节制
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
