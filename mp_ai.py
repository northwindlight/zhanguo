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
import threading

from game import (
    ARMY_HEAL_PER_TURN,
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
    RETREAT_ATK_PENALTY,
    RETREAT_RANGE,
    TERRAIN_STATS,
    UNIT_TYPES,
    letter_cost,
    unit_supply,
)
import ctx as ctxlib
import i18n
from console import dw as _dw, pad as _pad
from llm_provider import make_backend
from ctx import est_tokens
import rule_ai as rule_ai_registry
from mp import (BLOC_NAME_MAX, CROSS, DIPLO_COST, EXTRA_PROMPT_TURNS, FALL_TRUCE_TURNS,
                PLAN_MAX_TURNS, POLITY, REPORT_EVERY, RES_KEYS, RES_LABEL,
                RETREAT_DEF_COVER, SPY_COST, SPY_TURNS, SUMMARY_MIN_CHARS,
                build_econ, good_value)

MAIL_BRIEF_FULL = 3      # 状态面板里完整展示的新信数（更旧的只列摘要行）
MAIL_BRIEF_ROWS = 20     # 状态面板里最多列多少条旧信摘要

# ★ 局内上下文**不注入 README**（2026-09-15）：`rules` 一律返回 `_rules_sections()`
#   现算的规则文本，**所有政体同一份**。曾经匈奴的 rules 额外附一份 README 原文全文
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

# 文案小工具：全部返回 `i18n.Msg`（键 + 参数），渲染推迟到边界（口径见 i18n.py）。
# 分隔符/量纲词这些**语言属性**一律进模板（`ai.fmt.*`），代码里不再写 `'/'.join(...)`。

def _join(items, put):
    """若干段文案按语言自己的分隔符串起来（分隔符住在模板里，两两递归相连）。

    `put(a, b)` 给出「a 连 b」那一条模板（见下面的 `_by_*`——键名以字面量出现在源码里，
    `tests/test_i18n.py` 的键自洽才认得它们）。单项时直接返回那一项（不需要模板）；
    空列表返回 `""`（调用点的模板据此把整段省略）。
    """
    it = [x for x in items if x != ""]
    if not it:
        return ""
    if len(it) == 1:
        return it[0]
    return put(it[0], _join(it[1:], put))


# 连接符是**语言属性**（中文「、」/英文 ", "、中文「；」/英文 "; "…），所以一律住模板。
def _by_slash(a, b):   return i18n.Msg("ai.fmt.slash", a=a, b=b)
def _by_semi(a, b):    return i18n.Msg("ai.fmt.semi", a=a, b=b)
def _by_dun(a, b):     return i18n.Msg("ai.fmt.dun", a=a, b=b)
def _by_gap(a, b):     return i18n.Msg("ai.fmt.gap", a=a, b=b)
def _by_sp(a, b):      return i18n.Msg("ai.fmt.sp", a=a, b=b)
def _by_arrow(a, b):   return i18n.Msg("ai.fmt.arrow", a=a, b=b)
def _by_bar(a, b):     return i18n.Msg("ai.fmt.bar", a=a, b=b)
def _by_nl(a, b):      return i18n.Msg("ai.fmt.nl", a=a, b=b)
def _by_para(a, b):    return i18n.Msg("ai.fmt.para", a=a, b=b)


def _rows(lines, lang: str | None) -> str:
    """把一叠**行**组成多行表：逐行按 `lang` 渲染，行间 `\\n` 是排版不是文案。

    面板/规则全文都是这种「行表」——每行整句进模板，行与行之间只是版面。
    """
    return "\n".join(i18n.show(x, lang) for x in lines)


COST_SHORT = {"粮食": "粮", "装备": "装", "黄金": "金", "木头": "木", "矿石": "矿",
              "石油": "油", "补给": "补给"}


def _cost_msg(cost: dict[str, int]) -> i18n.Text:
    """把一份料单写成"10粮+5装"这样的短说法（面板/工具描述共用，不写死数字）。

    「+」两侧的空格与单位名（粮/装…）都住在模板与词库里 ⇒ 英文局自动成 "10 Food + 5 Equip"。
    """
    it = list(cost.items())
    if not it:
        return ""
    (k, amt), *rest = it
    item = i18n.Msg("ai.fmt.cost_item", amt=amt, unit=COST_SHORT.get(k, k))
    if not rest:
        return item
    return i18n.Msg("ai.fmt.cost", a=item, b=_cost_msg(dict(rest)))


def _move_brief(kind: str) -> i18n.Text:
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
        return i18n.Msg("ai.fmt.move_flat", n=open_cells)
    slow_cells = budget // max(costs.values())
    return i18n.Msg("ai.fmt.move_slow", n=open_cells,
                    terr=_join(slow, _by_slash), m=slow_cells)


def _move_cost_text() -> i18n.Text:
    """地形代价那一句——**由 `MOVE_COST` 现算**（改表 ⇒ 文案跟着变）。

    捷径/崎岖两类地形名、以及"崎岖能走几格"都从表里推，不写死。
    """
    costs = MOVE_COST["骑"]
    open_t = [t for t in TERRAIN_STATS if costs.get(t, 1) == min(costs.values())]
    slow_t = [t for t in TERRAIN_STATS if t not in open_t]
    budget = UNIT_TYPES["骑"]["speed"]
    slow_kinds = [UNIT_TYPES[k]["label"] for k in UNIT_TYPES if k != "骑"]
    return i18n.Msg("ai.text.move_cost",
                    fast=_join(open_t, _by_slash), slow=_join(slow_t, _by_slash),
                    n=budget // max(costs.values()),
                    kinds=_join(slow_kinds, _by_slash), flat=max(MOVE_COST["步"].values()))


def _move_rule_text() -> i18n.Text:
    """移动规则的完整说法——**全部由 `MOVE_COST` / `UNIT_TYPES` 现算**。

    2026-09-15 起：速度=移动力，逐格按地形扣（出发格与目标格取更贵的那个），
    多格移动逐格判定 ⇒ 崎岖地形"减速 + 穿不过去"。
    """
    return i18n.Msg("ai.text.move_rule", rows=_move_rows(), cost=_move_cost_text())


def _move_rows() -> list:
    """每个兵种「名称 + 能走几格」的一行（移动条款与 `attack` 描述共用）。"""
    return [i18n.Msg("ai.fmt.move_row", unit=UNIT_TYPES[k]["label"], brief=_move_brief(k))
            for k in UNIT_TYPES]


def _reach_brief() -> i18n.Text:
    """`atk` 够得着多远的短说法——**由 `MOVE_COST` / `UNIT_TYPES` 现算**。

    （这里原来手写着"步1格/骑2格"：移动代价表一改就成假话，而工具描述是 LLM 玩家
    做决策时唯一的规则来源。改表 ⇒ 这句跟着变。）
    """
    return i18n.Msg("ai.fmt.rows", rows=_move_rows())


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


def _diplo_chain_text() -> i18n.Text:
    """外交费递减链的**文案**（'10→5→2→1'，箭头由语言表给）。"""
    return _join(_diplo_chain(), _by_arrow)


def _recruit_desc(costs: dict[str, dict]) -> i18n.Text:
    """征召工具的说明：费用/补给/移动**全部现算**（`UNIT_TYPES` + 政体表）。

    `costs` = {兵种: 料单}，由「该国实际征召价」（`World.recruit_cost`）或
    `UNIT_TYPES[*]['recruit']`（无政体）给出 ⇒ 匈奴的骑兵特价自动体现在文案里。
    """
    rows = []
    for k, info in UNIT_TYPES.items():
        rows.append(i18n.Msg("ai.schema.recruit.kind", k=k, label=info["label"],
                             cost=_cost_msg(costs.get(k, info["recruit"])),
                             supply=info["supply"], reach=_move_brief(k),
                             extra=(i18n.Msg("ai.schema.recruit.militia") if k == "民" else "")))
    return i18n.Msg("ai.schema.recruit.desc", rows=_join(rows, _by_semi),
                    starve=ARMY_STARVE_DAMAGE)


# 匈奴教义（喂给匈奴 AI，令其贯彻；人类侧全文见 匈奴教义.md）
def _huns_doctrine() -> i18n.Text:
    """八条教义：一条一个键，整段由 `ai.prompt.huns.doc` 排版（数值仍从表里现算）。"""
    return i18n.Msg("ai.prompt.huns.doc",
                    b1=i18n.Msg("ai.prompt.huns.doc.1"),
                    b2=i18n.Msg("ai.prompt.huns.doc.2"),
                    b3=i18n.Msg("ai.prompt.huns.doc.3"),
                    b4=i18n.Msg("ai.prompt.huns.doc.4", atk=UNIT_TYPES['骑']['atk'],
                                hp=UNIT_TYPES['骑']['hp'], cav=_move_brief('骑'),
                                inf=_move_brief('步'), rng=RETREAT_RANGE),
                    b5=i18n.Msg("ai.prompt.huns.doc.5", cav_speed=UNIT_TYPES['骑']['speed'],
                                inf_speed=UNIT_TYPES['步']['speed']),
                    b6=i18n.Msg("ai.prompt.huns.doc.6"),
                    b7=i18n.Msg("ai.prompt.huns.doc.7"),
                    b8=i18n.Msg("ai.prompt.huns.doc.8"))


def _res_line(world, name, lang: str | None = None) -> str:
    """国力行：储备一行 + 电网一行（+ 有结算摘要时再来一行）。"""
    r = world.nations[name].res
    et, mt, short = world.energy_report.get(name, (0, 0, False))
    parts = [i18n.Msg("ai.fmt.res_item", label=RES_LABEL.get(k, k), v=r.get(k, 0))
             for k in RES_KEYS]
    grid = (i18n.Msg("ai.panel.grid_ok") if not short else i18n.Msg("ai.panel.grid_down"))
    summ = world.econ_summary.get(name, "")
    lines = [_join(parts, _by_gap),
             i18n.Msg("ai.panel.res.grid", et=et, mt=mt, grid=grid)]
    if summ:
        lines.append(i18n.Msg("ai.panel.res.econ", summ=summ))
    return _rows(lines, lang)


def _fmt_armies(world, name, lang: str | None = None) -> str:
    mine = [a for a in world.armies if a["owner"] == name]
    if not mine:
        return i18n.show(i18n.Msg("ai.panel.army.none"), lang)
    lines = []
    for a in sorted(mine, key=lambda x: x["id"]):
        t = world.tiles.get((a["x"], a["y"]))
        where = (t["name"] if t else i18n.Msg("ai.panel.army.wilds"))
        engaged = bool(a.get("engaged"))
        can_act = not engaged and a.get("moved_turn") != world.turn
        st = (i18n.Msg("ai.panel.army.st_engaged") if engaged
              else i18n.Msg("ai.panel.army.st_moved") if a.get("moved_turn") == world.turn
              else i18n.Msg("ai.panel.army.st_ready"))
        # 移动力与"本回合能挪到几格"现算（地形代价表见 balance.MOVE_COST）
        mv = _move_brief(a.get("type", "步"))
        reach = len(world._reachable(name, a)) - 1 if can_act else 0
        lines.append(i18n.Msg("ai.panel.army.row", nm=a['name'], id=a['id'], hp=a['hp'],
                              x=a['x'] + 1, y=a['y'] + 1, where=where, st=st, mv=mv,
                              reach=(i18n.Msg("ai.panel.army.reach", n=reach)
                                     if can_act else "")))
    return _rows(lines, lang)


def _fmt_land(world, name, cap=40, lang: str | None = None) -> str:
    own = world.own_tiles(name)
    lines = [i18n.Msg("ai.panel.land.head", n=len(own), cap=cap)]
    shown = 0
    for (x, y) in own:
        if shown >= cap:
            lines.append(i18n.Msg("ai.panel.land.more"))
            break
        t = world.tiles[(x, y)]
        b = t["buildings"]
        used = sum(b.values()) + sum((t.get("pending") or {}).values())
        res = _join([i18n.Msg("ai.fmt.res_x", k=k, v=t['resources'][k])
                     for k in ("矿石", "黄金", "耕地", "石油", "木头")], _by_sp)
        pend = _join([i18n.Msg("ai.panel.land.pending", bn=bn)
                      for bn, n in (t.get("pending") or {}).items() if n], _by_sp)
        built = _join([i18n.Msg("ai.panel.land.built", bn=bn, n=n)
                       for bn, n in b.items() if n], _by_sp) \
            or i18n.Msg("ai.panel.land.none_built")
        free = (i18n.Msg("ai.panel.land.buildable") if not t["built_this_turn"]
                else i18n.Msg("ai.panel.land.ordered"))
        gar = _join([i18n.Msg("ai.panel.land.garrison", id=a['id'], hp=a['hp'])
                     for a in world.armies
                     if a["owner"] == name and (a["x"], a["y"]) == (x, y)], _by_sp)
        extra = i18n.Msg("ai.panel.land.pending_col", what=pend) if pend else ""
        core = "♥" if t.get("core") == name else ""  # ♥=核心领土（同战线盟友夺回会自动归还）
        lines.append(i18n.Msg(
            "ai.panel.land.row", core=core, tile=t['name'], x=x + 1, y=y + 1,
            terrain=t['terrain'], lv=b['城堡'], used=used, max=MAX_SLOTS, res=res,
            built=built, extra=extra, free=free,
            gar=(i18n.Msg("ai.panel.land.garrison_col", who=gar) if gar else "")))
        shown += 1
    fr = sorted(world.frontier_of(name))
    # 本回合**够得着**的格子：至少一支军队能从现位置走/打到（逐格代价，见 balance.MOVE_COST）
    reach: set = set()
    for a in world.armies:
        if a["owner"] == name and a["hp"] > 0 and not a.get("engaged") \
                and a.get("moved_turn") != world.turn:
            reach |= set(world._reachable(name, a, for_attack=True))
    lines.append(i18n.Msg("ai.panel.land.frontier_head", n=len(fr)))
    frs = []
    for (x, y) in fr:
        guard = any(a["owner"] == "野人" and (a["x"], a["y"]) == (x, y) for a in world.armies)
        frs.append(i18n.Msg("ai.panel.land.frontier_cell", x=x + 1, y=y + 1,
                            ter=world.ter_char(x, y),
                            tag=(i18n.Msg("ai.panel.land.tag_savage") if guard
                                 else i18n.Msg("ai.panel.land.tag_empty")),
                            ok=(i18n.Msg("ai.panel.land.tag_reachable")
                                if (x, y) in reach and world.visible_to(name, x, y) else "")))
    for i in range(0, len(frs), 8):
        lines.append(i18n.Msg("ai.panel.land.frontier_row",
                              cells=_join(frs[i:i + 8], _by_sp)))
    lines.append(i18n.Msg("ai.panel.land.terrain_note", cost=_move_cost_text()))
    return _rows(lines, lang)


