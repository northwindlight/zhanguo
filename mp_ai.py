# -*- coding: utf-8 -*-
"""多国 agent 层：把全部玩家功能注册成 OpenAI function tools，按国家隔离执行。

- 每个 agent 只拿到「自己该知道」的状态（自己的面板/信箱/视野内事件），
  只能调用自己的合法工具（规则与引擎完全一致，无作弊入口）。
- execute(world, actor, tool, args)：执行一个工具调用并返回结果文本。
- run_openai_turn(...)：一个国家的「回合」——反复调 LLM 直到它 end_turn / 无工具。
- dummy_turn(...)：无 key 时的规则 AI（扩张流，**版本由配置选**，见 `rule_ai.py`），
  用于机制验证/看海 demo。
"""

from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path

from game import (
    HEAL_EQUIP_COST,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    building_effect,
    DIPLO_CENTER_MIN_COST,
    LETTER_CENTER_DISCOUNT,
    LETTER_CHARS_PER_GOLD,
    LETTER_COST,
    LETTER_COST_ALLY,
    LETTER_COST_MIN,
    LETTER_FREE_CHARS,
    MARKET,
    MARKET_SPREAD,
    MAX_SLOTS,
    MOVE_COST,
    PRICE_MIN_ABS,
    PRICE_MIN_RATIO,
    RESOURCES,
    RETREAT_ATK_PENALTY,
    RETREAT_RANGE,
    TERRAIN_STATS,
    UNIT_TYPES,
    letter_cost,
    unit_supply,
)
import ctx as ctxlib
from console import dw as _dw, pad as _pad
from llm_provider import REPLAY_KEY, make_backend
from ctx import est_tokens
import rule_ai as rule_ai_registry
from mp import (BANK_LOAN_GDP_MULT, BANK_LOAN_TURNS, BANK_RATE_MAX, BANK_RATE_MIN,
                BANK_SPREAD, BLOC_NAME_MAX, BUY_REPORT_COST, COMBAT_DIE_MOD, CROSS,
                DIPLO_COST,
                EXTRA_PROMPT_TURNS,
                FALL_TRUCE_TURNS,
                PLAN_MAX_TURNS, POLITY, REPORT_EVERY, RES_KEYS, RES_LABEL,
                RETREAT_DEF_COVER, SPY_COST, SPY_TURNS, SUMMARY_MIN_CHARS,
                build_econ, good_value, retreat_note)

MAIL_BRIEF_FULL = 3      # 状态面板里完整展示的新信数（更旧的只列摘要行）
MAIL_BRIEF_ROWS = 20     # 状态面板里最多列多少条旧信摘要
LAND_CAP = 40            # `query panel=land` 一次列几块（可传 cap= 覆盖）
BATTLE_CELL_CAP = 4      # `query panel=battle` 一次列几处交战（其余只给坐标）
BATTLE_PARTY_CAP = 3     # 每处交战最多列几方
BATTLE_UNIT_CAP = 6      # 每方最多列几支军（其余折叠成「…另 N 支」）
BATTLE_BRIEF_CAP = 2     # 状态行里那行摘要最多展开几处（其余只给个数）
MAP_MAX_CELLS = 2400     # 常驻地图最多画多少格；视野被远方飞地/盟友撑爆时退回"本土+邻圈"
# ★ 地图只用**纯 ASCII**：`■`(U+25A0)、`·`(U+00B7) 的 East Asian Width 是 Ambiguous，
#   在 CJK 等宽字体下按全角渲染 ⇒ 整张格子错位（2026-09-18 修）。
MAP_LEGEND = ("地形/国土图（每格 2 字符）：第 1 位=地形（p平原 f森林 h丘陵 m山地 d沙漠，"
              "**统一小写**——不靠大小写区分敌我）；第 2 位=归属：**国别代码**（见上方对照，"
              "你自己的地同样写你自己的代码）/ . 无主空地（可直接进驻）/ * 无主且有野人守军；"
              "视野外整格写作 ?.")
MIL_LEGEND = ("军事图（与地形图同框）：一格只画一个**国别符号**（和地形图同一套国别代码，"
              "你自己的军就是你的代码）；重叠时画优先级最高的那个（自家优先，其次按国序）。"
              "野人守军**不在**此图（见地形图的 *）。逐军明细看【军队】面板，视野内敌军看【威胁】面板——"
              "这张图只管「谁在哪儿」")

# ★ 局内上下文**不注入 README**（2026-09-15）：`rules` 一律返回**doc 目录那本
#   《游戏说明书》**（2026-10-06 起；此前是 `_help_sections()` 现算的手写规则段），
#   **所有政体同一份**。曾经匈奴的 rules 额外附一份 README 原文全文
#   （`mp_ai._README_TEXT`）——那份文本是给人类看的（配置项/项目结构/RL 线/结算说明），
#   对局内玩家大半是噪声，而且把"README 里能写什么"绑成了**规则约束**（分布类数字
#   一度因此不许进 README）。匈奴的专属机制由 system prompt 里的【教义】承担
#   （`_huns_prompt`），`rules` 不再重复。
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
    "bloc_transfer", "移交盟主", "bloc_dissolve", "解散联盟",
    "bloc_rename", "改盟名", "联盟改名",
}

# 匈奴教义（喂给匈奴 AI，令其贯彻；人类侧全文见 匈奴教义.md）
def _move_brief(kind: str) -> str:
    """某兵种"能走几格"的说法——**从 `MOVE_COST` / `UNIT_TYPES` 现算**，不写死数字。

    规则：移动力 = `UNIT_TYPES[kind]["speed"]`；一步的代价 = max(出发格, 目标格)，
    所以"开阔地形能走几格 = 移动力 ÷ 开阔代价"、"崎岖能走几格 = 移动力 ÷ 崎代价"。
    """
    budget = UNIT_TYPES[kind]["speed"]
    costs = MOVE_COST.get(kind, {}) or {t: 1 for t in TERRAIN_STATS}
    open_cost = min(costs.values())
    open_cells = budget // max(1, open_cost)
    slow = [t for t in TERRAIN_STATS if costs.get(t, 1) > open_cost]
    if not slow:
        return f"动{open_cells}格/回合（任何地形）"
    slow_cells = budget // max(costs.values())
    return f"动{open_cells}格/回合（{'/'.join(slow)}只 {slow_cells} 格）"


def _move_cost_text() -> str:
    """地形代价那一句——**由 `MOVE_COST` 现算**（改表 ⇒ 文案跟着变）。

    捷径/崎岖两类地形名、以及"崎岖能走几格"都从表里推，不写死。
    """
    costs = MOVE_COST["骑"]
    open_t = [t for t in TERRAIN_STATS if costs.get(t, 1) == min(costs.values())]
    slow_t = [t for t in TERRAIN_STATS if t not in open_t]
    budget = UNIT_TYPES["骑"]["speed"]
    return (f"每走一格按地形扣（出发格与目标格取更贵的那个）："
            f"骑兵在{'/'.join(open_t)}便宜、在{'/'.join(slow_t)}贵一倍"
            f"（⇒ 只能走 {budget // max(costs.values())} 格，且**穿不过去**）；"
            f"步兵/民兵任何地形每格 1")


def _move_rule_text() -> str:
    """移动规则的完整说法——**全部由 `MOVE_COST` / `UNIT_TYPES` 现算**。

    2026-09-15 起：速度=移动力，逐格按地形扣（出发格与目标格取更贵的那个），
    多格移动逐格判定 ⇒ 崎岖地形"减速 + 穿不过去"。
    """
    rows = [f"{UNIT_TYPES[k]['label']}{_move_brief(k)}" for k in UNIT_TYPES]
    return (f"**移动**：{'、'.join(rows)}；{_move_cost_text()}。"
            f"多格移动**逐格判定**：隔着一道山地/森林、或借道别人的地界，都冲不过去。")


def _reach_brief() -> str:
    """`atk` 够得着多远的短说法——**由 `MOVE_COST` / `UNIT_TYPES` 现算**。

    （这里原来手写着"步1格/骑2格"：移动代价表一改就成假话，而工具描述是 LLM 玩家
    做决策时唯一的规则来源。改表 ⇒ 这句跟着变。）
    """
    return "、".join(f"{UNIT_TYPES[k]['label']}{_move_brief(k)}" for k in UNIT_TYPES)


def _cost_text(cost: dict[str, int]) -> str:
    """把一份料单写成"10粮+5装"这样的短说法（面板/工具描述共用，不写死数字）。"""
    short = {"粮食": "粮", "装备": "装", "黄金": "金", "木头": "木", "矿石": "矿",
             "石油": "油", "补给": "补给"}
    return "+".join(f"{amt}{short.get(k, k)}" for k, amt in cost.items())


