# -*- coding: utf-8 -*-
"""多国 agent 层：把全部玩家功能注册成 OpenAI function tools，按国家隔离执行。

- 每个 agent 只拿到「自己该知道」的状态（自己的面板/信箱/视野内事件），
  只能调用自己的合法工具（规则与引擎完全一致，无作弊入口）。
- execute(world, actor, tool, args)：执行一个工具调用并返回结果文本。
- run_openai_turn(...)：一个国家的「回合」——反复调 LLM 直到它 end_turn / 无工具。
- dummy_turn(...)：无 key 时的简单规则 AI，用于机制验证/看海 demo。
"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

from game import (
    ARMY_HEAL_PER_TURN,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    CASTLE_DEFENSE_PER_LEVEL,
    ENGINEER_DISCOUNT,
    MARKET,
    MARKET_SPREAD,
    MAX_SLOTS,
    TERRAIN_STATS,
    TOWN_HALL_GOLD,
    TOWN_HALL_PER_SLOT,
    UNIT_TYPES,
    WATCHTOWER_RADIUS,
    unit_supply,
)
import ctx as ctxlib
from console import dw as _dw, pad as _pad
from ctx import est_tokens
from mp import PLAN_MAX_TURNS, REPORT_EVERY, RES_KEYS, RES_LABEL


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

# 匈奴教义（喂给匈奴 AI，令其贯彻；人类侧全文见 匈奴教义.md）
# 本分支为国家间永久中立（无外交），故教义只讲抢野人、抢产、活命，不涉通信/宣战/议和。
HUNS_DOCTRINE = (
    "· **抢产为生**：你真正的口粮是**夺来的金矿与补给厂**。\n"
    "   每座金矿 = 10 金/回合 ≈ 1 骑的口粮（骑耗 2 补给，买价 ≈5.3 金/单位，含价差滑点 ≈11~12 金/骑）；"
    "每座补给厂 = 2 补给/回合 = 1 骑的口粮（要 1粮+1矿+1电，抢它得连农场/矿场一起抢，否则空转）。"
    "→ **金矿优先、补给厂并列**。\n"
    "· **时间是敌人：越拖越容易死**。开局 200 补给 = 16 回合的命，之后每回合都在流血、别国在种田。"
    "必须在见底前把「存量」变成「流量」：抢到能持续产金/产补给的地才活得下去。"
    "抢地优先级：金矿地 > 补给厂地 > 粮木矿产地 > 无守军空地 > 有野人守的要塞（最后，能不碰就不碰）。\n"
    "· **骑兵战力与步兵相同**（都是 50 攻/100 血），你的优势**只有速度**：一次 atk 可达 2 格外"
    "（步兵只有 1 格），且 atk 是跳到目标格——**可跳过一格直取纵深目标**。\n"
    "· **分兵掠地**：骑兵分 2~3 路，各扑不同方向的无守军地块——你跳 2 格、别人走 1 格。"
    "**机动聚集**：把相邻几路临时聚起来，以 2 打 1、3 打 1 吃掉野人守军，打完立刻散开继续掠地。\n"
    "· **国家间永久中立**（本局无外交）：别国领土你进不去也打不了，扩张只能打野人、占无主地。"
    "所有资源流转只经世界市场——缺补给就 buy，多余的就 sell。\n"
    "· 拿到金第一件事：立刻 buy 补给（一次买够未来 10 回合的量），别囤黄金。"
    "补给仓空 = 每军按缺口比例扣血（满缺 -35HP/回合，交战中也照扣），几回合就死光。"
)


def _res_line(world, name) -> str:
    r = world.nations[name].res
    et, mt, short = world.energy_report.get(name, (0, 0, False))
    parts = []
    for k in RES_KEYS:
        parts.append(f"{RES_LABEL.get(k, k)}{r.get(k, 0)}")
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
        return "（无军队——先建兵营，再用 recruit 征兵）"
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
    lines = [f"世界市场（黄金是货币；可交易：{' '.join(GOODS_DISPLAY)}）",
             f"  成交=沿曲线均价结算，含 {MARKET_SPREAD:.0%} 买卖价差；每回合向供需均衡价回归。"]
    for g in GOODS_DISPLAY:
        p = world.prices[g]
        base = MARKET[g]
        eq = world.equilibrium.get(g, base)
        tag = "≈基准" if abs(p - base) <= 0.02 * base else ("贵" if p > base else "贱")
        bp, _ = world.market_quote(g, 1, "buy")
        sp, _ = world.market_quote(g, 1, "sell")
        _, s50 = world.market_quote(g, 50, "sell")
        _, b50 = world.market_quote(g, 50, "buy")
        lines.append(f"  {g} 现价{p:.2f}(基准{base} 均衡{eq:.2f} {tag}) 买{bp:.2f}/卖{sp:.2f} "
                     f"持有{r.get(g, 0)} ｜ 卖50≈{s50}金 买50≈{b50}金")
    return "\n".join(lines)


def observer_board(world) -> str:
    """Observer 全景大面板：各国国库/储备/国土/军队/累计消费，一屏尽览。"""
    alive = world.alive()
    L = [f"══════ 世界全景 · 第 {world.turn} 回合 · 现存 {'、'.join(alive)} ══════"]
    L.append("世界市场: " + "  ".join(f"{g}{world.market_price(g)}" for g in GOODS_DISPLAY))
    for n in alive:
        r = world.nations[n].res
        L.append(f"◆ {n}：国库{r['黄金']} 粮{r['粮食']} 木{r['木头']} 矿{r['矿石']} "
                 f"油{r['石油']} 装{r['装备']} 补给仓{r['补给']} | {world.econ_summary.get(n,'')}")
        L.append(f"   国土 {len(world.own_tiles(n))} 块 | 军队 {len(world.nation_armies(n))} 支 "
                 f"| 累计总消费 {world.spend_total(n):.0f}")
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
        elif k == "tower":
            note = (f"无产出不耗电；己方任一瞭望塔半径 {WATCHTOWER_RADIUS} 圆内的事件你都收得到"
                    "（含战报；视野=国土+相邻一圈+所有瞭望塔圈；只扩事件视野，不增加可拓地）")
        elif k == "academy":
            note = (f"无产出不耗电；本地块一切建造金价 -{ENGINEER_DISCOUNT}%（含城堡升级，与地形惩罚乘算，"
                    "只认已落成的）；需本地已用建筑位≥4、每地块限1座")
        elif k == "militia_camp":
            note = ("屯田+民兵编制：每回合 +1 粮（不耗电）。可在此征召民兵（50金+5粮/支；每座每回合1支，"
                    "**全国民兵总数 ≤ 全国军屯总数**；不耗电、电网停摆也不影响）；民兵=廉价驻守军队"
                    f"（{UNIT_TYPES['民']['hp']}HP/攻{UNIT_TYPES['民']['atk']}/动1格），驻**本格**不耗补给"
                    "（每座军屯覆盖本格1支，离格/超额照常吃）；需本地耕地≥1、每地块限1座")
        else:  # barracks
            note = "维持1电；每兵营每回合可征 1 支军队（耗 10粮 + 5装）；需本地已用建筑位≥3（含在建）"
        bld.append(f"  {nm}：造价 {cost}金 + {info['wood']}木 · {cap} · {note}")
    sections = [
        ("总览", (
            "大地图国战（**国家间永久中立：本局无外交**——不能通信/结盟/宣战，他国领土不可进入、不可攻击）："
            "每人从 5 块地起家，拓荒/建设/生产/建军，扩张只能打野人、占无主地。"
            "回合制：每回合你行动（可做多件事）→ 过回合统一结算（产出/电网/战斗/补给/市场回归）。"
            "地皮名字=ID，坐标 1-based。你能看的是自己地盘+相邻一圈（建瞭望塔可把事件视野再往外推）；他国国力只能推测。"
            "想细看任何机制就带主题调 rules，例如 rules(建筑) rules(战斗)。"
        )),
        ("地形", ter + "\n  占地一律走 atk：派军队进格——有野人守军打赢即占，敌人=0 进驻即占；"
                        "mv 只挪位不占地；没有『凭空拓荒』命令。"
                        "行军不打野人：mv 可直接往野地（含野人驻守格）移动/穿行，野人从不主动攻击、路过不打，只在被 atk 时才接战；"
                        "但野地上有别人正在打野时不得 atk 插足（可 mv 旁观待命）。"),
        ("建筑与造价", "\n".join(bld) + "\n  每地块 20 建筑位；每地块每回合限建 1 座；"
                                        "建好后下一回合才生效（在建中）。"
                                        "\n  表中造价为平原基准价；实际金价按地块地形建设惩罚上浮"
                                        "（如山地 ×1.5，只加金不加木），匈奴再乘 1.3。"),
        ("经济与能源", (
            "全国制：国库/木材/粮矿油装补给都在你账上（res 面板）。"
            "电网全国且不存储：能源厂发电；补给厂/装备厂/兵营/市政厅都要耗电维持，"
            "发电 < 维持则这些高级建筑全部停摆（能源厂除外）。"
            "补给厂(粮1+矿1→补给2)；装备厂(矿1+油1→装备2)；补给仓每军每回合耗 1（骑兵 2），"
            "空则每军按缺口比例扣血（满缺 -35HP/回合，交战中也照扣），可能饿毙。"
            "例外：民兵驻在自家军屯格不耗补给（每座军屯覆盖本格 1 支），离格照常吃。"
            "黄金矿场是稳定产金；市政厅(需本地已用位≥6·限1座·耗1电)每座每回合 = 5金基础"
            " + 该地块每座建筑×1金（不含自身，城越满越值）；"
            "也可在 world market 卖物资换金（卖得越多价压越低）。"
        )),
        ("军队与战斗", (
            "每军 100HP；兵营征召，每兵营每回合 1 支。兵种：步兵(耗10粮+5装，动1格/回合，耗补给1/回合)、"
            "骑兵(耗12粮+12装，动2格/回合，耗补给2/回合)、"
            "民兵(军屯征召 50金+5粮/支，80HP、攻20，动1格/回合，驻本格军屯不耗补给——廉价驻守军队，"
            "每军屯每回合1支、全国民兵总数≤全国军屯总数)。"


            "军队 id **各国独立编号、从 1 递增且阵亡不回收**：历史上的 #n 永远指同一支军队，"
            "引用一律以最近一次 query army 面板为准。"
            f"交战 = atk 冲入；每回合掷骰结算一轮；每军基础伤害 步/骑 {UNIT_TYPES['步']['atk']}、"
            f"民兵 {UNIT_TYPES['民']['atk']}，"
            "受守方地形+城堡防御%修正（地形与城堡为**相乘**叠加，山地+城堡L5≈75%而非100%）、总伤害分摊；攻方在野地无加成。"
            "**唯一的敌人是野人**（国家间永久中立，别国军队与你永不交战）：同格多方各打野人、每方掷自己的骰、"
            "伤害均分给各敌人；**地形减伤给守方**（未参战的和平驻军/野人——谁挨打谁是守方），"
            "交战中的进攻方一律不吃；野人只守无主格、只打进攻方。"
            "**不抢别人的战斗**：野地上有别人正在打野 → 不能 atk 插足（可 mv 旁观待命）。"
            "**占地看索取顺序**：野人清空后，进攻方里第一个 atk 的（索取者）占地，它若阵亡则顺位给最早入场者；"
            "和平驻守的第三方不占地也不参战（占地后回合末自动遣返）。"
            "撤出攻守对等：交战中的军队（含防守方守军）要离开战场一律用 retreat——耗移动，本回合末随战斗结算（伤害全场分摊；防御方撤退减伤50%；撤退军本回合输出-80%），结算后自动脱离；"
            "撤退固定只能退相邻 1 格，四周无合法撤退点（己方/无人荒地）则无法撤退；"
            "mv 不能从交战地撤离（会被拦）。"
            "交战中双方（含守军）一律不回血。"
            "打赢野人→该地归你；无守军的空地用 atk 直接进驻占领（mv 不占地）。"
            f"非交战且补给够时每回合回血 +{ARMY_HEAL_PER_TURN}HP；断粮则每军按缺口比例扣血"
            f"（-{ARMY_STARVE_DAMAGE}×缺口/需求，交战中也照扣），可能饿毙。"
            "野人=无人荒地守军（100HP、自给自足、不主动打）：**开局全图每块无主地都有**，不随视野出现；杀了不再生。"
        )),
        ("市场", (
            "世界市场 buy/sell：黄金是货币；可交易粮/木/矿/油/装/补给。"
            f"价格受供需影响：买→推高、卖→压低；成交按「沿曲线均价」结算（不是整笔按最差价），"
            f"另有 {MARKET_SPREAD:.0%} 买卖价差（买 +{MARKET_SPREAD/2:.0%} / 卖 −{MARKET_SPREAD/2:.0%}）。"
            "每回合市价向「供需均衡价」回归——全世界产得多就便宜、战时耗得多就贵（面板显示均衡价）。"
            "深度按商品分档（粮木深、装备浅），随现存国家数放大：国家越多，单笔买卖对市价的冲击越小。"
            "基准价：" + "  ".join(f"{g}{MARKET[g]}" for g in GOODS_DISPLAY) + "。分批慢慢卖比一次砸盘划算；"
            "粮/木是内需品（价低量大），矿/油/装备才是外贸主力。"
        )),
        ("经济报表", (
            f"每 {REPORT_EVERY} 回合**自动**给每国结一期经济报表（第 11/21/31… 回合起），"
            "**出表那一回合（第 11/21/31…）全文自动进你的状态面板【经济报表】**，"
            "其余回合只留一行摘要；历史期与跨期趋势用 report 工具查（免费、只读）——"
            "**无法手动运行**，也不能补做历史期。"
            "一期覆盖最近 10 回合，全部按当时市价折算："
            "①GDP（每回合）= 本期生产增加值 ÷10，**不含军费**（采集/工厂产出 + 金矿/市政厅金 − 中间投入 − 能源燃料）；"
            "②GDP 增长率 = 环比上期；"
            "③财政收入 = GDP − 军费；"
            "④军费（每回合）= 本期军队**实际消耗的补给** ÷10 × 现价（不看来源，自产/外购一视同仁）；"
            "⑤军费占 GDP 比；⑥国家总资产 = 全部建筑重置成本（造价金+木×现价，含夺来的地）；"
            "⑦资产增长率；⑧本期投资 = 本期建造实付（金 + 木×当时市价，含城堡升级）；"
            "⑨投资增长率；⑩外贸/内循环占比 = (买卖总额)/(自产+进口) 与自产自用部分。"
            "report all=true 看跨期趋势表；report turn=21 看指定期。"
            "军费是双刃剑：过高挤压投资、长期竞争落后；过低则成待宰羔羊、发展空间受限。"
        )),
        ("回合与存档", (
            "end_turn 结束你的本回合。每回合结算会：落地在建建筑→产出/电网→战斗→补给/回血→市场回归。"
            "存档每回合自动写 mp_save.json，随时可中断续局。"
            f"国策规划：用 plan 制定/修订（常驻上下文【国策规划】）；没有国策、或距上次修订"
            f"已满 {PLAN_MAX_TURNS} 回合时，end_turn 会被拦下，先 plan 再结束。"
            "计划建议涵盖 经济发展/军事扩张/内政节奏 三方面。"
        )),
    ]
    return sections


def rules_text(world, topic: str = "") -> str:
    """rules tool：按主题返回规则段落；主题识别不了就返回全文（不设限）。"""
    t = (topic or "").strip()
    secs = _help_sections()
    labels = {
        "建筑": "建筑", "建造": "建筑", "兵营": "建筑", "农场": "建筑", "城堡": "建筑",
        "瞭望塔": "建筑", "工程院": "建筑", "军屯": "建筑", "民兵": "建筑",
        "工厂": "建筑", "能源": "建筑", "电厂": "建筑", "造价": "建筑",
        "地形": "地形", "资源": "地形", "拓荒": "地形", "领土": "地形",
        "经济": "经济与能源", "电": "经济与能源", "能源": "经济与能源", "补给": "经济与能源",
        "装备": "经济与能源",
        "军队": "军队与战斗", "战斗": "军队与战斗", "征兵": "军队与战斗",
        "军队移动": "军队与战斗", "攻击": "军队与战斗", "野人": "军队与战斗", "视野": "军队与战斗",
        "市场": "市场", "买卖": "市场", "价格": "市场", "交易": "市场",
        "回合": "回合与存档", "存档": "回合与存档", "结算": "回合与存档",
        "报表": "经济报表", "GDP": "经济报表", "投资": "经济报表", "军费": "经济报表",
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


def _fmt_memory(world, name, since: int | None = None) -> str:
    """本国回合小结纪事（私有记忆，别国不可见）。

    since=已进 replay 的最早回合 → 只保留 replay 覆盖不到的更早小结，避免和完整记录重复。
    """
    mem = world.summaries.get(name, [])
    if since is not None:
        mem = [m for m in mem if int(m["turn"]) < since]
    if not mem:
        return "（无——近况见上文完整记录）" if since is not None else "（尚无往回合小结）"
    return "\n".join(f"  [第{m['turn']}回合] {m['text']}" for m in mem[-10:])


def _fmt_plan(world, name) -> str:
    """国策规划（常驻上下文）：无 plan 不能结束回合；每 PLAN_MAX_TURNS 回合须修订。"""
    pl = world.plans.get(name)
    if not pl or not str(pl.get("text", "")).strip():
        return ("（尚未制定）——结束回合(end_turn)前必须先 plan(content=…) 制定国策；"
                "可从 经济发展 / 军事扩张 / 内政节奏 三方面写明目标")
    since = world.turn - pl.get("turn", world.turn)
    flag = ""
    if since >= PLAN_MAX_TURNS:
        flag = f" ⚠ 已 {since} 回合未修订（每 {PLAN_MAX_TURNS} 回合必须修订一次才能结束）"
    elif PLAN_MAX_TURNS - since == 1:
        flag = f" ⚠ 本回合必须修订（下回合即满 {PLAN_MAX_TURNS} 回合）"
    return f"[第{pl.get('turn')}回合制定/修订]{flag}\n  {pl['text']}"


MIL_WARNING = ("⚠ 军费是双刃剑：开支过大会挤压投资，长期竞争落后；开支过低则成待宰羔羊，"
               "发展空间受限、短期竞争失利——自己权衡。")


def _pct(v: float | None, sign: bool = True) -> str:
    """增长率/占比显示：None（无上期）→「—」。"""
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%" if sign else f"{v * 100:.1f}%"


def _fmt_report_one(rep: dict) -> str:
    """单期经济报表。口径：GDP=生产增加值(市价,不含军费)/回合；军费=补给消耗×现价/回合。"""
    days = rep.get("span", REPORT_EVERY)
    start = rep.get("period_start", rep["period_end"] - days + 1)
    span = f"第 {start}–{rep['period_end']} 回合" + ("" if days == REPORT_EVERY else f"（{days} 回合）")
    gdp, mil = rep["gdp"], rep["military"]
    fiscal = gdp - mil
    L = [f"【经济报表 · 报表回合 {rep['report_turn']} · 覆盖{span}】"]
    L.append(f"  GDP（每回合，市价）      {gdp:>8.1f} 金   {_pct(rep['gdp_growth'])}"
             f"   （本期合计 {gdp * days:.0f} 金）")
    L.append(f"  财政收入（GDP−军费）     {fiscal:>8.1f} 金/回合"
             + ("   ⚠ 本期军费已超过 GDP，靠卖库存/吃老本维持" if fiscal < 0 else ""))
    L.append(f"  军费（每回合补给消耗）   {mil:>8.1f} 金   "
             f"占 GDP {_pct(rep['military_ratio'], sign=False)}")
    L.append(f"  国家总资产               {rep['assets']:>8.0f} 金   {_pct(rep['assets_growth'])}"
             "   （含夺地所得）")
    L.append(f"  本期投资                 {rep['invest']:>8.0f} 金   {_pct(rep['invest_growth'])}")
    L.append(f"  外贸 / 内循环            外贸 {_pct(rep['trade_ratio'], sign=False)} · "
             f"内循环 {_pct(1 - rep['trade_ratio'], sign=False)}"
             "   （外贸=买卖总额/(自产+进口)）")
    L.append(f"  （本期军队共消耗补给 {rep['supply_eaten']} 单位，按现价折 {mil:.1f} 金/回合"
             "——不看来源，自产/外购一视同仁；市场买入 "
             f"{rep['import_gold']:.0f} 金、卖出 {rep['export_gold']:.0f} 金）")
    L.append("")
    L.append(MIL_WARNING)
    return "\n".join(L)


def _fmt_report_trend(world, name) -> str:
    """跨期趋势表：一行一期，便于比较不同时期。"""
    reps = world.econ_reports.get(name, [])
    header = ["期", "报表回合", "GDP/回合", "GDP增长", "军费/回合", "军费占GDP",
              "投资", "投资增长", "总资产", "资产增长", "外贸占比"]
    rows = [header]
    for i, r in enumerate(reps, 1):
        rows.append([str(i), str(r["report_turn"]), f"{r['gdp']:.1f}", _pct(r["gdp_growth"]),
                     f"{r['military']:.1f}", _pct(r["military_ratio"], sign=False),
                     f"{r['invest']:.0f}", _pct(r["invest_growth"]), f"{r['assets']:.0f}",
                     _pct(r["assets_growth"]), _pct(r["trade_ratio"], sign=False)])
    widths = [max(_dw(row[c]) for row in rows) for c in range(len(header))]
    lines = [f"【经济报表 · 跨期趋势】共 {len(reps)} 期"
             f"（报表回合 {'、'.join(str(r['report_turn']) for r in reps)}）"]
    lines += ["  " + "  ".join(_pad(row[c], widths[c]) for c in range(len(header)))
              for row in rows]
    lines += ["", MIL_WARNING]
    return "\n".join(lines)


def _fmt_report(world, name, turn: int | None = None, all_: bool = False) -> str:
    """本国经济报表（只读）：不传参数=最新一期；all_=跨期趋势表；turn=指定报表回合。"""
    reps = world.econ_reports.get(name, [])
    if not reps:
        return ("（尚无经济报表：第 11 回合起每 10 回合自动出一期——第 11/21/31… 回合开局可查。"
                "这是自动生成的，不能手动运行。）")
    if all_:
        return _fmt_report_trend(world, name)
    if turn is None:
        return _fmt_report_one(reps[-1])
    for r in reps:
        if r["report_turn"] == turn:
            return _fmt_report_one(r)
    return (f"没有第 {turn} 回合的报表。已出："
            + "、".join(f"第{r['report_turn']}回合" for r in reps)
            + "（每 10 回合自动出一期，不能手动运行；趋势看 report all=true）")


def _fmt_report_panel(world, name) -> str:
    """状态面板里的经济报表：**只在出表那一回合（第 11/21/31…）显示全文**，
    其余回合只给一行摘要——省上下文，需要时用 report 取全文/历史/趋势。"""
    reps = world.econ_reports.get(name, [])
    if not reps:
        return "尚无（第 11 回合起每 10 回合自动出一期，出表那回合自动进本面板，不能手动运行）"
    r = reps[-1]
    if world.turn == r["report_turn"]:                   # 本期刚出 → 全文
        text = _fmt_report_one(r)
        if len(reps) > 1:
            text += f"\n（共 {len(reps)} 期；历史期 report turn=N，跨期趋势 report all=true）"
        return text
    return (f"最新第 {r['report_turn']} 回合（GDP {r['gdp']:.1f}/回合"
            f"（{_pct(r['gdp_growth'])}）、军费占 GDP {_pct(r['military_ratio'], sign=False)}、"
            f"总资产 {r['assets']:.0f}）——全文 report、跨期趋势 report all=true")


def _gval(world, good: str, amt: int, side: str = "mid") -> float:
    """把 amt 单位 good 折成金（黄金=矿场产出，按 MARKET['黄金'] 折算）。
    side='mid' 用中间价；'buy'/'sell' 用含价差的实际成交单价（自用替代 / 外销口径）。"""
    if amt <= 0:
        return 0.0
    if good == "黄金":
        return amt * MARKET["黄金"]
    if side in ("buy", "sell"):
        return amt * world.market_quote(good, 1, side)[0]
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
        if k == "gold":
            net = sum(_gval(world, g, a) for g, a in info["outputs"].items())
            tag = "固定+金"
        else:
            net = sum(_gval(world, g, a, "sell") for g, a in info["outputs"].items())
            tag = "外销(卖价)"
        pb = f"{capex / net:.0f}回合" if net > 0 else "—"
        return f"{building}: 造价折{capex:.0f}金 · 每回合产出{tag}≈{net:.0f}金 · 回本≈{pb}"
    if k == "energy":
        fuel = sum(_gval(world, f, a, "buy") for f, a in info["fuel"].items())
        return (f"{building}: 造价折{capex:.0f}金 · 每回合烧燃料现值≈{fuel:.0f}金 "
                f"→ 产{info['energy_out']}电（电不交易，供高级建筑维持）")
    if k == "factory":
        inv = sum(_gval(world, f, a, "buy") for f, a in info["inputs"].items())
        out_self = sum(_gval(world, g, a, "buy") for g, a in info["outputs"].items())
        out_sell = sum(_gval(world, g, a, "sell") for g, a in info["outputs"].items())
        net = out_self - inv
        ec = info.get("energy", 0) * _gval(world, "木头", 1, "buy") / 2  # 电按"1木发2电"的燃料成本估
        pb = f"{capex / net:.0f}回合" if net > 0 else "—"
        return (f"{building}: 造价折{capex:.0f}金 · 每回合投{inv:.0f}金(买价)料→产{out_self:.0f}金"
                f"(买价=自用替代；纯外销只值{out_sell:.0f}金)"
                f"（毛利{net:+.0f}金；另耗{info.get('energy', 0)}电≈{ec:.0f}金） · 回本≈{pb}")
    if k == "barracks":
        return (f"{building}: 造价折{capex:.0f}金 · 不自动产金，每兵营每回合可征1军"
                f"（步10粮5装 / 骑12粮12装，耗兵料另计）")
    if k == "townhall":
        return (f"{building}: 造价折{capex:.0f}金 · 每回合 = {TOWN_HALL_GOLD}金基础"
                f" + 该地块每座建筑×{TOWN_HALL_PER_SLOT}金（不含自身；10建筑城≈"
                f"{TOWN_HALL_GOLD + 10 * TOWN_HALL_PER_SLOT}金/回合）"
                f" · 需本地已用位≥6、每地块限1座、耗1电")
    if k == "tower":
        return f"{building}: 造价折{capex:.0f}金 · 不产金：事件视野 +{WATCHTOWER_RADIUS} 圆（情报投入）"
    if k == "academy":
        return (f"{building}: 造价折{capex:.0f}金 · 不产金：本地块一切建造金价 -{ENGINEER_DISCOUNT}%"
                "（需本地已用位≥4，后续建筑越贵回得越多）")
    if k == "militia_camp":
        return (f"{building}: 造价折{capex:.0f}金 · 屯田 +1粮/回合；可征民兵（50金+5粮/支，每座1支/回合，"
                "全国民兵总数≤全国军屯数），民兵驻本格不耗补给（需本地耕地≥1、每地块限1座）")
    return f"{building}: 无核算"


def _fmt_econ(world, name: str | None = None) -> str:
    """当前市价经济表：各建筑造价(折金)/毛利/回本，供建设决策。
    采集/金矿按「卖价」折算产出（外销口径）；加工厂按「买价」折算（自用替代口径）。"""
    L = ["【经济核算 · 当前市价】单位建筑投入产出（木头按现价折金入造价，市政厅=5金基础+本地建筑×1金/回合）："]
    L.append("现价: " + "  ".join(f"{g}={world.prices.get(g):.1f}" for g in GOODS_DISPLAY)
             + f"（买卖另有 {MARKET_SPREAD:.0%} 价差）")
    for b in BUILDINGS:
        L.append("  " + _econ_building(world, b))
    if name:
        ps = world.nation_armies(name)
        need = sum(unit_supply(a) for a in ps)
        if need:
            buy_p = world.market_quote("补给", 1, "buy")[0]
            L.append(f"  【你的口径】{len(ps)} 支军队每回合吃 {need} 补给；全按现买价 {buy_p:.2f} 买 ≈ "
                     f"{need * buy_p:.0f} 金/回合——补给厂/装备厂的价值随军队规模摊薄，别只盯外销价。")
    L.append("  注：粮/木是内需品（便宜、回本慢）；靠外贸赚钱卖 矿/油/装备。加工厂产出按「买价」折算，"
             "因为它的产出是替你去市场上买。")
    return "\n".join(L)


def full_state(world, name, replay_since: int | None = None) -> str:
    """本回合的新鲜状态（上下文尾部）。replay_since 见 turn_state。"""
    others = "、".join(n for n in world.alive() if n != name) or "（只剩你）"
    return "\n".join([
        f"你（{name}）现在进行第 {world.turn} 回合的行动。其余国家：{others}。",
        f"【国力】\n{_res_line(world, name)}",
        f"【国策规划】\n{_fmt_plan(world, name)}",
        f"【国土/视野】\n{_fmt_land(world, name)}",
        f"【军队】\n{_fmt_armies(world, name)}",
        f"【威胁】\n{_fmt_threats(world, name)}",
        f"【市场】\n{_fmt_market(world, name)}",
        f"【经济报表】\n{_fmt_report_panel(world, name)}",
        f"【纪事(近10回合)】\n{_fmt_memory(world, name, replay_since)}",
        f"【近讯】\n{_fmt_news(world, name)}",
    ])


def compact_state(world, name) -> str:
    r = world.nations[name].res
    n_army = len([a for a in world.armies if a["owner"] == name])
    return (
        f"【刷新】你{name} 国库{r['黄金']} 粮{r['粮食']} 木{r['木头']} 矿{r['矿石']} "
        f"油{r['石油']} 装{r['装备']} 补给仓{r['补给']} | 军队{n_army} | "
        f"累计总消费{world.spend_total(name):.0f} | 近讯见 events。继续你的行动，做完了调 end_turn。"
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
            "news": _fmt_news(world, actor),
            "threats": _fmt_threats(world, actor),
            "econ": _fmt_econ(world, actor),
            "plan": _fmt_plan(world, actor),
        }.get(which, full_state(world, actor))

    # ---- 规则查询（= README 的游戏规则；匈奴另附 README 原文全文）
    if tool in ("rules", "规则", "help", "帮助"):
        text = rules_text(world, str(args.get("topic", "") or ""))
        if world.polity.get(actor) == "huns":
            text = ("【匈奴教义（必须贯彻）】\n" + HUNS_DOCTRINE + "\n\n" + text
                    + "\n\n【README 原文（完整游戏文档，供检索细节）】\n" + _README_TEXT)
        return text

    # ---- 经济核算（建设回报，按当前市价）
    if tool in ("econ", "核算", "经济核算", "预算", "回本"):
        b = str(args.get("building", "") or "")
        if b in BUILDINGS:
            return _econ_building(world, b)
        return _fmt_econ(world, actor)

    # ---- 经济报表（只读：每 10 回合自动结一期，没有任何手动生成入口）
    if tool in ("report", "报表", "经济报表"):
        raw_turn = args.get("turn")
        try:
            turn = int(raw_turn) if raw_turn not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            turn = None
        all_ = str(args.get("all", "")).strip().lower() in ("1", "true", "yes", "y", "是", "全部")
        return _fmt_report(world, actor, turn, all_)

    # ---- 领土（无"凭空占"：只有军队 atk 才占地）
    if tool in ("expand", "拓荒", "activate"):
        return ("没有单独占地命令：占地一律走 atk——派军队 attack 目标格，若那格没有守军（敌人=0）军队直接进驻占领；"
                "有野人则打赢后自动占地；野地上只有别人和平驻守时也直接进驻占领（它们不参战、回合末被遣返）。"
                "mv 只挪位置、不占地。"
                "野地上有别人正在打野时不能 atk 插足。"
                "占地按索取顺序：第一个 atk 者优先，它阵亡则顺位最早入场者。"
                "国家间永久中立：他国领土不可进入、不可攻击，扩张只能打野人、占无主地。")

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
                f"建议从 经济发展/军事扩张/内政节奏 三方面写（可后续 plan 随时修订）。")

    # ---- 结束回合（必须带一句话小结 + 有效国策）
    if tool in ("end_turn", "结束回合", "done"):
        pl = world.plans.get(actor)
        if not pl or not str(pl.get("text", "")).strip():
            return ("本回合还不能结束：还没有国策规划。请先 plan(content=…) 制定国策"
                    "（常驻上下文作为长期目标；建议涵盖 经济发展/军事扩张/内政节奏）。")
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
        "name": "query", "description": "查询接口：随时获取你的各面板。res=国库与储备 / plan=国策规划 / land=地皮(国土+可拓荒地) / army=军队 / market=世界市场(现价/买价/卖价/均衡价+大单试算) / econ=经济核算(各建筑造价毛利回本) / news=近讯 / threats=视野内敌军 / all=全部。每个行动后状态会变，拿不准就再查一次。",
        "parameters": _props({"panel": {"type": "string", "enum": ["all", "res", "plan", "land", "army", "market", "econ", "news", "threats"], "description": "要查询的面板", "required": True}})}},
    {"type": "function", "function": {
        "name": "report", "description": "查本国经济报表（免费、只读）。每 10 回合**自动**结一期，第 11/21/31… 回合开局可查，**不能手动运行**。内容：市场计价 GDP 及增长率、扣除军费的财政收入、军费占 GDP 比、国家总资产及增长率、本期投资总量及增长率、外贸/内循环占比。不传参数=最新一期；turn=指定报表回合（如 21）；all=true=跨期趋势对比表。",
        "parameters": _props({"turn": {"type": "integer", "description": "报表回合（11/21/31…）；省略=最新一期"},
                              "all": {"type": "boolean", "description": "true=返回全部期的趋势对比表"}})}},
    {"type": "function", "function": {
        "name": "rules", "description": "查询完整游戏规则（相当于 README）：建筑造价与上限、地形、电网经济、军队战斗、市场、回合存档。可带 topic 只取相关段（如 '兵营'、'战斗'、'市场'）；不带则返回全文。",
        "parameters": _props({"topic": {"type": "string", "description": "想查的主题（可选）"}})}},
    {"type": "function", "function": {
        "name": "econ", "description": "按当前市价核算建设回报：某建筑的 造价(折金)/每回合毛利/回本时间；不带 building 则输出全部建筑经济表。做建设/买卖决策前先算再定。",
        "parameters": _props({"building": {"type": "string", "enum": BUILD_NAMES, "description": "要核算的建筑名（可选；省则输出全部）"}})}},
    {"type": "function", "function": {
        "name": "build", "description": "在自己的一块地上建一座建筑。每地块每回合限建1座。建筑: 城堡/林场/农场/矿场/黄金矿场/石油厂/木材能源厂/石油能源厂/补给厂/装备厂/兵营/市政厅/瞭望塔/工程院/军屯。采集类上限=本地资源量；补给厂/装备厂/能源厂任地可建（工业不挑地）；兵营需本地已用建筑位≥3；瞭望塔=事件视野+4圆；市政厅需本地已用位≥6且每地块限1；工程院=本地建造费-25%需本地位≥4；军屯=屯田+1粮、可征民兵(50金+5粮/支、全国民兵总数≤军屯数)且民兵驻本格不耗补给（需本地耕地≥1、每地块限1座）。",
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标如 '5 6' 或自家地块名（land 面板有）", "required": True},
                              "building": {"type": "string", "enum": BUILD_NAMES, "description": "建筑名", "required": True}})}},
    {"type": "function", "function": {
        "name": "recruit", "description": "在自己有兵营且电网正常的地块征召军队，每兵营每回合1支。兵种 kind：步=步兵(10粮+5装，动1格/回合、耗补给1)；骑=骑兵(12粮+12装，动2格/回合、耗补给2)；民=民兵(军屯征召：50金+5粮/支，80HP/攻20，动1格/回合、耗补给1；驻本格军屯不耗补给)——廉价驻守军队，每军屯每回合1支、全国民兵总数≤全国军屯总数（阵亡后才能补员）。注意补给仓必须跟上：补给不足时全军按缺口比例扣血（满缺 -35HP/军/回合，交战中也照扣），饿毙不复活。",
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标 '5 6' 或名字", "required": True},
                              "n": {"type": "integer", "description": "征召数量（默认1）"},
                              "kind": {"type": "string", "enum": ["步", "骑"], "description": "兵种（默认 步）"}})}},
    {"type": "function", "function": {
        "name": "move", "description": "把一支自己的军队以自身为中心按兵种速度移动（步兵 1 格=3×3、骑兵 2 格=5×5），纯移动不占地。每回合每支限1次。**行军不打野人**：合法移动目标只有两种：**野地（无人荒地）、自家格**——野地可直接走进/穿过（行军不打野人，野人只在被 atk 时接战）。**他国领土 mv 一律不得进入**（国家间永久中立）。交战中不能移动，须先 retreat 撤出。",
        "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "attack", "description": "军队(按兵种速度可及：步1格/骑2格)冲入目标地块并交战——打赢野人守军自动占地；格上**无任何军队**则直接进驻占领；野地上只有别人和平驻守时也直接进驻占领（它们不参战，回合末自动遣返）。**不抢别人的战斗**：野地上有别人正在打野 → 不能 atk 插足（可 mv 旁观）。占地按索取顺序：第一个 atk 者优先，它阵亡则顺位最早入场者。**他国领土不可攻击**（国家间永久中立）。",
        "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"}, "description": "参战本国军队id数组（各国独立从1编号）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "retreat", "description": "交战中的军队（含防守方守军）撤出——**固定只能退相邻 1 格**（所有人，不按兵种速度）。撤退不立刻结算：军队留在战场参与本回合末战斗结算（伤害全场分摊；防御方撤退减伤50%；撤退军本回合输出-80%），结算后自动脱离到目标格。目标限 己方/无人荒地；四周无合法撤退点则无法撤退。mv 不能从交战地撤离；想脱离战场一律用 retreat。",
        "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "buy", "description": "从世界市场买物资花黄金。买=推高市价；成交按「沿曲线均价」结算并含 5% 买价差，越急买越贵（试算见 query panel=market）。",
        "parameters": _props({"good": {"type": "string", "description": "物资：粮食/木头/矿石/石油/装备/补给", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "sell", "description": "向世界市场卖物资赚黄金。卖=压低市价；成交按「沿曲线均价」结算并扣 5% 卖价差，大单自己砸盘（试算见 query panel=market），分批慢慢卖更划算。",
        "parameters": _props({"good": {"type": "string", "description": "物资", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "plan", "description": "制定或修订你的国策（长期战略目标），会永久常驻你的上下文（【国策规划】标记），直到你再次修订。⚠ 结束回合(end_turn)前必须已有国策；且每 10 回合必须修订一次，否则 end_turn 会被拦。建议按三方面写：经济发展（粮木矿油/建设/卖买）、军事扩张（征兵/打野人/拓地）、内政节奏（电网/补给/国策）。",
        "parameters": _props({"content": {"type": "string", "description": "国策内容", "required": True}})}},
    {"type": "function", "function": {
        "name": "end_turn", "description": "结束本国本回合的行动。⚠ 必填 summary：用一句话总结你这回合做了什么/当前立场（例如：summary=这回合建了两座农场并继续拓荒）。没有这句小结就不算结束本回合。",
        "parameters": _props({"summary": {"type": "string", "description": "一句话回合小结（必填，>=4字）", "required": True}})}},
]

# 工具 schema 的固定 token 开销（每次请求都随 tools 发送，计入上下文预算）
TOOL_SCHEMAS_TOKENS = est_tokens(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))

_TOOL_SCHEMAS_HUNS: list[dict] | None = None


def tool_schemas(world, name) -> list[dict]:
    """该国本轮的工具 schema。匈奴政体骑兵征召价不同（8粮8装），需按政体替换描述——
    否则匈奴 AI 在 schema 里看到 12粮12装、在自己的 system prompt 里看到 8粮8装，两边打架。
    schema 对同一国跨回合稳定，不影响前缀缓存。"""
    if world.polity.get(name) != "huns":
        return TOOL_SCHEMAS
    global _TOOL_SCHEMAS_HUNS
    if _TOOL_SCHEMAS_HUNS is None:
        schemas = copy.deepcopy(TOOL_SCHEMAS)
        for t in schemas:
            fn = t["function"]
            if fn["name"] == "recruit":
                fn["description"] = fn["description"].replace("骑=骑兵(12粮+12装",
                                                              "骑=骑兵(8粮+8装")
        _TOOL_SCHEMAS_HUNS = schemas
    return _TOOL_SCHEMAS_HUNS


def _huns_prompt(world, name) -> str:
    return (
        "你是草原游牧帝国【" + name + "】（匈奴）的可汗，以劫掠、夺产维生。\n\n"
        "【政体约束（硬性）】本局**国家间永久中立、没有外交**：你不能通信、结盟、宣战，"
        "别国领土你进不去也打不了。你的扩张只能打野人、占无主地。\n"
        "【开局（事实）】你是骑兵开局：每骑每回合耗 2 补给，别饿空，否则全军按缺口比例扣血（满缺 -35HP/回合）。"
        "你建建筑有 +30% 惩罚（别走种田流），但你的骑兵征召只要 8 粮+8 装（比别人便宜）。\n"
        "【生存（事实）】你开局**粮木矿全为 0**（连一座建筑都盖不起），**补给只够十几回合，越拖越容易死**。"
        "必须在见底前把「存量」变成「流量」：先抢到能持续产金/产补给的地。"
        "缺什么就从世界市场 buy，多余的就 sell。"
        "补给仓空了每军按缺口比例扣血（满缺 -35HP/回合，交战中也照扣），会饿死。\n"
        "【教义（必须贯彻）】\n" + HUNS_DOCTRINE + "\n"
        "【信息】情报有迷雾，你只看得见自己地盘与相邻一圈。想细看机制就 rules 查。"
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
    return (
        "你是国家元首【" + name + "】，在一个大地图战略游戏里治国。\n\n"
        "这局没有预设目标：富国、拓荒、扩军、攒家底都行，由你自己判断；每种选择都有后果，后果也由你承担。\n"
        "【本局设定（硬性）】**国家间永久中立：没有外交**——不能通信/结盟/宣战，"
        "他国领土一律不可进入、不可攻击。各国各自打野人扩张、各自经营，"
        "唯一的交互是共用同一个世界市场（你买我卖会互相影响市价与均衡价）。\n"
        "【回合】每回合你可用工具做很多事：建设/拓荒/征兵/调兵/打仗/买卖。你有一个常驻的【国策规划】"
        "：结束回合前必须先 plan 制定，且每 10 回合必须修订一次（建议涵盖 经济发展/军事扩张/内政节奏）。"
        "做完用 end_turn 结束本回合，"
        "并在 summary 用一句话小结你这回合的作为。地理：每块地=1格，军队每回合限移动一次、"
        "按兵种速度以自身为中心移动（步兵 1 格=3×3、骑兵 2 格=5×5）；撤退( retreat )是例外——"
        "固定只能退相邻 1 格（不按兵种速度），撤退军队参与回合末战斗结算（伤害全场分摊；防御方撤退减伤50%；撤退军本回合输出-80%）后自动脱离，无合法撤退点（己方/荒地）则不能退。\n"
        "【资源用途（事实）】木头=建一切建筑+木材电厂燃料；粮=征兵(10/军)+补给厂原料；矿=装备厂+补给厂原料；"
        "油=装备厂原料+油电厂燃料；装=征兵(5/军)；补给=每军每回合耗1，仓空每军按缺口比例扣血（满缺 -35/回合）。\n"
        "【生产链（事实）】林场/农场/矿场/石油厂/黄金矿场=采集；木材厂(耗1木→2电)/油电厂(耗1油→8电)=发电，"
        "电不存储，电网不足则补给厂/装备厂/兵营/市政厅全停摆；补给厂(粮1+矿1→补给2)；装备厂(矿1+油1→装备2)；"
        "兵营(耗1电，需本地建筑位≥3)每回合可征1军(10粮5装)；黄金矿场+10金/回合；也可世界市场 buy/sell 换黄金。\n"
        "【扩张与战斗（事实）】占地一律走 atk：派军进目标格，有野人守军就打赢再占、"
        "敌人=0 就直接进驻占领；mv 只是挪位置，不占地。"
        "野地上有别人正在打野时不能 atk 插足（可 mv 旁观待命）；别人和平驻守的野地可 mv 旁观，也可 atk 占地（它们不参战、回合末被遣返）。"
        "占地按索取顺序：野人清空后，进攻方里第一个 atk 者占地，它阵亡则顺位最早入场者。"
        "撤出攻守对等：交战中的军队（含防守方）离开战场一律用 retreat（固定只能退相邻1格，回合末随战斗结算后脱离、守方撤退减伤50%——守方=该格未参战一方/野人；撤退军本回合输出-80%）；mv 不能从交战地撤离；"
        "交战中双方（含守军）一律不回血。军队非交战且补给够时每回合回25HP。"
        "**他国领土永远不可进入、不可攻击**——想扩地只有一条路：打野人、占无主地。\n"
        "【市场（事实）】黄金是货币；可交易粮/木/矿/油/装/补给。买推高市价、卖压低市价，"
        "成交按沿曲线均价结算并含 5% 价差；每回合市价向供需均衡价回归（全世界产得多就便宜、耗得多就贵）。"
        "分批慢慢卖比一次砸盘划算。\n"
        "【信息】情报有迷雾：你只看得见自己地盘与相邻一圈（建瞭望塔可把事件视野再往外推）；"
        "他国国库/储备/军队你看不到，只能从边界动静推断。\n"
        "【规则查询】完整玩法（造价/地形/战斗/市场细则）随时可查：调用 rules，可带主题如 rules(建筑)、rules(战斗)。\n"
        "【行动建议（自由）】动手前可用 query 看面板（res/land/army/market 随时可查）；"
        "想清楚再调用工具。你可以边想边做，也可以只做一两件事。没有『应该』怎么做，只有你想要什么后果。"
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


def turn_state(world, name, replay_since: int | None = None) -> str:
    """本回合的新鲜状态（消息尾部）。replay_since=已进 replay 的最早回合，
    用于把状态面板里重复的内容去掉（见 _fmt_memory/_fmt_news）。"""
    return (f"以上为过往回合记录，现在开始第 {world.turn} 回合行动。\n"
            + engine_call(full_state, world, name, replay_since))


def build_context(world, name, cfg) -> tuple[list[dict], "ctxlib.Plan"]:
    """构造一国本回合的完整 LLM 上下文（预算分配与缓存布局见 ctx.py）。

    顺序：system → 历史归档（已滑出回合的总结）→ replay（窗口内完整回合，含思考）
    → 本回合状态。返回 (messages, plan)，plan 供日志与下滑裁剪使用。

    状态面板的去重边界依赖 replay 起点，而 replay 深度又依赖状态大小——先按不去重
    （更保守）估一遍拿到起点，再用真实边界重建一次。
    """
    normalize_cfg(cfg)
    system_text = engine_call(system_prompt, world, name)
    mem, sums, blocks = _ctx_parts(world, name)

    def _build(tail: str):
        return ctxlib.build(cfg=cfg, mem=mem, sums=sums, blocks=blocks,
                            system_text=system_text, tail_text=tail,
                            tool_tokens=TOOL_SCHEMAS_TOKENS)

    tail1 = turn_state(world, name, None)
    msgs, plan = _build(tail1)
    tail2 = turn_state(world, name, plan.before_turn)
    if tail2 != tail1:
        msgs, plan = _build(tail2)
    return msgs, plan


def _store_turn_memory(world, name, messages, base: int, plan) -> list[dict]:
    """把本回合新增的消息（messages[base:]）存入该国 turn_memory，并按预算下滑。

    只存本回合 append 的部分（base 之前是历史 replay），避免把整个历史嵌进每条记录、
    记录间二次方膨胀。首条加 user 回合标记，回放时分隔回合边界。完整存，不截断。
    返回被裁掉的旧回合记录（调用方可据此生成阶段块总结）。
    """
    if name not in world.nations:
        return []
    body = messages[base:]
    rec = [{"role": "user", "content": f"【第{world.turn}回合 行动记录】"}] + [dict(m) for m in body]
    mem = world.turn_memory.setdefault(name, [])
    mem.append({"turn": world.turn, "messages": rec})
    return ctxlib.slide(world, name, plan)


# ---------------------------------------------------------------------------
# 阶段块总结：滑出 replay 的回合用一次 LLM 调用压成一段，进历史归档
# ---------------------------------------------------------------------------
COMPACT_SYSTEM = (
    "你是战略游戏 AI 的记忆压缩器。用户给你它自己某几个回合的行动记录"
    "（发言、工具调用与结果）以及逐回合小结。请压成一段中文总结（300~600 字），只保留："
    "做过什么、结果如何、当前战略处境、未了结的事务/承诺/敌人/威胁。"
    "不要评价、不要虚构、不要写建议。直接输出总结正文。"
)
COMPACT_INPUT_CHARS = 120_000   # 压缩调用的输入上限（约 6 万 token），超了从最旧略细节


def _compact_input(dropped: list[dict], sums: list[dict], from_turn: int) -> str:
    """拼压缩调用的输入：逐回合小结 + 行动记录（剥思考——不带 tools 时该字段被 API 忽略）。"""
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
        text = head + text[-COMPACT_INPUT_CHARS:]
    return text


def _compact_block(client, cfg, world, name, dropped: list[dict], emit=None) -> dict | None:
    """把滑出 replay 的回合压成一段块总结并写入 world.summary_blocks。失败返回 None。"""
    sums = world.summaries.get(name) or []
    # 只压这次真正滑出的那段（更早的回合已只剩一行小结，再压一次只会丢信息）
    from_turn = int(dropped[0]["turn"])
    to_turn = int(dropped[-1]["turn"])
    user = (f"请总结我第 {from_turn}~{to_turn} 回合的经历。\n\n"
            + _compact_input(dropped, sums, from_turn))
    if emit:
        emit(f"🧠 {name} 压缩记忆：第{from_turn}~{to_turn}回合 → 一次总结调用")
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=[{"role": "system", "content": COMPACT_SYSTEM},
                  {"role": "user", "content": user}],
        max_tokens=int(cfg.get("ctx_compact_tokens", 1500)),
        temperature=0.3,
        extra_body={"thinking": {"type": "disabled"}},   # 压缩不需要思考
    )
    text = (resp.choices[0].message.content or "").strip()
    if len(text) < 20:
        return None
    block = {"from": from_turn, "to": to_turn, "text": text, "turn": world.turn}
    with _engine_lock:
        world.summary_blocks.setdefault(name, []).append(block)
    return block


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
    # 上下文：窗口大小由配置 ctx_window 定义，深度/归档/下滑水位由 ctx.py 按预算动态分配
    messages, plan = build_context(world, name, cfg)
    if emit:
        emit(f"🧠 {name} 上下文: {plan.describe()}")
    base = len(messages)  # 本回合新增消息的起点（base 之前是历史 replay，存储时不再重复）
    done = 0
    stall = 0  # 连续"只思考/空转"轮数
    agg: dict = {"calls": 0, "wall": 0.0, "stream": 0.0, "first": 0.0,
                 "maxgap": 0.0, "out_tokens": 0, "reason_tokens": 0,
                 "hit": 0, "miss": 0}

    def _auto_summary(text) -> None:
        """收尾没带 summary 时，从正文里取一句补进回合小结——否则这一回合在归档里凭空消失。"""
        line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
        line = line or "（本回合无小结）"
        line = (line[:80] + "…") if len(line) > 80 else line
        engine_call(world.summaries.setdefault(name, []).append,
                    {"turn": world.turn, "text": line})
        engine_call(world.log, f"{name} 回合小结（自动归纳）：{line}", phase="行动", nation=name)

    def _finish(d: int) -> int:
        if agg.get("calls"):
            speed = (agg["out_tokens"] / agg["stream"]) if agg["stream"] > 0 else 0.0
            inp = agg["hit"] + agg["miss"]  # 缓存按输入前缀算：hit+miss=prompt tokens
            cache = (f"｜缓存命中{agg['hit'] / inp * 100:.0f}%({agg['hit']}/{inp}tok)"
                     if inp else "")
            world.log(
                f"📊 {name} 本回合: {agg['calls']}次调用 {agg['wall']:.0f}s｜"
                f"输出{agg['out_tokens']}tok(思考{agg['reason_tokens']}){cache}｜"
                f"首token均{agg['first'] / agg['calls']:.0f}s｜最长无输出{agg['maxgap']:.0f}s｜"
                f"真正输出{agg['stream']:.0f}s｜速度{speed:.1f}tok/s",
                phase="事件")
        dropped = _store_turn_memory(world, name, messages, base, plan)
        if dropped:
            if emit:
                emit(f"🧠 {name} 下滑：裁掉第{dropped[0]['turn']}~{dropped[-1]['turn']}回合"
                     f"（{len(dropped)} 回合）——本回合前缀缓存全段重建")
            if plan.compact and name in world.nations:
                try:
                    _compact_block(client, cfg, world, name, dropped, emit=emit)
                except Exception as e:   # 压缩失败不影响主流程：归档退回一行小结
                    if emit:
                        emit(f"⚠ {name} 记忆压缩失败({type(e).__name__})，归档仍用逐回合小结")
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
                            tools=tool_schemas(world, name), tool_choice="auto",
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
        for _k in ("wall", "stream", "first", "maxgap", "out_tokens", "reason_tokens",
                   "hit", "miss"):
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
            # 有正文——当作宣告/收尾（把宣告也写进记录，跨回合记忆能回放这次收尾）。
            # 但不能靠"只说话"绕过 end_turn 的门槛：没有有效国策就先催它 plan，不许收尾。
            pl = world.plans.get(name)
            if (not pl or not str(pl.get("text", "")).strip()
                    or world.turn - pl.get("turn", world.turn) >= PLAN_MAX_TURNS):
                asst = {"role": "assistant", "content": msg.get("content")}
                if reasoning:
                    asst["reasoning_content"] = reasoning
                messages.append(asst)
                messages.append({"role": "user", "content":
                                 "本回合还不能结束：还没有有效国策。请先 plan(content=…) "
                                 "制定/修订国策，再用 end_turn(summary=…) 收尾。"})
                stall += 1
                if stall >= 3:
                    _auto_summary(msg.get("content"))
                    return _finish(done)
                continue
            asst = {"role": "assistant", "content": msg.get("content")}
            if reasoning:
                asst["reasoning_content"] = reasoning
            messages.append(asst)
            engine_call(world.log, f"{name} 宣告:「{content}」", phase="行动", nation=name)
            _auto_summary(content)
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
    """无 key 的规则 AI：委托给**游戏层**的 `rule_ai.rule_turn`，并把每个动作写进看海日志。

    策略本身在 `rule_ai.py`——那是游戏层，不依赖本 LLM 层的工具 schema / 文本面板 / 国策。
    """
    from rule_ai import rule_turn
    acts = rule_turn(world, name, rng, max_actions=max_actions)
    for tool, args, ok, msg in acts:
        log_tool(world, name, tool, args, msg)
    return len(acts)