def _fmt_market(world, name, lang: str | None = None) -> str:
    r = world.nations[name].res
    lines = [i18n.Msg("ai.panel.market.head", goods=_join(GOODS_DISPLAY, _by_sp)),
             i18n.Msg("ai.panel.market.spread", spread=f"{MARKET_SPREAD:.0%}")]
    for g in GOODS_DISPLAY:
        p = world.prices[g]
        base = MARKET[g]
        eq = world.equilibrium.get(g, base)
        tag = (i18n.Msg("ai.panel.market.tag_base") if abs(p - base) <= 0.02 * base
               else i18n.Msg("ai.panel.market.tag_high") if p > base
               else i18n.Msg("ai.panel.market.tag_low"))
        bp, _ = world.market_quote(g, 1, "buy")
        sp, _ = world.market_quote(g, 1, "sell")
        _, s50 = world.market_quote(g, 50, "sell")
        _, b50 = world.market_quote(g, 50, "buy")
        lines.append(i18n.Msg("ai.panel.market.row", g=g, p=f"{p:.2f}", base=base,
                              eq=f"{eq:.2f}", tag=tag, bp=f"{bp:.2f}", sp=f"{sp:.2f}",
                              have=r.get(g, 0), s50=s50, b50=b50))
    return _rows(lines, lang)


def _fmt_mail(world, name, brief: bool = False, lang: str | None = None) -> str:
    """信箱。brief=True（每回合状态面板用）：只完整展示最新几封，旧信压成摘要行；
    brief=False（query panel=mail 按需查询用）：列出全部信件全文——面板里"旧信见
    query"的提示必须真能取回，否则老信件对 AI 永久丢失。"""
    box = world.mailbox.get(name, [])
    if not box:
        return i18n.show(i18n.Msg("ai.panel.mail.empty"), lang)

    def _one(m, text):
        return i18n.Msg("ai.panel.mail.row", turn=m['turn'], frm=m['from'], text=text)

    if not brief or len(box) <= MAIL_BRIEF_FULL:
        return _rows([i18n.Msg("ai.panel.mail.head", n=len(box)),
                      *(_one(m, m['text']) for m in reversed(box))], lang)
    lines = [i18n.Msg("ai.panel.mail.head_brief", n=len(box))]
    lines += [_one(m, m['text']) for m in reversed(box[-MAIL_BRIEF_FULL:])]
    old = box[:-MAIL_BRIEF_FULL]
    for m in reversed(old[-MAIL_BRIEF_ROWS:]):
        t = " ".join(str(m["text"]).split())
        lines.append(_one(m, t[:28] + ('…' if len(t) > 28 else '')))
    if len(old) > MAIL_BRIEF_ROWS:
        lines.append(i18n.Msg("ai.panel.mail.older", n=len(old) - MAIL_BRIEF_ROWS))
    return _rows(lines, lang)


def _fmt_diplomacy(world, name, lang: str | None = None) -> str:
    lines = [i18n.Msg("ai.panel.diplo.rel", rel=world.rel_desc(name))]
    bloc = world.bloc_of(name)
    if bloc is not None:
        chief = world.bloc_chief(bloc)
        me_chief = (i18n.Msg("ai.panel.diplo.me_chief") if chief == name
                    else i18n.Msg("ai.panel.diplo.chief", who=chief))
        fighting = [m for m in bloc["members"] if world.at_war(m)]
        lock = (i18n.Msg("ai.panel.diplo.warlock", who=_join(fighting, _by_dun))
                if fighting else "")
        lines.append(i18n.Msg("ai.panel.diplo.bloc", nm=bloc['name'], chief=chief,
                              members=_join(bloc['members'], _by_dun),
                              lock=lock, me=me_chief))
    # 本人在内的交战战线（议和须找谈判代表：主导者；有联盟时=其盟主）
    wars_in = []
    for w in world.wars:
        sides = world._war_sides(w)
        if name in sides[0] or name in sides[1]:
            fls = list(w["followers"]) + list(w.get("atk_followers", []))
            wars_in.append(i18n.Msg("ai.panel.diplo.war", atk=w['atk'], df=w['def'],
                                    follow=(i18n.Msg("ai.panel.diplo.followers",
                                                     who=_join(fls, _by_dun))
                                            if fls else "")))
    if wars_in:
        lines.append(i18n.Msg("ai.panel.diplo.wars", rows=_join(wars_in, _by_semi)))
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
            desc = i18n.Msg("ai.panel.diplo.vote_war", target=world.entity_label(pl.get('target')))
        elif v["kind"] == "入盟":
            desc = i18n.Msg("ai.panel.diplo.vote_join", who=pl.get('candidate'))
        elif v["kind"] == "缔约":
            act = (i18n.Msg("ai.panel.diplo.pact_cancel") if pl.get("cancel")
                   else i18n.Msg("ai.panel.diplo.pact_offer") if pl.get("offer")
                   else i18n.Msg("ai.panel.diplo.pact_accept"))
            desc = i18n.Msg("ai.panel.diplo.vote_pact", act=act, pact=pl.get('pact'),
                            a=world.entity_label(pl.get('A')), b=world.entity_label(pl.get('B')))
        elif pl.get("type") == "offer":
            desc = i18n.Msg("ai.panel.diplo.vote_peace", to=pl.get('to'), kind=pl.get('kind'),
                            gold=(i18n.Msg("ai.panel.diplo.gold_tail", v=pl.get('gold'))
                                  if pl.get('gold') else ""))
        else:
            desc = i18n.Msg("ai.panel.diplo.vote_accept", id=pl.get('offer_id'))
        tail = ""
        if mine and v["kind"] != "入盟":
            if name not in v["votes"]:
                tail = i18n.Msg("ai.panel.diplo.vote_hint", id=v['id'])
            else:
                tail = i18n.Msg("ai.panel.diplo.vote_done",
                                how={True: i18n.Msg("ai.panel.diplo.v_yes"),
                                     False: i18n.Msg("ai.panel.diplo.v_no"),
                                     None: i18n.Msg("ai.panel.diplo.v_abst")}[v["votes"][name]])
        lines.append(i18n.Msg("ai.panel.diplo.vote", id=v['id'], bloc=v['bloc'], kind=v['kind'],
                              proposer=v['proposer'], desc=desc, yes=yes, no=no, abst=abst,
                              todo=pending, tail=tail))
    my_ent = world.entity_of(name)
    incoming = [p for p in world.proposals
                if (p["kind"] == "联盟" and name in p.get("invitees", []))
                or (p["kind"] != "联盟" and p.get("B") == my_ent)]
    for p in incoming:
        if p["kind"] == "联盟":
            lines.append(i18n.Msg("ai.panel.diplo.invite_bloc", id=p['id'], a=p['a'],
                                  nm=p['name'],
                                  members=_join(p['invitees'], _by_dun)))
        else:
            lines.append(i18n.Msg(
                "ai.panel.diplo.invite_pact", id=p['id'], a=world.entity_label(p['A']),
                kind=p['kind'],
                how=(i18n.Msg("ai.panel.diplo.invite_vote", id=p['id']) if bloc is not None
                     else i18n.Msg("ai.panel.diplo.invite_plain", id=p['id']))))
    for p in world.peace_offers:
        if p["b"] == name:
            k = _peace_kind(world, name, p, "in")
            lines.append(i18n.Msg("ai.panel.diplo.peace_in", id=p['id'], a=p['a'], k=k,
                                  note=p.get('note', '')))
    my_e = world.entity_of(name)
    if world.guaranteed_by(my_e):
        lines.append(i18n.Msg("ai.panel.diplo.give_g", ent=world.entity_label(my_e),
                              who=_join([world.entity_label(e)
                                         for e in world.guaranteed_by(my_e)], _by_dun)))
    if world.guarantors_of(my_e):
        lines.append(i18n.Msg("ai.panel.diplo.got_g",
                              who=_join([world.entity_label(e)
                                         for e in world.guarantors_of(my_e)], _by_dun)))
    for p in world.peace_offers:
        if p["a"] == name:
            lines.append(i18n.Msg("ai.panel.diplo.peace_out", id=p['id'], b=p['b'],
                                  k=_peace_kind(world, name, p, "out")))
    return _rows(lines, lang)


def _peace_kind(world, name, p, side: str) -> i18n.Text:
    """一条求和的要点（谁赔/谁索/白和 + 约定的休战回合）。side=in 别人提给我的，out 我提的。

    键名一律以**字面量**出现（`Msg({...}[k], ...)` 的字典首参键自洽检查看不见）。
    """
    who, gold = p['a'], p['gold']
    if side == "in":
        if p["kind"] == "pay":
            k = i18n.Msg("ai.panel.diplo.pay_me", who=who, gold=gold)
        elif p["kind"] == "demand":
            k = i18n.Msg("ai.panel.diplo.demand_me", who=who, gold=gold)
        else:
            k = i18n.Msg("ai.panel.diplo.white")
    elif p["kind"] == "pay":
        k = i18n.Msg("ai.panel.diplo.pay_them", who=who, gold=gold)
    elif p["kind"] == "demand":
        k = i18n.Msg("ai.panel.diplo.demand_them", who=who, gold=gold)
    else:
        k = i18n.Msg("ai.panel.diplo.white")
    if p.get("truce"):
        k = i18n.Msg("ai.panel.diplo.truce", k=k, n=p['truce'])
    return k


def _fmt_countries(world, name, lang: str | None = None) -> str:
    """外交对象面板：先把『不是自己』的国家列出来选目标。所有 to= 参数只能填这里面的国名。"""
    lines = [i18n.Msg("ai.panel.countries.head")]
    for n in world.alive():
        if n == name:
            continue
        my, them = world.entity_of(name), world.entity_of(n)
        tags = []
        if them == my:
            tags.append(i18n.Msg("ai.panel.countries.same_bloc", ent=world.entity_label(my)))
        if world.has_pact("共同防御", my, them):
            tags.append(i18n.Msg("ai.panel.countries.pact_def"))
        if world.has_pact("保障", my, them):
            tags.append(i18n.Msg("ai.panel.countries.give_guar"))
        if world.has_pact("保障", them, my):
            tags.append(i18n.Msg("ai.panel.countries.got_guar"))
        if world.war_between(name, n):
            tags.append(i18n.Msg("ai.panel.countries.at_war"))
        if not tags:
            tags.append(i18n.Msg("ai.panel.countries.neutral"))
        # 是否接壤
        border = False
        for (x, y) in world.own_tiles(name):
            if any(world.owned_by(*p) == n for p in world.neighbors(x, y)):
                border = True
                break
        letters = sum(1 for m in world.mailbox.get(name, []) if m["from"] == n)
        vis = [a for a in world.armies if a["owner"] == n and world.visible_to(name, a["x"], a["y"])]
        lines.append(i18n.Msg(
            "ai.panel.countries.row", n=n, tags=_join(tags, _by_dun),
            border=(i18n.Msg("ai.panel.countries.border") if border
                    else i18n.Msg("ai.panel.countries.noborder")),
            letters=letters,
            visible=(i18n.Msg("ai.panel.countries.seen", n=len(vis)) if vis else "")))
    return _rows(lines, lang)