def _diplo_chain() -> list[str]:
    """外交中心叠加减半后的外交费序列（10→5→2→1…，下限 DIPLO_CENTER_MIN_COST）。"""
    c = DIPLO_COST
    out = []
    while True:
        out.append(str(c))
        if c <= DIPLO_CENTER_MIN_COST:
            break
        c = max(DIPLO_CENTER_MIN_COST, c // 2)
    return out


def _recruit_desc(costs: dict[str, dict]) -> str:
    """征召工具的说明：费用/补给/移动**全部现算**（`UNIT_TYPES` + 政体表）。

    `costs` = {兵种: 料单}，由「该国实际征召价」（`World.recruit_cost`）或
    `UNIT_TYPES[*]['recruit']`（无政体）给出 ⇒ 匈奴的骑兵特价自动体现在文案里。
    """
    rows = []
    for k, info in UNIT_TYPES.items():
        extra = "；驻本格军屯不耗补给" if k == "民" else ""
        rows.append(f"{k}={info['label']}({_cost_text(costs.get(k, info['recruit']))}、"
                    f"耗补给{info['supply']}/回合、{_move_brief(k)}{extra})")
    return ("在自己有兵营且电网正常的地块征召军队，每兵营每回合1支。兵种 kind："
            + "；".join(rows)
            + "——民兵是廉价驻守军队，每军屯每回合1支、全国民兵总数≤全国军屯总数（阵亡或遣散后才能补员）。"
            + "注意补给仓必须跟上：补给不足时全军按缺口比例扣血"
            + f"（满缺 -{ARMY_STARVE_DAMAGE}HP/军/回合，交战中也照扣），饿毙不复活。")




HUNS_DOCTRINE = (
    "· **抢产为生，不是勒索为生**：你真正的口粮是**夺来的金矿与补给厂**，勒索只是零钱。"
    "→ **金矿优先、补给厂并列**。\n"
    "· **时间是敌人：越拖越容易死**：开局补给有限，每回合都在流血、对方在种田。"
    "必须在见底前把「存量」变成「流量」——抢到能持续产金/产补给的地才活得下去。"
    "抢地优先级：金矿地 > 补给厂地 > 粮木矿产地 > 无驻军空城 > 有驻军要塞（最后，能不碰就不碰）。\n"
    "· 勒索要小而真：**别张口太大**——开口过高对方宁可开战，小数目他懒得打、反而真能到手。"
    "**别过度夸大兵威**：对手能 spy 数你的军（各兵种真实数量），虚报兵数只会让报价不可信、直接被拒；"
    "要不报具体数字，别报假数。\n"
    f"· **骑兵战力与步兵相同**（都是 {UNIT_TYPES['骑']['atk']} 攻/{UNIT_TYPES['骑']['hp']} 血），"
    f"你的优势**只有速度**：平地上你{_move_brief('骑')}、它{_move_brief('步')}——"
    f"但**森林/山地是慢速地形、而且穿不过去**（多格移动逐格判定，"
    f"「跳过一格直取纵深」那条已经没有了）；绕山绕林是常态，别把越格突袭当想当然。"
    f"你登场时对方早已发育成型，"
    f"**打不过任何中等国家的主力，永远避开**（与敌方主力保持距离；"
    f"被咬住用 retreat 撤：只退相邻 {RETREAT_RANGE} 格、回合末结算后脱离）。\n"
    f"· **分兵掠地**（对**大而旷**的国家最有效）：骑兵分几路，各扑不同方向的无驻军城市——"
    f"平地上你比它快（{UNIT_TYPES['骑']['speed']} 对 {UNIT_TYPES['步']['speed']}），"
    f"但崎岖地形会把这点优势抹平（两边都只能少走）⇒ **走平地、挑旷野**。**机动聚集**："
    f"它被迫分兵守城/回夺时，把相邻几路临时聚起来，"
    "以多打少吃掉它的**落单部队/小股守军**，打完立刻散开继续掠地。"
    "纵深抢来的城**不必守**（守不住；价值是打击经济+逼它分兵）；**靠边、成片、产金/产补给的地才值得留兵**。"
    "别挑小国要塞化的硬骨头。\n"
    "· **议和只跟能谈的谈，且绝不亏本**：拒绝谈判/只肯白和/不回信的国家，**别再发第二次——"
    "打疼了再说**（分兵抢它的城与产金地、吃它的落单部队，疼了它自己来求）。停战期对方一分不给，你的骑兵照吃补给"
    "，所以索款(demand) 必须 ≥（已打回合 + truce 停战回合）× 每回合开销；"
    "算不过账就把 truce 压低，或干脆不议和、继续抢。\n"
    "· **坚壁清野型**（不议和、不给钱、油盐不进）：别在它身上费回合，**全力摧毁**——"
    "不打要塞，专拆它无驻军的产金/产补给地块，一块一块吃干净，直到它服软。\n"
    "· **要它死，得拔市政厅**（亡国条件＝**市政厅尽失**，不是领土尽失）：只吃产金/产补给地，"
    "它只是变穷、**永远不会亡**。真下决心灭国时，把市政厅当目标清单"
    "（厅与城堡一样在**视野内公开**，地图摘要单列「视野内市政厅」）；"
    "**拔光它最后一座厅的那一刻，它剩下的地全成无主废墟——建筑留在原地、谁进驻归谁**，"
    "你可以直接去捡（含它没来得及盖完的厅）。\n"
    "· 拿到金/赔款第一件事：立刻 buy 补给备几回合的口粮，别囤黄金。"
    "补给仓的消耗与挨饿规则以 rules 为准（每军按缺口比例扣血，别饿死）。"
)


def _res_line(world, name) -> str:
    r = world.nations[name].res
    et, mt, short = world.energy_report.get(name, (0, 0, False))
    parts = []
    for k in RES_KEYS:
        parts.append(f"{RES_LABEL.get(k, k)}{r.get(k, 0)}")
    grid = "正常" if not short else "⚠停摆"
    summ = world.econ_summary.get(name, "")
    # ★ 国祚（用户 2026-09-21：灭国条件＝市政厅尽失）——**每回合常驻**，因为它就是命：
    #   丢了最后一座就当场亡国，没有第二次机会，所以不许它只藏在 land 面板里。
    halls = world.nation_building_count(name, "市政厅")
    return (
        f"{'  '.join(parts)}\n"
        f"电网: 产{et}/需{mt} {grid}（不足则补给厂/装备厂/兵营/市政厅全部停摆）\n"
        f"国祚: 市政厅 {halls} 座（★失去全部即亡国，余土沦为无主之地）"
        + (f"\n上一回合结算: {summ}" if summ else "")
    )


def _fmt_armies(world, name, brief: bool = False) -> str:
    """逐军明细；`brief=False`（`query panel=army`）**尾部附军事图**——谁在哪儿一眼看得出。

    ★ 2026-09-20 用户：「军队面板很不详细，应该展现军队地图，地图专门做了面板没用」——
    军事图（`_fmt_mil_map`）早就做好了，却只挂在 `panel=grid` 里，而 AI 查军队看的是
    `panel=army` ⇒ 那份图基本没人看。现在接进军队面板；常驻状态里仍只给列表 + 一句指路
    （免得每回合都把整张图塞进上下文）。
    """
    mine = [a for a in world.armies if a["owner"] == name]
    if not mine:
        return "（无军队——先建兵营，再用 recruit 征兵）"
    lines = []
    for a in sorted(mine, key=lambda x: x["id"]):
        t = world.tiles.get((a["x"], a["y"]))
        where = t["name"] if t else "野外"
        st = "交战中" if a.get("engaged") else ("已移动" if a.get("moved_turn") == world.turn else "可行动")
        # 移动力与"本回合能挪到几格"现算（地形代价表见 balance.MOVE_COST）
        mv = _move_brief(a.get("type", "步"))
        reach = len(world._reachable(name, a)) - 1 if st == "可行动" else 0
        cl = world.visible_buildings(name, a["x"], a["y"]).get("城堡", 0)
        lines.append(f"{a['name']}(#{a['id']}) | {a['hp']}HP | ({a['x']+1},{a['y']+1}) {where}"
                     + (f" 城L{cl}" if cl else "")          # 驻在自家城堡里就标出来（守城加成）
                     + f" | {st} | {mv}" + (f"｜本回合可及 {reach} 格" if st == "可行动" else ""))
    body = "\n".join(lines)
    if brief:
        return body + "\n  （想看「谁在哪儿」的军队位置图：query panel=army）"
    return (body + "\n\n" + _fmt_mil_map(world, name)
            + "\n  （军队管理：move 挪位 / attack 进攻 / **disband 遣散**——不打算再用的军"
              "就地解散，当回合起即不再吃军费与补给；★不返还、交战中不可用）")


def _visible_cells(world, name) -> set:
    """name 看得见的全部格（自己/盟友的地 + 其相邻一圈 + 己方与盟友瞭望塔圆内）。

    ★ 与 `World.visible_to` **同口径**，但**从地块侧反推**而不是逐格去问：
    `visible_to` 每次调用都要遍历全表找瞭望塔，按格问 60×60 就是 3600 次 × 全表 ——
    实测 29ms（地块越多越慢），反推只要 0.1ms（**280 倍**）。
    `tests/test_map_panel.py::test_visible_cells_matches_visible_to` 逐格钉住两者等价。
    """
    bloc = world.bloc_of(name)
    allies = set(bloc["members"]) if bloc is not None else {name}
    out: set = set()
    for (x, y), t in world.tiles.items():
        if t["owner"] in allies:
            out.add((x, y))
            out.update(world.neighbors(x, y))
    r = int(building_effect("瞭望塔", "vision_radius") or 0)
    if r > 0:
        for (x, y), t in world.tiles.items():
            if t["owner"] not in allies or not t["buildings"].get("瞭望塔"):
                continue
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if dx * dx + dy * dy <= r * r:
                        out.add((x + dx, y + dy))
    return {(x, y) for (x, y) in out if 0 <= x < world.size and 0 <= y < world.size}


def _has_barb(world, x: int, y: int) -> bool:
    return any(a["owner"] == "野人" and (a["x"], a["y"]) == (x, y) for a in world.armies)


def _reach_cells(world, name) -> set:
    """本回合**有军队够得着**的格（能直接 atk 入驻/开打的）。与 land 面板同口径。"""
    reach: set = set()
    for a in world.armies:
        if a["owner"] == name and a["hp"] > 0 and not a.get("engaged") \
                and a.get("moved_turn") != world.turn:
            reach |= set(world._reachable(name, a, for_attack=True))
    return reach


ATLAS_LEGEND = (
    "坐标地图：**按势力分段**（我 / 野人 / 其他各国，空行隔开），每行一格——"
    "`(x,y)归属地形，[L2城][，地名][，番号…]`（番号垫底）。番号=短兵种+军队番号（`步1、步2、楚骑3`，"
    "可对上【军队】面板的 `#n`；格主的部队省归属前缀、排前面；无主地的野人守军写作「野人」）。"
    "**只列你视野内的格**。想要格子图（ASCII 网格）用 query panel=grid。")


def _fmt_atlas(world, name) -> str:
    """**坐标地图**（常驻默认）：视野内逐格一行文字，**按势力分段**。

    格式：`(x,y)归属地形，[L2城][，地名][，番号…]`（番号段**不加标签、垫在最后**，用户 2026-09-19：
    「驻军两个字删了」——有数字的就是军队番号，没有的就是地名，不必再点名）
    · **归属** = 我 / 野(无主) / 国名；**只列视野内的格**。
    · **番号 = 短兵种 + 军队番号**：`步1、步2、楚骑3`（番号各国独立编号、阵亡不回收），
      一眼能对上【军队】面板的 `#n`；格主的部队省归属前缀并排前面；野人守军写作「野人」。
    · 城堡 `L2城` 一律公开（视野内）。
    · **分段**：我 → 野人(无主) → 其他国（每国一段，空行隔开；用户 2026-09-19：
      「按自己，野人，其他国排开，要一个回车分割势力」）。

    为什么要文字、为什么分段：地图是给 LLM 读的——网格要配图例、还得把 2 字符格对齐成坐标；
    而这一行自带语义、零解码；分段则让"谁的地盘"一眼分块，不用在 500 行里找边界。
    网格版仍在（`_fmt_map` / `_fmt_mil_map`），走 `query panel=grid` 按需取。
    """
    vis = _visible_cells(world, name)
    if not vis:
        return "（你还没有国土，也没有视野）"
    own = set(world.own_tiles(name))
    order = {n: i for i, n in enumerate(world.order)}
    army_at: dict = {}
    for a in world.armies:
        army_at.setdefault((a["x"], a["y"]), []).append(a)

    def line_of(x: int, y: int) -> str:
        t = world.tiles.get((x, y))
        o = world.owned_by(x, y)
        who = "我" if (x, y) in own else (o if o else "野")
        line = f"({x + 1},{y + 1}){who}{world.tile_terrain(x, y)}"
        cl = t["buildings"].get("城堡", 0) if t else 0
        if cl:
            line += f"，L{cl}城"
        if t and t.get("name"):
            line += f"，{t['name']}"
        # ★ 番号段放**最后**（用户 2026-09-19：「先地名后军队，这样美观，军队会扩展」）：
        #   它的长度不定，垫底才不会把城/地名挤得忽长忽短。
        here = army_at.get((x, y), ())
        if here:
            # 列**番号**（不是计数）：格主的部队排前面，其余按国序/番号
            def _key(a: dict):
                return (0 if a["owner"] == o else 1, order.get(a["owner"], 99), a["id"])
            tags = []
            for a in sorted(here, key=_key):
                if a["owner"] == "野人":
                    tags.append("野人")
                    continue
                pre = "" if a["owner"] == o else ("我" if a["owner"] == name else a["owner"])
                tags.append(f"{pre}{a.get('type', '步')}{a['id']}")   # 短兵种+番号：步1 / 骑5 / 楚骑3
            line += "，" + "、".join(tags)
        return line

    # 分段：我 → 野人(无主) → 其他国（每国一段）
    buckets: dict = {}
    for p in vis:
        o = world.owned_by(*p)
        key = "我" if p in own else ("野人" if not o else o)
        buckets.setdefault(key, []).append(p)
    seq = [k for k in ("我", "野人") if k in buckets]
    seq += sorted((k for k in buckets if k not in ("我", "野人")), key=lambda n: order.get(n, 99))
    parts = []
    for k in seq:
        parts.append(f"【{k}】\n" + "\n".join(line_of(*p) for p in sorted(buckets[k])))
    lines = ["\n\n".join(parts)]
    lines.append(f"国土 {len(own)} 块 · 视野内 {len(vis)} 格（非自家 {len(vis - own)}）· "
                 f"可拓荒地 {len(world.frontier_of(name))} 块")
    reach = sorted(p for p in _reach_cells(world, name) if p in vis)
    if reach:
        head = reach[:24]
        txt = " ".join(f"({x + 1},{y + 1}){world.tile_terrain(x, y)}"
                       f"{'野人' if _has_barb(world, x, y) else '空地'}" for (x, y) in head)
        more = f" …另 {len(reach) - len(head)} 块" if len(reach) > len(head) else ""
        lines.append(f"本回合可及（有军队够得着，可直接 atk 入驻）: {txt}{more}")
    lines.append(ATLAS_LEGEND)
    return "\n".join(lines)


def _nation_letters(world) -> dict:
    """国别字母（A/B/C…，按世界顺序、跳过已亡国）——与看海大地图同款口径，两张地图共用。"""
    out = {}
    for i, n in enumerate(world.order):
        if n in world.nations:
            out[n] = chr(65 + i) if i < 26 else str(i - 25)
    return out


def _map_frame(world, name, vis):
    """两张地图**共用的取景框**：视野包围盒；过大（被远方飞地/盟友撑爆）时退回"本土+邻圈"。
    同框是为了上下对照着读——地形图与军事图的行列必须一一对应。"""
    own = set(world.own_tiles(name))
    x0, x1 = min(p[0] for p in vis), max(p[0] for p in vis)
    y0, y1 = min(p[1] for p in vis), max(p[1] for p in vis)
    cropped = False
    if (x1 - x0 + 1) * (y1 - y0 + 1) > MAP_MAX_CELLS:
        near = set(own)
        for (x, y) in own:
            near |= set(world.neighbors(x, y))
        near &= vis
        if near:
            x0, x1 = min(p[0] for p in near), max(p[0] for p in near)
            y0, y1 = min(p[1] for p in near), max(p[1] for p in near)
            cropped = True
    return x0, x1, y0, y1, cropped


def _axis_lines(x0, x1, y0, y1) -> list:
    """列头用**两行**（十位/个位），与 2 字符格严格对齐：单行写会在 9→10 处变成
    "9101112…"，模型有读错坐标的风险（读错 = 白烧一个动作）。"""
    lab = [f"{(x + 1) % 100:02d}" for x in range(x0, x1 + 1)]
    return ["      " + "".join(d[0] + " " for d in lab),
            "      " + "".join(d[1] + " " for d in lab)]


def _fmt_map(world, name) -> str:
    """**地形/国土图**（常驻）。只画自己国土 + 视野内的格。

    · **军队不在这张图上**（见 `_fmt_mil_map`）：军队每回合都在动，混在一起既挤又乱。
    · 视野内的**他国领地**：地形照给（小写），第 2 位放该国的**国别字母** ——
      即"既显示地形、也显示归属"，两者不冲突（地形占第 1 位、归属占第 2 位）。
    · 只用纯 ASCII：`■`/`·` 的宽度是 Ambiguous，CJK 等宽字体下按全角渲染会整张错位。
    """
    own = set(world.own_tiles(name))
    vis = _visible_cells(world, name)
    if not vis:
        return "（你还没有国土，也没有视野）"
    x0, x1, y0, y1, cropped = _map_frame(world, name, vis)
    letters = _nation_letters(world)

    def cell(x: int, y: int) -> str:
        """第 1 位地形（统一小写）、第 2 位归属。**只有一条规则**，没有例外分支：
        自家的地和别国的地写法完全一样，只是代码不同（用户 2026-09-18：「和外国一样，
        只是换成本国代码」）。建筑不再占位——要找建筑走 `land filter=<建筑名>`。"""
        o = world.owned_by(x, y)
        if o != name and (x, y) not in vis:
            # 视野外一律 `?.`：地形不显、归属/守军更不显（曾漏了归属那一支，
            # 他国的地画出 "?D" ＝白送一张势力图）。
            return "?."
        c = world.ter_char(x, y).lower()             # 地形：统一小写
        if o:
            return c + letters.get(o, "o")           # 归属：一律国别代码（含自家）
        if _has_barb(world, x, y):
            return c + "*"                           # 无主 + 有野人守军
        return c + "."                               # 无主空地：可直接 atk 进驻

    lines = [f"地形/国土图 x {x0 + 1}→{x1 + 1}、y {y0 + 1}→{y1 + 1}"
             f"（坐标 1-based，每格 2 字符；列头上下两行拼起来就是 x）"]
    lines += _axis_lines(x0, x1, y0, y1)
    for y in range(y0, y1 + 1):
        lines.append(f"  {y + 1:3d} " + "".join(cell(x, y) for x in range(x0, x1 + 1)))
    lines.append(f"国土 {len(own)} 块 · 视野内 {len(vis)} 格（非自家 {len(vis - own)}）· "
                 f"可拓荒地 {len(world.frontier_of(name))} 块")
    forts, halls = [], []
    for (fx, fy) in sorted(vis):
        t2 = world.tiles.get((fx, fy))
        if not t2 or t2["owner"] == name:
            continue
        lv = t2["buildings"].get("城堡", 0)
        if lv:
            forts.append(((fx, fy), lv))
        th = t2["buildings"].get("市政厅", 0)
        if th:
            halls.append(((fx, fy), th))
    if forts:
        head = forts[:12]
        txt = " ".join(f"L{lv}@({fx + 1},{fy + 1})" for (fx, fy), lv in head)
        more = f" …另 {len(forts) - len(head)} 座" if len(forts) > len(head) else ""
        lines.append(f"视野内城堡（看得见的要塞，攻它之前先掂量）：{txt}{more}")
    if halls:
        # ★ 市政厅与城堡同档公开（2026-09-21）：它是**国祚**——拔光某国全部市政厅即灭其国。
        #   不给这张清单，那条规则就无从瞄准（看不见的东西打不着）。
        #   无主的那种（前朝废墟）显式标出来：它不是谁的国祚，是**白捡的**。
        head = halls[:12]
        txt = " ".join(f"×{n}@({fx + 1},{fy + 1})"
                       + ("" if world.owned_by(fx, fy) else "[无主]") for (fx, fy), n in head)
        more = f" …另 {len(halls) - len(head)} 处" if len(halls) > len(head) else ""
        lines.append(f"视野内市政厅（各家的国祚——拔光即亡国；[无主]=废墟白捡）：{txt}{more}")
    lines.append("国别代码：" + "  ".join(
        f"{letters[n]}={n}" + ("(你)" if n == name else "")
        for n in world.order if n in world.nations))
    reach = sorted(p for p in _reach_cells(world, name) if p in vis)
    if reach:
        head = reach[:24]
        txt = " ".join(f"({x + 1},{y + 1}){world.ter_char(x, y)}"
                       f"{'野人' if _has_barb(world, x, y) else '空地'}" for (x, y) in head)
        more = f" …另 {len(reach) - len(head)} 块" if len(reach) > len(head) else ""
        lines.append(f"本回合可及（有军队够得着，可直接 atk 入驻）: {txt}{more}")
    if cropped:
        lines.append("（视野里有远离本土的格，未画进图——用 query panel=land 看全清单）")
    lines.append(MAP_LEGEND)
    lines.append("逐格明细：query panel=land（cap= 列几块 / offset= 从第几块起 / "
                 "filter= 只看含某建筑或某资源的格）；单格全明细：query panel=tile x= y=（或 at=地名）")
    return "\n".join(lines)


def _fmt_mil_map(world, name) -> str:
    """**军事图**（常驻）：谁在哪儿——自己全部军队 + 视野内的他国军队。

    · 格内是**归属国的国别代码**（和地形图同一套），不是逐军编号；
    · **一格只画一个**（重叠时画优先级最高的：自家优先 → 国序 → 番号），
      跨国的重叠位置会另起一行列出（同格混编），同国叠兵不重复标；
    · **野人守军不在此图**：它们驻在每一块无主地上（静态属性），见地形图的 `*`；
    · 逐军明细（番号/血量/状态/坐标）在【军队】/【威胁】面板，本图不重复。
    """
    vis = _visible_cells(world, name)
    if not vis:
        return "（无视野）"
    x0, x1, y0, y1, cropped = _map_frame(world, name, vis)
    letters = _nation_letters(world)
    order = {n: i for i, n in enumerate(world.order)}
    mine = sorted((a for a in world.armies if a["owner"] == name), key=lambda a: a["id"])
    foreign = sorted((a for a in world.armies
                      if a["owner"] != name and a["owner"] != "野人"
                      and (a["x"], a["y"]) in vis),
                     key=lambda a: (order.get(a["owner"], 99), a["id"]))
    by_tile: dict = {}
    for a in mine + foreign:
        if x0 <= a["x"] <= x1 and y0 <= a["y"] <= y1:
            by_tile.setdefault((a["x"], a["y"]), []).append(a)

    def pick(lst: list) -> dict:
        return sorted(lst, key=lambda a: (0 if a["owner"] == name else 1,
                                          order.get(a["owner"], 99), a["id"]))[0]

    lines = [f"军事图 x {x0 + 1}→{x1 + 1}、y {y0 + 1}→{y1 + 1}"
             f"（与地形图同框；一格只画一个国家符号）"]
    lines += _axis_lines(x0, x1, y0, y1)
    for y in range(y0, y1 + 1):
        cells = []
        for x in range(x0, x1 + 1):
            lst = by_tile.get((x, y))
            cells.append((letters.get(pick(lst)["owner"], "?") if lst else ".") + " ")
        lines.append(f"  {y + 1:3d} " + "".join(cells))
    counts = {}
    for a in mine + foreign:
        counts[a["owner"]] = counts.get(a["owner"], 0) + 1
    lines.append("自家军 %d 支 · 视野内他国军 %d 支（%s）· 国别代码同地形图"
                 % (len(mine), len(foreign),
                    " ".join(f"{letters.get(o, '?')}×{c}" for o, c in
                             sorted(counts.items(), key=lambda kv: order.get(kv[0], 99))
                             if o != name) or "无"))
    mixed = [p for p in sorted(by_tile)
             if len({a["owner"] for a in by_tile[p]}) > 1]
    if mixed:
        lines.append("同格混编：" + " ".join(
            "+".join(sorted({letters.get(a["owner"], "?") for a in by_tile[p]}))
            + f"@({p[0] + 1},{p[1] + 1})" for p in mixed[:8])
            + (f" …另 {len(mixed) - 8} 处" if len(mixed) > 8 else ""))
    left = [a for a in mine if (a["x"], a["y"]) not in by_tile]
    if left:
        lines.append(f"（另有 {len(left)} 支自家军在框外："
                     + " ".join(f"{a['name']}@{a['x'] + 1},{a['y'] + 1}" for a in left[:6])
                     + "——见 query panel=army）")
    if cropped:
        lines.append("（视野里有远离本土的格，未画进图）")
    lines.append(MIL_LEGEND)
    return "\n".join(lines)

def _fmt_land(world, name, cap=LAND_CAP, offset=0, filter_="") -> str:
    """国土**逐格明细**（按需查询；常驻只画 `_fmt_map`）。

    cap=一次列几块；offset=从第几块开始（翻页）；filter_=只看含某**建筑**或某**资源**的格
    （只认这两类名字，别的会被拒并列出全部可选项）。
    """
    own = world.own_tiles(name)
    flt = str(filter_ or "").strip()
    if flt:
        if flt in BUILDINGS:
            own = [p for p in own if world.tiles[p]["buildings"].get(flt)
                   or (world.tiles[p].get("pending") or {}).get(flt)]
        elif flt in RESOURCES:
            own = [p for p in own if world.tiles[p]["resources"].get(flt, 0) > 0]
        else:
            return ("filter 只认**建筑名**或**地块资源名**，别的查不了：\n"
                    f"  建筑：{' '.join(BUILDINGS)}\n"
                    f"  资源：{' '.join(RESOURCES)}\n"
                    "（想按别的条件找格，用 query panel=tile 逐格看）")
    total = len(own)
    head = (f"筛「{flt}」命中 {total} 块（原国土 {len(world.own_tiles(name))} 块）"
            if flt else f"国土 {total} 块")
    off = max(0, min(int(offset or 0), max(0, total - 1))) if total else 0
    page = own[off:off + max(1, int(cap or LAND_CAP))]
    span = f"第 {off + 1}–{off + len(page)} 块" if page else "0 块"
    lines = [f"{head}（按坐标排序；本次列 {span}，cap={cap} offset={off}）:"]
    for (x, y) in page:
        t = world.tiles[(x, y)]
        b = t["buildings"]
        used = sum(b.values()) + sum((t.get("pending") or {}).values())
        res = " ".join(f"{k}x{t['resources'][k]}" for k in RESOURCES)
        pend = " ".join(f"{bn}在建" for bn, n in (t.get("pending") or {}).items() if n)
        built = " ".join(f"{bn}×{n}" for bn, n in b.items() if n) or "无"
        free = "可建" if not t["built_this_turn"] else "本回合已下单"
        gar = " ".join(f"军{a['id']}({a['hp']})" for a in world.armies
                       if a["owner"] == name and (a["x"], a["y"]) == (x, y))
        extra = f" 在建:{pend}" if pend else ""
        core = "♥" if t.get("core") == name else ""   # ♥=核心领土（同战线盟友夺回会自动归还）
        lines.append(
            f"  {core}{t['name']} ({x + 1},{y + 1}){t['terrain']} 城L{b['城堡']} 位{used}/{MAX_SLOTS} "
            f"[资源 {res}] 建筑:{built}{extra} {free}{(' 驻:' + gar) if gar else ''}"
        )
    if off + len(page) < total:
        lines.append(f"  …还有 {total - off - len(page)} 块，用 offset={off + len(page)} 接着看")
    if not flt:
        fr = sorted(world.frontier_of(name))
        vis = _visible_cells(world, name)
        reach = _reach_cells(world, name)
        lines.append(f"可拓荒地 {len(fr)} 块（[野人]=有守军需 atk 打赢；[空地]=无守军，atk 进驻即占；"
                     f"可及=本回合有军队够得着）:")
        frs = []
        for (x, y) in fr:
            tag = "[野人]" if _has_barb(world, x, y) else "[空地]"
            ok = "可及" if (x, y) in reach and (x, y) in vis else ""
            frs.append(f"({x + 1},{y + 1}){world.ter_char(x, y)}{tag}{ok}")
        for i in range(0, len(frs), 8):
            lines.append("  " + " ".join(frs[i:i + 8]))
        lines.append(f"  地形挡路：{_move_cost_text()}。")
    return "\n".join(lines)


def _fmt_tile(world, name, xy) -> str:
    """**单格全明细**（精确查询）。三条纪律，缺一不可：

    · **视野外** → 一律不答（与地图 / 威胁面板同一套口径）；
    · **自家地** → 全明细：资源 / 建筑位 / 已建成与在建 / 可否下单 / 驻军；
    · **非自家地**（无主野地 或 他国领土）→ **只给地形与归属**（+ 看得见的军队、能否够着）。
      资源与建筑**不报**——那是"占下来才知道"的东西，也是 spy / 换图才拿得到的底细；
      顺手给"本回合可否建造"更是一种误导：那不是你的地。
      （2026-09-18 用户：「野地不是没有资源视野吗，只有地形」。）
    · **例外：无主故土**（前朝废墟——亡国留下的、`owner is None` 且已物化的格，2026-09-21）：
      **全报**（资源 + 存留建筑）。那条纪律护的是"别国的内政"，而这里已经没有国了；
      不报就等于让"建筑保留"这条规则谁也看不见、没人去捡。
    """
    x, y = xy
    if not (0 <= x < world.size and 0 <= y < world.size):
        return f"({x + 1},{y + 1}) 超出地图范围（1~{world.size}）"
    if (x, y) not in _visible_cells(world, name):
        return (f"({x + 1},{y + 1}) 不在你视野内——你只看得见自己国土 + 相邻一圈"
                "（联盟共享视野；瞭望塔再往外扩）。")
    t = world.tiles.get((x, y))
    owner = world.owned_by(x, y)
    mine = owner == name
    terrain = world.tile_terrain(x, y)
    st = TERRAIN_STATS[terrain]
    if mine:
        who = "你的国土"
    elif owner is None and _has_barb(world, x, y):
        who = "无主（有野人守军，atk 打赢才能占）"
    elif owner is None and t is not None:
        # 物化过、又没有主 —— 只可能是**亡国留下的无主故土**（见 mp._eliminate_if_dead）
        who = "无主故土·前朝废墟（atk 进驻即占，存留建筑归你）"
    elif owner is None:
        who = "无主空地（atk 进驻即占）"
    else:
        who = f"{owner} 的领土"
    lines = [f"({x + 1},{y + 1}) {terrain}｜{who}"
             + (f"｜{t['name']}" if t and t.get("name") else "")
             + ("｜♥ 你的核心领土" if t and t.get("core") == name else "")]
    lines.append(f"  地形：防御 {st['defense']:+d}%、建设惩罚 {st['build_penalty']:+d}%")
    if mine:
        res = t["resources"] if t else world.tile_resources(x, y)
        lines.append("  资源：" + " ".join(f"{k}x{res.get(k, 0)}" for k in RESOURCES))
        if t:
            b, pend = t["buildings"], (t.get("pending") or {})
            used = sum(b.values()) + sum(pend.values())
            built = " ".join(f"{bn}×{n}" for bn, n in b.items() if n) or "无"
            lines.append(f"  建筑位 {used}/{MAX_SLOTS}；已建成：{built}"
                         + ("；在建：" + " ".join(f"{bn}×{n}" for bn, n in pend.items() if n)
                            if pend else ""))
            lines.append("  本回合可下令建造："
                         + ("可以" if not t["built_this_turn"] else "不行（本回合已下过单）"))
    elif owner is None and t is not None and any(t["buildings"].values()):
        # ★ 无主故土（前朝废墟，2026-09-21 起才有）：**全报**——无主即无机密
        #   （口径同 `visible_buildings`）。这是"亡国的余产"，看得见才有人去捡。
        b = t["buildings"]
        used = sum(b.values())
        lines.append("  资源：" + " ".join(f"{k}x{t['resources'].get(k, 0)}" for k in RESOURCES))
        lines.append(f"  建筑位 {used}/{MAX_SLOTS}；存留建筑："
                     + (" ".join(f"{bn}×{n}" for bn, n in b.items() if n) or "无"))
        lines.append("  （无主之地：atk 进驻即占，**存留建筑归占领者**；谁先到谁得）")
    else:
        # 城堡**公开**（2026-09-19 用户：「不知道城堡很吃亏」）——它是看得见的要塞；
        # ★ 市政厅（2026-09-21）同档公开：它是**国祚**（拔光即亡国），看不见就没法瞄准；
        #   其余建筑与地块资源仍未探明（占下来才知道，别国建设底细靠 spy / 换图）。
        for bn, cnt in world.visible_buildings(name, x, y).items():
            if bn == "城堡":
                cd = cnt * building_effect("城堡", "defense_per_level")
                lines.append(f"  城堡：L{cnt}（该格再加 {cd:+d}% 防御，与地形**相乘**叠加）")
            elif bn == "市政厅":
                lines.append(f"  市政厅：{cnt} 座 —— **此国之祚**：拔光即亡国，"
                             "它剩下的地会全成无主之地（建筑留原地）")
        lines.append("  资源与其它建筑：**未探明**（那是别人的地/无主地——占下来才知道，"
                     "别国的建设底细只能靠 spy / 换图）")
    mine_army = [a for a in world.armies if a["owner"] == name and (a["x"], a["y"]) == (x, y)]
    if mine_army:
        lines.append("  你的驻军：" + " ".join(f"{a['name']}({a['hp']}HP)" for a in mine_army))
    outside = [a for a in world.armies
               if a["owner"] not in (name, "野人") and (a["x"], a["y"]) == (x, y)]
    if outside:
        lines.append("  他国军队：" + " ".join(f"{a['name']}({a['owner']},{a['hp']}HP)"
                                              for a in outside))
    lines.append("  本回合有军队够得着：" + ("是" if (x, y) in _reach_cells(world, name) else "否"))
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
    warn = world.churn_brief(name)     # 本回合的空转（成交回执里已当场报过，这里给个汇总）
    if warn:
        lines.append(f"  {warn} —— 同一批货倒手＝白付两趟买卖价差，别倒回来"
                     "（详见 rules(经济手册) 第四节）")
    return "\n".join(lines)


def _fmt_mail(world, name, brief: bool = False) -> str:
    """信箱。brief=True（每回合状态面板用）：只完整展示最新几封，旧信压成摘要行；
    brief=False（query panel=mail 按需查询用）：列出全部信件全文——面板里"旧信见
    query"的提示必须真能取回，否则老信件对 AI 永久丢失。"""
    box = world.mailbox.get(name, [])
    if not box:
        return "（收件箱为空）"
    if not brief:
        lines = [f"收件箱 {len(box)} 封（寄出后下回合到）:"]
        for m in reversed(box):
            lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{m['text']}")
        return "\n".join(lines)
    if len(box) <= MAIL_BRIEF_FULL:
        lines = [f"收件箱 {len(box)} 封（寄出后下回合到）:"]
        for m in reversed(box):
            lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{m['text']}")
        return "\n".join(lines)
    lines = [f"收件箱 {len(box)} 封（寄出后下回合到；旧信只列摘要，全文用 query panel=mail）:"]
    for m in reversed(box[-MAIL_BRIEF_FULL:]):
        lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{m['text']}")
    old = box[:-MAIL_BRIEF_FULL]
    for m in reversed(old[-MAIL_BRIEF_ROWS:]):
        t = " ".join(str(m["text"]).split())
        lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{t[:28]}{'…' if len(t) > 28 else ''}")
    if len(old) > MAIL_BRIEF_ROWS:
        lines.append(f"  （更早 {len(old) - MAIL_BRIEF_ROWS} 封见 query panel=mail）")
    return "\n".join(lines)


def _fmt_public_affairs(world, me: str) -> str:
    """【公开条约与战线】：**全世界公开**的国家行为——谁与谁缔约/保障、谁在打谁、有哪些联盟。

    口径（2026-09-19 用户：「**必须知情**」）：条约与战争是**公开行为**，第三方有权知道；
    **商议过程**（联盟投票与计票、求和提议的来回、写信）仍然只有当事人看得见。
    这些条目走 `World.proclaim` → `broadcast`（`seen`=全体，不受视野过滤），
    所以这里可以放心把全世界的条约/战线一次列全——它跟各国近讯里收到的播报是同一份事实。
    """
    parts = []
    for b in world.blocs:
        mem = [m for m in b["members"] if m in world.nations]
        if mem:
            parts.append(f"🤝 联盟「{b['name']}」（盟主 {world.bloc_chief(b)}）成员：{'、'.join(mem)}")
    for p in world.pacts:
        arrow = "→" if p["kind"] == "保障" else "↔"      # 保障单向、共同防御双向
        parts.append(f"🕊 {p['kind']} {world.entity_label(p['a'])}{arrow}"
                     f"{world.entity_label(p['b'])}")
    for w in world.wars:
        fls = list(w["followers"]) + list(w.get("atk_followers", []))
        parts.append(f"⚔ {w['atk']} ↔ {w['def']}" + (f"（跟随：{'、'.join(fls)}）" if fls else ""))
    for pair, until in list(world.truce.items()):
        if until < world.turn:
            continue
        a, b = sorted(pair)
        merged = world.entity_of(a) == world.entity_of(b)   # 同盟内部：和约已被联盟合并
        parts.append(f"🕊 {a} ↔ {b} 休战至第 {until} 回合"
                     + (f"（已并入 {world.entity_label(world.entity_of(a))}"
                        "，盟内本就互不攻击；联盟解散则按原到期回合恢复）" if merged else ""))
    head = "  【公开条约与战线】（全世界可见：签了什么、谁打谁——商议过程不公开）"
    return head + "\n" + ("    " + "\n    ".join(parts) if parts else "暂无：全世界还没有联盟/条约/战争")


def _fmt_diplomacy(world, name) -> str:
    lines = [f"国家关系: {world.rel_desc(name)}"]
    bloc = world.bloc_of(name)
    if bloc is not None:
        chief = world.bloc_chief(bloc)
        me_chief = ("（你是盟主：可否决议案、可改盟名、可移交盟主、可解散联盟）"
                    if chief == name else f"（盟主 {chief} 可否决议案）")
        fighting = [m for m in bloc["members"] if world.at_war(m)]
        lock = (f" ⚠ 战时锁死：{'、'.join(fighting)} 正在交战——不退盟、不解散，只能议和。"
                if fighting else "")
        lines.append(
            f"  🤝 你的联盟「{bloc['name']}」（盟主 {chief}）成员：{'、'.join(bloc['members'])}"
            " —— 盟内互通领土/互不攻击/共享视野；**对外以「联盟」这个外交实体出面**："
            "保障独立/共同防御/宣战/议和一律须联盟投票通过（vote 表态），成员个人签不了任何条约；"
            "议和由盟主出面并经投票；同战线盟友会自动归还你的核心领土（land 面板 ♥ 标记）。"
            f"{lock}\n     {me_chief}"
        )
    # 本人在内的交战战线（议和须找谈判代表：主导者；有联盟时=其盟主）
    # ★ 2026-10-06：把「我的身份」与「谈判代表是谁」直接摊在面板上——实测有国家把
    #   「被宣战方」读成「跟随方」，于是认定自己不能求和、也不敢反击（燕 T80）。
    #   身份由 `_peace_rep` 现算（引擎权威），不让模型自己从散文里推。
    wars_in = []
    for w in world.wars:
        sides = world._war_sides(w)
        if name not in sides[0] and name not in sides[1]:
            continue
        fls = list(w["followers"]) + list(w.get("atk_followers", []))
        side = "atk" if name in sides[0] else "def"
        rep = world._peace_rep(w, side)
        role = (f"★{'进攻' if side == 'atk' else '防御'}主导，**谈判代表=你，可直接 offer_peace**"
                if name == rep else f"本方谈判代表={rep}（你不能单独议和）")
        wars_in.append(f"{w['atk']}↔{w['def']}"
                       + (f"(跟随 {'、'.join(fls)})" if fls else "(无跟随)")
                       + f"｜你的身份：{role}")
    if wars_in:
        lines.append("  交战战线: " + "；".join(wars_in))
        lines.append("     ★ **反击无罪**：这条战线上你们已经开打了——atk 夺地、打人**不会再触发任何**"
                     "保障/共同防御/联盟条约（传导只在「宣战」那一刻结算过**一次**、且**一对实体只走一跳**，"
                     "此后参战名单冻结，"
                     "不会再拉进任何国家）。**放手打，缩手才是亏。**")
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
        abst = sum(1 for m in members if m in v["votes"] and v["votes"][m] is None)
        pending = len(members) - yes - no - abst
        pl = v["payload"]
        if v["kind"] == "宣战":
            desc = f"对 {world.entity_label(pl.get('target'))} 宣战"
        elif v["kind"] == "入盟":
            desc = f"{pl.get('candidate')} 申请入盟"
        elif v["kind"] == "缔约":
            act = ("撤回/解除" if pl.get("cancel")
                   else ("对外提议" if pl.get("offer") else "接受对方邀约"))
            desc = (f"{act}{pl.get('pact')}（{world.entity_label(pl.get('A'))} ↔ "
                    f"{world.entity_label(pl.get('B'))}）")
        elif pl.get("type") == "offer":
            desc = f"向 {pl.get('to')} 求和（{pl.get('kind')}{(' ' + str(pl.get('gold')) + '金') if pl.get('gold') else ''}）"
        else:
            desc = f"接受议和#{pl.get('offer_id')}"
        tail = ""
        if mine and v["kind"] != "入盟":
            if name not in v["votes"]:
                tail = f" —— vote {v['id']} choice=yes/no/abstain 表态"
            else:
                tail = "（你已投" + {True: "赞成", False: "反对", None: "弃权"}[v["votes"][name]] + "）"
        lines.append(f"  🗳 投票#{v['id']}（{v['bloc']}·{v['kind']}，发起 {v['proposer']}）{desc}"
                     f" 赞成{yes}/反对{no}/弃权{abst}/未投{pending}"
                     f"（需赞成>反对；盟主投 no 可否决）{tail}")
    my_ent = world.entity_of(name)
    incoming = [p for p in world.proposals
                if (p["kind"] == "联盟" and name in p.get("invitees", []))
                or (p["kind"] != "联盟" and p.get("B") == my_ent)]
    if incoming:
        for p in incoming:
            if p["kind"] == "联盟":
                lines.append(f"  📨 邀约#{p['id']}: {p['a']} 提议结盟「{p['name']}」"
                             f"（创始成员：{'、'.join(p['invitees'])}；respond_proposal {p['id']} true/false）")
            else:
                how = (f"——回应即提交联盟表决（respond_proposal {p['id']} true/false）"
                       if bloc is not None else f"（respond_proposal {p['id']} true/false）")
                lines.append(f"  📨 邀约#{p['id']}: {world.entity_label(p['A'])} 提议 {p['kind']} {how}")
    offers = [p for p in world.peace_offers if p["b"] == name]
    if offers:
        for p in offers:
            k = {"pay": f"{p['a']}愿赔{p['gold']}金", "demand": f"{p['a']}要你赔{p['gold']}金",
                 "white": "白和"}[p["kind"]]
            if p.get("truce"):
                k += f"（休战{p['truce']}回合）"
            lines.append(f"  🕊 求和#{p['id']}（{p['a']}→你）: {k} —— {p.get('note','')}（accept_peace/reject_peace {p['id']}）")
    my_e = world.entity_of(name)
    if world.guaranteed_by(my_e):
        lines.append("  你的实体（" + world.entity_label(my_e) + "）保障: "
                     + "、".join(world.entity_label(e) for e in world.guaranteed_by(my_e)))
    if world.guarantors_of(my_e):
        lines.append("  保障你的实体: "
                     + "、".join(world.entity_label(e) for e in world.guarantors_of(my_e)))
    own_offers = [p for p in world.peace_offers if p["a"] == name]
    for p in own_offers:
        k = {"pay": f"你愿赔{p['gold']}金", "demand": f"你索{p['gold']}金", "white": "白和"}[p["kind"]]
        if p.get("truce"):
            k += f"（休战{p['truce']}回合）"
        lines.append(f"  你提的求和#{p['id']}（给{p['b']}）: {k}")
    # 世界层面的公开事实放最后：前面是"你要行动的东西"（投票/邀约/求和），这里才是"天下大势"
    lines.append(_fmt_public_affairs(world, me=name))
    return "\n".join(lines)


def _fmt_countries(world, name) -> str:
    """外交对象面板：先把『不是自己』的国家列出来选目标。所有 to= 参数只能填这里面的国名。"""
    lines = [f"可选外交对象（其余国家；外交/通信的 to= 必须是其中之一，不能是自己）:"]
    for n in world.alive():
        if n == name:
            continue
        my, them = world.entity_of(name), world.entity_of(n)
        tags = []
        if them == my:
            tags.append(f"联盟成员(同属{world.entity_label(my)})")
        if world.has_pact("共同防御", my, them):
            tags.append("共同防御")
        if world.has_pact("保障", my, them):
            tags.append("本实体保障它")
        if world.has_pact("保障", them, my):
            tags.append("它保障本实体")
        if world.war_between(name, n):
            tags.append("交战")
        if not tags:
            tags.append("中立")
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


def _econ_manual() -> str:
    """【经济学手册】：规则段讲"**能做**什么"，这一节讲"**为什么**该这么做"。

    用户 2026-09-22 口述整本、要求「**可查询**」——所以它走 `rules` 的章节表：
    `rules(经济手册)` 单独取，`rules` 全文里也有一节。**只做润色与分节，不改主张**；
    里面的数字一律现读 `balance`（价差、地板价、贷款倍数），不手抄第二份。
    六节依次是：①扩张＝买建设的期权 ②兵与钱互为前提 ③军费不能榨干现金流
    ④别做"先卖后买"的转手 ⑤市场是个"没人消费就崩"的循环 ⑥战争是最快的获取资源方式。
    """
    half = MARKET_SPREAD / 2
    return (
        "规则段讲的是「**能做什么**」，这一节讲「**为什么该这么做**」。\n"
        "一、扩张是**买建设的期权**，不是买资源。\n"
        "  占下来的地本身**不产一粒粮、不出一块矿**——它是**建筑位**，是一张「以后在这儿盖什么」的"
        "期权，兑现还得再掏一次建造钱。所以：**光有兵，只拿得下战略纵深**（防线、缓冲、通路、"
        "逼近敌人的位置），**换不来经济增长**；地占着不建设，就是一笔压死在手里的钱。\n"
        "二、兵与钱互为前提。\n"
        "  **光有资本没有兵** ⇒ 没有投资位置（好地都在别人手里，除非你去打）；"
        "**光有兵没有资本** ⇒ 打下来也建设不起，等于拿血换了一堆空地。"
        "**资本创造复利，军队创造新的资本投资地块、同时保卫资本。** 两条腿缺一条，都走不远。\n"
        "三、军队千万不能榨干现金流。\n"
        "  军费是**每回合**持续的补给支出，吃掉的正是你本该拿去建造、再投资的那笔现金。"
        "如果你已经被榨干（国库见底、产出全被军费吃掉）：**先遣散（disband）不打算再用的军队**"
        "——免费、当回合就止损，这是最干净的一刀；没有可遣散的，才主动送掉一批军队——去打一场"
        "明知打不赢的仗，用一次战损把每回合的补给负担甩掉，**换回经济增长**。"
        "**否则你一定会被滚雪球滚死**：别人在复利，你在给军队发口粮。\n"
        "四、同一批货，别先卖后买。\n"
        f"  市场有 {MARKET_SPREAD:.0%} 买卖价差（买 +{half:.0%} / 卖 −{half:.0%}），同一批货卖了再"
        "买回来**必然亏本**——那是纯替市场转手、白送手续费。要买就直接买、要卖就直接卖，"
        "别拿市场当仓库周转。\n"
        "  ★ 引擎会盯着这条：**同一回合**在同一个商品上又买又卖，成交回执里**当场**报出"
        "「空转量与净亏」（那一刻起每笔都报一次，面板与回合摘要也会带上）——倒手量按两边"
        "重叠的部分算，多出来的那截是真实仓位、不算空转。**跨回合分批不算**：那是省钱的正确做法。\n"
        "五、市场是个循环：没人消费，价格就崩。\n"
        "  市价由全世界的产/耗决定（每回合向供需均衡价回归）：\n"
        "  · **没有军队、也没有新地块可建** ⇒ 没人消费资源 ⇒ 供大于求 ⇒ 市价一路跌到地板"
        f"（{PRICE_MIN_RATIO:g}× 基准价，最低 {PRICE_MIN_ABS:g} 金/单位）。"
        "**所有资源同时变便宜 = 大家同时变穷**：同样的存货换不回同样的金，更养不起消费，"
        "于是更没人消费——这就是**经济危机**：市场吞不下过量的资源，价格触底，谁也卖不出钱。\n"
        "  · 反过来：**大家一起大力消费**（建造、征兵、打仗的军需）⇒ 资源变贵 ⇒ 军队也养得起 ⇒ "
        "把赚到的再投出去赚新利润 ⇒ **同时也托住了自己手里资源的价格**。"
        "也就是说：**你花钱本身就在替自己维持物价**——都捂着不花，先崩的是你自己的资产。\n"
        "六、战争是最快的获取资源方式。\n"
        "  吞并一个国家：它的地、地上一砖一瓦、它的产出，一起归你——**你的 GDP 与资产可能瞬间"
        "翻倍**，如同**秦王扫六合**，滚起来就不可阻挡。所以能打的仗要敢打；"
        "但**开打前先回看第三节**：养不起的军队，先垮的是自己。"
    )


def war_manual() -> str:
    """【战争手册】：**只要在打仗，每回合强行挂载**（`World.at_war` 为真时由 system_prompt 追加）。

    用户 2026-10-06 口述要点、要求写成"战时必看"：「必须要求战争速战速决，快速夺厅，
    打兵没有用，消灭军队是服务于战略意图，对工业国磨死一两个士兵总是可以增援上的」
    ＋「防御条约不是安全网……多次重申联盟才安全可靠」。

    ★ 2026-10-07 追加（用户口述）：「**战略比战术更重要**——不要老是想着必赢的战斗，
    那实际上是给对手调兵和恢复的战略时间；机会丢失只会无限慢性失血；一旦某处触发强制
    和平，就是浪费时间换不来任何战果」，以及兵力比的经验值（**以打平原为基准**）：
    高出三成＝决定性歼灭战、五成＝稳操胜券、一倍＝一回合零阵亡全歼。
    ★ 2026-10-07 再追加（用户原话）：「**不是不堆到一格，不集中意味着你根本无法防守**」
    ＋「**不是更要有兵，是你的安全假设必须成立**」
    ＋「**而且不是什么常驻，为什么要把根本不可能被进攻的地守一堆军队，等于学燕，被运动战
    早晚打烂**」——所以第一节那段的落点是
    「你的进攻计划建立在『后方安全』这个假设上，**它必须成立、每回合都要验**」，
    **不是**「别把建筑堆在一格」（集中是对的：§四「要守的点少而厚」），
    **不是**「厅上要多堆几支」，更**不是**「每座厅常驻一支」——
    **警报只在该假设已破时响**：那格没人 **且** 看得见的敌军**本回合就够得着**
    （`_reachable(for_attack=True)`）。够不着的厅**不需要兵**。
    ★★ 那三档是**模糊值**，用户明确「模糊值够了」，并且**立意是劝它早打、不是劝它等**：
    实盘里燕国多次手握巨大优势却按兵不动（"等双倍"），所以 **30% 是动手门槛**、
    **100% 只是奢侈品**——这一节的落点是「够三成就打」，不是「堆够两倍再上」。
    不要把它改成精确公式/查表，也不要为了更准去接战斗 DP（用户 2026-10-07 已否掉 DP：
    「有点作弊了」，`tests/test_battle_panel.py::TestNoProbabilitySolver` 是那条禁令的守卫）。

    ★ 每一条都是**引擎事实**，不是劝告，落点全在代码里：
    · 亡国判据 = 市政厅尽失 —— `World._eliminate_if_dead` / `has_townhall`；
    · 厅与城堡同档、只在**你视野内**露出 —— `World.visible_buildings`（视野内的他国地只报城堡/市政厅）；
      **不是全球广播**：视野外一概看不见 ⇒ 想拔谁的厅先把视野推过去（面板「视野内市政厅」那一行）；
    · 伤害 = Σ各军攻击、按人头分摊 —— `World._resolve_battles`（`_combat_power`/`_spread`）；
    · 灭国收线 + 余土变无主 + 强制作战休战 —— `_eliminate_if_dead` 的 wars 清场 + `FALL_TRUCE_TURNS`；
    · 条约不给通行/不给视野 —— `World.allied_between`（只认同一实体）与 `World.visible_to`（只认 bloc）。
    数字一律现读 balance，不手抄第二份。停战后可用 `rules(战争手册)` 取回。
    """
    inf, cav = UNIT_TYPES["步"]["recruit"], UNIT_TYPES["骑"]["recruit"]
    hall = BUILDINGS["市政厅"]
    return (
        "**你正在打仗，这本册子每回合都要读一遍。**\n"
        "一、战争的**唯一得分动作**是拔厅。\n"
        "  亡国的**唯一**条件是**市政厅被拔光**——不是丢光领土。还握着大片土地、市政厅却被"
        "拔光的国家**照样亡国**。⚠ **但厅不是全球广播的**：它和城堡同档，**只在「你视野内」"
        "的格子上自动露出**（面板里那行「视野内市政厅」就是它；视野外一概看不见）⇒ **想拔谁"
        "的厅，先把视野推到它头上**。**占地本身不判生死，拔厅才判。**\n"
        f"  ⚠ 但市政厅**可以重建**（{hall['cost']} 金 + {hall['wood']} 木，需该格已用建筑位 ≥6）"
        "⇒ **你每慢一回合，它就把厅补回来。**\n"
        "  ★★ **把这一节反过来再读一遍：你的厅也是对手唯一的得分动作。** 空着的厅不是"
        "「后方」，是**一格白送的国祚**——敌人一支孤军 `atk` 进无军格就是**零战斗直接进驻**"
        "（引擎里没有守军就没有仗可打，它连减速都不会）。\n"
        "  ⇒ **时刻保证你的后方安全：被换家意味着战略完全失败。** 前线赢得再多，"
        "家里被走进来一次，国祚和战争工业一起归零——**这不是「打得不好」，是战略本身散了**。\n"
        "  ⇒ 推论①：**把兵全压出去之前，先问一句：「我的后方安全，靠什么成立？」**"
        "靠的**不是**「每个厅都钉一支兵」——那是**学燕**：把兵摊在自己家里，正面遇上运动战"
        "早晚被打烂（要守的点**少而厚**，见第四节）。集中的资产**只需要守一处**，"
        "那正是集中的好处；但**那一处在敌人够得着的时候，不能是空的**。\n"
        "  ⇒ 推论②：**你所有的进攻计划都建立在一个假设上——「我的后方是安全的」。**"
        "这个假设**必须成立**。它一旦不成立，你前线打得再好也全部作废"
        "（实盘：有人按时间表拔下了对手的国祚，同一时间自己的国祚连同战争工业被一格端走，"
        "整场战役的成果当场归零）。\n"
        "  ⇒ 所以每回合**验一遍**它「现在还成不成立」，而不是假定它还在。尤其是集中的资产"
        "被端掉是**连锁**的：那格若还堆着补给厂/装备厂/兵营，一格易主就一起停摆"
        "（耗电建筑**全有全无**，电网一破全部停产）。\n"
        "  ★ 状态面板顶部的【国祚警报】就是替你验这条假设的：**当你有厅没驻军、"
        "而且看得见的敌军本回合就够得着**，它会点名是哪几处、谁够得着——**它一出现，"
        "就说明假设已经破了，立刻回兵**。\n"
        "  ★ 它平时**不响**：够不着的厅**不需要**放兵——为那种地方驻军，是**被运动战拖死的"
        "第一步**（这也是「集中」的意义：让你只有一处需要守）。\n"
        "  ★ **落锤前先 `query panel=battle`**：它会摊开这一格每一方的**支数·兵种构成·"
        "合计 HP·输出基数**，以及**仅防御方**吃的地形/城堡/合计减伤——那几样就是你算"
        "「这一拳打不打得动」的全部数据（引擎不替你算胜负）。**你的军队站在哪一格，"
        "那一格就列得出来**；不在你视野内的格标「盲战」，只报我方、减伤不明——\n"
        "  ⚠ 盲战的代价是**真的**：引擎照收那格地形与城堡的减伤，只是不告诉你。\n"
        "二、**打兵不得分。** 兵是产能的产物。\n"
        f"  一支步兵 = {inf['粮食']} 粮 + {inf['装备']} 装（骑兵 {cav['粮食']} 粮 + {cav['装备']} 装）。"
        "对一个还在运转的工业国，你磨掉它一两支兵，它下回合就补回来 ⇒ **净收益约等于零**；"
        "而你为这两支兵付出的是**回合数**，回合数**不可再生**。\n"
        "  ⇒ 消灭敌军的**唯一**用处是**清掉守厅的路**。除此之外的杀兵，都是拿自己的回合数"
        "买对面的可再生产。\n"
        "  ⇒ 战果该这样记：**拔了几座厅、净拿了多少块站得住的地**——不是「杀了它几支兵」。\n"
        "三、**拖 = 纯亏。拖长战争得不到任何优势，只会让你在全世界落后。**\n"
        "  ★★ 先把这句钉死：**时间本身不产生任何优势。** 它不给你加成、不给你资源、"
        "不给你位置——它只做一件事：**把你每一回合的军费烧掉，同时让别人的复利多滚一圈。**\n"
        "  · **你不动，别人在动。** 战场之外没有「暂停」键：你在前线「稳住阵线」的每一个回合，"
        "后方各国都在占空地、盖建筑、滚复利。**你拖的不是敌人，是你自己。**\n"
        "  · **军费是每回合的固定支出**，而这些钱本可以拿去投资复利。"
        "⇒ **兵力占优还故意拖延，就是被自己的军费拖死**——你的优势正被你自己的开销"
        "一回合、一回合地吃掉。\n"
        "  · ⇒ 结论：**拖延是这局里最贵的动作。** 它什么也买不到，只买到一个"
        "「全世界都比你大了」的结局。**有优势而不动手，是极其愚蠢的一种输法。**\n"
        "  ⇒ 判据：**N 回合内没让对手少一座厅、或少一片站得住的地 ⇒ 这 N 回合你是纯亏的。**\n"
        "  ⇒ 所以**速战速决**：开战就写下「几回合内拔哪几座厅」，然后**按这个期限调兵**，"
        "不要按「哪格挨打救哪格」调兵。\n"
        "  ★★ **战略大于战术——别把力气花在「这一仗我一定能赢」上。** 必胜的小仗**不是战果，"
        "是慢性失血**：你赢一次，掉一批血、回去修几个回合；而对手这几个回合在调兵、在回血、"
        "在补厅、在把兵往你身上堆。**反复啃必胜的小仗＝节奏握在对手的增援速度手里**，"
        "你只是在替它把战线拖长。\n"
        "  ⇒ 你要的不是「每仗都赢」，是「**这条战线几个回合内结束**」：看准了就"
        "**一击拔厅**，然后收兵。**别为了「更稳」再等几回合——等来的通常不是更稳，"
        "是对方的援军。** 机会窗口不会一直开着——\n"
        f"  ⚠ **战场之外任何一处灭国，都会当场终止全天下所有战线**，并把 {FALL_TRUCE_TURNS} "
        f"回合压给每一对国家——这 {FALL_TRUCE_TURNS} 回合里**谁也不能宣战谁、谁也入不了盟**。"
        "★ **正在打的仗也一起停**（不只是与亡国者有关的那几条），停在哪个位置就是哪个位置。\n"
        "  ⇒ 所以**你算好的那一拳要是拖到那天，就白算了**：战线没了，兵还得养，"
        "对手白得一段喘息。**要么在别人亡国之前打完，要么就别指望那条线还留着。**\n"
        "四、**集中，永远集中。**\n"
        "  伤害 = 该方**各军攻击之和**；受击伤害**按人头分摊**。⇒ 兵力是**平方级**的："
        "堆在一起不只打得更疼，还**每人挨得更少**。\n"
        "  ⇒ **1 支军单独守一格 = 送。** 分散驻防不是「守土」，是把兵力拆成一份份礼物。\n"
        "  ⇒ **守土靠往同一格堆 2~3 支**（要守的点**少而厚**，不要多而薄）；**进攻同理**——"
        "要打就一次堆够，别「先打再补」。\n"
        "  ★ **打之前先看倍数**：**你的输出基数 ÷ 它的输出基数**"
        f"（步/骑各 {UNIT_TYPES['步']['atk']}、民兵 {UNIT_TYPES['民']['atk']}；撤退中的军打两折）。"
        "下面是**打平原**的基准（打丘陵/城堡要再往上抬，见下）：\n"
        "    · **高出 30%** ⇒ **决定性的歼灭战**——守军被全歼。**这就是动手的门槛，"
        "不是「还差一点」。**\n"
        "    · **高出 50%** ⇒ **稳操胜券**：两回合全歼，自己还剩三成血，翻不了盘。\n"
        "    · **高出 100%** ⇒ 一回合零阵亡全歼——**那是奢侈品，不是门槛。"
        "别拿「等双倍」当拖延的借口。**\n"
        "  ★★ **「等双倍」是本作最贵的错误。** 你等的每一个回合，对方都在**往回调兵、"
        "补厅、回血、修工事**——**你的优势会自己过期作废**。分明已经占优、却因为「还不够"
        "稳」而不动手，是这局反复出现的一种输法：等到最后，优势没了，兵也白养了。\n"
        "  ⇒ **够三成就打。** 打两回合、掉一半血，也远好过把优势拖成平手——"
        "**打掉的是它的兵和它的厅，不打掉的是你自己的回合数。**\n"
        "  ⇒ 一倍左右是**同归于尽**；贴着 1 打就是赌命（每回合双方各掷骰 ±"
        f"{max(COMBAT_DIE_MOD.values())}%）。所以要的是「**明显**高出一截」，"
        "不是「吓人的倍数」。\n"
        "  ⇒ 守方吃**地形+城堡**减伤（**相乘**叠加，只给格主/守方，沙漠 −10% 反而更好打）："
        "丘陵 +25%、森林 +10%、城堡每级 +10%。**同一拳打到丘陵上，要多堆两三成兵**才算数。\n"
        "五、**三种军队：先看清你买的是什么。**\n"
        f"  步兵：攻 {UNIT_TYPES['步']['atk']} / 血 {UNIT_TYPES['步']['hp']} / "
        f"速 {UNIT_TYPES['步']['speed']} / 口粮 {UNIT_TYPES['步']['supply']}/回合 / "
        f"征兵 {inf['粮食']} 粮 + {inf['装备']} 装\n"
        f"  骑兵：攻 {UNIT_TYPES['骑']['atk']} / 血 {UNIT_TYPES['骑']['hp']} / "
        f"**速 {UNIT_TYPES['骑']['speed']}** / 口粮 **{UNIT_TYPES['骑']['supply']}**/回合 / "
        f"征兵 {cav['粮食']} 粮 + {cav['装备']} 装\n"
        f"  民兵：攻 **{UNIT_TYPES['民']['atk']}** / 血 **{UNIT_TYPES['民']['hp']}** / "
        f"速 {UNIT_TYPES['民']['speed']} / 口粮 {UNIT_TYPES['民']['supply']}"
        f"（驻自家军屯格**免费**）/ 征兵 {UNIT_TYPES['民']['recruit']['黄金']} 金 + "
        f"{UNIT_TYPES['民']['recruit']['粮食']} 粮\n"
        "  ★ **正规军只吃粮和装备，不吃金**——只要农场/补给厂/装备厂还在转，兵就是**可再生产的**。"
        "所以「打兵」打不掉对手的战争能力，**只有拔厅能**。\n"
        "  ★ **骑兵强在哪：不是单挑，是位置。** 它的攻击和血**和步兵完全一样**，一对一它一点不占优。\n"
        f"    ① **速度 {UNIT_TYPES['骑']['speed']}**：平地上**一回合跨 2 格**⇒堆得比谁都快。"
        "伤害＝各军攻击之和、受击按人头摊 ⇒ 谁先在**同一格**堆出明显优势谁赢。"
        "**骑兵是「把集中变成可能」的兵种。**\n"
        "    ② **进得去也出得来**：撤退固定只退 1 格（所有人），但**平时移动按兵种速度** ⇒ "
        "骑兵撤下来一回合就能回前线，步兵要爬两回合。\n"
        "    ③ **它能在对方看不见的地方穿插**——这条最容易被忽略。视野是「自家地及其相邻一圈」，"
        "所以**别人视野之外就是你的盲飞区**：骑兵一回合 2 格，可以贴着野地/自家地绕过去，"
        "**出现在它后方，而它直到你踏进它的视野才看得见你**。步兵办不到：1 格/回合，绕同样的路"
        "要多花一倍回合，**而且它追不上你**（同向跑，速度差恒为 1）。\n"
        "       ⚠ 市政厅同理（**只在视野内露出**，见第一节）：**你的厅也藏不住**——推进到你家门口"
        "的敌人一样看得见它；反过来，想直取它的厅就**先把视野推过去**。\n"
        "       ⇒ 这就是「直取市政厅」的战术形态：**不要一层层推前线——绕开它的军队，去砸它的厅。**\n"
        f"    ⚠ 代价：**口粮 ×2、征兵贵**（装备 {cav['装备']} vs {inf['装备']}），"
        "且**崎岖地形吃满移动力**（森林/山地一步花光 2 点）⇒ **在森林/山地里骑兵和步兵没区别，"
        "却还在付双倍口粮**。**骑兵的主场是平地。**\n"
        f"  ★ **民兵是守备兵，不是野战兵**：攻击 {UNIT_TYPES['民']['atk']} ＝步骑四成。"
        "它的价值是**零维持**（驻自家军屯格不耗口粮、回血不吃装备）。"
        "**用它钉格子，别用它进攻。**\n"
        "六、**灭国能一夜终结整条战线。**\n"
        "  一方**主导者亡国 ⇒ 整条战线当场消失**（不用议和、不用赢下每一场）。"
        "亡国的连锁后果**全世界都会收到广播**：① 它的**全部余土立刻变成无主空地**，地上的建筑"
        "（含市政厅/城堡/兵营）**原样留着、先到先得**；② 它的军队**当场解散**；"
        f"③ **全天下强制休战 {FALL_TRUCE_TURNS} 回合**——**所有战线当场终止**"
        "（含与你无关的那些），"+ "期满各自回到中立，要再打就得重新宣战、"
        "**重新触发一遍全部条约传导**（防连环征服）。\n"
        "  ★ **休战期不是外交冻结期：不得宣战，但条约照签、盟照结。**"
        "全天下同时停火的这段窗口，**是唯一一段谁都打不了谁、可以放心谈的时候**"
        "——趁它把盟立起来；一旦那个刚跟你签了和约的对手也进了同一个联盟，"
        "**那纸和约就并入联盟了**（盟内本就互不攻击，比和约更强）。\n"
        "  ⇒ 如果对方是这条战线的**主导者**，**直取它的厅，比在边境换一百次地都快**。\n"
        "七、**防御条约不是安全网——只有联盟是。**\n"
        "  「保障独立」「共同防御」**不是保险**：\n"
        "  · **只在「宣战」那一刻结算一次**，此后这条战线的名单就**冻结**了——不是持续保护。\n"
        "  · **不给军事通行**：它们的领土**不是你的地**，你的军队**走不进去、撤退也退不进去**。\n"
        "  · **不共享视野**：你不知道它的军队在哪，它也不知道你哪一格吃紧。\n"
        "  · ⇒ **协同防御在引擎里做不到。** 它会被义务拖进来，然后**站在自己家里**；"
        "**你无从得知它会不会来**——「参战」不等于「来救你」。反过来，你也救不了它。\n"
        "  ★ **只有联盟是安全的**：联盟 = **同一个外交实体**——互通领土（自由通行 + 合法撤退地）、"
        "**共享视野**、互不攻击、**任一成员被打全盟自动参战（防守不需投票）**、"
        "同战线**自动归还你的核心领土**。\n"
        "  ⇒ **要安全，就去结盟。靠条约，等于没穿衣服。**\n"
        "八、**兵比对方多、却一直在吃败仗？立刻停下，把这四问答完再动手。**\n"
        "  「兵多」本身不是优势；**「兵多、集中、并且用出去」才是优势**。数量占优还在输，"
        "只有一种解释：**你的兵根本没被打出去。**\n"
        "  ① **为什么兵多还输？** 把最近三场交战的双方兵力列出来。若每一场你都是 1 打 2，"
        "那就不是运气——**是你自己把兵摊开了**。\n"
        "  ② **为什么我在故意分散？** 你是在「守土」，还是在**把兵力拆成一份份礼物**？"
        "要守的点必须**少而厚**：占着一堆格子、每格一支，等于每格都守不住。\n"
        "  ③ **为什么不直接消灭对手？** 你算过双方总量吗？如果你有 2:1 以上的兵力，"
        "却还在「稳住阵线」——那你不是打不赢，**是没打**。\n"
        "  ④ **为什么兵多还被动？** 被动是**选择**，不是处境。有优势却等对方先动手，"
        "等于把主动权白送：对方每回合挑你最薄的那格打，你只能到处救火。\n"
        "  ⇒ **兵多还挨打，答案永远在自己这边，不在对面。**\n"
        "  ⇒ **纠正动作**：把散出去的兵收拢成 2~3 个拳头，挑一个**一步够得到、且对方救不到**"
        "的目标，**一次堆够打掉它**；打完再收拢、再挑下一个。"
        "**别再做「哪格挨打救哪格」的救火队。**"
    )


def opening_guide() -> str:
    """【开局指南】：**前 20 回合**该知道的事（讲"开局怎么下手"，不重复规则段）。

    来源是八国局（size 36 / 第 184 回合）的尸检结论，用户 2026-09-26 拍板：
    「他们一开始居然不知道把建筑放在首都的市政厅好，会产生额外利润，这次得写明白点，
    写一本开局指南，强行挂开局 20 回合」。

    两件事要说清：
    ① **它不是新规则**——每一条都能在 `rules(建筑)` / `rules(地形)` 里查到；
       它做的是把"规则"变成"开局的动作"，并点名最贵的那几个坑；
    ② **正文里的数字一律现读 `balance`/`game`**（厅金公式、门槛位、损耗、城墙），不手抄。

    挂法：`World.opening_guide` 为真时，由 `system_prompt` 在**前 EXTRA_PROMPT_TURNS 回合**
    追加；过期后仍可用 `rules(开局指南)` 取回（走 `_lectures()` 的讲义表）。
    六节：①核心格最便宜 ②资源位 vs 建筑位 ③生产链 ④野人是静态的 ⑤攻城的价 ⑥开局次序。

    ★ **签名必须保持零参数、正文必须逐字节稳定**（与 `_econ_manual()` 同规矩，见 `ctx.py`
    开头那条"system 必须逐字节稳定"）：它进的是 system 前缀，随回合变一个字就是**每回合**
    整段前缀连同 replay 一起失效。窗口到期那一次失效（第 EXTRA_PROMPT_TURNS 回合）是有意的，
    也只该付那一次——所以这里**不许**取 world/name，不许写"第 N 回合"、价格、国名。
    ★ 另一条硬约束：正文里**不许出现 `世界央行` / `buy_report`**——`rules_text(w, "")` 无主题时
    会把全部章节（含本节）拼起来，而 tests/test_bank.py 断言"央行关着时全文不含这两个词"。
    """
    hall = BUILDINGS["市政厅"]
    hall_base = building_effect("市政厅", "gold_base")
    hall_slot = building_effect("市政厅", "gold_per_slot")
    hall_min = hall["min_slots"]
    bar_min = BUILDINGS["兵营"]["min_slots"]
    eng_min = BUILDINGS["工程院"]["min_slots"]
    dip_min = BUILDINGS["外交中心"]["min_slots"]
    eng_disc = building_effect("工程院", "build_discount")
    castle_def = building_effect("城堡", "defense_per_level")
    gold_mine = BUILDINGS["黄金矿场"]["outputs"]["黄金"] * MARKET["黄金"]
    sup_in = BUILDINGS["补给厂"]["inputs"]
    sup_out = BUILDINGS["补给厂"]["outputs"]["补给"]
    ar_in = BUILDINGS["装备厂"]["inputs"]
    ar_out = BUILDINGS["装备厂"]["outputs"]["装备"]
    wood_e = BUILDINGS["木材能源厂"]["energy_out"]
    oil_e = BUILDINGS["石油能源厂"]["energy_out"]
    terr = "、".join(f"{k} {v['defense']:+d}%" for k, v in TERRAIN_STATS.items())
    elec = "、".join(f"{k}×{v['energy']}" for k, v in BUILDINGS.items() if v.get("energy"))
    return (
        f"开局前 {EXTRA_PROMPT_TURNS} 回合，这一节会**直接挂在你的 system prompt 里**"
        f"（所以你现在读得到它；过期之后仍可用 `rules(开局指南)` 随时取回）。\n"
        f"  下面**没有一条是新规则**——每一条都能在 `rules(建筑)`/`rules(地形)` 里查到；"
        "它做的事只有一件：把那些规则变成**开局该做的动作**，并点出最贵的几个坑。\n"
        "一、你脚下那一格，是全场最便宜的地。\n"
        "  你的**核心格必为平原**（建造 0 惩罚），且**开局自带一座市政厅**"
        "（＝国祚：被拔光就亡国）。市政厅每回合给你 "
        f"**{hall_base} + 本格其他建筑数×{hall_slot}** 金 ⇒ **往这一格上放的每一座建筑，"
        "除了它自己的产出，还白送 "
        f"{hall_slot} 金/回合**。所以开局最赚的一件事：**把「不占资源位」的建筑往核心格上堆**"
        "（兵营、补给厂、装备厂、电厂、工程院、瞭望塔、城堡）。两条限制："
        f"**每地块每回合只能下 1 座单**（堆格子是慢工，要趁早起步）、**每格最多 {MAX_SLOTS} 个建筑位**"
        "（城堡级数也占位）。\n"
        "  （八国局实测：秦、魏、韩、周前 60 回合在自家核心格上一座都没盖；燕盖了，"
        "14 座厅平均每格 3.3 座 ⇒ 厅金 119 金/回合，若每格堆到 6 座则是 154——"
        "**同一张图、同一条规则，差出来的全是白捡的**。）\n"
        "二、采集类看资源位，工厂类只看建筑位。\n"
        "  林场/农场/矿场/石油厂/黄金矿场**受本地块资源量限制**（格子写着「木头×3」就只能盖 3 座林场）；"
        "军屯限耕地且每格限 1 座。兵营/补给厂/装备厂/电厂/市政厅/工程院/瞭望塔/外交中心"
        "**不受资源位限制** ⇒ 它们才是堆核心格的主力。\n"
        "  这两类的**成本结构也是分开的**：基础楼**料贵钱贱**（木材要得多、金要得少），"
        "高级楼**钱贵料不变**——所以「光有钱」铺不出一片采集楼，「光有木」也上不了工厂，"
        "两条腿都得有（查 `rules(建筑)` 看现价）。\n"
        "  **黄金矿场**"
        f"（{BUILDINGS['黄金矿场']['cost']} 金 + {BUILDINGS['黄金矿场']['wood']} 木）"
        f"每回合产 1 黄金 = **{gold_mine} 金/回合**，是最肥的金源——见到黄金位就占。\n"
        "  另记一条容易漏的：**军屯本身不产东西**，它是**民兵编制**——全国民兵总数 ≤ 全国军屯数，"
        "每屯每回合可征 1 支（民兵比正规军便宜、且驻自家军屯格**不吃补给**），开局的廉价守备就靠它。\n"
        f"  有门槛的建筑：兵营需本格已用 ≥{bar_min} 位、工程院 ≥{eng_min}、外交中心 ≥{dip_min}、"
        f"市政厅 ≥{hall_min}。\n"
        "三、生产链（这条最常被记错）。\n"
        f"  补给厂吃 {'+'.join(f'{k}{v}' for k, v in sup_in.items())} → 产补给 {sup_out}；"
        f"装备厂吃 {'+'.join(f'{k}{v}' for k, v in ar_in.items())} → 产装备 {ar_out}；"
        f"木材能源厂烧 木头1 → 电 {wood_e}，石油能源厂烧 石油1 → 电 {oil_e}。\n"
        "  ⇒ **木头只去两个地方：建材、发电——它不是任何工厂的原料**，别把它当成生产瓶颈。\n"
        f"  耗电建筑（{elec}）在**电网不足时全部停摆**（不是停一部分）"
        "⇒ 上耗电件之前先算电网余量。\n"
        "四、野人是静态的：开局的扩张零风险、不要钱。\n"
        "  全图每一格无主地都站着**一支野人**，它们**永不移动、永不增援、死了不重生**。"
        "实测：**2 支满血步兵**打一格野人**稳拿**，代价随地形递增——平原合计损 ~70 HP、森林 ~79、"
        "丘陵 ~94、山地 ~120 ⇒ **别拿伤兵去打山地**（残兵上去会全灭）。"
        "另外两条硬规矩：进攻目标**必须与军队所在格相邻**（隔 2 格会被拒），"
        "且**每支军每回合只能动一次**。\n"
        "五、攻城没那么可怕，但也确实要付钱。\n"
        f"  守方吃**地形减伤 × 城堡减伤**（相乘）：{terr}，城堡每级 {castle_def}%；"
        "**谁挨打谁是守方**（交战中的进攻方不吃加成）。"
        "实测（4 支满血守军、平原城 L1）：**6 支攻方稳拿**（约 2 回合、损 4 支军），8 支损 3 支出头 "
        "⇒ **2:1 兵力就够**。「打不下来」多半只是没凑够兵。\n"
        f"  另记一条常被忽略的成本：**伤兵回血不是免费的**——每支回血的军队每回合吃 "
        f"{HEAL_EQUIP_COST} 件装备（**民兵除外**）。所以「打完一仗」的账要连修兵一起算。\n"
        "六、前 20 回合的次序（建议，不是规则）。\n"
        f"  ① **第 1~3 回合**：核心格先落 **{bar_min - 1} 座不占资源的建筑**"
        f"（够了 {bar_min} 位才能盖**兵营**）；同时在资源最密的自己格上铺采集楼；"
        "**开工前先买料**（木价低于均衡时囤木）。\n"
        f"  ② **第 4~10 回合**：征兵 2 支，开始吃相邻野地；核心格继续一座一格地堆，"
        f"堆到 {eng_min} 位补**工程院**（本格建造 −{eng_disc}%）。\n"
        "  ③ **第 10~20 回合**：补电网（木电厂 1 木→2 电）→ 上**补给厂**"
        "（军队每回合吃补给，断供掉血）；挑一块资源密的格堆到 "
        f"{hall_min} 位，盖**第 2 座市政厅**（多一条国祚、多一份厅金）。\n"
        "  ④ 三条底线：**电网别欠**（欠了工厂和厅一起停）、**补给仓别空**、**国库别囤着**"
        "——钱只有变成建筑和军队才算数。"
    )

# ★ 2026-10-06：手写规则段 `_help_sections()` **已整体退役**——局内 `rules` 改从
#   doc 目录的《游戏说明书》取（见下面 `manual_sections()`）。三本**讲义**
#   （经济手册 / 开局指南 / 战争手册）仍在代码里，由 `_lectures()` 供给。
#   用户原话：「别再用旧总则，换成规则说明书」「整套手写规则段退役」。




def _bank_rules() -> str:
    """【世界央行】规则段（**只有开行时才返回**——关着就整节不存在，不占 token）。"""
    return (
        "国库现金**默认就是储蓄**（不用存）：每回合按储蓄利率结息——正=入账，负=扣钱"
        "（**扣到 0 为止**：储蓄永远扣不成负的）。\n"
        f"可向央行借款（`loan`）：**额度与期限都不能自选**，只有一种贷款——"
        f"**额度 = 你当时的 GDP × {BANK_LOAN_GDP_MULT}**、**期限固定 {BANK_LOAN_TURNS} 回合**；"
        "**还清前不能再借**（一国同时只有一笔）。贷款利率 = 储蓄利率 + "
        f"{BANK_SPREAD:.0%}（**可为负**：那时欠款每回合缩水）。\n"
        "每回合自动累计应还额（面板与近讯都会报）；**到期一次性强制扣款**——"
        "这一笔允许把国库**扣成负的**（欠债不还，国库先扣穿）。"
        "所以借款要拿去用在**回本快于到期日**的地方，否则到期那一下就白干。"
        f"（期限只有 {BANK_LOAN_TURNS} 回合就是这个道理：拖到 10 回合，利息翻一倍多，"
        "大半个期限都在替央行打工。）\n"
        "借钱**算外交动作**，按外交费计（成功才扣；有外交中心照样减半）。\n"
        f"央行还**卖别国已公布的经济报表**：{BUY_REPORT_COST} 金一份（`buy_report`，目标用 to=，"
        "可选 turn= 指定期；当场到手，进 query panel=spy）。报表是每国每 "
        f"{REPORT_EVERY} 回合自动结一期、**生成时全世界都收到过一行公告**的公开账，"
        "央行卖的只是**明细全文**——要偷**当下**的国库/储备/每块地建设，仍得用 spy。\n"
        "利率由**世界央行**设定，变更时**全世界播报**：上调＝抑制通货膨胀、下调＝减少紧缩。"
        "利率为负时囤现金每回合缩水——那是央行在逼你把钱花出去或投出去。"
    )


def _fmt_bank(world, name) -> str:
    """【央行】面板（开行才进常驻状态）：利率 + 你的借款状况 + 你的授信额度。"""
    r, lr = world.bank_rate(), world.bank_loan_rate()
    ln = world.bank["loans"].get(name)
    gdp, credit = world.nation_gdp(name), world.bank_credit(name)
    if ln:
        st = (f"你欠央行 {ln['due']} 金（本金 {ln['principal']}、利率 {ln['rate']:+.1%}、"
              f"还剩 {ln['turns_left']} 回合到期）——到期**强制扣款**，还清前不能再借")
    elif credit > 0:
        st = (f"你的授信 {credit} 金（= 当前 GDP {gdp:.1f} × {BANK_LOAN_GDP_MULT}）："
              f"loan 一次借满、期限 {BANK_LOAN_TURNS} 回合（**都不能自选**）；目前无欠款")
    else:
        st = (f"你还没有授信：额度 = 当前 GDP × {BANK_LOAN_GDP_MULT}，而你现在 GDP 为 0"
              "——GDP 是**上一回合的产出**（市价），先去建设/扩张，产出过东西才有授信")
    if r < 0:
        tip = "⚠ 储蓄利率为负：囤现金每回合都在缩水——央行在逼你把钱花出去或投出去。"
    elif r > 0:
        tip = "储蓄利率为正：现金每回合自动生息（别把它当成免费的——市场那本账另算）。"
    else:
        tip = "储蓄利率为 0：现金不生息也不被扣。"
    return (f"储蓄利率 {r:+.1%}（国库现金默认就是储蓄，每回合结息）｜"
            f"贷款利率 {lr:+.1%}（= 储蓄 + {BANK_SPREAD:.0%}）\n  {st}\n  {tip}\n"
            f"  央行也卖别国已公布的经济报表：{BUY_REPORT_COST} 金一份"
            f"（buy_report to=目标国）；要偷当下的底细仍得 spy。")


# ---- 局内规则文本的唯一来源：doc 目录那本《游戏说明书》-----------------------
#
# 用户 2026-10-06，四句话定下这个口径：
#   「别再用旧总则，换成规则说明书」＋「doc 目录的」→ 手写的 `_help_sections()` 退役；
#   「尽量不要给全文」→ 认不出主题就回**章节目录**，不倒整本；
#   「剔掉上帝视角章」→ 政体参数/终局结算/延伸阅读等不给局内 AI。
#
# ★ 漂移链：这本的数值块由 `docs/sync_manual.py` 从 `balance.py` **现算**
#   （`tests/test_manual_sync.py` 守漂移）。所以**改了 balance 必须跑一次
#   `python3 docs/sync_manual.py`**，否则局内 AI 读到的是旧数。
MANUAL_PATH = Path(__file__).resolve().parent / "docs" / "游戏说明书.md"

# 不进局内的章/节（**上帝视角**：人类侧操作与设计说明）。按标题前缀匹配。
MANUAL_SKIP = (
    "十、政体",        # 政体参数——文档自己写着「对局内普通国家保密（游戏内 rules 查不到）」
    "十二、终局结算",   # 人类跑 settlement.py 的那一套
    "延伸阅读",        # 外链别的文档
    "世界央行",        # 配置开关 + 观察者调息 + 平衡论证（央行另有 _bank_rules，受 bank_on 门控）
    "看海台",          # 观察者终端命令（say / 公告）
)

# 目录里每章的一句摘要（认不出主题时回目录用）。
_CHAPTER_BLURB = {
    "一局游戏": "回合制、结算顺序、存亡看市政厅",
    "经济与能源": "一本账、电网、补给仓、黄金",
    "建筑": "造价/门槛/每回合行为；地形建设惩罚",
    "军队与战斗": "兵种、移动、交战、野人、撤退",
    "外交": "实体、条约、宣战与传导、投票、情报",
    "联盟与核心领土": "联盟效果、核心领土与自动归还",
    "市场": "买卖价差、均衡价回归、空转检测",
    "经济报表": "GDP / 资产 / 军费 / 投资",
    "国策、回合与存档": "plan、行动纪律、存档兼容",
    "命令表": "玩家能做的全部动作",
    "经济手册": "讲义：**为什么**该这么搞经济",
    "开局指南": "讲义：**开局怎么下手**",
    "战争手册": "讲义：**这仗该怎么打**（战时必读）",
    "世界央行": "利率、贷款、报表买卖（开行才有）",
}


def _chapter_key(title: str) -> str:
    """章标题 → 短名（去掉「一、二、…」序号）。"""
    return re.sub(r"^[一二三四五六七八九十]+、", "", title).strip()


def _clean_manual(text: str) -> str:
    """章内清理：去 HTML 注释、去外链（留文字）、去**设计/实测注记**。

    ★ 只删不增，且只删"明显是写给开发者看的"那几种形状——**宁可漏删**：
    漏删的后果是 AI 多读一句；误删的后果是**规则没了**。
    """
    t = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)          # 开发标记
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)                 # [文字](路径.md) → 文字
    # 括号注记里含「内部符号 / 提交日期 / 实测 / 补记 / 用户口述」的，整段去掉
    t = re.sub(r"（[^（）]*(?:\.py\b|_[a-z_]{3,}\b|[A-Z][A-Z_]{3,}\b|[A-Z][a-z]+\.[a-z_]+"
               r"|20\d\d-\d\d|实测|补记|用户)[^（）]*）", "", t)
    return re.sub(r"\n{3,}", "\n\n",
                  "\n".join(ln.rstrip() for ln in t.splitlines())).strip()