def observer_board(world, lang: str | None = None) -> str:
    """Observer 全景大面板：各国国库/储备/国土/军队/关系/近期通信，一屏尽览。"""
    alive = world.alive()
    L = [i18n.Msg("ai.observer.head", turn=world.turn, alive=_join(alive, _by_dun)),
         i18n.Msg("ai.observer.market",
                  prices=_join([i18n.Msg("ai.fmt.res_item", label=g, v=world.market_price(g))
                                for g in GOODS_DISPLAY], _by_gap))]
    if world.blocs:
        L.append(i18n.Msg("ai.observer.blocs", desc=world.bloc_desc()))
    for n in alive:
        r = world.nations[n].res
        L.append(i18n.Msg("ai.observer.nation", n=n, gold=r['黄金'], food=r['粮食'],
                          wood=r['木头'], ore=r['矿石'], oil=r['石油'], equip=r['装备'],
                          supply=r['补给'], summ=world.econ_summary.get(n, '')))
        L.append(i18n.Msg("ai.observer.power", tiles=len(world.own_tiles(n)),
                          armies=len(world.nation_armies(n)), rel=world.rel_desc(n)))
        msgs = []
        # 只提示"本回合新到"的信（避免旧信内容在全景头每回合重打）
        for m in [m for m in (world.mailbox.get(n) or []) if m.get("turn") == world.turn][-1:]:
            msgs.append(i18n.Msg("ai.observer.got_mail", frm=m['from'], text=m['text'][:50]))
        for m in [m for m in world.mail_pending
                  if m["from"] == n and m["arrive"] == world.turn + 1][-1:]:
            msgs.append(i18n.Msg("ai.observer.sent_mail", to=m['to'], text=m['text'][:50]))
        if msgs:
            L.append(i18n.Msg("ai.observer.mails", row=_join(msgs, _by_bar)))
    dead = [n for n in world.order if n not in world.nations]
    if dead:
        L.append(i18n.Msg("ai.observer.dead", who=_join(dead, _by_dun)))
    return _rows(L, lang)


def observer_map(world, lang: str | None = None) -> str:
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
    return i18n.show(i18n.Msg("ai.observer.map", body="\n".join(rows), legend=legend), lang)


# ---- 规则全文：一节一个键（`ai.rules.*`），数值全部从 balance 现算 ⇒ 模板只留槽位。
#      节序 = `rules` 不带主题时的输出序；路由关键词按语言各存一份（英文局用英文词路由）。

RULE_SECTIONS = ("overview", "terrain", "buildings", "economy", "army", "bloc",
                 "diplomacy", "mail", "market", "report", "turn")

RULE_TITLES: dict[str, i18n.Text] = {          # 节的标题（键写字面量：死键检查要看得见）
    "overview": i18n.Msg("ai.rules.title.overview"),
    "terrain": i18n.Msg("ai.rules.title.terrain"),
    "buildings": i18n.Msg("ai.rules.title.buildings"),
    "economy": i18n.Msg("ai.rules.title.economy"),
    "army": i18n.Msg("ai.rules.title.army"),
    "bloc": i18n.Msg("ai.rules.title.bloc"),
    "diplomacy": i18n.Msg("ai.rules.title.diplomacy"),
    "mail": i18n.Msg("ai.rules.title.mail"),
    "market": i18n.Msg("ai.rules.title.market"),
    "report": i18n.Msg("ai.rules.title.report"),
    "turn": i18n.Msg("ai.rules.title.turn"),
}

RULE_KEYWORDS: dict[str, i18n.Text] = {        # 每节的路由关键词（逗号分隔，随语言而变）
    "overview": i18n.Msg("ai.rules.kw.overview"),
    "terrain": i18n.Msg("ai.rules.kw.terrain"),
    "buildings": i18n.Msg("ai.rules.kw.buildings"),
    "economy": i18n.Msg("ai.rules.kw.economy"),
    "army": i18n.Msg("ai.rules.kw.army"),
    "bloc": i18n.Msg("ai.rules.kw.bloc"),
    "diplomacy": i18n.Msg("ai.rules.kw.diplomacy"),
    "mail": i18n.Msg("ai.rules.kw.mail"),
    "market": i18n.Msg("ai.rules.kw.market"),
    "report": i18n.Msg("ai.rules.kw.report"),
    "turn": i18n.Msg("ai.rules.kw.turn"),
}


def _terrain_rows() -> i18n.Text:
    return _join([i18n.Msg("ai.rules.terrain.row", ter=k,
                           d=f"{v['defense']:+d}", p=f"{v['build_penalty']:+d}")
                  for k, v in TERRAIN_STATS.items()], _by_nl)


def _res_x(items: dict[str, int]) -> list:
    """{"粮食": 1, "矿石": 1} → 供模板槽位用的清单（渲染器按语言连接：中文顿号、英文逗号）。"""
    return [i18n.Msg("ai.fmt.res_x", k=k, v=a) for k, a in items.items()]


def _building_line(nm: str, info: dict) -> i18n.Text:
    """一种建筑的一行规则说明：造价/占地上限由表给，机制按 kind 走各自的键。"""
    k = info["kind"]
    if k == "castle":
        note = i18n.Msg("ai.rules.bld.castle",
                        per=building_effect('城堡', 'defense_per_level'), max=info['max_level'])
    elif k == "extract":
        note = i18n.Msg("ai.rules.bld.extract", out=_res_x(info["outputs"]))
    elif k == "gold":
        note = i18n.Msg("ai.rules.bld.gold", gold=info["outputs"].get("黄金", 0) * MARKET["黄金"])
    elif k == "energy":
        note = i18n.Msg("ai.rules.bld.energy", fuel=_res_x(info["fuel"]), out=info['energy_out'])
    elif k == "factory":
        note = i18n.Msg("ai.rules.bld.factory", en=info['energy'], ins=_res_x(info["inputs"]),
                        out=_res_x(info["outputs"]))
    elif k == "townhall":
        note = i18n.Msg("ai.rules.bld.townhall", en=info['energy'],
                        base=building_effect('市政厅', 'gold_base'),
                        per=building_effect('市政厅', 'gold_per_slot'),
                        need=info['min_slots'], limit=info.get('limit', 1))
    elif k == "tower":
        note = i18n.Msg("ai.rules.bld.tower", r=building_effect('瞭望塔', 'vision_radius'))
    elif k == "diplomat":
        note = i18n.Msg("ai.rules.bld.diplomat", chain=_diplo_chain_text(),
                        floor=DIPLO_CENTER_MIN_COST, nlimit=info['limit_nation'],
                        need=info['min_slots'])
    elif k == "academy":
        note = i18n.Msg("ai.rules.bld.academy", disc=building_effect('工程院', 'build_discount'),
                        need=info['min_slots'])
    elif k == "militia_camp":
        note = i18n.Msg("ai.rules.bld.militia_camp", cost=_cost_msg(UNIT_TYPES['民']['recruit']),
                        cap=info['effects'].get('militia_cap', 1), hp=UNIT_TYPES['民']['hp'],
                        atk=UNIT_TYPES['民']['atk'], reach=_move_brief('民'))
    else:                                                       # barracks
        note = i18n.Msg("ai.rules.bld.barracks", en=info['energy'],
                        cap=info['effects'].get('recruit_cap', 1),
                        cost=_cost_msg(info['army_cost']), need=info['min_slots'])
    cap = (i18n.Msg("ai.rules.bld.cap_res", res=info["cap_resource"]) if info["cap_resource"]
           else i18n.Msg("ai.rules.bld.any_site"))
    return i18n.Msg("ai.rules.bld.line", nm=nm, cost=info["cost"], wood=info['wood'],
                    cap=cap, note=note)


def _sec_overview() -> i18n.Text:
    return i18n.Msg("ai.rules.overview", n=len(CROSS))


def _sec_terrain() -> i18n.Text:
    return i18n.Msg("ai.rules.terrain", rows=_terrain_rows(),
                    prose=i18n.Msg("ai.rules.terrain.prose"))


def _sec_buildings() -> i18n.Text:
    return i18n.Msg("ai.rules.buildings",
                    rows=_join([_building_line(nm, info) for nm, info in BUILDINGS.items()],
                               _by_nl),
                    max=MAX_SLOTS,
                    mult=f"{1 + TERRAIN_STATS['山地']['build_penalty'] / 100:g}",
                    huns=f"{POLITY['huns']['build_cost_pct'] / 100:.2g}")


def _sec_economy() -> i18n.Text:
    return i18n.Msg(
        "ai.rules.economy",
        sup_in=_cost_msg(BUILDINGS['补给厂']['inputs']),
        sup_out=_cost_msg(BUILDINGS['补给厂']['outputs']),
        eq_in=_cost_msg(BUILDINGS['装备厂']['inputs']),
        eq_out=_cost_msg(BUILDINGS['装备厂']['outputs']),
        inf_sup=UNIT_TYPES['步']['supply'], cav_sup=UNIT_TYPES['骑']['supply'],
        starve=ARMY_STARVE_DAMAGE, need=BUILDINGS['市政厅']['min_slots'],
        limit=BUILDINGS['市政厅']['limit'], en=BUILDINGS['市政厅']['energy'],
        base=building_effect('市政厅', 'gold_base'),
        per=building_effect('市政厅', 'gold_per_slot'))


def _sec_army() -> i18n.Text:
    return i18n.Msg(
        "ai.rules.army", maxhp=ARMY_MAX_HP,
        inf_cost=_cost_msg(UNIT_TYPES['步']['recruit']), inf_sup=UNIT_TYPES['步']['supply'],
        cav_cost=_cost_msg(UNIT_TYPES['骑']['recruit']), cav_sup=UNIT_TYPES['骑']['supply'],
        mil_cost=_cost_msg(UNIT_TYPES['民']['recruit']), mil_hp=UNIT_TYPES['民']['hp'],
        mil_atk=UNIT_TYPES['民']['atk'], mil_cap=BUILDINGS['军屯']['effects']['militia_cap'],
        move=_move_rule_text(), atk=UNIT_TYPES['步']['atk'],
        lv=BUILDINGS['城堡']['max_level'],
        combo=f"{100 - ((100 - TERRAIN_STATS['山地']['defense']) * (100 - BUILDINGS['城堡']['max_level'] * building_effect('城堡', 'defense_per_level'))) // 100}",
        cover=RETREAT_DEF_COVER, pen=RETREAT_ATK_PENALTY, rng=RETREAT_RANGE,
        heal=ARMY_HEAL_PER_TURN, starve=ARMY_STARVE_DAMAGE)


def _sec_bloc() -> i18n.Text:
    return i18n.Msg("ai.rules.bloc", maxlen=BLOC_NAME_MAX, ally=LETTER_COST_ALLY,
                    cost=LETTER_COST, free=LETTER_FREE_CHARS, per=LETTER_CHARS_PER_GOLD)


def _sec_diplomacy() -> i18n.Text:
    return i18n.Msg("ai.rules.diplomacy", fall=FALL_TRUCE_TURNS,
                    inf=UNIT_TYPES['步']['speed'], cav=UNIT_TYPES['骑']['speed'],
                    diplo=DIPLO_COST, ally=LETTER_COST_ALLY, cost=LETTER_COST,
                    free=LETTER_FREE_CHARS, disc=LETTER_CENTER_DISCOUNT, floor=LETTER_COST_MIN,
                    per=LETTER_CHARS_PER_GOLD, spy=SPY_COST, spy_turns=SPY_TURNS)


def _sec_mail() -> i18n.Text:
    return i18n.Msg("ai.rules.mail", ally=LETTER_COST_ALLY, cost=LETTER_COST,
                    free=LETTER_FREE_CHARS, per=LETTER_CHARS_PER_GOLD)


def _sec_market() -> i18n.Text:
    return i18n.Msg("ai.rules.market", spread=f"{MARKET_SPREAD:.0%}",
                    half=f"{MARKET_SPREAD / 2:.0%}",
                    base=_join([i18n.Msg("ai.fmt.res_item", label=g, v=MARKET[g])
                                for g in GOODS_DISPLAY], _by_gap))


def _sec_report() -> i18n.Text:
    return i18n.Msg("ai.rules.report", every=REPORT_EVERY,
                    t1=REPORT_EVERY + 1, t2=2 * REPORT_EVERY + 1, t3=3 * REPORT_EVERY + 1)


def _sec_turn() -> i18n.Text:
    return i18n.Msg("ai.rules.turn", plan=PLAN_MAX_TURNS)


def _rules_sections() -> list[tuple[str, i18n.Text]]:
    """规则全文（=《游戏说明书》的规则），按主题分节，供 `rules` 按需返回。"""
    return [("overview", _sec_overview()),
            ("terrain", _sec_terrain()),
            ("buildings", _sec_buildings()),
            ("economy", _sec_economy()),
            ("army", _sec_army()),
            ("bloc", _sec_bloc()),
            ("diplomacy", _sec_diplomacy()),
            ("mail", _sec_mail()),
            ("market", _sec_market()),
            ("report", _sec_report()),
            ("turn", _sec_turn())]


def _route(topic: str, lang: str | None) -> set[str]:
    """主题串 → 命中的节 id 集合（关键词表随语言走，中英各路由各的）。"""
    want: set[str] = set()
    for sid in RULE_SECTIONS:
        kws = [k for k in i18n.show(RULE_KEYWORDS[sid], lang).split(",") if k]
        if any(k in topic for k in kws):
            want.add(sid)
    return want


def rules_text(world, topic: str = "", lang: str | None = None) -> str:
    """rules tool：按主题返回规则段落；主题识别不了就返回全文（不设限）。"""
    want = _route((topic or "").strip(), lang)
    out = [i18n.Msg("ai.rules.block", title=RULE_TITLES[sid], body=body)
           for sid, body in _rules_sections() if not want or sid in want]
    return i18n.show(_join(out, _by_para), lang)     # 节与节之间空一行（版式，住模板）


def _fmt_threats(world, name, lang: str | None = None) -> str:
    """视野内（自家地盘+相邻一圈）的他国/野人军队。"""
    rows = [i18n.Msg("ai.panel.threats.row", nm=a['name'], who=a['owner'], hp=a['hp'],
                     x=a['x'] + 1, y=a['y'] + 1)
            for a in world.armies
            if a["owner"] != name and world.visible_to(name, a["x"], a["y"])]
    if not rows:
        return i18n.show(i18n.Msg("ai.panel.threats.none"), lang)
    return i18n.show(i18n.Msg("ai.panel.threats", rows=_join(rows, _by_nl)), lang)


def _fmt_news(world, name, lang: str | None = None) -> str:
    ev = world.events_for(name, limit=10, lang=lang)
    if not ev:
        return i18n.show(i18n.Msg("ai.panel.news.none"), lang)
    return i18n.show(i18n.Msg("ai.panel.news", rows=_join(ev, _by_nl)), lang)


def _fmt_memory(world, name, since: int | None = None, lang: str | None = None) -> str:
    """本国回合小结纪事（私有记忆，别国不可见）。

    since=已进 replay 的最早回合 → 只保留 replay 覆盖不到的更早小结，避免和完整记录重复。
    """
    mem = world.summaries.get(name, [])
    if since is not None:
        mem = [m for m in mem if int(m["turn"]) < since]
    if not mem:
        return i18n.show(i18n.Msg("ai.panel.memory.seen") if since is not None
                         else i18n.Msg("ai.panel.memory.none"), lang)
    return _rows([i18n.Msg("ai.panel.memory.row", turn=m['turn'], text=m['text'])
                  for m in mem[-10:]], lang)


def _fmt_plan(world, name, lang: str | None = None) -> str:
    """国策规划（常驻上下文）：无 plan 不能结束回合；每 PLAN_MAX_TURNS 回合须修订。"""
    pl = world.plans.get(name)
    if not pl or not str(pl.get("text", "")).strip():
        return i18n.show(i18n.Msg("ai.panel.plan.none"), lang)
    since = world.turn - pl.get("turn", world.turn)
    flag = ""
    if since >= PLAN_MAX_TURNS:
        flag = i18n.Msg("ai.panel.plan.stale", n=since, every=PLAN_MAX_TURNS)
    elif PLAN_MAX_TURNS - since == 1:
        flag = i18n.Msg("ai.panel.plan.due", every=PLAN_MAX_TURNS)
    return i18n.show(i18n.Msg("ai.panel.plan.row", turn=pl.get('turn'), flag=flag,
                              text=pl['text']), lang)


def _fmt_intel_hint(world, name, lang: str | None = None) -> str:
    """收到的地图情报摘要（简短提示，完整见 query panel=intel）。"""
    ms = world.maps.get(name, [])
    if not ms:
        return i18n.show(i18n.Msg("ai.panel.intel.none", turns=SPY_TURNS), lang)
    last = ms[-1]
    return i18n.show(i18n.Msg("ai.panel.intel.hint", n=len(ms), frm=last['from'],
                              turn=last['turn']), lang)


def _fmt_intel(world, name, lang: str | None = None) -> str:
    """完整地图情报：最近收到的别国地图。"""
    ms = world.maps.get(name, [])
    if not ms:
        return i18n.show(i18n.Msg("ai.panel.intel.empty"), lang)
    out = [i18n.Msg("ai.panel.intel.head", n=len(ms))]
    for m in ms[-3:]:
        out.append(i18n.Msg("ai.panel.intel.map_head", turn=m['turn'], frm=m['from']))
        out.append(m["text"])
    return _rows(out, lang)


def _fmt_spy_hint(world, name, lang: str | None = None) -> str:
    """收到的间谍情报摘要（完整见 query panel=spy）。"""
    es = world.econ_intel.get(name, [])
    if not es:
        return i18n.show(i18n.Msg("ai.panel.spy.none", cost=SPY_COST, turns=SPY_TURNS), lang)
    last = es[-1]
    return i18n.show(i18n.Msg("ai.panel.spy.hint", n=len(es), frm=last['from'],
                              turn=last['turn']), lang)


def _fmt_spy(world, name, lang: str | None = None) -> str:
    """完整间谍情报：最近拿到的别国经济底细 + 粗略军情（各兵种数量）。"""
    es = world.econ_intel.get(name, [])
    if not es:
        return i18n.show(i18n.Msg("ai.panel.spy.empty"), lang)
    return _rows([m["text"] for m in es[-2:]], lang)


# 常驻提醒（报表全文/趋势表都带这一行）：军费与发展投资的取舍
MIL_WARNING = i18n.Msg("ai.warn.military")


def _pct(v: float | None, sign: bool = True) -> str:
    """增长率/占比显示：None（无上期）→「—」。"""
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%" if sign else f"{v * 100:.1f}%"


def _fmt_report_one(rep: dict, lang: str | None = None) -> str:
    """单期经济报表。口径：GDP 与军费都是**结报那一回合**的实际值（run-rate，不平均、
    不攒期累计）；投资/资产/贸易仍是整期值。覆盖回合数只在表头交代时间跨度。"""
    days = rep.get("span", REPORT_EVERY)
    start = rep.get("period_start", rep["period_end"] - days + 1)
    span = i18n.Msg("ai.panel.report.span", a=start, b=rep['period_end'])
    if days != REPORT_EVERY:
        span = i18n.Msg("ai.panel.report.span_days", span=span, n=days)
    gdp, mil = rep["gdp"], rep["military"]
    fiscal = gdp - mil
    L = [i18n.Msg("ai.panel.report.head", turn=rep['report_turn'], span=span),
         i18n.Msg("ai.panel.report.gdp", gdp=gdp, g=_pct(rep['gdp_growth'])),
         i18n.Msg("ai.panel.report.fiscal", v=fiscal,
                  warn=(i18n.Msg("ai.panel.report.overrun") if fiscal < 0 else "")),
         i18n.Msg("ai.panel.report.military", mil=mil,
                  ratio=_pct(rep['military_ratio'], sign=False)),
         i18n.Msg("ai.panel.report.assets", v=rep['assets'], g=_pct(rep['assets_growth'])),
         i18n.Msg("ai.panel.report.invest", v=rep['invest'], g=_pct(rep['invest_growth'])),
         i18n.Msg("ai.panel.report.trade", out=_pct(rep['trade_ratio'], sign=False),
                  home=_pct(1 - rep['trade_ratio'], sign=False)),
         i18n.Msg("ai.panel.report.foot", n=rep.get('supply_eaten_turn', 0), mil=mil,
                  total=rep['supply_eaten'], imp=rep['import_gold'], exp=rep['export_gold']),
         "", MIL_WARNING]
    return _rows(L, lang)


def _fmt_report_trend(world, name, lang: str | None = None) -> str:
    """跨期趋势表：一行一期，便于比较不同时期。表头是文案（逐语言），列宽按渲染结果排。"""
    reps = world.econ_reports.get(name, [])
    header = [i18n.show(x, lang) for x in (
        i18n.Msg("ai.panel.report.h_period"), i18n.Msg("ai.panel.report.h_turn"),
        i18n.Msg("ai.panel.report.h_gdp"), i18n.Msg("ai.panel.report.h_gdp_g"),
        i18n.Msg("ai.panel.report.h_mil"), i18n.Msg("ai.panel.report.h_mil_r"),
        i18n.Msg("ai.panel.report.h_invest"), i18n.Msg("ai.panel.report.h_invest_g"),
        i18n.Msg("ai.panel.report.h_assets"), i18n.Msg("ai.panel.report.h_assets_g"),
        i18n.Msg("ai.panel.report.h_trade"))]
    rows = [header]
    for i, r in enumerate(reps, 1):
        rows.append([str(i), str(r["report_turn"]), f"{r['gdp']:.1f}", _pct(r['gdp_growth']),
                     f"{r['military']:.1f}", _pct(r['military_ratio'], sign=False),
                     f"{r['invest']:.0f}", _pct(r['invest_growth']), f"{r['assets']:.0f}",
                     _pct(r['assets_growth']), _pct(r['trade_ratio'], sign=False)])
    widths = [max(_dw(row[c]) for row in rows) for c in range(len(header))]
    lines = [i18n.Msg("ai.panel.report.trend_head", n=len(reps),
                      turns=_join([str(r['report_turn']) for r in reps], _by_dun))]
    lines += [i18n.Msg("ai.panel.report.trend_row",
                       cols="  ".join(_pad(row[c], widths[c]) for c in range(len(header))))
              for row in rows]
    lines += ["", MIL_WARNING]
    return _rows(lines, lang)


def _fmt_report(world, name, turn: int | None = None, all_: bool = False,
                lang: str | None = None) -> str:
    """本国经济报表（只读）：不传参数=最新一期；all_=跨期趋势表；turn=指定报表回合。"""
    reps = world.econ_reports.get(name, [])
    if not reps:
        return i18n.show(i18n.Msg("ai.panel.report.empty", every=REPORT_EVERY,
                                  t1=REPORT_EVERY + 1, t2=2 * REPORT_EVERY + 1,
                                  t3=3 * REPORT_EVERY + 1), lang)
    if all_:
        return _fmt_report_trend(world, name, lang)
    if turn is None:
        return _fmt_report_one(reps[-1], lang)
    for r in reps:
        if r["report_turn"] == turn:
            return _fmt_report_one(r, lang)
    return i18n.show(i18n.Msg(
        "ai.panel.report.no_turn", turn=turn, every=REPORT_EVERY,
        have=_join([i18n.Msg("ai.panel.report.turn_ref", n=r['report_turn']) for r in reps],
                   _by_dun)), lang)


def _fmt_report_panel(world, name, lang: str | None = None) -> str:
    """状态面板里的经济报表：**只在出表那一回合（第 11/21/31…）显示全文**，
    其余回合只给一行摘要——省上下文，需要时用 report 取全文/历史/趋势。"""
    reps = world.econ_reports.get(name, [])
    if not reps:
        return i18n.show(i18n.Msg("ai.panel.report.none_panel", every=REPORT_EVERY,
                                  t1=REPORT_EVERY + 1), lang)
    r = reps[-1]
    if world.turn == r["report_turn"]:                   # 本期刚出 → 全文
        text = _fmt_report_one(r, lang)
        if len(reps) > 1:
            text = i18n.show(i18n.Msg("ai.panel.report.more", body=text, n=len(reps)), lang)
        return text
    return i18n.show(i18n.Msg("ai.panel.report.latest", turn=r['report_turn'],
                              gdp=f"{r['gdp']:.1f}", g=_pct(r['gdp_growth']),
                              ratio=_pct(r['military_ratio'], sign=False),
                              assets=f"{r['assets']:.0f}"), lang)


def _econ_building(world, building: str, lang: str | None = None) -> str:
    """单个建筑的经济核算文案。**数字全部来自 mp.build_econ**——与规则 AI v9 同一份
    计算，LLM 看到的回本和 bot 排序用的回本不会有第二套口径。
    （文案与旧版逐字一致；仅加工厂「回本」从"毛利不含电"改为按含电的 per_turn 计，
      与 build_econ/`payback` 对齐——旧版这里本来自相矛盾：毛利行标了电耗、回本行不含。）"""
    info = BUILDINGS[building]
    e = build_econ(world, building)
    cost, capex, k = e["cost"], e["capex"], info["kind"]
    pb = (i18n.Msg("ai.econ.turns", n=f"{e['payback']:.0f}") if e["payback"]
          else i18n.Msg("ai.econ.dash"))
    if k == "castle":
        msg = i18n.Msg("ai.econ.castle", nm=building, cost=cost, wood=info['wood'],
                       capex=f"{capex:.0f}", per=building_effect('城堡', 'defense_per_level'))
    elif k == "militia_camp":
        msg = i18n.Msg("ai.econ.militia_camp", nm=building, capex=f"{capex:.0f}",
                       cost=_cost_msg(UNIT_TYPES['民']['recruit']),
                       cap=info['effects'].get('militia_cap', 1),
                       res=info['cap_resource'], limit=info.get('limit', 1))
    elif k in ("extract", "gold"):
        msg = i18n.Msg("ai.econ.extract", nm=building, capex=f"{capex:.0f}",
                       tag=(i18n.Msg("ai.econ.tag_gold") if k == "gold"
                            else i18n.Msg("ai.econ.tag_export")),
                       net=f"{e['detail']['net']:.0f}", payback=pb)
    elif k == "energy":
        msg = i18n.Msg("ai.econ.energy", nm=building, capex=f"{capex:.0f}",
                       fuel=f"{e['detail']['fuel']:.0f}", out=info['energy_out'])
    elif k == "factory":
        d = e["detail"]
        msg = i18n.Msg("ai.econ.factory", nm=building, capex=f"{capex:.0f}",
                       inv=f"{d['inputs_value']:.0f}", own=f"{d['outputs_buy']:.0f}",
                       sell=f"{d['outputs_sell']:.0f}", gross=f"{d['net']:+.0f}",
                       en=info.get('energy', 0), ec=f"{d['energy_cost']:.0f}", payback=pb)
    elif k == "barracks":
        msg = i18n.Msg("ai.econ.barracks", nm=building, capex=f"{capex:.0f}",
                       inf=_cost_msg(UNIT_TYPES['步']['recruit']),
                       cav=_cost_msg(UNIT_TYPES['骑']['recruit']))
    elif k == "townhall":
        msg = i18n.Msg("ai.econ.townhall", nm=building, capex=f"{capex:.0f}",
                       base=building_effect('市政厅', 'gold_base'),
                       per=building_effect('市政厅', 'gold_per_slot'), max=MAX_SLOTS,
                       full=building_effect('市政厅', 'gold_base')
                       + MAX_SLOTS * building_effect('市政厅', 'gold_per_slot'),
                       need=info['min_slots'], limit=info.get('limit', 1),
                       en=info.get('energy', 0))
    elif k == "tower":
        msg = i18n.Msg("ai.econ.tower", nm=building, capex=f"{capex:.0f}",
                       r=building_effect('瞭望塔', 'vision_radius'))
    elif k == "diplomat":
        msg = i18n.Msg("ai.econ.diplomat", nm=building, capex=f"{capex:.0f}",
                       chain=_diplo_chain_text(), nlimit=info['limit_nation'])
    elif k == "academy":
        msg = i18n.Msg("ai.econ.academy", nm=building, capex=f"{capex:.0f}",
                       disc=building_effect('工程院', 'build_discount'), need=info['min_slots'])
    else:
        msg = i18n.Msg("ai.econ.none", nm=building)
    return i18n.show(msg, lang)