def manual_sections() -> list[tuple[str, str]] | None:
    """doc 目录《游戏说明书》→ `[(章标题, 正文)]`（`###` 子节并入所属章）。

    读不到文件返回 `None`——调用方负责**优雅降级**，绝不让一次读盘失败崩掉整个回合。
    """
    try:
        raw = MANUAL_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    # ① 拆块：第一个 `##` 之前的内容（书名与开发说明）整段丢掉
    blocks: list[tuple[str, str, list[str]]] = []
    for ln in raw.splitlines():
        if ln.startswith("## "):
            blocks.append(("h2", ln[3:].strip(), []))
        elif ln.startswith("### "):
            blocks.append(("h3", ln[4:].strip(), []))
        elif blocks:
            blocks[-1][2].append(ln)
    # ② 拼章：`###` 降级成一行小标题并进上一章；被跳过的那一节**连正文一起丢**
    merged: list[tuple[str, str]] = []
    for kind, title, lines in blocks:
        if kind == "h2":
            merged.append((title, "\n".join(lines)))
        elif not any(title.startswith(p) for p in MANUAL_SKIP) and merged:
            head, body = merged[-1]
            merged[-1] = (head, f"{body}\n\n〔{title}〕\n" + "\n".join(lines))
    # ③ 整章过滤 + 清理
    out: list[tuple[str, str]] = []
    for title, body in merged:
        if any(title.startswith(p) for p in MANUAL_SKIP):
            continue
        b = _clean_manual(body)
        if b:
            out.append((title, b))
    return out