def _fmt_econ(world, name: str | None = None, lang: str | None = None) -> str:
    """当前市价经济表：各建筑造价(折金)/毛利/回本，供建设决策。
    采集/金矿按「卖价」折算产出（外销口径）；加工厂按「买价」折算（自用替代口径）。"""
    L = [i18n.Msg("ai.panel.econ.head"),
         i18n.Msg("ai.panel.econ.prices",
                  prices=_join([i18n.Msg("ai.fmt.price_eq", g=g, p=f"{world.prices.get(g):.1f}")
                                for g in GOODS_DISPLAY], _by_gap),
                  spread=f"{MARKET_SPREAD:.0%}")]
    for b in BUILDINGS:
        L.append(i18n.Msg("ai.panel.econ.row", body=_econ_building(world, b, lang)))
    if name:
        ps = world.nation_armies(name)
        need = sum(unit_supply(a) for a in ps)
        if need:
            buy_p = world.market_quote("补给", 1, "buy")[0]
            L.append(i18n.Msg("ai.panel.econ.mine", n=len(ps), need=need,
                              price=f"{buy_p:.2f}", cost=f"{need * buy_p:.0f}"))
    L.append(i18n.Msg("ai.panel.econ.foot"))
    return _rows(L, lang)


def full_state(world, name, replay_since: int | None = None, lang: str | None = None) -> str:
    """本回合的新鲜状态（上下文尾部）。replay_since 见 turn_state。

    各分项面板是「表」，按 `lang` 各自渲染一次；这里只把成品嵌进带【标题】的框架
    ——框架本身（哪些段、什么顺序、什么标题）也是模板，所以英文局的标题是英文的。
    """
    others = [n for n in world.alive() if n != name] or i18n.Msg("ai.panel.full.only")
    return i18n.show(i18n.Msg(
        "ai.panel.full", nation=name, turn=world.turn, others=others,
        res=_res_line(world, name, lang), plan=_fmt_plan(world, name, lang),
        land=_fmt_land(world, name, lang=lang), army=_fmt_armies(world, name, lang),
        threats=_fmt_threats(world, name, lang), market=_fmt_market(world, name, lang),
        report=_fmt_report_panel(world, name, lang),
        memory=_fmt_memory(world, name, replay_since, lang),
        intel=_fmt_intel_hint(world, name, lang), spy=_fmt_spy_hint(world, name, lang),
        mail=_fmt_mail(world, name, True, lang),
        diplomacy=_fmt_diplomacy(world, name, lang),
        news=_fmt_news(world, name, lang)), lang)


def compact_state(world, name, lang: str | None = None) -> str:
    r = world.nations[name].res
    n_army = len([a for a in world.armies if a["owner"] == name])
    box = world.mailbox.get(name, [])
    return i18n.show(i18n.Msg("ai.panel.refresh", nation=name, gold=r['黄金'], food=r['粮食'],
                              wood=r['木头'], ore=r['矿石'], oil=r['石油'], equip=r['装备'],
                              supply=r['补给'], armies=n_army, mails=len(box),
                              rel=world.rel_desc(name)), lang)


# ---------------------------------------------------------------------------
# 坐标/物品解析
# ---------------------------------------------------------------------------

def _tile_xy(world, actor, ref: str, lang: str | None = None) -> tuple[int, int] | None:
    """ref 可形如 '5 6'（1-based 坐标）、地块名、或『名字 (x,y)』混合串。返回 0-based 坐标或 None。

    英文局里模型看到的是词库拼出来的地名（`ScarletPark`），它会照原样回传 ⇒ 名字比对
    过一遍 `label`（口径见 `i18n.label`：整串命中或逐语素拼）。
    """
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
        if not nm:
            continue
        hit = nm in s or (lang and lang != i18n.BASE_LANG and i18n.label(nm, lang) in s)
        if hit and len(nm) > bl:
            best, bl = (x, y), len(nm)
    return best


# 参数值 → 规范名：模型从面板上抄的是**显示名**（英文局 `Farm`/`Food`），落地前译回来。
#   键是 (工具, 参数)，因为 `kind` 在 recruit/peace/条约里完全不同义。
#   候选域必须给窄（`unlabel` 的 `among`）：全域反查会把 `Gold` 认成地名词素「金」。
_DENAME: dict[tuple[str, str], tuple] = {
    ("build", "building"): tuple(BUILDINGS),
    ("econ", "building"): tuple(BUILDINGS),
    ("recruit", "kind"): ("步", "骑", "民"),
    ("propose", "kind"): ("共同防御",),
    ("buy", "good"): tuple(GOODS_DISPLAY),
    ("sell", "good"): tuple(GOODS_DISPLAY),
    ("gift", "good"): tuple(GOODS_DISPLAY) + ("黄金",),
}


def _dename(tool: str, args: dict, lang: str | None) -> dict:
    """把带规范名取值的参数从显示名换回规范名（中文局直接原样返回）。"""
    if not lang or lang == i18n.BASE_LANG or not isinstance(args, dict):
        return args
    out = dict(args)
    for (tl, arg), among in _DENAME.items():
        if tl == tool and arg in out and isinstance(out[arg], str):
            out[arg] = i18n.unlabel(out[arg], lang, among)
    return out


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


# 「小结：」这类前缀也是文案 ⇒ 走各自的模板（见 `ai.mem.tag.*`）。

_TAG_LONG = "long"      # 长期记忆那条的**内部标签**（非数字 ⇒ 排序时垫底），显示名才走语言表


def _memory_search(world, name: str, args: dict, lang: str | None = None) -> str:
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
        return i18n.show(i18n.Msg("ai.mem.usage"), lang)
    _sep = {",", "，", "、", ";", "；", "	"}
    kws = [k for k in "".join(c if c not in _sep else " " for c in query).split() if k]
    if not kws:
        return i18n.show(i18n.Msg("ai.mem.empty_query", q=query), lang)

    # 收集候选单元：(回合标签, 正文, 前文, 后文)。标签里的数字是**回合号（数据）**，
    # 「长期记忆」这类占位标签才是文案，渲染时才定语言。
    units: list[tuple[str, str, str, str]] = []
    for rec in (world.turn_memory.get(name) or []):
        msgs = rec.get("messages") or []
        for i, m in enumerate(msgs):
            parts = []
            if m.get("role") == "tool":
                parts.append(i18n.show(m.get("content") or "", lang))
            else:
                if m.get("content"):
                    parts.append(i18n.show(m["content"], lang))
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
        units.append((str(it["turn"]),
                      i18n.show(i18n.Msg("ai.mem.tag.summary", text=str(it.get("text") or "")),
                                lang), "", ""))
    for b in (world.summary_blocks.get(name) or []):
        units.append((f"{b['from']}-{b['to']}",
                      i18n.show(i18n.Msg("ai.mem.tag.block", text=str(b.get("text") or "")),
                                lang), "", ""))
    lm = (world.long_memory.get(name) or "").strip()
    if lm:
        units.append((_TAG_LONG, lm, "", ""))
    pl = (world.plans.get(name) or {}).get("text", "")
    if pl:
        units.append((str((world.plans.get(name) or {}).get("turn", "") or 0),
                      i18n.show(i18n.Msg("ai.mem.tag.plan", text=str(pl)), lang), "", ""))

    scored = [(sum(1 for k in kws if k in u[1]), u) for u in units
              if any(k in u[1] for k in kws)]
    if not scored:
        return i18n.show(i18n.Msg("ai.mem.no_hit", q=query), lang)
    # 排序：分数降序 → 回合新→旧；非数字回合（长期记忆/阶段）放最后
    def _tkey(x):
        try:
            return -int(x[1][0])
        except (TypeError, ValueError):
            return 1
    scored.sort(key=lambda x: (-x[0], _tkey(x)))
    L = [i18n.Msg("ai.mem.hits", q=query, n=len(scored), limit=limit)]
    for i, (_, u) in enumerate(scored[:limit], 1):
        tag, text, prev, nxt = u
        head = (i18n.Msg("ai.panel.report.turn_ref", n=tag) if tag.isdigit()
                else i18n.Msg("ai.mem.head_tag",
                              tag=(i18n.Msg("ai.mem.tag.long") if tag == _TAG_LONG else tag)))
        L.append(i18n.Msg("ai.mem.hit_row", i=i, head=head, text=_mem_cut(text, _MEM_CUT)))
        if prev or nxt:
            L.append(i18n.Msg("ai.mem.hit_near", prev=prev,
                              mid=" … " if prev and nxt else "", nxt=nxt))
    return _rows(L, lang)


# ---------------------------------------------------------------------------
# 工具执行（隔离：所有动作都以 actor=本国身份执行，引擎自会校验合法）
# ---------------------------------------------------------------------------

def execute(world, actor: str, tool: str, args: dict, lang: str | None = None) -> i18n.Text:
    """执行一个工具调用，返回**回执**（喂回给 LLM 前由回合循环渲染成本国语言）。

    回执两种形状，界限就是「谁是读者」：
    · **查询面板 / 规则全文 / 报表** = 已按 `lang` 渲染好的成品串——它们是「表」，
      行内整句进模板、行间只是排版，且看海纪事要按 [:180] 截断存原文（整张表塞进
      键值会把存档撑爆）；
    · **动作回执**（引擎的与 `ai.err.*` 这类拒绝提示）= `Msg`，由边界决定语言 ⇒
      存进纪事的仍是键值，各国与看海端各渲染各的。
    `str()` 走缺省语言，所以直接拿它当字符串用的老调用点照旧。机制校验全在引擎内完成。

    顶层兜底：单次工具调用参数非法或内部异常只记为一次失败返回给模型，
    绝不让异常穿出回合循环炸掉整个看海进程（结算前的行动会全部丢失）。
    """
    if actor not in world.nations:
        return i18n.show(i18n.Msg("ai.err.dead"), lang)
    try:
        return _exec(world, actor, tool, args, lang)
    except Exception as e:
        return i18n.Msg("ai.err.tool_crash", tool=tool, etype=type(e).__name__, why=e)


def _charge(world, actor: str, cost: int, fn, *a, **k) -> i18n.Text:
    """外交/换图/馈赠/信件统一收费：先验国库，动作成功后扣费。

    动作合法失败（已同盟/已交战/物资不足等）不烧金，避免模型空试浪费；
    国库 < 费用时动作直接不发。
    """
    if world.res(actor, "黄金") < cost:
        return i18n.Msg("ai.err.no_gold", cost=cost, have=world.res(actor, '黄金'))
    ok, msg = fn(*a, **k)
    if ok:
        world.add_res(actor, "黄金", -cost)
    return msg