def _lectures() -> list[tuple[str, str]]:
    """三本**讲义**（在代码里，各有独立 doc 与 sync 通道）：经济 / 开局 / 战争手册。"""
    return [("经济手册", _econ_manual()), ("开局指南", opening_guide()),
            ("战争手册", war_manual())]


def rules_text(world, topic: str = "") -> str:
    """rules tool：按主题返回 doc 目录《游戏说明书》的对应章节。

    识别不出主题（或没给主题）→ 返回**章节目录**，**不倒全文**
    （用户 2026-10-06：「尽量不要给全文」）。
    """
    t = (topic or "").strip()
    secs = manual_sections()
    missing = secs is None
    secs = list(secs or [])
    secs += _lectures()
    if world.bank_on():                      # 开行才有这一节（关着不占 token）
        secs += [("世界央行", _bank_rules())]
    labels = {
        "建筑": "建筑", "建造": "建筑", "兵营": "建筑", "农场": "建筑", "城堡": "建筑",
        "瞭望塔": "建筑", "外交中心": "建筑", "工程院": "建筑", "军屯": "建筑", "民兵": "建筑",
        "工厂": "建筑", "能源": "建筑", "电厂": "建筑", "造价": "建筑",
        # 说明书**没有独立的「地形」章**（刻意外链到《地图生成与资源分布》），
        # 地形防御口径落在【军队与战斗】里 ⇒ 先把"地形"指到那儿（见 B5 待办）。
        "地形": "军队与战斗", "资源": "经济与能源", "拓荒": "军队与战斗",
        "领土": "联盟与核心领土",
        "经济": "经济与能源", "电": "经济与能源", "能源": "经济与能源", "补给": "经济与能源",
        "装备": "经济与能源",
        "军队": "军队与战斗", "战斗": "军队与战斗", "战争": "军队与战斗", "征兵": "军队与战斗",
        # 军队的「减员」词（实测 AI 反复问 topic=「解散/裁军/复员/遣散」，从前全都兜底成全书）：
        # ★ **不收裸「解散」**——「最长命中优先」下它会与「联盟」同长同命中，问「解散联盟」
        #   会同时吐【军队与战斗】和【联盟与核心领土】两节；只收与军队连写的完整说法。
        "遣散": "军队与战斗", "裁军": "军队与战斗", "复员": "军队与战斗",
        "解散军队": "军队与战斗", "裁撤": "军队与战斗", "撤编": "军队与战斗",
        "军队移动": "军队与战斗", "攻击": "军队与战斗", "野人": "军队与战斗",
        "移动": "军队与战斗", "速度": "军队与战斗", "移动力": "军队与战斗",
        "射程": "军队与战斗", "距离": "军队与战斗", "路": "军队与战斗",
        "地形挡路": "军队与战斗",
        "外交": "外交", "保障": "外交", "宣战": "外交", "求和": "外交",
        "共同防御": "外交", "休战": "外交",
        # 战争手册（战时必读）：问"这仗怎么打"的都指它
        "战争手册": "战争手册", "速战速决": "战争手册", "拔厅": "战争手册",
        "国祚": "战争手册", "集中兵力": "战争手册", "战果": "战争手册", "歼灭": "战争手册",
        "联盟": "联盟与核心领土", "同盟": "联盟与核心领土", "入盟": "联盟与核心领土",
        "退盟": "联盟与核心领土", "盟主": "联盟与核心领土", "投票": "联盟与核心领土",
        "核心": "联盟与核心领土", "归还": "联盟与核心领土", "视野": "联盟与核心领土",
        # 说明书里没有独立的「信箱」章 ⇒ 并到【外交】（写信的费用口径在那儿）。
        "信箱": "外交", "邮件": "外交",
        "市场": "市场", "买卖": "市场", "价格": "市场", "交易": "市场",
        "回合": "回合与存档", "存档": "回合与存档", "结算": "回合与存档",
        "报表": "经济报表", "GDP": "经济报表", "投资": "经济报表", "军费": "经济报表",
        # 经济手册（讲义）：问"为什么"的词都指它——"经济"本身仍指【经济与能源】那节
        "手册": "经济手册", "经济学": "经济手册", "经济哲学": "经济手册", "讲义": "经济手册",
        "扩张": "经济手册", "滚雪球": "经济手册", "现金流": "经济手册", "复利": "经济手册",
        "资本": "经济手册", "经济危机": "经济手册", "通缩": "经济手册", "消费": "经济手册",
        # 开局指南（开局该做什么）：与讲义分开——问"怎么开局"指它
        "开局": "开局指南", "指南": "开局指南", "开局指南": "开局指南", "起步": "开局指南",
    }
    if world.bank_on():
        labels.update({"央行": "世界央行", "利率": "世界央行", "贷款": "世界央行",
                       "借款": "世界央行", "储蓄": "世界央行"})
    # ★ 2026-10-06 补：实测**一问就返回全书**的缺口（原先这些说法一个都不在表里）。
    #   值可以是单个章节名，也可以是元组（一个词指多节，如「市政厅」既是建筑也是国祚）。
    labels.update({
        "打仗": "军队与战斗", "开战": "军队与战斗", "进攻": "军队与战斗",
        "攻城": "军队与战斗", "突击": "军队与战斗", "兵力": "军队与战斗",
        "部队": "军队与战斗", "野战": "军队与战斗",
        "议和": "外交", "停战": "外交", "和平": "外交",
        "结盟": "联盟与核心领土", "缔盟": "联盟与核心领土",
        "市政厅": ("建筑", "战争手册"),
        "买": "市场", "卖": "市场", "采购": "市场", "出售": "市场",
        "命令": "命令表", "工具": "命令表", "能做什么": "命令表",
        "情报": "外交", "间谍": "外交", "视野": "联盟与核心领土",
        # 旧手写版的「总览」节已退役，问"总览/总则/一局游戏"都指新书的第一章
        "总览": "一局游戏", "总则": "一局游戏", "一局游戏": "一局游戏",
    })
    # ★ 2026-10-06 修匹配口径：原先是「谁命中都要」——问「战争手册」会连带把
    #   「战争」→军队与战斗、「手册」→经济手册 一起吐出来；而只要有一个词没收录
    #   就直接兜底成**全书**。改成**最长命中优先**：先求出命中关键词的最大长度，
    #   只保留这个长度的命中（更长＝更具体，短词不再污染）。
    hits = [(len(k), v) for k, v in labels.items() if k in topic]
    picks: list[str] = []
    if hits:
        best = max(n for n, _ in hits)
        for n, v in hits:
            if n != best:
                continue
            for lb in ((v,) if isinstance(v, str) else v):
                if lb not in picks:
                    picks.append(lb)
    if picks:
        # picks 存的是**章节名片段**（如 "军队与战斗"），对真实标题做包含匹配——
        # 这样章节标题里的「一、二、…」序号与括注（「命令表（玩家能做的事）」）都不用管。
        hit = [(label, text) for label, text in secs
               if any(p in label for p in picks)]
        if hit:
            return "\n\n".join(f"【{label}】\n{text}" for label, text in hit)
    # ★ 认不出主题（或没给主题）⇒ **回目录，不倒全文**（用户 2026-10-06）。
    lines = ["【规则书目录】查哪一节就用 topic 点名，例如 rules(建筑) / rules(外交) / "
             "rules(战争手册)；也可以直接问某个词（兵营、宣战、补给、市政厅…）。"]
    if missing:
        lines.append("⚠ 《游戏说明书》暂时读不到（文件缺失）——以下仅列**讲义**，"
                     "其余规则请稍后再试。")
    for label, _ in secs:
        blurb = next((v for k, v in _CHAPTER_BLURB.items() if k in label), "")
        lines.append(f"· {label}" + (f" —— {blurb}" if blurb else ""))
    return "\n".join(lines)


def _fmt_threats(world, name) -> str:
    """视野内的**他国军队**。

    ★ 野人守军**一律不列**（2026-09-19 用户：「顺便野人也不应该列威胁，全过滤算了」）：
    它们从不主动攻击、HP 恒 100、每块无主地都有一只，而**坐标地图每格已经用「，野人」标了它们**。
    实测第 120 回合：原本 1668 字符 / 64 行里 **59 行（90%）是野人条目**，全砍掉省 ≈750 token/回合。
    想看某个守军的血量/位置：地图那一行（或 `query panel=tile x= y=`）。
    """
    rows = []
    for a in world.armies:
        if a["owner"] == name or a["owner"] == "野人":   # 野人不算威胁（见上）
            continue
        if not world.visible_to(name, a["x"], a["y"]):
            continue
        cl = world.visible_buildings(name, a["x"], a["y"]).get("城堡", 0)
        rows.append(f"{a['name']}({a['owner']}) {a['hp']}HP @({a['x']+1},{a['y']+1})"
                    + (f" 城L{cl}" if cl else ""))     # 城堡公开：报"敌军在某格"就一并报它脚下的城
    return (("视野内的他国军队:\n  " + "\n  ".join(rows)) if rows else
            "视野内没有他国军队（野人守军不列在此——它们从不主动进攻，见坐标地图里每格的「，野人」）")


def _battle_cells(world, name, cap: int):
    """**交战格的唯一取数门禁** —— `_fmt_battle` 与 `battle_brief` 都只走这里。

    一格入选当且仅当 **`可见 ∪ 我有活军在场`**：
      · 纯视野（别国互殴）：看得到就报；**看不到的连计数都不给**——计数本身就是
        泄漏"别处正在打"；
      · **我有军在场**：必须出现。`attack` 允许打视野外的格，而军是**站在那格上**的，
        自己不出现的话 AI 连"我正在盲打"都不知道（2026-10-07 的起因）。

    候选集走 `world.troops`（缓存过的**非野人**名单）而不是 `world.armies`：
    一局里野人常有 1500+ 支，而野人永不是交战方（`battle_sides` 的 `attacker` 不含野人）。

    返回 `(rows, extra_cells)`；`rows = [(x, y, sides, seen)]`，`seen` = 该格是否在我视野内
    （决定报不报敌方与减伤——盲战只报我方）；`extra_cells` = 入选但超出 `cap` 的坐标。
    """
    vis = _visible_cells(world, name)
    mine = {(a["x"], a["y"]) for a in world.troops
            if a["owner"] == name and a["hp"] > 0}
    cells = sorted({(a["x"], a["y"]) for a in world.troops if a.get("engaged")})
    rows, extra_cells = [], []
    for (x, y) in cells:
        if (x, y) not in vis and (x, y) not in mine:
            continue                      # 看不见、我也没在场 ⇒ 连存在都不提
        sides = world.battle_sides(x, y)
        if sides is None:
            continue
        if len(rows) >= cap:
            extra_cells.append((x, y))
            continue
        rows.append((x, y, sides, (x, y) in vis))
    return rows, extra_cells


def _side_line(world, F: str, units: list[dict], sides: dict) -> str:
    """一方的一行汇总：支数 · 兵种构成 · 合计 HP · 输出基数。"""
    kinds: dict[str, int] = {}
    for a in units:
        k = a.get("type") or "野人"          # 野人**没有 type 键**（与引擎 _spawn_guardian 一致）
        kinds[k] = kinds.get(k, 0) + 1
    comp = " ".join(f"{k}{v}" if k != "野人" else "野人" for k, v in sorted(kinds.items()))
    hp = sum(a["hp"] for a in units)
    return (f"{len(units)} 支 · {comp} · 合计 {hp}HP · 输出基数 {sides['atk_base'][F]}")


def _fmt_battle(world, name) -> str:
    """当前交战的**事实**面板：谁在攻、谁在守、双方投入多少、吃几档减伤。

    ★ **只给事实，不给判断**——不算胜率、不预判胜负（用户 2026-10-07：接战斗 DP
      「有点作弊了」；项目元规律「给事实不给判断」）。AI 要算自己算：燕 T121 的原话
      就是「按伤害模型再打两轮就是全歼」——它缺的是数据，不是求解器。

    ★ **盲战**：该格不在我视野内、但**我有军在场** ⇒ 该格出现（否则不知道自己在盲打），
      但**只报我方**，敌方与减伤一律「敌情不明」。**尤其不报合计减伤**：它含城堡分量，
      报出去等于让 AI 用算术把雾里的城防反推出来。
    """
    rows, extra_cells = _battle_cells(world, name, BATTLE_CELL_CAP)
    if not rows:
        return ("（当前没有交战——你没有军队在打，视野内也没有别国在打。\n"
                "  要开打：`query panel=army` 选军 → `attack` 目标格；开打后这里会列出各方的\n"
                "  兵种构成、逐支 HP、输出基数，以及**仅防御方**吃的地形/城堡/合计减伤。）")
    out = [f"【交战】{len(rows) + len(extra_cells)} 处"
           "（只列你视野内、或有你军队在场的格；视野外且与你无关的仗不在此列）", ""]
    order_hint = {"攻": 0, "守": 1, "旁观": 2}
    for (x, y, sides, seen) in rows:
        t = world.tiles.get((x, y))
        hall = world.visible_buildings(name, x, y).get("市政厅", 0)
        head = (f"⚔ ({x+1},{y+1}){world.ter_char(x, y)}"
                + (f" 城L{world.visible_buildings(name, x, y).get('城堡', 0)}"
                   if world.visible_buildings(name, x, y).get("城堡", 0) else "")
                + ("【市政厅·国祚】" if hall else ""))
        if not seen:
            head += " 【**盲战**·该格不在你视野内】"
        nm = (t or {}).get("name")
        head += ("｜**无主野地**" if sides["owner"] is None else f"｜{sides['owner']} 的领土")
        head += f"「{nm}」" if nm else ""
        out.append(head)
        fs = sorted(sides["forces"], key=lambda F: (order_hint[sides["role"][F]], F))
        # ★ 盲战（该格不在我视野内）**只列我方**——别人的番号、兵力、构成一概不给。
        #   这是本面板唯一的泄漏面：逐方循环天生会把格上所有人都打出来。
        shown = fs if seen else [F for F in fs if F == name]
        for F in shown[:BATTLE_PARTY_CAP]:
            role = sides["role"][F]
            tag = {"攻": "攻方", "守": "守方", "旁观": "在场未参战"}[role]
            if F == name:
                tag += "（你）"
            if F == "野人":
                tag += "（野人）"
            elif role == "攻" and sides["soak_elig"].get(F):
                tag += "（兼格主，仍吃本格减伤）"
            out.append(f"   {tag} {F}：{_side_line(world, F, sides['forces'][F], sides)}")
            for a in sides["forces"][F][:BATTLE_UNIT_CAP]:
                extra_u = ""
                # ⚑ 撤退细节（目标格 + 减伤档）**只给自己的军**：`retreat_to` 是引擎内部
                # 状态（落地时才公开），报了等于把对方下回合的落点提前告诉 AI。
                if F == name and a.get("retreat_to"):
                    tx, ty = a["retreat_to"]
                    extra_u = (f" ⚑撤退中→({tx+1},{ty+1})｜"
                               f"{retreat_note(a.get('retreat_role') != '攻', a.get('retreat_cover', 100))}")
                out.append(f"     · {a['name']}(#{a['id']}) {a['hp']}HP{extra_u}")
            if len(sides["forces"][F]) > BATTLE_UNIT_CAP:
                out.append(f"     …另 {len(sides['forces'][F]) - BATTLE_UNIT_CAP} 支")
        if len(shown) > BATTLE_PARTY_CAP:
            out.append(f"   …另 {len(shown) - BATTLE_PARTY_CAP} 方")
        if seen:
            terrain = t["terrain"] if t else world.tile_terrain(x, y)
            per_lv = building_effect("城堡", "defense_per_level") or 1
            out.append("   减伤（**仅防御方**吃，进攻方恒 0）：")
            for F in fs:
                if sides["role"][F] == "攻":
                    continue
                td, cd, total = sides["soak_parts"][F]
                # 城堡档位从 soak_parts 反推（cd = 级数×每级），不再去读地块——
                # 地块是**惰性物化**的，未物化的格 `tiles.get()` 是 None
                castle = (f"城堡 L{cd // per_lv} +{cd}%" if cd else "城堡 —")
                note = "（格主）" if F == sides["owner"] else "（非格主，无城堡加成）"
                if td < 0:
                    note += " ★负地形：反而多挨打"
                out.append(f"     {F}{note}：地形 {terrain} {td:+d}% × {castle}"
                           f" ⇒ 合计减伤 {total}%")
        else:
            out.append("   敌方：**敌情不明**（该格不在你视野内——守军构成、地形与城堡减伤"
                       "均不可知，结算照常进行）")
        # 缺粮：**只报己方**——补给是内政底细（与国库/产出同级，引擎只让 spy/买报表看）。
        # 断粮**交战中也照扣**，而 AI 现在只在内政日志里看得到一句「缺 N」，看不到代价。
        if name in sides["forces"]:
            need, short, per = world.supply_shortfall(name)
            if short:
                out.append(f"   ⚠ 你补给断粮：仓不够，缺 {short} ⇒ **全军每军 −{per}HP/回合**"
                           "（交战中也照扣）")
        out.append("   · 本格在交战 ⇒ 双方**本回合都不回血**（回血的前提是「不在交战格」）")
        out.append("")
    out.append("   · 本引擎无「阵营」：攻/守按每方自己的 engaged 与宣战关系**逐方**判定，"
               "同盟方各打各的。")
    out.append("   · 输出基数是 Σ兵种攻击的基数，**不含骰子**——实际结果看回合末结算。")
    if extra_cells:
        rest = "、".join(f"({x+1},{y+1})" for (x, y) in extra_cells)
        out.append(f"   …另 {len(extra_cells)} 处：{rest}（用 `query panel=tile x= … y= …` 单看）")
    return "\n".join(out)


def battle_brief(world, name) -> str:
    """一行交战摘要（每次行动后自动挂）—— 只答"有没有仗、这场仗读起来是不是在赢"。

    ★ 与 `_fmt_battle` **共用同一个门禁**：不可见的别国互殴既不进列表也不计数。
    ★ 尺寸有界：最多 `BATTLE_BRIEF_CAP` 格，其余只给个数（明细让 AI 自己 `query`）。
    """
    rows, extra_cells = _battle_cells(world, name, BATTLE_BRIEF_CAP)
    if not rows:
        return ""
    parts = []
    for (x, y, sides, seen) in rows:
        mine = name if name in sides["forces"] else None
        if mine is None:
            parts.append(f"别国交战({x+1},{y+1})" if seen else "")
            continue
        role = sides["role"][mine]
        hp_me = sum(a["hp"] for a in sides["forces"][mine])
        seg = f"你{'攻' if role == '攻' else '守'}({x+1},{y+1}) " \
              f"你{len(sides['forces'][mine])}支{hp_me}HP"
        if not seen:
            parts.append(seg + "/敌情不明【盲战】")
            continue
        foes = [F for F in sides["forces"] if F != mine and sides["role"][F] != "旁观"]
        if foes:
            F = foes[0]
            seg += f"/{'守' if role == '攻' else '攻'}{len(sides['forces'][F])}支" \
                   f"{sum(a['hp'] for a in sides['forces'][F])}HP"
            if role == "攻" and sides["soak_elig"].get(F):
                seg += f"·守方减伤{sides['soak_pct'][F]}%"
            elif role == "守" and sides["soak_elig"].get(mine):
                seg += f"·你方减伤{sides['soak_pct'][mine]}%"
        parts.append(seg)
    parts = [p for p in parts if p]
    if not parts:
        return ""
    n = len(rows) + len(extra_cells)
    tail = f"＋另{len(extra_cells)}处" if extra_cells else ""
    return f"⚔交战{n}处：" + "；".join(parts[:BATTLE_BRIEF_CAP]) + tail + "｜明细 query panel=battle"