def _exec(world, actor: str, tool: str, args: dict, lang: str | None = None) -> i18n.Text:
    """execute 的实质分发（兜底由 execute 负责）。"""
    args = _dename(tool, args, lang)      # 面板上抄来的显示名先译回规范名，再进引擎
    if world.polity.get(actor) == "huns" and tool in HUNS_BLOCKED:
        return i18n.Msg("ai.err.huns_blocked", tool=tool)
    # ---- 面板（查询接口）
    if tool in ("query", "view", "panel", "查", "查询", "看", "面板"):
        which = str(args.get("panel", "all")).lower()
        return {
            "all": full_state(world, actor, lang=lang),
            "res": _res_line(world, actor, lang),
            "land": _fmt_land(world, actor, lang=lang),
            "army": _fmt_armies(world, actor, lang),
            "market": _fmt_market(world, actor, lang),
            "mail": _fmt_mail(world, actor, lang=lang),
            "diplomacy": _fmt_diplomacy(world, actor, lang),
            "countries": _fmt_countries(world, actor, lang),
            "news": _fmt_news(world, actor, lang),
            "threats": _fmt_threats(world, actor, lang),
            "econ": _fmt_econ(world, actor, lang),
            "intel": _fmt_intel(world, actor, lang),
            "spy": _fmt_spy(world, actor, lang),
            "plan": _fmt_plan(world, actor, lang),
        }.get(which, full_state(world, actor, lang=lang))

    # ---- 规则查询：**所有政体同一份**（`_rules_sections()` 按代码数值现算）
    #   政体专属的机制写在各自的 system prompt 里（如匈奴的【教义】），rules 不重复。
    if tool in ("rules", "规则", "help", "帮助"):
        return rules_text(world, str(args.get("topic", "") or ""), lang)

    # ---- 记忆检索（翻旧账：只搜本体正文，不含思考；免费只读）
    if tool in ("memory_search", "search_memory", "检索记忆", "记忆检索", "回想", "回忆", "history", "翻旧账"):
        return _memory_search(world, actor, args, lang)

    # ---- 外交对象（先选一个非自己的国家）
    if tool in ("countries", "外交对象", "国家列表", "对手"):
        return _fmt_countries(world, actor, lang)

    # ---- 经济核算（建设回报，按当前市价）
    if tool in ("econ", "核算", "经济核算", "预算", "回本"):
        b = str(args.get("building", "") or "")
        if b in BUILDINGS:
            return _econ_building(world, b, lang)
        return _fmt_econ(world, actor, lang)

    # ---- 经济报表（只读：每 10 回合自动结一期，没有任何手动生成入口）
    if tool in ("report", "报表", "经济报表"):
        raw_turn = args.get("turn")
        try:
            turn = int(raw_turn) if raw_turn not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            turn = None
        all_ = str(args.get("all", "")).strip().lower() in ("1", "true", "yes", "y", "是", "全部")
        return _fmt_report(world, actor, turn, all_, lang)

    # ---- 领土（无"凭空占"：只有军队 mv 移入"敌人=0"的地格才占地）
    if tool in ("expand", "拓荒", "activate"):
        return i18n.Msg("ai.tip.expand")

    # ---- 建设 / 征兵
    if tool in ("build", "建", "建造"):
        ref = str(args.get("tile", ""))
        xy = _tile_xy(world, actor, ref, lang)
        if xy is None:
            return i18n.Msg("ai.err.bad_tile", ref=ref)
        ok, msg = world.build(actor, xy[0], xy[1], str(args.get("building", "")))
        return msg
    if tool in ("recruit", "征兵", "r"):
        ref = str(args.get("tile", ""))
        xy = _tile_xy(world, actor, ref, lang)
        if xy is None:
            return i18n.Msg("ai.err.bad_tile_short", ref=ref)
        ok, msg = world.recruit(actor, xy[0], xy[1], int(args.get("n", 1)),
                                str(args.get("kind", "步") or "步"))
        return msg

    # ---- 军队
    if tool in ("move", "mv", "移动"):
        aid = int(args.get("army_id", 0))
        try:
            x, y = int(args["x"]) - 1, int(args["y"]) - 1
        except (KeyError, TypeError, ValueError):
            return i18n.Msg("ai.err.need_xy_1b")
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
            return i18n.Msg("ai.err.need_xy")
        ok, msg = world.attack(actor, aids, x, y)
        return msg
    if tool in ("retreat", "撤", "撤出"):
        aid = int(args.get("army_id", 0))
        try:
            x, y = int(args["x"]) - 1, int(args["y"]) - 1
        except (KeyError, TypeError, ValueError):
            return i18n.Msg("ai.err.need_xy_retreat")
        ok, msg = world.retreat(actor, aid, x, y)
        return msg

    # ---- 市场
    if tool in ("buy", "买"):
        g = GOOD_ALIAS.get(str(args.get("good", "")).lower())
        if g is None:
            return i18n.Msg("ai.err.bad_good", goods=list(GOODS_DISPLAY))
        ok, msg = world.buy(actor, g, int(args.get("qty", args.get("amount", 0))))
        return msg
    if tool in ("sell", "卖"):
        g = GOOD_ALIAS.get(str(args.get("good", "")).lower())
        if g is None:
            return i18n.Msg("ai.err.bad_good", goods=list(GOODS_DISPLAY))
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
            return i18n.Msg("ai.err.no_gold_letter", chars=len(text), cost=lc,
                            base=(LETTER_COST_ALLY if allied else LETTER_COST),
                            ally=(i18n.Msg("ai.letter.ally_tag") if allied else ""),
                            free=LETTER_FREE_CHARS, disc=LETTER_CENTER_DISCOUNT,
                            floor=LETTER_COST_MIN, over=max(0, len(text) - LETTER_FREE_CHARS),
                            per=LETTER_CHARS_PER_GOLD, have=world.res(actor, '黄金'))
        ok, msg = world.send_mail(actor, to, text)
        if ok:
            world.add_res(actor, "黄金", -lc)
            return i18n.Msg("ai.letter.sent", receipt=msg, chars=len(text), gold=lc)
        return msg

    # ---- 外交馈赠（本国储备垫支赠他国，下回合到账；另扣 10 金手续费）
    if tool in ("gift", "赠送", "赠予", "馈赠"):
        to = str(args.get("to", ""))
        g = GOOD_ALIAS.get(str(args.get("good", "")).lower())
        if g is None:
            g = {"黄金": "黄金", "金": "黄金", "gold": "黄金"}.get(str(args.get("good", "")).lower())
        if g is None:
            return i18n.Msg("ai.err.bad_good_gift", goods=list(GOODS_DISPLAY))
        try:
            n = int(args.get("qty", args.get("amount", 0)))
        except (TypeError, ValueError):
            return i18n.Msg("ai.err.need_int_qty")
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
                return i18n.Msg("ai.err.need_vote")
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
            return i18n.Msg("ai.err.need_plan_content")
        is_new = actor not in world.plans
        world.plans[actor] = {"text": text, "turn": world.turn}
        tail = i18n.Msg("ai.plan.tail", every=PLAN_MAX_TURNS)
        if is_new:
            return i18n.Msg("ai.plan.made", turn=world.turn, tail=tail)
        return i18n.Msg("ai.plan.revised", turn=world.turn, tail=tail)

    # ---- 结束回合（必须带一句话小结 + 有效国策）
    if tool in ("end_turn", "结束回合", "done"):
        pl = world.plans.get(actor)
        if not pl or not str(pl.get("text", "")).strip():
            return i18n.Msg("ai.turn.need_plan")
        since = world.turn - pl.get("turn", world.turn)
        if since >= PLAN_MAX_TURNS:
            return i18n.Msg("ai.turn.stale_plan", n=since, every=PLAN_MAX_TURNS)
        summary = str(args.get("summary", "")).strip()
        if len(summary) < SUMMARY_MIN_CHARS:
            return i18n.Msg("ai.turn.need_summary", min=SUMMARY_MIN_CHARS)
        world.log(i18n.Msg("ai.journal.turn_summary", nation=actor, summary=summary),
                  phase="行动", nation=actor)
        mem = world.summaries.setdefault(actor, [])
        mem.append({"turn": world.turn, "text": summary})  # 全留（每行很短），供旧回合前情回顾汇总
        return i18n.Msg("ai.turn.ended", summary=summary)
    return i18n.Msg("ai.err.unknown_tool", tool=tool)


def log_tool(world, actor, tool, args, result):
    """把一次工具调用写进看海日志（Observer 能看到每个行动）。

    `result` 可能是 `Msg` —— 那就**按键值存进纪事**，看的人各自渲染自己的语言；
    只有面板那类已渲染成品的回执才截断存原文（回执本身不再预先渲染，见 i18n.py）。
    参数值（`building=农场`）是规范名 ⇒ 纪事里也存规范名，`label` 只在**显示层**发生
    （存英文串进存档就等于把语言写进了存档，各国看到的纪事会互相污染）。
    """
    def _kv(k, v):
        val = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        return i18n.Msg("ai.fmt.kv", k=k, v=val)     # 值过 label 是在渲染时（i18n.render）

    a = args or {}
    a_str = _join([_kv(k, v) for k, v in a.items()], _by_sp)
    x = a.get("x")
    y = a.get("y")
    try:
        x = int(x) - 1 if x is not None else None
        y = int(y) - 1 if y is not None else None
    except (TypeError, ValueError):
        x = y = None
    res = result if isinstance(result, i18n.Msg) else str(result)[:180]
    world.action(actor, tool, a_str, res, x=x, y=y)


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


def _enum(values, lang: str) -> list[str]:
    """枚举值按查看语言显示（中文局逐字不变）。模型照面板抄回 `Farm` 时由 `_dename` 译回来。"""
    return [i18n.label(v, lang) for v in values]