def _fmt_hall_alert(world, name) -> str | None:
    """**国祚警报**：我有厅的格上没人守，**且看得见的敌军本回合就够得着** ⇒ 假设已破。

    来由（用户 2026-10-07，实盘秦魏之战）：秦把国祚连同**全部战争工业**（补给厂×7 /
    装备厂×3 / 兵营 / 能源厂 / 市政厅）堆在晴桥一格，又为一场"闪击战"把兵全压到前线
    ——自家南翼一兵未留。魏一支步军 `atk` 进空格：**敌人为 0、零战斗直接进驻**，
    秦当场丢了工业首都；那两格能源厂一掉**电网停摆**，其余全部停产。

    ★★ 触发条件**不是**"哪座厅上没有兵"（第一版就是那么写的，被用户当场驳回两次）：

      · 「**不是不堆到一格，不集中意味着你根本无法防守**」——集中是对的（手册 §四
        「要守的点少而厚」），错的是集中了却空着；
      · 「**为什么要把根本不可能被进攻的地守一堆军队，等于学燕，被运动战早晚打烂**」
        ——要求"每座厅常驻"就是把兵钉死在家里挨运动战。

    口径落成用户那句话：**你的安全假设必须成立**。所以这条警报的语义是
    「**你这个假设，此刻已经不成立了**」——那里没人 **且** 有人**本回合就能走进去**。
    都够不着 ⇒ 安全假设成立 ⇒ 返回 None，整段不出现（不白占 token）。

    够得着用引擎自己的 `World._reachable(for_attack=True)` 算（含交战判定与逐格移动代价），
    不另抄一份移动规则；只在**看得见**的敌军里找（迷雾就是迷雾，看不见的算不出来）。

    ★ 在建（`pending`）的厅还不是国祚 ⇒ 不算；口径与 `nation_building_count` 一致。
    """
    naked: set[tuple[int, int]] = set()
    for (x, y), t in world.tiles.items():
        if t["owner"] != name:
            continue
        if (t.get("buildings") or {}).get("市政厅", 0) <= 0:
            continue
        naked.add((x, y))
    if not naked:
        return None
    naked -= {(a["x"], a["y"]) for a in world.armies if a["owner"] == name}
    if not naked:
        return None

    # 看得见、且与我交战的敌军里，谁本回合就能踩到这些空格上（野人不主动进攻，不列）
    threats: list[tuple[tuple[int, int], dict]] = []
    for a in world.troops:
        enemy = a["owner"]
        if enemy == name or not world.war_between(name, enemy):
            continue
        if not world.visible_to(name, a["x"], a["y"]):
            continue
        for cell in sorted(naked & set(world._reachable(enemy, a, for_attack=True))):
            threats.append((cell, a))
    if not threats:
        return None

    def _where(x: int, y: int) -> str:
        t = world.tiles.get((x, y)) or {}
        return f"「{t.get('name') or '?'}」({x + 1},{y + 1})"

    cells: dict[tuple[int, int], list[str]] = {}
    for cell, a in threats:
        cells.setdefault(cell, []).append(a["name"])
    rows = [f"  {_where(x, y)} ← {'、'.join(who[:3])}" for (x, y), who in sorted(cells.items())[:4]]
    more = f"\n  （另 {len(cells) - 4} 处）" if len(cells) > 4 else ""
    return (
        f"⚠ 【国祚警报】**你的后方安全假设已经不成立**：{len(cells)} 座没有驻军的市政厅，"
        "敌军**本回合就够得着**。\n" + "\n".join(rows) + more + "\n"
        "  那里没人守，它一步 `atk` 进去就是**零战斗直接进驻**——丢的不只是那一格。\n"
        "  ★★ **被换家意味着战略完全失败**：前线赢得再多，家里被走进来一次，国祚和"
        "战争工业一起归零。**要么回兵，要么承认这格是丢的——别让它在敌人够得着时空着。**"
    )


def _fmt_alerts(world, name) -> str | None:
    """**领土警报**——钉在状态面板**最前**（用户 2026-09-21：「领土变更，被侵略是大事，得强调」）。

    原先这些只落在面板**最末尾**的【近讯】里，前面压着几百行地图与十来个面板 ⇒ 一屏滚过去
    就没了紧迫感；而"我刚丢了地/敌人正踩在我地上"是**要立刻改国策**的事。

    只报**自上次行动以来**（上一轮结算 + 本回合，`turn >= world.turn - 1`）真正发生的：
      ✖ **失地**：被夺走的自己的地（♥ 标核心——那种"同战线盟友夺回会自动归还"，口径不同；
        带市政厅的格另标「市政厅」——2026-09-21 起它是**国祚**，丢光了当场亡国）；
      ＋ **得地**：自己夺来/拓来的地（进项也一并报，免得只报坏消息）；
    两样都没有 ⇒ 返回 None，**整段不出现**（不白占 token）。

    ★ 不列"敌军站在我地上"（用户 2026-09-21：「**不存在踩上去没丢地，脱裤子放屁**」）：
    进我的地只能靠 `atk`，那一脚就已经进交战了——回合末结算要么这格易主（上面 ✖ 失地
    那行会报）、要么它被打退；而"视野内他国军队"本来就在【威胁】面板里（含对角/盟国
    共享视野/瞭望塔半径）。单列一行纯属重复。
    ★ 也不按"国境/境内"说话：本引擎没有这种多边形模型，领土是**一格一格的地块**，
    看见口径只有 `visible_to`（该格本身或含对角的相邻格里有自家/盟国地）。

    ★ 全部按**结构化字段**查（`phase/nation/lost_by/x/y`），**不解析文本**：
    措辞是给人读的，解析它等于把面板钉死在文案上。
    """
    since = world.turn - 1
    lost: list[str] = []
    gain: list[str] = []
    for h in world.history:
        if h.get("phase") != "领土" or int(h.get("turn", 0)) < since:
            continue
        x, y = h.get("x"), h.get("y")
        if x is None or y is None:
            continue
        t = world.tiles.get((x, y)) or {}
        where = f"「{t.get('name') or '?'}」({x + 1},{y + 1})"
        if h.get("lost_by") == name:
            # ★ 地块上的市政厅也标出来（2026-09-21 起它是**国祚**：丢光了就亡国）。
            #   建筑随城易主、原地留存 ⇒ 直接看这格现在的建筑就知道丢的是什么。
            tags = "，".join(x for x in ("♥核心" if t.get("core") == name else "",
                                         "市政厅" if (t.get("buildings") or {}).get("市政厅") else "")
                             if x)
            lost.append(where + f" 被 {h.get('nation')} 夺去" + (f"（{tags}）" if tags else ""))
        elif h.get("nation") == name:
            gain.append(where + (f" 夺自 {h['lost_by']}" if h.get("lost_by") else " 拓疆"))
    rows: list[str] = []
    if lost:
        rows.append(f"  ✖ **失地 {len(lost)} 块**：" + "；".join(lost))
    if gain:
        rows.append(f"  ＋ 得地 {len(gain)} 块：" + "；".join(gain))
    if not rows:
        return None
    return "⚠ 【领土警报】\n" + "\n".join(rows)


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
        return f"无（可用 share_map 与别国互发地图：对方下回合到；或 spy 间谍偷地图：{SPY_TURNS} 回合后到）"
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
    """收到的间谍情报摘要（完整见 query panel=spy）。"""
    es = world.econ_intel.get(name, [])
    # 开行才有 buy_report 这条替代路线——关行时工具压根不存在，提示里也别提它（不占 token）
    alt = (f"；或 buy_report 花 {BUY_REPORT_COST} 金买它**已公布**的经济报表（明细，当场到手）"
           if world.bank_on() else "")
    if not es:
        return (f"无（可用 spy 花{SPY_COST}金刺探别国，{SPY_TURNS}回合后到手经济底细"
                f"+外交关系+粗略军情数量+地图{alt}）")
    last = es[-1]
    return (f"{len(es)} 份，最新 {last['from']}（第{last['turn']}回合）；"
            f"完整见 query panel=spy{alt}")


def _fmt_spy(world, name) -> str:
    """完整间谍情报：最近拿到的别国经济底细 + 外交关系 + 粗略军情（各兵种数量）。"""
    es = world.econ_intel.get(name, [])
    if not es:
        return "（你尚未拿到任何间谍情报）"
    return "\n".join(m["text"] for m in es[-2:])


MIL_WARNING = ("⚠ 军费是双刃剑：开支过大会挤压投资，长期竞争落后；开支过低则成待宰羔羊，"
               "发展空间受限、短期竞争失利——自己权衡。")


def _pct(v: float | None, sign: bool = True) -> str:
    """增长率/占比显示：None（无上期）→「—」。"""
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%" if sign else f"{v * 100:.1f}%"


def _fmt_report_one(rep: dict) -> str:
    """单期经济报表。口径：GDP 与军费都是**结报那一回合**的实际值（run-rate，不平均、
    不攒期累计）；投资/资产/贸易仍是整期值。覆盖回合数只在表头交代时间跨度。"""
    days = rep.get("span", REPORT_EVERY)
    start = rep.get("period_start", rep["period_end"] - days + 1)
    span = f"第 {start}–{rep['period_end']} 回合" + ("" if days == REPORT_EVERY else f"（{days} 回合）")
    gdp, mil = rep["gdp"], rep["military"]
    fiscal = gdp - mil
    L = [f"【经济报表 · 报表回合 {rep['report_turn']} · 覆盖{span}】"]
    L.append(f"  GDP（本回合，市价）      {gdp:>8.1f} 金   {_pct(rep['gdp_growth'])}")
    L.append(f"  财政收入（GDP−军费）     {fiscal:>8.1f} 金"
             + ("   ⚠ 本回合军费已超过 GDP，靠卖库存/吃老本维持" if fiscal < 0 else ""))
    L.append(f"  军费（本回合补给消耗）   {mil:>8.1f} 金   "
             f"占 GDP {_pct(rep['military_ratio'], sign=False)}")
    L.append(f"  国家总资产               {rep['assets']:>8.0f} 金   {_pct(rep['assets_growth'])}"
             "   （含夺地所得）")
    L.append(f"  本期投资                 {rep['invest']:>8.0f} 金   {_pct(rep['invest_growth'])}")
    L.append(f"  外贸 / 内循环            外贸 {_pct(rep['trade_ratio'], sign=False)} · "
             f"内循环 {_pct(1 - rep['trade_ratio'], sign=False)}"
             "   （外贸=买卖总额/(自产+进口)）")
    L.append(f"  （本回合军队吃掉补给 {rep.get('supply_eaten_turn', 0)} 单位、折 {mil:.1f} 金；"
             f"整期累计吃掉 {rep['supply_eaten']} 单位；市场买入 "
             f"{rep['import_gold']:.0f} 金、卖出 {rep['export_gold']:.0f} 金）")
    L.append("")
    L.append(MIL_WARNING)
    return "\n".join(L)


def _fmt_report_trend(world, name) -> str:
    """跨期趋势表：一行一期，便于比较不同时期。"""
    reps = world.econ_reports.get(name, [])
    header = ["期", "报表回合", "GDP/回合", "GDP增长", "军费/回合", "军费占GDP",
              "投资(期)", "投资增长", "总资产", "资产增长", "外贸占比"]
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
        return (f"（尚无经济报表：第 {REPORT_EVERY+1} 回合起每 {REPORT_EVERY} 回合自动出一期——第 {REPORT_EVERY+1}/{2*REPORT_EVERY+1}/{3*REPORT_EVERY+1}… 回合开局可查。"
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
            + f"（每 {REPORT_EVERY} 回合自动出一期，不能手动运行；趋势看 report all=true）")


def _report_of(world, to: str, turn: int | None = None):
    """取**任意国家**某期经济报表的快照：返回 dict，取不到则返回一句错误文案（str）。

    给 `buy_report`（向央行买别国报表）用——与本国 `report` 工具读的是同一份快照，
    所以买家看到的明细与卖主自己看到的一模一样。不传 turn = 最新一期。
    """
    if to not in world.nations:
        return f"没有这个国家：{to or '（空）'}（现存：" + "、".join(world.nations) + "）"
    reps = world.econ_reports.get(to) or []
    if not reps:
        return (f"{to} 还没有任何经济报表：每 {REPORT_EVERY} 回合自动结一期，"
                f"第 {REPORT_EVERY+1} 回合起才有（现第 {world.turn} 回合）")
    if turn is None:
        return reps[-1]
    for r in reps:
        if r["report_turn"] == turn:
            return r
    return (f"{to} 没有第 {turn} 回合的报表。它已出的期："
            + "、".join(f"第{r['report_turn']}回合" for r in reps))


def _fmt_report_panel(world, name) -> str:
    """状态面板里的经济报表：**只在出表那一回合（第 11/21/31…）显示全文**，
    其余回合只给一行摘要——省上下文，需要时用 report 取全文/历史/趋势。"""
    reps = world.econ_reports.get(name, [])
    if not reps:
        return f"尚无（第 {REPORT_EVERY+1} 回合起每 {REPORT_EVERY} 回合自动出一期，出表那回合自动进本面板，不能手动运行）"
    r = reps[-1]
    if world.turn == r["report_turn"]:                   # 本期刚出 → 全文
        text = _fmt_report_one(r)
        if len(reps) > 1:
            text += f"\n（共 {len(reps)} 期；历史期 report turn=N，跨期趋势 report all=true）"
        return text
    return (f"最新第 {r['report_turn']} 回合（GDP {r['gdp']:.1f}/回合"
            f"（{_pct(r['gdp_growth'])}）、军费占 GDP {_pct(r['military_ratio'], sign=False)}、"
            f"总资产 {r['assets']:.0f}）——全文 report、跨期趋势 report all=true")


def _econ_building(world, building: str) -> str:
    """单个建筑的经济核算文案。**数字全部来自 mp.build_econ**——与规则 AI v9 同一份
    计算，LLM 看到的回本和 bot 排序用的回本不会有第二套口径。
    （文案与旧版逐字一致；仅加工厂「回本」从"毛利不含电"改为按含电的 per_turn 计，
      与 build_econ/`payback` 对齐——旧版这里本来自相矛盾：毛利行标了电耗、回本行不含。）"""
    info = BUILDINGS[building]
    e = build_econ(world, building)
    cost, capex, k = e["cost"], e["capex"], info["kind"]
    if k == "castle":
        return (f"{building}: L1造价 {cost}金+{info['wood']}木(折{capex:.0f}金) · "
                f"每级+{building_effect('城堡', 'defense_per_level')}%防御，不产金")
    if k == "militia_camp":
        return (f"{building}: 造价折{capex:.0f}金 · **不产粮**（纯民兵编制，无产出）；"
                f"可征民兵（{_cost_text(UNIT_TYPES['民']['recruit'])}/支，每座{info['effects'].get('militia_cap', 1)}支/回合，"
                "全国民兵总数≤全国军屯数），民兵驻本格不耗补给"
                f"（需本地{info['cap_resource']}≥1、每地块限{info.get('limit', 1)}座）")
    if k in ("extract", "gold"):
        net = e["detail"]["net"]
        if k == "gold":
            tag = "固定+金"
        else:
            tag = "外销(卖价)"
        pb = f"{e['payback']:.0f}回合" if e["payback"] else "—"
        return f"{building}: 造价折{capex:.0f}金 · 每回合产出{tag}≈{net:.0f}金 · 回本≈{pb}"
    if k == "energy":
        fuel = e["detail"]["fuel"]
        return (f"{building}: 造价折{capex:.0f}金 · 每回合烧燃料现值≈{fuel:.0f}金 "
                f"→ 产{info['energy_out']}电（电不交易，供高级建筑维持）")
    if k == "factory":
        d = e["detail"]
        inv, out_self, out_sell, ec = (d["inputs_value"], d["outputs_buy"],
                                       d["outputs_sell"], d["energy_cost"])
        net = d["net"]
        pb = f"{e['payback']:.0f}回合" if e["payback"] else "—"
        return (f"{building}: 造价折{capex:.0f}金 · 每回合投{inv:.0f}金(买价)料→产{out_self:.0f}金"
                f"(买价=自用替代；纯外销只值{out_sell:.0f}金)"
                f"（毛利{net:+.0f}金；另耗{info.get('energy', 0)}电≈{ec:.0f}金） · 回本≈{pb}")
    if k == "barracks":
        return (f"{building}: 造价折{capex:.0f}金 · 不自动产金，每兵营每回合可征1军"
                f"（步{_cost_text(UNIT_TYPES['步']['recruit'])} / 骑{_cost_text(UNIT_TYPES['骑']['recruit'])}，耗兵料另计）")
    if k == "townhall":
        return (f"{building}: 造价折{capex:.0f}金 · 每回合 = "
                f"{building_effect('市政厅', 'gold_base')}金基础"
                f" + 该地块每座建筑×{building_effect('市政厅', 'gold_per_slot')}金（不含自身；{MAX_SLOTS}格满城≈"
                f"{building_effect('市政厅', 'gold_base') + MAX_SLOTS * building_effect('市政厅', 'gold_per_slot')}金/回合）"
                f" · 需本地已用位≥{info['min_slots']}、每地块限{info.get('limit', 1)}座、耗{info.get('energy', 0)}电")
    if k == "tower":
        return (f"{building}: 造价折{capex:.0f}金 · 不产金：事件视野 "
                f"+{building_effect('瞭望塔', 'vision_radius')} 圆（情报投入）")
    if k == "diplomat":
        return (f"{building}: 造价折{capex:.0f}金 · 不产金：外交费每座再减半（{'→'.join(_diplo_chain())}）；"
                "写信起步价吃减免、超字费不吃；"
                f"自建全国限{info['limit_nation']}座，第2座只能抢")
    if k == "academy":
        return (f"{building}: 造价折{capex:.0f}金 · 不产金：本地块一切建造金价 "
                f"-{building_effect('工程院', 'build_discount')}%"
                f"（需本地已用位≥{info['min_slots']}，后续建筑越贵回得越多）")
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
    """本回合的新鲜状态（上下文尾部）。replay_since 见 turn_state。

    ★ **【威胁】排最上 + 【领土警报】紧随**（用户 2026-09-21：「威胁排最上」；丢地/被侵略
    是"立刻改国策"的事）：原先【威胁】压在第 5 段（国力/国策/地图/军队之后）、丢地在最末尾的
    近讯里，一屏滚过去就没了紧迫感。警报没事时整段不出现。
    """
    others = "、".join(n for n in world.alive() if n != name) or "（只剩你）"
    alerts = _fmt_alerts(world, name)
    hall_alert = _fmt_hall_alert(world, name)      # 空着的国祚——常驻，直到你回兵
    return "\n".join([
        f"你（{name}）现在进行第 {world.turn} 回合的行动。其余国家：{others}。",
        f"【威胁】\n{_fmt_threats(world, name)}",       # 视野内他国军队——最要紧的放最前
        *([hall_alert] if hall_alert else []),         # 国祚有空档——"你此刻有个洞"
        *([alerts] if alerts else []),
        f"【国力】\n{_res_line(world, name)}",
        f"【国策规划】\n{_fmt_plan(world, name)}",
        f"【国土/视野】\n{_fmt_atlas(world, name)}",
        f"【军队】\n{_fmt_armies(world, name, brief=True)}",   # 常驻只给列表（图按需查）
        f"【市场】\n{_fmt_market(world, name)}",
        *([f"【央行】\n{_fmt_bank(world, name)}"] if world.bank_on() else []),
        f"【经济报表】\n{_fmt_report_panel(world, name)}",
        f"【纪事(近10回合)】\n{_fmt_memory(world, name, replay_since)}",
        f"【地图情报】\n{_fmt_intel_hint(world, name)}",
        f"【间谍情报】\n{_fmt_spy_hint(world, name)}",
        f"【信箱】\n{_fmt_mail(world, name, brief=True)}",
        f"【外交】\n{_fmt_diplomacy(world, name)}",
        f"【近讯】\n{_fmt_news(world, name)}",
    ])


def compact_state(world, name) -> str:
    r = world.nations[name].res
    n_army = len([a for a in world.armies if a["owner"] == name])
    box = world.mailbox.get(name, [])
    b = battle_brief(world, name)      # 有交战才挂；没有则整段不出现（不白占 token）
    return (
        f"【刷新】你{name} 国库{r['黄金']} 粮{r['粮食']} 木{r['木头']} 矿{r['矿石']} "
        f"油{r['石油']} 装{r['装备']} 补给仓{r['补给']} | 军队{n_army}"
        + (f" | {b}" if b else "") + " | "
        f"收信{len(box)} | "
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


def _diplo_cost(world, actor: str, to: str | None = None, *, incoming: bool = False) -> int:
    """外交基础费：盟内免费；向他国提议 结盟/联盟/议和 且对方有外交中心 → 免费（incoming）；
    否则每座本国（含抢来的）外交中心使费用再减半（10→5→2→1，下限 1 金）。"""
    if to and world.allied_between(actor, to):
        return 0
    if incoming and to and world.nation_building_count(to, "外交中心") > 0:
        return 0
    c = DIPLO_COST
    for _ in range(world.nation_building_count(actor, "外交中心")):
        c = max(DIPLO_CENTER_MIN_COST, c // 2)
    return c



# ---------------------------------------------------------------------------
# 记忆检索（翻旧账：只搜本国历史记忆的正文，不含思考；免费只读）
# ---------------------------------------------------------------------------
_MEM_CUT = 160      # 命中正文预览长度
_MEM_NEIGH = 60     # 相邻上下文单侧预览长度


def _mem_cut(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n] + "…"


def _memory_search(world, name: str, args: dict) -> str:
    """按关键词搜本国历史记忆的正文，返回命中回合号 + 相邻上下文。

    只搜 `turn_memory`/`summaries`/`summary_blocks`/`long_memory`/`plans` 的
    **content**（assistant 的 reasoning_content 刻意不索引——那是思考不是事实）；
    只搜得到该国自己的记忆，符合迷雾纪律。免费、只读。
    """
    query = str(args.get("query", "") or "").strip()
    try:
        limit = max(1, min(8, int(args.get("limit") or 3)))
    except (TypeError, ValueError):
        limit = 3
    if not query:
        return "用法：memory_search(query=关键词 用空格分隔多个词, limit=条数)，例：memory_search(query=盟约 楚)"
    _sep = {",", "，", "、", ";", "；", "	"}
    kws = [k for k in "".join(c if c not in _sep else " " for c in query).split() if k]
    if not kws:
        return f"记忆检索「{query}」：空关键词。"

    # 收集候选单元：(回合标签, 正文, 前文, 后文)
    units: list[tuple[str, str, str, str]] = []
    for rec in (world.turn_memory.get(name) or []):
        msgs = rec.get("messages") or []
        for i, m in enumerate(msgs):
            # ★记录首条是本回合的状态面板原文（为了前缀缓存逐字节存进来的，见
            #   `_store_turn_memory`）。它只当"相邻上下文"用，不做检索单元——否则
            #   每回合几千字的状态会淹没正文命中（检索的语义是"我当时做了什么"）。
            is_state = i == 0 and str(m.get("content") or "").startswith(TURN_STATE_HEAD)
            parts = []
            if m.get("role") == "tool":
                parts.append(str(m.get("content") or ""))
            elif not is_state:
                if m.get("content"):
                    parts.append(str(m["content"]))
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    parts.append(f"{fn.get('name')}({fn.get('arguments')})")
            text = " ".join(p for p in parts if p)   # 不含 reasoning_content
            if not text:
                continue
            prev = _mem_cut(msgs[i - 1].get("content"), _MEM_NEIGH) if i > 0 else ""
            nxt = _mem_cut(msgs[i + 1].get("content"), _MEM_NEIGH) if i + 1 < len(msgs) else ""
            units.append((str(rec.get("turn", "?")), text, prev, nxt))
    for it in (world.summaries.get(name) or []):
        units.append((str(it["turn"]), "小结：" + str(it.get("text") or ""), "", ""))
    for b in (world.summary_blocks.get(name) or []):
        units.append((f"{b['from']}-{b['to']}", "阶段总结：" + str(b.get("text") or ""), "", ""))
    lm = (world.long_memory.get(name) or "").strip()
    if lm:
        units.append(("长期记忆", lm, "", ""))
    pl = (world.plans.get(name) or {}).get("text", "")
    if pl:
        units.append((str((world.plans.get(name) or {}).get("turn", "") or 0),
                      "国策：" + str(pl), "", ""))

    scored = [(sum(1 for k in kws if k in u[1]), u) for u in units
              if any(k in u[1] for k in kws)]
    if not scored:
        return f"记忆检索「{query}」：没有命中（正文检索，不含思考过程）。"
    # 排序：分数降序 → 回合新→旧；非数字回合（长期记忆/阶段）放最后
    def _tkey(x):
        try:
            return -int(x[1][0])
        except (TypeError, ValueError):
            return 1
    scored.sort(key=lambda x: (-x[0], _tkey(x)))
    L = [f"📇 记忆检索「{query}」命中 {len(scored)} 处（正文，不含思考），列出前 {limit}："]
    for i, (_, u) in enumerate(scored[:limit], 1):
        tag, text, prev, nxt = u
        head = f"第{tag}回合" if tag.isdigit() else f"【{tag}】"
        L.append(f"{i}. {head} ▸ {_mem_cut(text, _MEM_CUT)}")
        if prev or nxt:
            L.append(f"    └ 相邻：{prev}{' … ' if prev and nxt else ''}{nxt}")
    return "\n".join(L)


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
        if which == "land":
            def _num(v, d):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return d
            return _fmt_land(world, actor, cap=_num(args.get("cap"), LAND_CAP),
                             offset=_num(args.get("offset"), 0),
                             filter_=str(args.get("filter", "") or ""))
        if which in ("grid", "gridmap", "网格", "格子图"):
            # 网格版地图**按需**取（默认给的是坐标地图）：地形/国土图 + 军事图
            return (_fmt_map(world, actor) + "\n\n" + _fmt_mil_map(world, actor)
                    + "\n\n（这是网格版；默认面板给的是**坐标地图**——每行一格、自带语义、"
                      "不用解码格子。两者信息等价，网格版适合「想一眼看出形状」的时候。）")
        if which == "tile":
            # 坐标可以给 x= y=，也可以给 at=地名（复用 _tile_xy，支持 "5 6"/地名/"名字 (x,y)"）
            ref = str(args.get("at", "") or "").strip()
            if not ref:
                ref = f"{args.get('x', '')} {args.get('y', '')}"
            xy = _tile_xy(world, actor, ref)
            if xy is None:
                return ("用法：query panel=tile x= 5 y= 6（或 at=地名，如 at=沃港）。"
                        "只查得到你视野内的格。")
            return _fmt_tile(world, actor, xy)
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
            "battle": _fmt_battle(world, actor),
            "econ": _fmt_econ(world, actor),
            "intel": _fmt_intel(world, actor),
            "spy": _fmt_spy(world, actor),
            "plan": _fmt_plan(world, actor),
        }.get(which, full_state(world, actor))

    # ---- 规则查询：**所有政体同一份**（doc 目录《游戏说明书》+ 三本讲义）
    #   政体专属的机制写在各自的 system prompt 里（如匈奴的【教义】），rules 不重复。
    if tool in ("rules", "规则", "help", "帮助"):
        return rules_text(world, str(args.get("topic", "") or ""))

    # ---- 记忆检索（翻旧账：只搜本体正文，不含思考；免费只读）
    if tool in ("memory_search", "search_memory", "检索记忆", "记忆检索", "回想", "回忆", "history", "翻旧账"):
        return _memory_search(world, actor, args)

    # ---- 外交对象（先选一个非自己的国家）
    if tool in ("countries", "外交对象", "国家列表", "对手"):
        return _fmt_countries(world, actor)

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

    # ---- 领土（无"凭空占"：只有军队 mv 移入"敌人=0"的地格才占地）
    if tool in ("expand", "拓荒", "activate"):
        return ("没有单独占地命令：占地一律走 atk——派军队 attack 目标格，若那格没有守军/敌军（敌人=0）军队直接进驻占领；"
                "有野人/敌军则打赢后自动占地；野地上只有中立/盟友和平驻守时也直接进驻占领（它们不参战、回合末被遣返）。"
                "mv 只挪位置、不占地；野地有敌军驻守（含正在打野的）时不能 mv，只能 atk。"
                "野地上有与你非敌非盟的一方正在打野时不能 atk 插足；敌人/盟友在打野则可以参战。"
                "占地按索取顺序：第一个 atk 者优先，它阵亡则顺位最早入场的同盟者。")

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
    # ★ 元组字面量不能改成集合/变量：`docs/sync_manual.py` 的别名是**正则**从这行现算的
    #   （`if tool in \(([^)]*)\):`），写成别的形状命令表里的别名就凭空消失。
    if tool in ("disband", "遣散", "裁军", "复员", "解散军队"):
        aids = args.get("army_ids", args.get("army_id"))
        if isinstance(aids, int):
            aids = [aids]
        aids = [int(a) for a in aids] if isinstance(aids, list) else []   # 归一化同 attack
        ok, msg = world.disband(actor, aids)
        return msg

    # ---- 市场
    # ---- 世界央行：借款（开行才可用）
    #   ★「贷款算外交，要 10 块钱」（用户 2026-09-19）⇒ 走 _charge + _diplo_cost：
    #     先验国库、**成功才扣**、有外交中心照样减半。
    #   ★「匈奴也可以借」⇒ **不**放进 HUNS_BLOCKED（它不是国与国的外交，是第三方的钱）。
    if tool in ("loan", "贷款", "借款"):
        # ★ 额度与期限**都不能自选**（用户 2026-09-22：「改成不能选贷款额和时间」）：
        #   只有一种贷款——当时 GDP×BANK_LOAN_GDP_MULT、固定 BANK_LOAN_TURNS 回合。
        #   参数一律忽略，但**不静默**：模型要是还塞了 amount/turns（旧提示词或记忆残留），
        #   回执里点明"已按固定额度/期限放款"，免得它以为自己的数字生效了。
        msg = _charge(world, actor, _diplo_cost(world, actor), world.bank_loan, actor)
        given = [k for k in ("amount", "qty", "turns", "期", "额") if args.get(k)]
        if given:
            msg += (f"（你给的 {'、'.join(given)} 已被忽略：贷款额与期限都不能自选，"
                    f"一律按 当时 GDP×{BANK_LOAN_GDP_MULT} / {BANK_LOAN_TURNS} 回合放款）")
        return msg

    # ---- 世界央行：买别国**已公布**的经济报表（BUY_REPORT_COST 金/次，开行才有）
    #   卖的是明细不是秘密（每期报表生成时全世界都收到过一行公告），故不进 HUNS_BLOCKED。
    if tool in ("buy_report", "买报表", "购买报表", "央行报表"):
        to = str(args.get("to", "")).strip()
        turn = args.get("turn", args.get("期", None))
        if turn not in (None, "", 0, "0"):
            try:
                turn = int(turn)
            except (TypeError, ValueError):
                return "报表回合（turn）要写数字，如 turn=21；省略=最新一期"
        else:
            turn = None
        rep = _report_of(world, to, turn)
        if isinstance(rep, str):
            return rep                      # 没这个国家 / 没那期报表：**不收钱**
        return world.bank_sell_report(actor, to, rep["report_turn"],
                                      _fmt_report_one(rep))[1]

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

    # ---- 信箱（起步价联盟 10 / 非联盟 20，吃外交中心减免；超字费不吃减免）
    if tool in ("send_letter", "写信", "letter"):
        to = str(args.get("to", ""))
        text = str(args.get("content", ""))
        allied = world.allied_between(actor, to)
        lc = letter_cost(text, allied=allied,
                         diplo_centers=world.nation_building_count(actor, "外交中心"))
        if world.res(actor, "黄金") < lc:
            base = LETTER_COST_ALLY if allied else LETTER_COST
            over = max(0, len(text) - LETTER_FREE_CHARS)
            return (f"国库不足：写这封 {len(text)} 字的信需 {lc} 金"
                    f"（起步价 {base} 金{'（联盟内）' if allied else ''}含前 {LETTER_FREE_CHARS} 字、"
                    f"外交中心每座 -{LETTER_CENTER_DISCOUNT} 金，下限 {LETTER_COST_MIN}；"
                    f"超出 {over} 字 × 每 {LETTER_CHARS_PER_GOLD} 字 1 金**不打折**），"
                    f"你现 {world.res(actor, '黄金')} 金")
        ok, msg = world.send_mail(actor, to, text)
        if ok:
            world.add_res(actor, "黄金", -lc)
            msg += f"（{len(text)} 字，-{lc} 金）"
        return msg

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

    # ---- 间谍（100金，3回合后盗回目标经济底细+粗略军情+地图进 intel；不能对自己用）
    if tool in ("spy", "间谍", "刺探"):
        return world.spy(actor, str(args.get("to", "")))[1]

    # ---- 外交（每成功一次扣基础 10 金）
    if tool in ("propose", "提议"):
        to = str(args.get("to", ""))
        kind = PACT_MAP.get(str(args.get("kind", "")).lower(), args.get("kind"))
        return _charge(world, actor, _diplo_cost(world, actor, to, incoming=True),
                       world.propose_pact, kind, actor, to)
    if tool in ("respond_proposal", "回应邀约"):
        pid = int(args.get("proposal_id", args.get("id", 0)))
        accept = str(args.get("accept", "")).lower() in ("true", "yes", "1", "接受", "是")
        p = next((x for x in world.proposals if x["id"] == pid), None)
        to = (p["a"] if (p and p["kind"] == "联盟") else (p.get("asker") if p else None))
        cost = _diplo_cost(world, actor, to)
        if accept:
            return _charge(world, actor, cost, world.accept_pact, actor, pid)
        return _charge(world, actor, cost, world.reject_pact, actor, pid)
    if tool in ("break_alliance", "断盟"):
        to = str(args.get("to", ""))
        return _charge(world, actor, _diplo_cost(world, actor), world.break_pact, "同盟", actor, to)

    # ---- 联盟（多边实体：起名结盟 / 申请入盟 / 单方面退盟 / 联盟投票）
    if tool in ("bloc_found", "结盟", "发起结盟"):
        tos = args.get("tos", args.get("to", []))
        if isinstance(tos, str):
            tos = [tos]
        tos = [str(t).strip() for t in (tos or []) if str(t).strip()]
        hub = next((t for t in tos if world.nation_building_count(t, "外交中心") > 0), None)
        return _charge(world, actor, _diplo_cost(world, actor, hub, incoming=True),
                       world.propose_bloc, actor, str(args.get("name", "")), tos)
    if tool in ("bloc_join", "入盟", "申请入盟"):
        return _charge(world, actor, _diplo_cost(world, actor), world.bloc_join, actor,
                       str(args.get("name", args.get("bloc", ""))))
    if tool in ("bloc_leave", "退盟", "退出联盟"):
        return _charge(world, actor, _diplo_cost(world, actor), world.bloc_leave, actor)
    if tool in ("bloc_rename", "改盟名", "联盟改名"):
        return _charge(world, actor, 0, world.bloc_rename, actor,
                       str(args.get("name", "")))      # 盟内操作免费
    if tool in ("bloc_transfer", "移交盟主"):
        return _charge(world, actor, 0, world.bloc_transfer, actor,
                       str(args.get("to", "")))      # 盟内操作免费
    if tool in ("bloc_dissolve", "解散联盟"):
        return _charge(world, actor, 0, world.bloc_dissolve, actor)
    if tool in ("vote", "投票"):
        vid = int(args.get("vote_id", args.get("id", 0)))
        raw = args.get("choice", args.get("vote", args.get("approve", args.get("accept"))))
        if isinstance(raw, bool):
            choice = raw
        else:
            s = str(raw or "").strip().lower()
            if s in ("yes", "y", "true", "1", "赞成", "同意", "接受", "是"):
                choice = True
            elif s in ("no", "n", "false", "0", "反对", "否决", "拒绝"):
                choice = False
            elif s in ("abstain", "abstention", "弃权", "中立", "不表态"):
                choice = None
            else:
                return "vote 需要 choice=yes/no/abstain（你要怎么表态？）"
        return _charge(world, actor, 0, world.cast_vote, actor, vid, choice)  # 盟内投票免费
    if tool in ("break_defense", "解除共同防御"):
        to = str(args.get("to", ""))
        return _charge(world, actor, _diplo_cost(world, actor), world.break_pact, "共同防御", actor, to)
    if tool in ("guarantee", "保障独立"):
        to = str(args.get("to", ""))
        return _charge(world, actor, _diplo_cost(world, actor), world.declare_guarantee, actor, to)
    if tool in ("cancel_guarantee", "撤回保障"):
        to = str(args.get("to", ""))
        return _charge(world, actor, _diplo_cost(world, actor), world.cancel_guarantee, actor, to)
    if tool in ("declare_war", "宣战"):
        to = str(args.get("to", ""))
        return _charge(world, actor, _diplo_cost(world, actor), world.declare_war, actor, to)
    if tool in ("offer_peace", "求和"):
        to = str(args.get("to", ""))
        kind = KIND_MAP.get(str(args.get("kind", "")).lower(), args.get("kind"))
        gold = int(args.get("gold", 0) or 0)
        note = str(args.get("note", "") or "")
        truce = int(args.get("truce", 0) or 0)
        return _charge(world, actor, _diplo_cost(world, actor, to, incoming=True),
                       world.offer_peace, actor, to, kind, gold, note, truce)
    if tool in ("accept_peace", "接受议和"):
        return _charge(world, actor, _diplo_cost(world, actor), world.accept_peace, actor,
                       int(args.get("offer_id", 0)))
    if tool in ("reject_peace", "拒绝议和"):
        return _charge(world, actor, _diplo_cost(world, actor), world.reject_peace, actor,
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
        if len(summary) < SUMMARY_MIN_CHARS:
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
        "name": "query", "description": f"查询接口：随时获取你的各面板。★ 你的**国土与视野已作为「坐标地图」常驻**在每回合的状态里（按势力分段：我 / 野人 / 各国；每行一格：`(x,y)归属地形，[L2城][，地名][，番号…]`），所以这里查的是**细节**。land=地皮逐格明细（可翻页/按建筑或资源过滤） / tile=**单格全明细**（x= y= 或 at=地名） / grid=**网格版地图**（ASCII 格子图，适合想一眼看形状时） / res=国库与储备 / plan=国策规划 / army=军队 / market=世界市场(现价/买价/卖价/均衡价+大单试算) / econ=经济核算(各建筑造价毛利回本) / intel=收到的地图情报(全部坐标) / spy=间谍情报(别国经济底细+外交关系+粗略军情) / mail=信箱 / countries=可选外交对象 / diplomacy=外交 / news=近讯 / threats=视野内他国军队（野人守军不列，见地图标记） / battle=**当前交战**（每方的支数·兵种构成·合计HP·输出基数、逐支 HP、地形/城堡/合计减伤——**仅防御方**吃；视野外的格标「盲战」只报我方） / all=全部。每个行动后状态会变，拿不准就再查一次。",
        "parameters": _props({"panel": {"type": "string", "enum": ["all", "res", "plan", "land", "tile", "grid", "army", "market", "econ", "intel", "spy", "mail", "countries", "diplomacy", "news", "threats", "battle"], "description": "要查询的面板", "required": True},
                              "cap": {"type": "integer", "description": f"panel=land：本次列几块（默认 {LAND_CAP}）"},
                              "offset": {"type": "integer", "description": "panel=land：从第几块开始列（翻页用）"},
                              "filter": {"type": "string", "description": "panel=land：只看含该**建筑**或该**资源**的格（如 兵营 / 耕地 / 军屯）"},
                              "x": {"type": "integer", "description": "panel=tile：坐标 x（1-based）"},
                              "y": {"type": "integer", "description": "panel=tile：坐标 y（1-based）"},
                              "at": {"type": "string", "description": "panel=tile：地名（可代替 x/y）"}})}},
    {"type": "function", "function": {
        "name": "report", "description": f"查本国经济报表（免费、只读）。每 {REPORT_EVERY} 回合**自动**结一期，第 {REPORT_EVERY+1}/{2*REPORT_EVERY+1}/{3*REPORT_EVERY+1}… 回合开局可查，**不能手动运行**。内容：市场计价 GDP 及增长率、扣除军费的财政收入、军费占 GDP 比、国家总资产及增长率、本期投资总量及增长率、外贸/内循环占比。不传参数=最新一期；turn=指定报表回合（如 {REPORT_EVERY+1}）；all=true=跨期趋势对比表。",
        "parameters": _props({"turn": {"type": "integer", "description": f"报表回合（{REPORT_EVERY+1}/{2*REPORT_EVERY+1}/{3*REPORT_EVERY+1}…）；省略=最新一期"},
                              "all": {"type": "boolean", "description": "true=返回全部期的趋势对比表"}})}},
    {"type": "function", "function": {
        "name": "countries", "description": "列出所有可选外交对象（除你之外的每个国家：关系/是否接壤/有无来信）。外交动作前先用它选一个目标，再以 to=该国家 行动；绝不能对自己用外交工具。",
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "rules", "description": "查询完整游戏规则：建筑造价与上限、地形、电网经济、军队战斗、外交、信箱、市场、回合存档，以及三本讲义——rules(经济手册)「为什么这样搞经济」、rules(开局指南)「开局怎么下手」、rules(战争手册)「这仗该怎么打」。可带 topic 只取相关段（如 '兵营'、'外交'、'宣战'、'战争手册'）；**不带或认不出主题则返回章节目录**——照目录里的名字再查一次即可。★ **开战之前先查 rules(战争手册)**：一旦开打它会被强制挂进你的 system prompt，但在你还没开打时就查它，是**你的先手**——先用它算清「这仗几回合能拔几座厅、值不值得打」，再决定要不要开战。",
        "parameters": _props({"topic": {"type": "string", "description": "想查的主题（可选）"}})}},
    {"type": "function", "function": {
        "name": "econ", "description": "按当前市价核算建设回报：某建筑的 造价(折金)/每回合毛利/回本时间；不带 building 则输出全部建筑经济表。做建设/买卖决策前先算再定。",
        "parameters": _props({"building": {"type": "string", "enum": BUILD_NAMES, "description": "要核算的建筑名（可选；省则输出全部）"}})}},
    {"type": "function", "function": {
        "name": "build", "description": f"在自己的一块地上建一座建筑。每地块每回合限建1座。建筑: 城堡/林场/农场/矿场/黄金矿场/石油厂/木材能源厂/石油能源厂/补给厂/装备厂/兵营/市政厅/瞭望塔/外交中心/工程院/军屯。采集类上限=本地资源量；补给厂/装备厂/能源厂任地可建（工业不挑地）；兵营需本地已用建筑位≥{BUILDINGS['兵营']['min_slots']}；瞭望塔=事件视野+{building_effect('瞭望塔', 'vision_radius')}圆；市政厅需本地已用位≥{BUILDINGS['市政厅']['min_slots']}且每地块限{BUILDINGS['市政厅']['limit']}（★**它是国祚：市政厅尽失即亡国**，余土沦为无主之地、建筑留原地；开局核心白送1座）；外交中心=外交费减半可叠加但自建全国限{BUILDINGS['外交中心']['limit_nation']}（第2座只能抢）；工程院=本地建造费-{building_effect('工程院', 'build_discount')}%需本地位≥{BUILDINGS['工程院']['min_slots']}；军屯=**不产粮**的民兵编制、可征民兵({_cost_text(UNIT_TYPES['民']['recruit'])}/支、全国民兵总数≤军屯数)且民兵驻本格不耗补给（需本地{BUILDINGS['军屯']['cap_resource']}≥1、每地块限{BUILDINGS['军屯']['limit']}座）。",
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标如 '5 6' 或自家地块名（land 面板有）", "required": True},
                              "building": {"type": "string", "enum": BUILD_NAMES, "description": "建筑名", "required": True}})}},
    {"type": "function", "function": {
        "name": "recruit", "description": _recruit_desc(
            {k: v["recruit"] for k, v in UNIT_TYPES.items()}),
        "parameters": _props({"tile": {"type": "string", "description": "地块：坐标 '5 6' 或名字", "required": True},
                              "n": {"type": "integer", "description": "征召数量（默认1）"},
                              "kind": {"type": "string", "enum": ["步", "骑", "民"], "description": "兵种（默认 步；民=民兵，只能在自家军屯格征召）"}})}},
    {"type": "function", "function": {
        "name": "move", "description": "把一支自己的军队挪位置，纯移动不占地。每回合每支限1次。" + _move_rule_text() + " **行军不打野人**：合法移动目标只有三种：**野地（无人荒地）、自家格、盟国格**——野地可直接走进/穿过（行军不打野人，野人只在被 atk 时接战）；★★ **走进 ≠ 占下：mv 走进去不改变归属**，无主地走完**还是无主地**（国土不会多一块）——**要占地必须用 atk**，那是本作唯一的占地动作；自家/盟国格被混战敌军占着也可以 mv 进去（增援，入格即随军参战）。**敌国领土 mv 一律不得进入（空格也是）**：每一步进敌境都是 atk——会交战或直接进占。**野地上有与你交战的敌军驻守（含正在打野的）也不得 mv**——必须 atk 交战；中立/盟友驻守的野地可以 mv 进去（旁观待命，互不干扰）。交战中不能移动，须先 retreat 撤出。",
        "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "attack", "description": "军队(" + f"按兵种移动力可及：{_reach_brief()}" + ")冲入目标地块并交战——打赢该地守军自动占地；格上**无任何军队**则直接进驻占领；野地上只有中立/盟友和平驻守时也直接进驻占领（它们不参战，回合末自动遣返）；他国领土上有非敌军队则不能进驻。**不抢别人的战斗**：野地上有与你非敌非盟的一方正在打野 → 不能 atk 插足（可 mv 旁观）；敌人/盟友在打野 → 可以参战（同格多方各打各的敌人，互相宣战才互打）。占地按索取顺序：第一个 atk 者优先，它阵亡则顺位最早入场的同盟者。与别国开打需已宣战。★★ **反击无罪**：已经交战的两国之间，atk 夺地/打人**不会再触发任何条约**——保障/共同防御/联盟的自动参战**只在宣战那一刻结算过一次**，此后这条战线的参战名单冻结，不会再拉进任何国家。**放手打，缩手才是亏。**",
        "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"}, "description": "参战本国军队id数组（各国独立从1编号）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "retreat", "description": f"交战中的军队（含防守方守军）撤出——**固定只能退相邻 {RETREAT_RANGE} 格**（所有人，不按兵种速度、也不看地形代价）。撤退不立刻结算：军队留在战场参与本回合末战斗结算（伤害全场分摊；防御方撤退减伤{RETREAT_DEF_COVER}%；撤退军本回合输出-{RETREAT_ATK_PENALTY}%），结算后自动脱离到目标格。目标限 己方/同盟/无人荒地；四周无合法撤退点则无法撤退。mv 不能从交战地撤离；想脱离战场一律用 retreat。",
        "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                              "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                              "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
    {"type": "function", "function": {
        "name": "disband", "description": "**遣散军队**（解甲归田）：把本国军队撤编、就地解散——免费、不限次数、**境内境外野地都行**（孤军深陷敌境时用它止损）。**当回合起就不再吃补给、不再计军费**（军费是每回合的持续支出，裁军是治「军费占 GDP 过高」的正当手段之一）；民兵遣散会**当场释放编制名额**（全国民兵总数 ≤ 全国军屯总数）。★ **不返还**：兵员、装备、粮食一概不退——这是明确的代价，别指望靠遣散回血。★ **交战中（含正在挨打的守军）不能遣散**：先用 retreat 撤出战场，下回合再遣散。★ 遣散**不亡国**（亡国只认市政厅全失），但军队没了就是没了——番号不回收、也不能补回来，要兵得重新征兵。",
        "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"},
                                          "description": "要遣散的本国军队id数组（各国独立从1编号，以 query panel=army 为准）；一次可遣散多支", "required": True}})}},
    {"type": "function", "function": {
        "name": "buy", "description": f"从世界市场买物资花黄金。买=推高市价；成交按「沿曲线均价」结算并含 {MARKET_SPREAD/2:.0%} 买价差，越急买越贵（试算见 query panel=market）。⚠ 同一回合在**同一个商品**上又卖又买＝**空转**（白付两趟价差），回执里会当场报出倒手量与净亏——先想清楚再下单。",
        "parameters": _props({"good": {"type": "string", "description": "物资：粮食/木头/矿石/石油/装备/补给", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "sell", "description": f"向世界市场卖物资赚黄金。卖=压低市价；成交按「沿曲线均价」结算并扣 {MARKET_SPREAD/2:.0%} 卖价差，大单自己砸盘（试算见 query panel=market），分批慢慢卖更划算（**跨回合**分批才划算）。⚠ 同一回合在**同一个商品**上又买又卖＝**空转**（白付两趟价差），回执里会当场报出倒手量与净亏。",
        "parameters": _props({"good": {"type": "string", "description": "物资", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "send_letter", "description": f"给别国写信。**按字数计价：起步价联盟内 {LETTER_COST_ALLY} 金 / 非联盟 {LETTER_COST} 金（含前 {LETTER_FREE_CHARS} 字，吃外交中心减免：每座 {LETTER_CENTER_DISCOUNT}、下限 {LETTER_COST_MIN}）；超出 {LETTER_FREE_CHARS} 字的部分每 {LETTER_CHARS_PER_GOLD} 字 1 金、不吃任何减免**。成功即扣、下回合送达；联盟成员不再免费。写长信就是花钱——写信前先算账：值不值？预期收益（贡品/结盟/情报/逼降）明显大于信价才写，说不出收益就别写，更别拿写信闲聊；国库紧张时短写或不写。诉求能并进一次正式外交提议（外交费）就别单独写信。to 必须用 countries 选出的别国，不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "收信国名", "required": True},
                              "content": {"type": "string", "description": "信件正文", "required": True}})}},
    {"type": "function", "function": {
        "name": "gift", "description": f"把本国储备赠给别国（to=countries 里的别国，不能是自己）：good=粮食/木头/矿石/石油/装备/补给 或 黄金，qty=数量。本回合垫支扣出、下回合到账；另扣外交手续费（基准 {DIPLO_COST} 金；有外交中心则减半；收件人是联盟成员则免费）。示好/资助盟国/买通可用。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                              "good": {"type": "string", "description": "物资名", "required": True},
                              "qty": {"type": "integer", "description": "数量", "required": True}})}},
    {"type": "function", "function": {
        "name": "share_map", "description": f"把你的整张已知地图（全部国土块+边界外可见块，含坐标）发给别国，对方下一回合在 query panel=intel 收到（外交费，基准 {DIPLO_COST} 金，成功才扣；对象是联盟成员则免费）。换情报/亮家底/协同步调可用。to=countries 里的别国，不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "spy", "description": f"不想开口问（懒得谈、钱多）时派间谍刺探别国：花 {SPY_COST} 金（国库不足会被拒），{SPY_TURNS} 回合后拿回该国全部经济情报（query panel=spy 看——国库/储备、上回合收入、每一块地的建筑与在建）**、它的外交关系（该国视角：与谁 交战/共同防御/保障、在哪个联盟、哪条战线）**、粗略军情（仅各兵种数量，军队位置/血量/番号不外泄），以及它的整张已知地图（进 query panel=intel）。目标不能是自己。",
        "parameters": _props({"to": {"type": "string", "description": "刺探对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "plan", "description": f"制定或修订你的国策（长期战略目标），会永久常驻你的上下文（【国策规划】标记），直到你再次修订。⚠ 结束回合(end_turn)前必须已有国策；且每 {PLAN_MAX_TURNS} 回合必须修订一次，否则 end_turn 会被拦。建议按四方面写：经济发展（粮木矿油/建设/卖买）、军事规划（扩军/攻防/结盟）、情报管理（间谍/换图/来信研判）、外交方向（结盟/宣战/求和/馈赠立场）。",
        "parameters": _props({"content": {"type": "string", "description": "国策内容", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_found", "description": f"发起结盟（多边联盟）：**必须给联盟起名**（1~{BLOC_NAME_MAX} 字、不含空格、全局唯一）并邀请创始成员。全体创始成员 respond_proposal 接受后联盟成立（任一拒绝即流产），**发起方自动成为盟主**。★ 联盟本身就是**外交实体**：此后保障独立/共同防御/宣战/议和都由联盟出面、且须联盟投票通过，成员个人签不了任何条约；**入盟即放弃个人条约**（成员国原有的保障/共同防御一律作废）。盟内效果：互通领土/互不攻击/共享视野；同战线自动归还核心领土。**战争期间不能缔结同盟**；★ **休战期（和约在身）不拦结盟**——你与盟友之间那纸和约会被联盟**合并**（不再独立成立；联盟解散/退盟则按原到期回合自动恢复）。发起扣外交费（基准 {DIPLO_COST} 金；受邀方有外交中心则免费）。",
        "parameters": _props({"name": {"type": "string", "description": "联盟名（1~{BLOC_NAME_MAX}字，全局唯一）", "required": True},
                              "tos": {"type": "array", "items": {"type": "string"}, "description": "创始成员国名数组（至少1个，须为 countries 里的别国）", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_join", "description": f"申请加入指定联盟：现成员投票，**赞成 > 反对**即通过（盟主投 no 可否决）；一国同时只属一个联盟；与该联盟成员交战、或自己正在交战 → 不能申请。★ 有和约在身**不拦入盟**（和约会被联盟合并）。扣外交费（基准 {DIPLO_COST} 金）。",
        "parameters": _props({"name": {"type": "string", "description": "联盟名", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_leave", "description": f"退出所在联盟：普通成员单方面退出、立即生效（滞留前盟友领土的军队回合末自动遣返）。★ **战争期间一律不准退盟**——任一成员在交战即被拒，盟员在战时被锁死，先议和。**盟主不能退盟**——请先 bloc_transfer 移交，或 bloc_dissolve 解散。扣外交费（基准 {DIPLO_COST} 金）。",
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "bloc_rename", "description": f"【盟主专属】给联盟改名（1~{BLOC_NAME_MAX} 字、不含空格、全局唯一）。免费。",
        "parameters": _props({"name": {"type": "string", "description": "新联盟名", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_transfer", "description": "【盟主专属】把盟主之位移交给本联盟另一成员（移交后你变成普通成员，从此可自由退盟）。盟主身份由发起方自动获得，只有现任盟主能移交。免费。",
        "parameters": _props({"to": {"type": "string", "description": "接任盟主的成员国名（须在本盟成员里）", "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_dissolve", "description": "【盟主专属】解散你的联盟：全体成员恢复独立（各自重新成为外交实体）、条约作废、进行中的联盟投票一并作废。**战争期间不能解散**（任一成员正在交战即被拒，先议和停战）。盟主不能退盟，想脱身就用解散或移交。免费。",
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "vote", "description": "对所在联盟进行中的投票表态（宣战/议和/入盟/缔约）。choice=yes 赞成 / no 反对 / abstain 弃权（不投=到期算弃权）。**赞成 > 反对即通过**（弃权不计入分母）并立即执行；**盟主投 no 是一票否决，议案立即作废**。可改票；联盟成员投票免费。★「缔约」表决是本作**唯一**的签约通道：保障独立、共同防御都只能这样签或撤。",
        "parameters": _props({"vote_id": {"type": "integer", "description": "投票id（diplomacy 面板有）", "required": True},
                              "choice": {"type": "string", "enum": ["yes", "no", "abstain"], "description": "你的表态", "required": True}})}},
    {"type": "function", "function": {
        "name": "propose", "description": f"提议『共同防御』（仅守：它被打才自动并肩参战，你主动开战它不上）。**签约方是外交实体**：你所在的联盟出面与你**是独立国家**出面，效果不同——你在联盟里 → 调用先转为**联盟投票**，通过后由联盟出面提议；对方是联盟 → 它接受时同样要过它的联盟投票（一盟一票）。全面结盟请用 bloc_found。to 必须用 countries 选出的别国，不能是自己。**双方实体都必须在和平状态**（任一方在交战 → 不能缔结，先议和）。外交费（基准 {DIPLO_COST} 金；同实体免费；对方有外交中心则免费），成功才扣。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                              "kind": {"type": "string", "enum": ["共同防御"], "description": "类型", "required": True}})}},
    {"type": "function", "function": {
        "name": "respond_proposal", "description": "回应收到的邀约（共同防御/结盟：accept=true 接受 / false 拒绝；联盟创始须全体接受才成立；同实体免费）。★ 你**在联盟里**时，共同防御邀约不是你说了就算：accept 只是把它**提交联盟表决**（随后 vote 表态，赞成>反对才缔结）；拒绝由盟主代表实体对外出面。",
        "parameters": _props({"proposal_id": {"type": "integer", "description": "邀约id（diplomacy面板有）", "required": True},
                              "accept": {"type": "boolean", "description": "接受? true/false", "required": True}})}},
    {"type": "function", "function": {
        "name": "break_defense", "description": "解除共同防御。★ **战争期间一律不准解除**（条约在战时冻结：缔结与解除都不行，只有议和能停战）——原先「断约即退出该盟约带来的战线」，那是一条打不过就跑路的脱战通道，已封。你在联盟里 → 先转为**联盟投票**，通过后由联盟解除（成员个人无权解约）；你是独立国家 → 直接解除。解除后跟随方身份一并解除，滞留对方领土的军队回合末自动遣返。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "guarantee", "description": f"宣布保障别国（实体）独立：任何国家攻击它，你方实体将自动参战。**签约方是外交实体**：你在联盟里 → 先转为**联盟投票**，通过后由**联盟**保障它（恩义记在联盟头上、全盟担义务）；你是独立国家 → 你自己就是实体，直接生效。保障与共同防御互斥（共同防御更高一档，缔结它会自动解除保障）。**双方实体都必须在和平状态**（任一方在交战 → 不能保障，先议和）。to=别国（不能自己）。成功扣外交费（基准 {DIPLO_COST} 金）。",
        "parameters": _props({"to": {"type": "string", "description": "被保障国", "required": True}})}},
    {"type": "function", "function": {
        "name": "cancel_guarantee", "description": "撤回独立保障。★ **战争期间不准撤回**（条约在战时冻结，与 break_defense 同口径）。你在联盟里 → 先转为联盟投票，通过后由联盟撤回。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "declare_war", "description": "对别国宣战（对方必须应战，即刻生效；成功扣外交费）。先 countries 选目标，to=别国（不能自己）。★ **交战方是外交实体**：你在联盟里 → 调用即转为**联盟宣战投票**（多数决通过后全盟参战、盟主为进攻主导）；你是独立国家 → 直接开战。战争传导（**一对实体只走一跳**）：对方的**直接**保障国与**直接**共同防御伙伴自动参战打你（A 保 B、B 与 C 共防 → 打 B 则 C 也上）。★ **援军自己的条约不再往下传**——C 的其它盟友、保障 C 的人都不动（条约的触发条件是「它被打」，不是「它被卷进来」）。唯一**不限跳数**的是**联盟**：打盟员 = 打整个联盟，全盟一起上。★ **传导只在「宣战」这一刻结算一次**——结算完这条战线的参战名单就**冻结**：已经在打的人再 atk 夺地/打人，**不会再触发任何条约、不会再拉进任何国家**（别怕「先动手的会被围」——围不围，在你宣战那一刻就定完了）。若目标正与你方实体的盟友/共同防御伙伴交战，宣战会**并入其现有战线**当跟随方（跟随方不能单独议和，主导者议和整条战线停战）。",
        "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
    {"type": "function", "function": {
        "name": "offer_peace", "description": "向对方谈判代表求和（★ **被宣战方同样是主导者（防御主导），一样能主动求和**——求和不是宣战方的特权；每侧代表=主导者；主导者有联盟时=其盟主；普通成员/跟随方不能谈，to=diplomacy 面板所示对方代表；成功扣外交费）。⚠ 盟主求和会先发起联盟投票，多数同意才正式提出。pay=我方向对方赔X金；demand=要求对方赔X金；white=白和。接受后整条战线（含跟随方）停战，且各方实际持有地块重算为核心领土。truce=休战回合数（0=不休战）。★ 战时盟员**不能退盟、不能解散**，议和是唯一的解套路径。",
        "parameters": _props({"to": {"type": "string", "description": "对方谈判代表国", "required": True},
                              "kind": {"type": "string", "enum": ["pay", "demand", "white"], "description": "pay=我方赔款 / demand=要求对方赔款 / white=白和", "required": True},
                              "gold": {"type": "integer", "description": "赔款量（pay/demand 必填>0）"},
                              "truce": {"type": "integer", "description": "休战回合数（自行约定，0=不休战）"},
                              "note": {"type": "string", "description": "附加条件/说明（可选）"}})}},
    {"type": "function", "function": {
        "name": "accept_peace", "description": "接受对方求和（diplomacy 面板可看提议编号；只有被点名的一方=谈判代表能接受；成功扣外交费）。⚠ 若你是盟主，接受会先发起联盟投票，多数同意才正式生效。",
        "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}},
    {"type": "function", "function": {
        "name": "reject_peace", "description": "拒绝对方求和，战争继续（成功扣外交费）。",
        "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}},
    {"type": "function", "function": {
        "name": "memory_search", "description": "检索你自己的历史记忆（只看行动/发言/结果的**正文**，不看思考过程）：按关键词找出相关回合，返回**回合号与相邻上下文**。适合翻旧账——「我之前答应过楚国什么」「哪几场仗烧补给最凶」「谁对我宣过战」。关键词用空格分隔（如 盟约 楚），全部命中优先、部分命中次之；limit 控制返回条数。只搜得到你自己的记忆，不影响他人。免费、只读。",
        "parameters": _props({"query": {"type": "string", "description": "检索关键词（用空格分隔多个词）", "required": True},
                              "limit": {"type": "integer", "description": "最多返回几条（默认3，最大8）"}})}},
    {"type": "function", "function": {
        "name": "end_turn", "description": "结束本国本回合的行动。⚠ 必填 summary：用一句话总结你这回合做了什么/当前立场（例如：summary=这回合建了两座农场并继续拓荒）。没有这句小结就不算结束本回合。",
        "parameters": _props({"summary": {"type": "string", "description": f"一句话回合小结（必填，>={SUMMARY_MIN_CHARS}字）", "required": True}})}},
]

# 工具 schema 的固定 token 开销（每次请求都随 tools 发送，计入上下文预算）
TOOL_SCHEMAS_TOKENS = est_tokens(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))

_SCHEMA_CACHE: dict[str, list[dict]] = {}


BANK_TOOL_SCHEMA = {"type": "function", "function": {
    "name": "loan",
    "description": f"向世界央行借一笔（现金立刻到账）。**金额与期限都不能自选**："
                   f"额度 = 你**当时 GDP × {BANK_LOAN_GDP_MULT}**、期限固定 "
                   f"{BANK_LOAN_TURNS} 回合；**还清前不能再借**（一国同时只有一笔）。"
                   f"利率 = 储蓄利率 + {BANK_SPREAD:.0%}，**可为负**（那时欠款每回合缩水）。"
                   "每回合自动累计应还额，**到期一次性强制扣款**——那一笔允许把国库扣成负的，"
                   "所以只借**回本快于到期日**的钱。当前利率与额度见状态面板的【央行】。"
                   f"借钱算外交动作，按外交费计（{DIPLO_COST} 金，成功才扣，有外交中心减半）。",
    "parameters": _props({})}}


BUY_REPORT_TOOL_SCHEMA = {"type": "function", "function": {
    "name": "buy_report",
    "description": f"向世界央行买一份**别国已公布**的经济报表（{BUY_REPORT_COST} 金一次，"
                   "当场到手；国库不足会被拒、不收钱）。★ 这不是偷：报表每 "
                   f"{REPORT_EVERY} 回合自动结一期，**生成时全世界都收到过一行公告**"
                   "（GDP / 军费占 GDP / 总资产），央行卖的只是那张表的**明细全文**"
                   "（市场计价 GDP 及增长率、军费占比、总资产及增长、本期投资、外贸/内循环占比）。"
                   "要偷**当下**的国库、储备与每块地的建设，仍得用 spy（100 金、3 回合后到手）。"
                   "买到手进你的情报库（query panel=spy 可重看）。to=卖谁（不能是自己）；"
                   "turn=指定报表回合，省略=它最新一期。",
    "parameters": _props({
        "to": {"type": "string", "description": "买哪一国的报表（别国名）", "required": True},
        "turn": {"type": "integer", "description": "指定报表回合（如 21）；省略=它最新一期"}})}}

# 开行才挂上的工具（整局不变 ⇒ 工具表逐字节稳定、不吃缓存）
BANK_TOOL_SCHEMAS = (BANK_TOOL_SCHEMA, BUY_REPORT_TOOL_SCHEMA)


def tool_schemas(world, name) -> list[dict]:
    """该国的工具 schema。**政体差异（如匈奴骑兵征召特价）由引擎现算**，
    再据此重建征召描述——不再对描述文本做字符串替换（那种补丁改一处漂一处）。

    口径唯一来源：`World.recruit_cost()`（读 `balance.POLITY`）。
    schema 对同一政体跨回合稳定 ⇒ 不影响前缀缓存（按政体缓存一份）。

    ★ 银行工具（借款 / 买报表）挂在 **`bank_on()`** 上，**与政体无关**：
    面板与 `rules` 从来就只看开没开行（手册那句「**匈奴也能借**」），
    以前匈奴走的是 `_SCHEMA_CACHE` 那条路、漏挂了银行工具 ⇒ 看得见【央行】面板却借不到钱
    （2026-09-19 随「央行卖报表」一并修）。
    """
    key = world.polity.get(name) or ""
    if not key:
        base = TOOL_SCHEMAS                      # 关行时**原样返回**（身份不变，省一次拷贝）
    elif key not in _SCHEMA_CACHE:
        schemas = copy.deepcopy(TOOL_SCHEMAS)
        costs = {k: world.recruit_cost(name, k) for k in UNIT_TYPES}
        for t in schemas:
            fn = t["function"]
            if fn["name"] == "recruit":
                fn["description"] = _recruit_desc(costs)
        _SCHEMA_CACHE[key] = schemas
        base = schemas
    else:
        base = _SCHEMA_CACHE[key]
    return base + list(BANK_TOOL_SCHEMAS) if world.bank_on() else base


def _huns_prompt(world, name) -> str:
    return (
        "你是草原游牧帝国【" + name + "】（匈奴）的单于，以劫掠、夺产、勒索维生。\n\n"
        "【政体约束（硬性）】你不搞结盟/共同防御/保障/馈赠/交换地图那套外交。你能用的只有："
        "send_letter（写信威吓勒索贡品）、spy（刺探对方国情）、declare_war（宣战）、"
        "offer_peace（要求投降/赔款求和）、accept_peace / reject_peace（议和/拒绝）。\n"
        "【开局（事实）】你是骑兵开局，别饿空补给（消耗与补给情况随 query 面板现算）；"
        f"你建建筑要贵 {world.polity_rule(name, 'build_cost_pct', 100) - 100}%（别走种田流），"
        f"但你的骑兵征召只要 {_cost_text(world.polity_rule(name, 'recruit', {}).get('骑', {}))}"
        f"（寻常国家 {_cost_text(UNIT_TYPES['骑']['recruit'])}）。开局农耕资源近乎为零，连一座建筑都盖不起；"
        "**补给撑不了太久，越拖越容易死**。\n"
        "【教义（必须贯彻）】\n" + HUNS_DOCTRINE + "\n"
        "【信息】情报有迷雾：你只看得见自己地盘与相邻一圈；写信对象随时可用 countries 选。"
        "所有规则的细节（消耗/造价/战斗/市场）一律以 rules 返回值与 query 面板为准，别凭记忆猜。"
    )


def system_prompt(world, name) -> str:
    if world.polity.get(name) == "huns":
        p = _huns_prompt(world, name)
    else:
        p = _default_system_prompt(world, name)
    # 《开局指南》：开局前 EXTRA_PROMPT_TURNS 回合**强行挂给每一国**（讲开局该做什么）。
    # ★ 一律 getattr 兜底：system_prompt 会被结算厅那类"shim 世界"调用（settlement.py），
    #   少一个字段不该把整局带崩——而它由 build_context 在**回合中途**调用，
    #   AttributeError 会留下打了一半的回合，比启动即崩更难查。
    # 《战争手册》：**只要在打仗就每回合强行挂载**（用户 2026-10-06：「写一本战争手册，
    # 战争时必看」）。判据用引擎自己的 `at_war`（含跟随方），不另立口径。
    # ★ getattr 兜底的理由同下：结算厅那种 shim 世界可能没有 at_war。
    _at_war = getattr(world, "at_war", None)
    if callable(_at_war) and _at_war(name):
        p += "\n\n【战争手册（你在交战，每回合必读；停战后可用 rules(战争手册) 取回）】\n" + war_manual()
    if getattr(world, "opening_guide", False) and world.turn < EXTRA_PROMPT_TURNS:
        p += (f"\n\n【开局指南（开局前 {EXTRA_PROMPT_TURNS} 回合常驻；"
              f"过期后仍可用 rules(开局指南) 取回）】\n" + opening_guide())
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


TURN_STATE_HEAD = "以上为过往回合记录"   # 本回合状态消息的抬头（存档/检索靠它认这条）


def turn_state(world, name, replay_since: int | None = None) -> str:
    """本回合的新鲜状态（消息尾部）。replay_since=已进 replay 的最早回合，
    用于把状态面板里重复的内容去掉（见 _fmt_memory/_fmt_news/_fmt_mail）。

    ★这条消息**原样进 replay**（见 `_store_turn_memory`）：它是本回合请求的最后一条，
    下回合必须按同一串字节把它摆在 replay 里，请求才是"上一次请求 + 新状态"的纯追加。"""
    return (f"{TURN_STATE_HEAD}，现在开始第 {world.turn} 回合行动。\n"
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
                            tool_tokens=TOOL_SCHEMAS_TOKENS,
                            long_memory=world.long_memory.get(name, ""))

    tail1 = turn_state(world, name, None)
    msgs, plan = _build(tail1)
    tail2 = turn_state(world, name, plan.before_turn)
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


# ---------------------------------------------------------------------------
# 阶段块总结：滑出 replay 的回合用一次 LLM 调用压成一段，进历史归档
# ---------------------------------------------------------------------------
# ★★ 2026-10-02：压缩调用改成「**上一次请求的前缀** + 一条尾随指令」。
#
# 旧形状是另起一份请求：`[{system: COMPACT_SYSTEM}, {user: 长期记忆 + 改写稿}]`，不带 tools。
# 它与主请求**零字节重叠** ⇒ 那次调用几乎全 miss（实测压缩回合命中 9.8%、普通回合 91%）。
# 而它要压掉的那些回合，本来就是上一次请求里「system + 归档」之后紧跟着的最老一段——
# **热前缀本来就在那儿**，只是被自己另起炉灶丢掉了。现在的形状：
#
#     [system 原样][归档 原样][即将滑掉的回合原文…][长期记忆维护指令(user)]
#
# 前三段与上一次请求逐字节相同 ⇒ 由提供方前缀缓存命中（能压掉多少历史就命中多少），
# 只有尾随指令与摘要输出是新字节。三个不许动的点：
#   · 字节一律取**真发出去的那份 messages 的切片**，不重渲染——`assemble` 末尾那次
#     `merge_same_role` 会把归档(user) 与紧随其后的回合首条(user) 合成**一条**消息，
#     重渲染成两条独立 user 消息就会让命中断在归档末尾（正好是被压区间的起点）；
#   · **system 不许重算**：`system_prompt()` 逐回合可变（开局指南、临时情报 until、
#     遗留总结），压缩那一刻重算出来的未必是发出去的那份字节；
#   · **tools 照发、tool_choice 保持与主请求同值**：Anthropic 那路的请求前缀顺序是
#     tools → system → messages，少发一份就等于零命中；而 2026-10-02 真端点实测还多一条
#     ——把 tool_choice 改成 `none` 会让网关**整个丢掉 tools 段**，前缀从 system 之后
#     当场断掉（冷缓存那轮命中只剩 40%，照发 auto 是 93.6%）。工具调用只能靠下面那句
#     "不要调用任何工具"约束（DSH 的 compact 也是这么做的：指令里写死，不动 tool_choice）。
#
# 代价要认：这次压缩落地后 `long_memory` 变字节、而它压在归档最前 ⇒ 归档之后照旧整段
# miss（"替换"语义的固有成本，只能靠"压得稀"摊薄，见 ctx.py 顶部那段）。改的只是
# **压缩这次调用自己**的命中率：以前它是自造的冷启动，现在它接在热前缀后面。
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
COMPACT_INPUT_CHARS = 120_000    # 溢出降级稿（_compact_input）的字符上限：超了从最旧略细节
COMPACT_REPLAY_TOKENS = 120_000  # 逐字回放预算（token）。超了**只砍尾部**——前缀性质必须
                                 # 保住；砍掉的旧回合降级成小结行接在指令之前（新字节很少）。



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


# ---------------------------------------------------------------------------
# OpenAI 回合循环
# ---------------------------------------------------------------------------

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
    messages, plan = build_context(world, name, cfg)
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


# ---------------------------------------------------------------------------
# 无 key 的规则 AI（验证机制 + 看海 demo）
# ---------------------------------------------------------------------------

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