def _make_schemas(lang: str, costs: dict[str, dict]) -> list[dict]:
    """按 **(语言, 政体征召价)** 生成 35 个工具的 schema。

    · `description` 与参数说明一律 `i18n.t(键, lang, …)` ⇒ 英文局拿到的描述是英文的，
      中文局逐字节等于键值化前那份（对拍钉着）；
    · `enum` 里是**规范名（数据）**：建筑名/兵种名/面板名不翻译，模型发回来的参数值
      也仍是规范名（`_exec` 按规范名校验，别名回退见 `GOOD_ALIAS`/`KIND_MAP`/`PACT_MAP`）；
    · 数值全部现算（`balance.py` / 政体表），模板里只留槽位 ⇒ 改表就改文案，不留第二套口径；
    · 结果只取决于 (lang, costs)，与回合无关 ⇒ 同一国跨回合拿到**字节完全相同**的 schema，
      提供商的前缀缓存才命中（`TOOL_SCHEMAS_TOKENS` 按这份固定开销计入上下文预算）。
    """
    P = costs
    return [
    {"type": "function", "function": {
        "name": "query", "description": i18n.t("ai.schema.query.desc", lang),
        "parameters": _props({"panel": {"type": "string", "enum": ["all", "res", "plan", "land", "army", "market", "econ", "intel", "spy", "mail", "countries", "diplomacy", "news", "threats"], "description": i18n.t("ai.schema.query.p.panel", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "report", "description": i18n.t("ai.schema.report.desc", lang, every=REPORT_EVERY, t1=REPORT_EVERY + 1, t2=2 * REPORT_EVERY + 1, t3=3 * REPORT_EVERY + 1),
        "parameters": _props({"turn": {"type": "integer", "description": i18n.t("ai.schema.report.p_turn", lang, t1=REPORT_EVERY + 1, t2=2 * REPORT_EVERY + 1, t3=3 * REPORT_EVERY + 1)},
                              "all": {"type": "boolean", "description": i18n.t("ai.schema.report.p_all", lang)}})}},
    {"type": "function", "function": {
        "name": "countries", "description": i18n.t("ai.schema.countries.desc", lang),
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "rules", "description": i18n.t("ai.schema.rules.desc", lang),
        "parameters": _props({"topic": {"type": "string", "description": i18n.t("ai.schema.rules.p_topic", lang)}})}},
    {"type": "function", "function": {
        "name": "econ", "description": i18n.t("ai.schema.econ.desc", lang),
        "parameters": _props({"building": {"type": "string", "enum": _enum(BUILD_NAMES, lang), "description": i18n.t("ai.schema.econ.p_building", lang)}})}},
    {"type": "function", "function": {
        "name": "build", "description": i18n.t(
            "ai.schema.build.desc", lang, buildings=_join(list(BUILDINGS), _by_slash),
            barracks_need=BUILDINGS['兵营']['min_slots'],
            tower_r=building_effect('瞭望塔', 'vision_radius'),
            th_need=BUILDINGS['市政厅']['min_slots'], th_limit=BUILDINGS['市政厅']['limit'],
            dip_limit=BUILDINGS['外交中心']['limit_nation'],
            academy_disc=building_effect('工程院', 'build_discount'),
            academy_need=BUILDINGS['工程院']['min_slots'],
            militia_cost=_cost_msg(UNIT_TYPES['民']['recruit']),
            camp_res=BUILDINGS['军屯']['cap_resource'], camp_limit=BUILDINGS['军屯']['limit']),
        "parameters": _props({"tile": {"type": "string", "description": i18n.t("ai.schema.build.p_tile", lang), "required": True},
                              "building": {"type": "string", "enum": _enum(BUILD_NAMES, lang), "description": i18n.t("ai.schema.build.p_building", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "recruit", "description": _recruit_desc(P).render(lang),
        "parameters": _props({"tile": {"type": "string", "description": i18n.t("ai.schema.recruit.p_tile", lang), "required": True},
                              "n": {"type": "integer", "description": i18n.t("ai.schema.recruit.p_n", lang)},
                              "kind": {"type": "string", "enum": _enum(["步", "骑", "民"], lang), "description": i18n.t("ai.schema.recruit.p_kind", lang)}})}},
    {"type": "function", "function": {
        "name": "move", "description": i18n.t("ai.schema.move.desc", lang, rule=_move_rule_text()),
        "parameters": _props({"army_id": {"type": "integer", "description": i18n.t("ai.schema.p_army_id", lang), "required": True},
                              "x": {"type": "integer", "description": i18n.t("ai.schema.p_x", lang), "required": True},
                              "y": {"type": "integer", "description": i18n.t("ai.schema.p_y", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "attack", "description": i18n.t("ai.schema.attack.desc", lang, reach=_reach_brief()),
        "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"}, "description": i18n.t("ai.schema.attack.p_ids", lang), "required": True},
                              "x": {"type": "integer", "description": i18n.t("ai.schema.p_x", lang), "required": True},
                              "y": {"type": "integer", "description": i18n.t("ai.schema.p_y", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "retreat", "description": i18n.t("ai.schema.retreat.desc", lang, rng=RETREAT_RANGE, cover=RETREAT_DEF_COVER, pen=RETREAT_ATK_PENALTY),
        "parameters": _props({"army_id": {"type": "integer", "description": i18n.t("ai.schema.p_army_id", lang), "required": True},
                              "x": {"type": "integer", "description": i18n.t("ai.schema.p_x", lang), "required": True},
                              "y": {"type": "integer", "description": i18n.t("ai.schema.p_y", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "buy", "description": i18n.t("ai.schema.buy.desc", lang, spread=f"{MARKET_SPREAD / 2:.0%}"),
        "parameters": _props({"good": {"type": "string", "description": i18n.t("ai.schema.buy.p_good", lang,
                                      goods=_join(list(GOODS_DISPLAY), _by_slash)), "required": True},
                              "qty": {"type": "integer", "description": i18n.t("ai.schema.p_qty", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "sell", "description": i18n.t("ai.schema.sell.desc", lang, spread=f"{MARKET_SPREAD / 2:.0%}"),
        "parameters": _props({"good": {"type": "string", "description": i18n.t("ai.schema.sell.p_good", lang), "required": True},
                              "qty": {"type": "integer", "description": i18n.t("ai.schema.p_qty", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "send_letter", "description": i18n.t("ai.schema.send_letter.desc", lang, ally=LETTER_COST_ALLY, cost=LETTER_COST, free=LETTER_FREE_CHARS, disc=LETTER_CENTER_DISCOUNT, floor=LETTER_COST_MIN, per=LETTER_CHARS_PER_GOLD),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.send_letter.p_to", lang), "required": True},
                              "content": {"type": "string", "description": i18n.t("ai.schema.send_letter.p_content", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "gift", "description": i18n.t("ai.schema.gift.desc", lang,
                           goods=_join(list(GOODS_DISPLAY), _by_slash), diplo=DIPLO_COST),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.p_to", lang), "required": True},
                              "good": {"type": "string", "description": i18n.t("ai.schema.gift.p_good", lang), "required": True},
                              "qty": {"type": "integer", "description": i18n.t("ai.schema.p_qty", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "share_map", "description": i18n.t("ai.schema.share_map.desc", lang, diplo=DIPLO_COST),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.p_to", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "spy", "description": i18n.t("ai.schema.spy.desc", lang, cost=SPY_COST, turns=SPY_TURNS),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.spy.p_to", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "plan", "description": i18n.t("ai.schema.plan.desc", lang, every=PLAN_MAX_TURNS),
        "parameters": _props({"content": {"type": "string", "description": i18n.t("ai.schema.plan.p_content", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_found", "description": i18n.t("ai.schema.bloc_found.desc", lang, maxlen=BLOC_NAME_MAX, diplo=DIPLO_COST),
        "parameters": _props({"name": {"type": "string", "description": i18n.t("ai.schema.bloc_found.p_name", lang, maxlen=BLOC_NAME_MAX), "required": True},
                              "tos": {"type": "array", "items": {"type": "string"}, "description": i18n.t("ai.schema.bloc_found.p_tos", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_join", "description": i18n.t("ai.schema.bloc_join.desc", lang, diplo=DIPLO_COST),
        "parameters": _props({"name": {"type": "string", "description": i18n.t("ai.schema.bloc_join.p_name", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_leave", "description": i18n.t("ai.schema.bloc_leave.desc", lang, diplo=DIPLO_COST),
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "bloc_rename", "description": i18n.t("ai.schema.bloc_rename.desc", lang, maxlen=BLOC_NAME_MAX),
        "parameters": _props({"name": {"type": "string", "description": i18n.t("ai.schema.bloc_rename.p_name", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_transfer", "description": i18n.t("ai.schema.bloc_transfer.desc", lang),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.bloc_transfer.p_to", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "bloc_dissolve", "description": i18n.t("ai.schema.bloc_dissolve.desc", lang),
        "parameters": _props({})}},
    {"type": "function", "function": {
        "name": "vote", "description": i18n.t("ai.schema.vote.desc", lang),
        "parameters": _props({"vote_id": {"type": "integer", "description": i18n.t("ai.schema.vote.p_id", lang), "required": True},
                              "choice": {"type": "string", "enum": ["yes", "no", "abstain"], "description": i18n.t("ai.schema.vote.p_choice", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "propose", "description": i18n.t("ai.schema.propose.desc", lang, diplo=DIPLO_COST),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.p_to", lang), "required": True},
                              "kind": {"type": "string", "enum": _enum(["共同防御"], lang), "description": i18n.t("ai.schema.propose.p_kind", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "respond_proposal", "description": i18n.t("ai.schema.respond_proposal.desc", lang),
        "parameters": _props({"proposal_id": {"type": "integer", "description": i18n.t("ai.schema.respond_proposal.p_id", lang), "required": True},
                              "accept": {"type": "boolean", "description": i18n.t("ai.schema.respond_proposal.p_accept", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "break_defense", "description": i18n.t("ai.schema.break_defense.desc", lang),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.p_to", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "guarantee", "description": i18n.t("ai.schema.guarantee.desc", lang, diplo=DIPLO_COST),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.guarantee.p_to", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "cancel_guarantee", "description": i18n.t("ai.schema.cancel_guarantee.desc", lang),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.p_to", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "declare_war", "description": i18n.t("ai.schema.declare_war.desc", lang),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.p_to", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "offer_peace", "description": i18n.t("ai.schema.offer_peace.desc", lang),
        "parameters": _props({"to": {"type": "string", "description": i18n.t("ai.schema.offer_peace.p_to", lang), "required": True},
                              "kind": {"type": "string", "enum": ["pay", "demand", "white"], "description": i18n.t("ai.schema.offer_peace.p_kind", lang), "required": True},
                              "gold": {"type": "integer", "description": i18n.t("ai.schema.offer_peace.p_gold", lang)},
                              "truce": {"type": "integer", "description": i18n.t("ai.schema.offer_peace.p_truce", lang)},
                              "note": {"type": "string", "description": i18n.t("ai.schema.offer_peace.p_note", lang)}})}},
    {"type": "function", "function": {
        "name": "accept_peace", "description": i18n.t("ai.schema.accept_peace.desc", lang),
        "parameters": _props({"offer_id": {"type": "integer", "description": i18n.t("ai.schema.peace.p_offer_id", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "reject_peace", "description": i18n.t("ai.schema.reject_peace.desc", lang),
        "parameters": _props({"offer_id": {"type": "integer", "description": i18n.t("ai.schema.peace.p_offer_id", lang), "required": True}})}},
    {"type": "function", "function": {
        "name": "memory_search", "description": i18n.t("ai.schema.memory_search.desc", lang),
        "parameters": _props({"query": {"type": "string", "description": i18n.t("ai.schema.memory_search.p_query", lang), "required": True},
                              "limit": {"type": "integer", "description": i18n.t("ai.schema.memory_search.p_limit", lang)}})}},
    {"type": "function", "function": {
        "name": "end_turn", "description": i18n.t("ai.schema.end_turn.desc", lang),
        "parameters": _props({"summary": {"type": "string", "description": i18n.t("ai.schema.end_turn.p_summary", lang, min=SUMMARY_MIN_CHARS), "required": True}})}},
    ]


# 中文那一份（无政体加成价目）：`docs/sync_manual.py` 与 `TOOL_SCHEMAS_TOKENS` 都读它，
# 模块级名字必须保留 ⇒ 它是 `_make_schemas` 在 (zh, 常规价) 下的产物。
TOOL_SCHEMAS = _make_schemas(i18n.BASE_LANG, {k: v["recruit"] for k, v in UNIT_TYPES.items()})

# 工具 schema 的固定 token 开销（每次请求都随 tools 发送，计入上下文预算）
TOOL_SCHEMAS_TOKENS = est_tokens(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))

_SCHEMA_CACHE: dict[tuple[str, str], list[dict]] = {}


def tool_schemas(world, name, lang: str | None = None) -> list[dict]:
    """该国的工具 schema：**按 (语言, 政体) 缓存**。

    · 语言逐国而定（配置里各国可写 `lang`），政体差异（如匈奴骑兵征召特价）由引擎现算
      （口径唯一来源：`World.recruit_cost()` 读 `balance.POLITY`）⇒ 不再对描述文本做
      字符串替换（那种补丁改一处漂一处）。
    · 中文 + 无政体 ⇒ 直接返回模块级 `TOOL_SCHEMAS`（同一份对象，前缀缓存的字节基准）。
    · 同一国跨回合拿到的是缓存里同一份对象 ⇒ schema 与回合无关，前缀缓存才不被打断。
    """
    lg = lang or i18n.get_lang()
    polity = world.polity.get(name) or ""
    if not polity and lg == i18n.BASE_LANG:
        return TOOL_SCHEMAS
    key = (lg, polity)
    if key not in _SCHEMA_CACHE:
        costs = ({k: world.recruit_cost(name, k) for k in UNIT_TYPES} if polity
                 else {k: v["recruit"] for k, v in UNIT_TYPES.items()})
        _SCHEMA_CACHE[key] = _make_schemas(lg, costs)
    return _SCHEMA_CACHE[key]


def _huns_prompt(world, name) -> i18n.Text:
    """匈奴（游牧政体）的 system prompt：政体约束 + 开局事实 + 八条教义。"""
    return i18n.Msg("ai.prompt.huns", nation=name,
                    pct=world.polity_rule(name, 'build_cost_pct', 100) - 100,
                    cav=_cost_msg(world.polity_rule(name, 'recruit', {}).get('骑', {})),
                    std=_cost_msg(UNIT_TYPES['骑']['recruit']), doc=_huns_doctrine())


def _default_system_prompt(world, name) -> i18n.Text:
    return i18n.Msg("ai.prompt.default", nation=name)


def _system_msg(world, name) -> i18n.Text:
    """本国 system prompt = 政体本稿 + 临时密谕/遗留总结（若还在有效期）+ 常驻尾注。"""
    base = (_huns_prompt(world, name) if world.polity.get(name) == "huns"
            else _default_system_prompt(world, name))
    extra = summary = ""
    ep = world.extra_prompt.get(name)
    if ep:
        if world.turn < ep.get("until", world.turn):
            extra = i18n.Msg("ai.prompt.extra", n=EXTRA_PROMPT_TURNS, text=ep.get("text", ""))
        elif ep.get("summary"):
            summary = i18n.Msg("ai.prompt.summary", text=ep["summary"])
    return i18n.Msg("ai.prompt.frame", base=base, extra=extra, summary=summary,
                    tail=i18n.Msg("ai.prompt.tail"))


def system_prompt(world, name, lang: str | None = None) -> str:
    """给模型的本国 system prompt（成品串：它要直接进 messages）。"""
    return i18n.show(_system_msg(world, name), lang)


def normalize_cfg(cfg: dict) -> dict:
    """补全上下文配置（就地）。small_ctx 是旧键：等价于按 256k 窗口标定。"""
    if cfg.get("small_ctx") and not cfg.get("ctx_window"):
        cfg["ctx_window"] = 262144
    return cfg


def _ctx_parts(world, name) -> tuple[list, list, list]:
    return (world.turn_memory.get(name) or [],
            world.summaries.get(name) or [],
            world.summary_blocks.get(name) or [])


def turn_state(world, name, replay_since: int | None = None, lang: str | None = None) -> str:
    """本回合的新鲜状态（消息尾部）。replay_since=已进 replay 的最早回合，
    用于把状态面板里重复的内容去掉（见 _fmt_memory/_fmt_news/_fmt_mail）。"""
    return (i18n.show(i18n.Msg("ai.context.turn_head", turn=world.turn), lang)
            + engine_call(full_state, world, name, replay_since, lang))


def build_context(world, name, cfg, lang: str | None = None) -> tuple[list[dict], "ctxlib.Plan"]:
    """构造一国本回合的完整 LLM 上下文（预算分配与缓存布局见 ctx.py）。

    顺序：system → 历史归档（已滑出回合的总结）→ replay（窗口内完整回合，含思考）
    → 本回合状态。返回 (messages, plan)，plan 供日志与下滑裁剪使用。

    状态面板的去重边界依赖 replay 起点，而 replay 深度又依赖状态大小——先按不去重
    （更保守）估一遍拿到起点，再用真实边界重建一次。
    """
    normalize_cfg(cfg)
    lg = lang or cfg.get("lang")
    system_text = engine_call(system_prompt, world, name, lg)
    mem, sums, blocks = _ctx_parts(world, name)

    def _build(tail: str):
        return ctxlib.build(cfg=cfg, mem=mem, sums=sums, blocks=blocks,
                            system_text=system_text, tail_text=tail,
                            tool_tokens=TOOL_SCHEMAS_TOKENS,
                            long_memory=world.long_memory.get(name, ""))

    tail1 = turn_state(world, name, None, lg)
    msgs, plan = _build(tail1)
    tail2 = turn_state(world, name, plan.before_turn, lg)
    if tail2 != tail1:
        msgs, plan = _build(tail2)
    return msgs, plan


def _store_turn_memory(world, name, messages, base: int, plan,
                       lang: str | None = None) -> list[dict]:
    """把本回合新增的消息（messages[base:]）存入该国 turn_memory，并按预算下滑。

    只存本回合 append 的部分（base 之前是历史 replay），避免把整个历史嵌进每条记录、
    记录间二次方膨胀。首条加 user 回合标记，回放时分隔回合边界。完整存，不截断。
    返回被裁掉的旧回合记录（调用方可据此生成阶段块总结）。
    """
    if name not in world.nations:
        return []
    body = messages[base:]
    rec = ([{"role": "user",
             "content": i18n.show(i18n.Msg("ai.context.record_head", turn=world.turn), lang)}]
           + [dict(m) for m in body])
    mem = world.turn_memory.setdefault(name, [])
    mem.append({"turn": world.turn, "messages": rec})
    return ctxlib.slide(world, name, plan)


# ---------------------------------------------------------------------------
# 阶段块总结：滑出 replay 的回合用一次 LLM 调用压成一段，进历史归档
# ---------------------------------------------------------------------------
COMPACT_SYSTEM = i18n.Msg("ai.compact.system")
COMPACT_INPUT_CHARS = 120_000   # 压缩调用的输入上限（约 6 万 token），超了从最旧略细节


def _compact_input(dropped: list[dict], sums: list[dict], from_turn: int,
                   lang: str | None = None) -> str:
    """拼压缩调用的输入：逐回合小结 + 行动记录（剥思考——不带 tools 时该字段被 API 忽略）。"""
    lines: list[str] = []
    by_turn = {int(s["turn"]): str(s.get("text", "")) for s in sums}
    turn_tag = lambda t: i18n.show(i18n.Msg("ai.compact.turn", turn=t), lang)
    for rec in dropped:
        t = int(rec["turn"])
        lines.append(turn_tag(t))
        if by_turn.get(t):
            lines.append(i18n.show(i18n.Msg("ai.compact.summary", text=by_turn[t]), lang))
        for m in rec.get("messages") or []:
            role = m.get("role")
            if role == "assistant":
                if m.get("content"):
                    lines.append(i18n.show(i18n.Msg("ai.compact.speech", text=m["content"]), lang))
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    lines.append(i18n.show(i18n.Msg("ai.compact.action", tool=fn.get('name'),
                                                    args=fn.get('arguments')), lang))
            elif role == "tool":
                txt = i18n.show(m.get("content") or "", lang)
                lines.append(i18n.show(i18n.Msg("ai.compact.result",
                                                text=txt if len(txt) <= 800 else txt[:800] + "…"),
                                       lang))
    text = "\n".join(lines)
    if len(text) > COMPACT_INPUT_CHARS:
        # 超长时保尾部（较新的回合信息更重要），头部留个说明
        head = i18n.show(i18n.Msg("ai.compact.truncated", turn=from_turn), lang)
        text = head + text[-COMPACT_INPUT_CHARS:]
    return text


def _compact_block(backend, cfg, world, name, dropped: list[dict], emit=None,
                   lang: str | None = None) -> dict | None:
    """把滑出 replay 的回合并入"递归累积的长期记忆"（酒馆式）。

    - 旧记忆已存在 → 以它为基础扩写（长程规划/盟约/教训不断层）；
    - 结果写回 world.long_memory（稳定头块，只在压缩回合变字节 → 缓存友好），
      同时也记一段 summary_blocks 保留块史。失败返回 None。
    """
    lg = lang or cfg.get("lang")
    sums = world.summaries.get(name) or []
    from_turn = int(dropped[0]["turn"])
    to_turn = int(dropped[-1]["turn"])
    prev = (world.long_memory.get(name) or "").strip()
    user = i18n.show(i18n.Msg(
        "ai.compact.user", mem=(prev if prev else i18n.Msg("ai.compact.empty")),
        frm=from_turn, to=to_turn,
        body=_compact_input(dropped, sums, from_turn, lg)), lg)
    if emit:
        emit(f"🧠 {name} 压缩记忆：第{from_turn}~{to_turn}回合 → 递归扩写长期记忆"
             + ("（有旧记忆为基础）" if prev else "（首次建立）"))
    text = backend.complete_text(
        [{"role": "system", "content": i18n.show(COMPACT_SYSTEM, lg)},
         {"role": "user", "content": user}], cfg)
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

def _pair_tool_calls(msgs: list[dict], lang: str | None = None) -> int:
    """给悬空的 tool_calls 补桩 tool 响应，返回补的数量（原地修复）。
    半途而废的批次（批中亡国 / 日志层抛错）会留下"assistant 声明了 N 个调用、
    只回了 k 个"的残缺对话——进 replay 后多数 OpenAI 兼容端点此后每回合 400，
    该国永久卡死在烧 max_steps 的空转里。入库/续跑前先修平。"""
    have = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    out, n = [], 0
    stub = i18n.show(i18n.Msg("ai.nudge.voided"), lang)
    for m in msgs:
        out.append(m)
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                tid = tc.get("id")
                if tid and tid not in have:
                    out.append({"role": "tool", "tool_call_id": tid, "content": stub})
                    have.add(tid)
                    n += 1
    if n:
        msgs[:] = out
    return n


def run_openai_turn(world, name, cfg, max_steps: int = 16, emit=None) -> int:
    """跑一国一回合：反复调 LLM 用工具，直到 end_turn **被引擎回执认可** / 正文宣告 / 步数上限。
    返回执行次数。

    提供方差异（OpenAI 兼容 / Anthropic 预留）收口在 llm_provider，循环只见
    OpenAI 形态消息。三条纪律：
    ① 结束只认 execute 的回执（✅）——模型递个非空 summary 不算收尾（拒绝文案
       会作为 tool 响应回喂，继续逼它补）；
    ② 任何 return / 异常续跑前都过 _pair_tool_calls——没配对的命令不许进记忆；
    ③ 一切**给模型看的字**都按本国 `lang` 渲染（面板/回执/催办语），而**进纪事的
       回执仍是键值**——看海端与别国各渲染各的。

    deepseek-v4-flash 这类推理模型把思考放在 reasoning_content（独立于 content），
    且可能连续多轮纯思考后才调用工具：每轮思考回显给下一轮，直到它真正行动。
    """
    backend = make_backend(cfg)
    lang = cfg.get("lang")         # 本国的显示语言（回执/面板/纪事都按它渲染；缺省=config 顶层）
    # 上下文：窗口大小由配置 ctx_window 定义，深度/归档/下滑水位由 ctx.py 按预算动态分配
    messages, plan = build_context(world, name, cfg, lang)
    if emit:
        _rl = ctxlib.rolling_hit(name)
        emit(f"🧠 {name} 上下文: {plan.describe()}"
             + (f"｜实测命中≈{_rl * 100:.0f}%（近20次滚动）" if _rl is not None else ""))
    base = len(messages)  # 本回合新增消息的起点（base 之前是历史 replay，存储时不再重复）
    done = 0
    stall = 0  # 连续"只思考/空转"轮数
    agg: dict = {"calls": 0, "wall": 0.0, "stream": 0.0, "first": 0.0,
                 "maxgap": 0.0, "out_tokens": 0, "reason_tokens": 0,
                 "hit": 0, "miss": 0}

    def _auto_summary(text) -> None:
        """收尾没带 summary 时，从正文里取一句补进回合小结——否则这一回合在归档里凭空消失。"""
        line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
        line = line or i18n.show(i18n.Msg("ai.turn.no_summary"), lang)
        line = (line[:80] + "…") if len(line) > 80 else line
        engine_call(world.summaries.setdefault(name, []).append,
                    {"turn": world.turn, "text": line})
        engine_call(world.log, i18n.Msg("ai.journal.auto_summary", nation=name, text=line),
                    phase="行动", nation=name)

    def _finish(d: int) -> int:
        _pair_tool_calls(messages, lang)     # 终闸：任何提前 return 的残批都在此处修平后才入库
        if agg.get("calls"):
            speed = (agg["out_tokens"] / agg["stream"]) if agg["stream"] > 0 else 0.0
            inp = agg["hit"] + agg["miss"]  # 缓存按输入前缀算：hit+miss=prompt tokens
            cache = (f"｜缓存命中{agg['hit'] / inp * 100:.0f}%({agg['hit']}/{inp}tok)"
                     if inp else "")
            if inp:
                ctxlib.record_hit(name, agg["hit"], agg["miss"])
            world.log(i18n.Msg("ai.journal.stats", nation=name, calls=agg['calls'],
                               wall=f"{agg['wall']:.0f}", out=agg['out_tokens'],
                               reason=agg['reason_tokens'], cache=cache,
                               first=f"{agg['first'] / agg['calls']:.0f}",
                               maxgap=f"{agg['maxgap']:.0f}", stream=f"{agg['stream']:.0f}",
                               speed=f"{speed:.1f}"), phase="事件")
        dropped = _store_turn_memory(world, name, messages, base, plan, lang)
        if dropped:
            if emit:
                emit(f"🧠 {name} 下滑：裁掉第{dropped[0]['turn']}~{dropped[-1]['turn']}回合"
                     f"（{len(dropped)} 回合）——本回合前缀缓存全段重建")
            if plan.compact and name in world.nations:
                try:
                    _compact_block(backend, cfg, world, name, dropped, emit=emit, lang=lang)
                except Exception as e:   # 压缩失败不影响主流程：归档退回一行小结
                    if emit:
                        emit(f"⚠ {name} 记忆压缩失败({type(e).__name__})，归档仍用逐回合小结")
        return d

    for step in range(max_steps):
        if name not in world.nations:
            return _finish(done)
        try:
            # 调用+流式聚合+重试分类全在 llm_provider（POSIX 硬超时/Windows 降级也在彼处），
            # 循环只见 (msg, stats)。on_retry 把重试进度回显给看海终端。
            msg, stream_stats = backend.chat_turn(
                messages, tool_schemas(world, name, lang), cfg,
                on_retry=lambda a, tag, wait, total: emit and emit(
                    f"⚠ {name} 第{a}次调用失败({tag})，{wait:.0f}s 后重试（共 {total} 次）"))
        except Exception as e:
            _pair_tool_calls(messages, lang)   # 防御：异常路径若留下未配对残批，续跑前先修平
            messages.append({"role": "user",
                             "content": i18n.show(i18n.Msg("ai.nudge.api_error", why=e), lang)})
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
            for tc in tool_calls:
                fn = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                raw = engine_call(execute, world, name, fn, args, lang)
                # 回执可能是 Msg（引擎与本层都按键值给）：喂模型前按**本国语言**渲染成字符串，
                # 记进纪事的仍是键值 —— 看海端与各国有各自的语言。
                result = i18n.show(raw, lang)
                is_end = fn in ("end_turn", "结束回合", "done")
                if not is_end:
                    # end_turn 的小结由 execute 自己记一条即可，避免在 feed 里重复
                    engine_call(log_tool, world, name, fn, args, raw)
                    if emit:
                        a_s = " ".join(f"{k}={v}" for k, v in (args or {}).items())
                        emit(f"{world.turn}回合·{name} ◇ {fn} {a_s}")
                        emit(f"      ↳ {result}")
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                done += 1
                if name not in world.nations:
                    return _finish(done)      # 批中亡国：剩余调用由 _finish 的配对闸补桩后入库
                if is_end:
                    # ★ 只认 execute 的回执：✅ 才算真结束。递了非空 summary 但没国策/国策过期/
                    #   小结太短，都被 execute 拒绝（拒绝文案已回喂），继续逼它补。
                    #   （en 模板同样以 ✅ 开头 ⇒ 这条判定链与语言无关。）
                    if str(result).startswith("✅"):
                        return _finish(done)
            if name in world.nations:  # 每次行动后都回填一次最新状态（默认塞查询）
                messages.append({"role": "user",
                                 "content": engine_call(compact_state, world, name, lang)})
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
                messages.append({"role": "user",
                                 "content": i18n.show(i18n.Msg("ai.nudge.need_plan"), lang)})
                stall += 1
                if stall >= 3:
                    _auto_summary(msg.get("content"))
                    return _finish(done)
                continue
            asst = {"role": "assistant", "content": msg.get("content")}
            if reasoning:
                asst["reasoning_content"] = reasoning
            messages.append(asst)
            engine_call(world.log, i18n.Msg("ai.journal.declare", nation=name, text=content),
                        phase="行动", nation=name)
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
                                 "content": i18n.show(i18n.Msg("ai.nudge.keep_going"), lang)})
                stall = 0
            continue
        # 空回复：催一次，若再空就结束——补兜底小结，别让这一回合在归档里凭空蒸发
        messages.append({"role": "user",
                         "content": i18n.show(i18n.Msg("ai.nudge.decide"), lang)})
        stall += 1
        if stall >= 3:
            _auto_summary(i18n.show(i18n.Msg("ai.nudge.silent"), lang))
            return _finish(done)
    _auto_summary(i18n.show(i18n.Msg("ai.nudge.max_steps"), lang))
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
