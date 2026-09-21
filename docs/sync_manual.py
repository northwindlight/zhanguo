#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《游戏说明书》数值同步器：文档里的数字**一律**从 `balance.py` 现算，不手抄。

用法::

    python3 docs/sync_manual.py            # 就地刷新 docs/游戏说明书.md 里的 AUTO 块
    python3 docs/sync_manual.py --check     # 只校验：漂移就打印 diff 并以退出码 1 退出
    python3 docs/sync_manual.py --lint      # 顺手提示「AUTO 块外的正文里出现了数字」的行

三条纪律（与 `balance.py` 的「唯一权威」一致）：

1. **数字只住在 AUTO 块里。** 块内容全部由本文件从 `balance.py`（必要时 + `mp_ai.py`
   的工具表）现算，改平衡后跑一次刷新即可，不存在"文档抄了一份数字"。
2. **AUTO 块外的手改会被抓住。** `--check` 把文档里的块与现算结果逐字节比，
   不一致就报出 diff；`tests/test_manual_sync.py` 跑的就是同一条校验。
3. **遇到没覆盖的新东西就炸，不静默。** 新增建筑 kind / 新增工具而这里没写文案时，
   直接抛 `SyncError`（宁可红色报错，也不要文档里悄悄少一行）。

`--lint` 是**建议**（不改退出码）：正文里的数字迟早会与数值表脱钩，能挪进 AUTO 块就挪。
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys

DOC_PATH = pathlib.Path(__file__).resolve().parent / "游戏说明书.md"
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import balance as B  # noqa: E402  —— 纯数据模块（不 import 引擎，见 balance.py 的纪律 2）


class SyncError(RuntimeError):
    """文档与数值表对不上的硬错误（没覆盖的分支、工具表缺项…）。"""


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _cost_dict(d: dict) -> str:
    """{"粮食": 10, "装备": 5} → ``10 粮食 + 5 装备``。"""
    return " + ".join(f"{v} {k}" for k, v in d.items())


def _res_list(d: dict) -> str:
    return "、".join(f"{k} ×{v}" for k, v in d.items())


def _table(head: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


MOVE_TERRAINS = list(B.TERRAIN_STATS)  # 保持「平原/森林/丘陵/山地/沙漠」的顺序


# --------------------------------------------------------------------------
# 各 AUTO 块
# --------------------------------------------------------------------------


def blk_start() -> str:
    """开局与地块：开局资源 + 建筑位/每回合建造配额。"""
    rows = [[k, f"{v}"] for k, v in B.START_RES.items()]
    return (
        _table(["资源", "开盘数量"], rows)
        + "\n\n"
        + _table(
            ["地块与建设配额", "值"],
            [
                ["每国起家地块数", "5（中心 + 上下左右十字）"],
                ["中心格地形", "**必为平原**（国祚所在；该格资源按平原重算）"],
                ["开局市政厅", "核心格 1 座（白送、已落成；不受「本地已用位 ≥6」那道门槛约束）"],
                ["亡国条件", "**市政厅尽失**（领土还在也照亡；余土沦为无主之地，建筑留在原地）"],
                ["每地块建筑位", f"{B.MAX_SLOTS}（城堡的每一级也占一位）"],
                ["每地块每回合动工上限", "1 座"],
                ["在建工期", "1 回合（结算末尾落地，下回合起才生效）"],
            ],
        )
    )


def _b_cost(info: dict) -> str:
    cost = info["cost"]
    if isinstance(cost, list):
        return " → ".join(str(c) for c in cost) + "（逐级）"
    return str(cost)


def _b_limit(name: str, info: dict) -> str:
    parts = [f"≤ 本地{info['cap_resource']}" if info["cap_resource"] else "任地可建"]
    if info["kind"] == "castle":
        parts = [f"每地块一座（逐级升到 L{info['max_level']}）"]
    elif info.get("max_level"):
        parts.append(f"最多 L{info['max_level']}")
    if info.get("min_slots"):
        parts.append(f"需本地已用位 ≥{info['min_slots']}")
    if info.get("limit"):
        parts.append(f"每地块限 {info['limit']} 座")
    if info.get("limit_nation"):
        parts.append(f"自建全国限 {info['limit_nation']} 座")
    return "，".join(parts)


def _b_behavior(name: str, info: dict) -> str:
    k = info["kind"]
    eff = info.get("effects", {})
    if k == "castle":
        return f"每级 +{eff['defense_per_level']}% 防御（占建筑位）"
    if k == "extract":
        return "每回合 " + "、".join(f"{g} +{a}" for g, a in info["outputs"].items())
    if k == "gold":
        amt = info["outputs"]["黄金"] * B.MARKET["黄金"]
        return f"每回合 +{amt} 金入国库（稳定产金）"
    if k == "energy":
        return (f"耗 {_res_list(info['fuel'])} → 发 {info['energy_out']} 电"
                "（电不存储；能源厂自己不耗电维持）")
    if k == "factory":
        return (f"维持 {info['energy']} 电：投 {_res_list(info['inputs'])}"
                f" → 产 {_res_list(info['outputs'])}")
    if k == "barracks":
        return (f"维持 {info['energy']} 电：每座每回合可征 {eff['recruit_cap']} 支军队"
                f"（{_cost_dict(info['army_cost'])}/支）")
    if k == "townhall":
        return (f"维持 {info['energy']} 电：每座每回合 {eff['gold_base']} 金"
                f" + 本格其他建筑位数 × {eff['gold_per_slot']} 金入国库（不含自身，地越满越值）。"
                "**国祚**：厅全部易主/被毁即亡国（不要求领土尽失）")
    if k == "tower":
        return (f"不耗电：己方/盟方任一塔半径 {eff['vision_radius']} 圆内的事件可见"
                "（只扩事件视野，不增加可拓地）")
    if k == "diplomat":
        return ("不耗电：每座（含抢来的）外交费再减半、他国向你提议免费；"
                "写信起步价吃这个减免、超字费不吃")
    if k == "academy":
        return (f"不耗电：本地块一切建造金价 −{eff['build_discount']}%"
                "（含城堡升级，与地形惩罚乘算，只认已落成的）")
    if k == "militia_camp":
        return ("不耗电：**纯民兵编制，不产粮**"
                f"（每座每回合可征 {eff['militia_cap']} 支民兵，"
                "全国民兵总数 ≤ 全国军屯总数）")
    raise SyncError(f"未覆盖的建筑 kind：{k}（{name}）—— 请在本文件补一段行为文案")


def blk_buildings() -> str:
    rows = [[nm, _b_cost(info), str(info["wood"]), _b_limit(nm, info), _b_behavior(nm, info)]
            for nm, info in B.BUILDINGS.items()]
    body = _table(["建筑", "造价（金）", "耗木", "建造上限 / 门槛", "每回合行为"], rows)
    note = (
        "\n\n表中造价为**平原基准价**。实际金价按顺序**乘算**："
        "地形建设惩罚（`TERRAIN_STATS`，见 [`地图生成与资源分布.md`](地图生成与资源分布.md)）→ "
        f"本格**已落成**工程院的 −{B.BUILDINGS['工程院']['effects']['build_discount']}% → "
        f"政体（匈奴 ×{B.POLITY['huns']['build_cost_pct'] / 100:g}）。"
        "**只有金价被上浮，木材耗量不变**；城堡逐级升级，每级各花一次钱与木。"
        "采集类建筑的建造上限 = 该地块该项资源的**当前数值**（资源量即上限）。"
    )
    return body + note


def blk_units() -> str:
    rows = []
    for kind, u in B.UNIT_TYPES.items():
        costs = B.MOVE_COST.get(kind, {})
        speed = u["speed"]
        if not costs:
            move = f"移动力 {speed}"
        elif len(set(costs.values())) == 1:
            move = f"移动力 {speed}（任何地形每步 {next(iter(costs.values()))}）"
        else:
            detail = "、".join(f"{t} {costs[t]}" for t in MOVE_TERRAINS if t in costs)
            blocked = [t for t in MOVE_TERRAINS if costs.get(t, 1) >= speed]
            move = f"移动力 {speed}（每步：{detail}）"
            if blocked:
                move += f"；{'、'.join(blocked)}一步吃满 ⇒ 只能走 1 格、**穿不过去**"
        note = {
            "步": "兵营征召的常规主力",
            "骑": "兵营征召；平地跑得快，进山林就慢",
            "民": ("只能在自己军屯征召；驻**本格**军屯不耗补给（每座军屯覆盖本格 1 支，"
                   "离格/超额照常吃）；全国民兵总数 ≤ 全国军屯总数"),
        }.get(kind)
        if note is None:
            raise SyncError(f"未覆盖的兵种：{kind}（{u.get('label')}）—— 请在本文件补一段备注")
        rows.append([u["label"], _cost_dict(u["recruit"]), str(u["hp"]), str(u["atk"]),
                     str(u["supply"]), move, note])
    return _table(["兵种", "征召耗材", "满血", "基础伤害", "补给/回合", "移动", "备注"], rows)


def blk_move() -> str:
    rows = []
    for kind, costs in B.MOVE_COST.items():
        rows.append([B.UNIT_TYPES[kind]["label"]]
                    + [str(costs.get(t, 1)) for t in MOVE_TERRAINS]
                    + [str(B.UNIT_TYPES[kind]["speed"])])
    body = _table(["兵种"] + MOVE_TERRAINS + ["每回合移动力"], rows)
    return (body + "\n\n一步的实际代价 = **出发格与目标格取更贵的那个**（待在林子里也慢）；"
            "多格移动**逐格判定**：隔着一道山地/森林、或借道别人的地界，都过不去。")


def blk_combat() -> str:
    die = _table(["骰面（每回合每方各掷一枚）", "本回合伤害修正"],
                 [[str(k), ("+" if v >= 0 else "−") + f"{abs(v)}%"]
                  for k, v in B.COMBAT_DIE_MOD.items()])
    params = _table(["战斗参数", "值"], [
        ["每军满血（步 / 骑）", str(B.UNIT_TYPES["步"]["hp"])],
        ["每军满血（民兵）", str(B.UNIT_TYPES["民"]["hp"])],
        ["基础伤害（步 / 骑）", str(B.UNIT_TYPES["步"]["atk"])],
        ["基础伤害（民兵）", str(B.UNIT_TYPES["民"]["atk"])],
        ["守方加成的叠加方式", "地形防御与城堡防御**相乘**（叠不到无敌）"],
        ["撤退距离（不分兵种）", f"相邻 {B.RETREAT_RANGE} 格"],
        ["撤退军本回合输出", f"−{B.RETREAT_ATK_PENALTY}%"],
        ["防御方撤退时受到的伤害", f"{B.RETREAT_DEF_COVER}%（上限 100 = 免伤）"],
        ["断粮伤害（按缺口比例，上限）", f"−{B.ARMY_STARVE_DAMAGE} HP/回合（交战中照扣）"],
        ["非交战且补给充足的回血", f"+{B.ARMY_HEAL_PER_TURN} HP/回合"],
    ])
    return die + "\n\n" + params


def blk_diplo() -> str:
    chain = []
    c = B.DIPLO_COST
    while True:
        chain.append(str(c))
        if c <= B.DIPLO_CENTER_MIN_COST:
            break
        c = max(B.DIPLO_CENTER_MIN_COST, c // 2)
    costs = _table(["外交动作", "费用（金）"], [
        ["提议 / 回应邀约 / 解除共同防御 / 保障与撤回 / 宣战 / 求和（提出与回应）/ 换图 / 馈赠",
         f"{B.DIPLO_COST}（成功才扣；联盟成员之间免费）"],
        ["写信 `send_letter`", "按字数计价，见下"],
        ["间谍 `spy`", f"{B.SPY_COST}（固定价，不吃外交中心减免）"],
        ["盟内操作（投票 / 改盟名 / 移交盟主 / 解散）", "免费"],
    ])
    letter = _table(["写信计价", "值"], [
        ["起步价（非联盟）", f"{B.LETTER_COST} 金"],
        ["起步价（联盟内）", f"{B.LETTER_COST_ALLY} 金"],
        ["起步价内含免费字数", f"{B.LETTER_FREE_CHARS} 字"],
        ["超字费", f"每 {B.LETTER_CHARS_PER_GOLD} 字 +1 金"
                   f"（不足 {B.LETTER_CHARS_PER_GOLD} 字按 {B.LETTER_CHARS_PER_GOLD} 字算；"
                   "**不吃任何减免**）"],
        ["外交中心对起步价的减免", f"每座 −{B.LETTER_CENTER_DISCOUNT} 金，下限 {B.LETTER_COST_MIN} 金"],
    ])
    center = _table(["外交中心的叠减", "值"], [
        ["外交费", " → ".join(chain) + f"（下限 {B.DIPLO_CENTER_MIN_COST} 金）"],
        ["自建名额", f"全国限 {B.BUILDINGS['外交中心']['limit_nation']} 座"
                     "（第 2 座只能从别国手里抢）"],
        ["联盟名长度上限", f"{B.BLOC_NAME_MAX} 字（不含空格、全局唯一）"],
    ])
    return costs + "\n\n" + letter + "\n\n" + center


def blk_market() -> str:
    price = _table(["可交易物资", "基准价（金/单位）", "深度（单位）"],
                   [[g, str(B.MARKET[g]), str(B.MARKET_DEPTH.get(g, "—"))] for g in B.TRADEABLE])
    params = _table(["价格参数", "值"], [
        ["每单位推动市价", f"基准价 × {B.PRICE_IMPACT:g} ÷ 深度"],
        ["深度随国家数缩放", f"× 现存国家数 ÷ {B.MARKET_DEPTH_NATIONS_DIV}"],
        ["买卖价差", f"{B.MARKET_SPREAD * 100:g}%"
                     f"（买 +{B.MARKET_SPREAD * 50:g}% / 卖 −{B.MARKET_SPREAD * 50:g}%）"],
        ["每回合向均衡价回归", f"{100 - B.PRICE_REVERT * 100:g}%"],
        ["市价区间（占基准价）", f"{B.PRICE_MIN_RATIO:g}× ~ {B.PRICE_MAX_RATIO:g}×"],
        ["市价绝对下限", f"{B.PRICE_MIN_ABS:g} 金/单位（地板价 = 两者取高：便宜货可跌破 1 金）"],
        ["均衡价区间（占基准价）", f"{B.MARKET_EQ_MIN_RATIO:g}× ~ {B.MARKET_EQ_MAX_RATIO:g}×"],
        ["供需敏感度", f"全世界缺口比每 ±1，均衡价 ×(1 ± {B.MARKET_SENS:g})"
                       "（过剩变便宜、紧缺变贵）"],
        ["单边流量时的缺口比", f"±{B.MARKET_GAP_ONE_SIDE:g}"],
    ])
    return price + "\n\n" + params


def blk_rhythm() -> str:
    return _table(["节奏与限额", "值"], [
        ["间谍回报", f"派出后第 {B.SPY_TURNS} 回合"],
        ["国策最长有效期", f"{B.PLAN_MAX_TURNS} 回合（到期不修订则结束回合被拦）"],
        ["经济报表结期", f"每 {B.REPORT_EVERY} 回合自动一期（第 "
                         f"{B.REPORT_EVERY + 1}/{2 * B.REPORT_EVERY + 1}/"
                         f"{3 * B.REPORT_EVERY + 1}… 回合开局可查）"],
        ["回合小结最短字数", f"{B.SUMMARY_MIN_CHARS} 字"],
        ["灭国后全天下强制休战", f"{B.FALL_TRUCE_TURNS} 回合"],
        ["开局临时密谕（`extra_prompt`）有效期", f"{B.EXTRA_PROMPT_TURNS} 回合（之后只留小结）"],
    ])


def blk_bank() -> str:
    """世界央行（2026-09-22 授信改革）：只有一种贷款——额度、期限都不能自选。"""
    return _table(["世界央行（`world_bank` 配置开关）", "值"], [
        ["储蓄利率", "观察者设（`rate N`），可设区间 "
                     f"{B.BANK_RATE_MIN:+.0%} ~ {B.BANK_RATE_MAX:+.0%}；"
                     "国库现金默认就是储蓄，每回合结息（负则扣钱，**扣到 0 为止**）"],
        ["贷款利率", f"储蓄利率 + {B.BANK_SPREAD:.0%}（可为负 ⇒ 欠款每回合缩水）"],
        ["贷款额度", f"借款当时的 GDP × {B.BANK_LOAN_GDP_MULT}（**不能自选金额**）"],
        ["贷款期限", f"{B.BANK_LOAN_TURNS} 回合（**不能自选**；利息按复利滚，"
                     f"拖到 {B.BANK_LOAN_TURNS * 2} 回合要还 ≈ 本金 ×"
                     f"{(1 + B.BANK_SPREAD) ** (B.BANK_LOAN_TURNS * 2):.2f}）"],
        ["同时笔数", "一国一笔：**还清前不能再借**"],
        ["到期", "一次性**强制扣款**（这一笔允许把国库扣成负的）"],
        ["借款手续费", f"算外交动作，按外交费 {B.DIPLO_COST} 金计（成功才扣，外交中心照样减半）"],
        ["买别国报表", f"{B.BUY_REPORT_COST} 金一份（`buy_report`；买不到一分钱不收）"],
    ])


def _huns_blocked() -> list[str]:
    try:
        import mp_ai
    except Exception as e:  # pragma: no cover - 环境缺依赖时的明确报错
        raise SyncError(f"读不到 mp_ai.HUNS_BLOCKED（{type(e).__name__}: {e}）") from e
    canon = set(_tool_names())
    return sorted(t for t in mp_ai.HUNS_BLOCKED if t in canon)


def _start_text(start: dict) -> str:
    """政体缺省开局：``{"黄金": 1000, "补给": 200, "骑": 6}`` → 人话。"""
    words = {"黄金": "黄金 {v}", "补给": "补给 {v}", "骑": "骑兵 {v} 支"}
    return "、".join(words.get(k, "{k} {v}").format(k=k, v=v).replace("{v}", str(v))
                     for k, v in start.items())


def blk_polity() -> str:
    huns = B.POLITY["huns"]
    start = huns.get("start", {})
    base_ride = B.UNIT_TYPES["骑"]["recruit"]
    ride = huns.get("recruit", {}).get("骑", base_ride)
    params = _table(["匈奴政体", "值"], [
        ["建造金价", f"×{huns['build_cost_pct'] / 100:g}（与地形惩罚、工程院折扣乘算）"],
        ["骑兵征召特价", f"{_cost_dict(ride)}/支（寻常国家 {_cost_dict(base_ride)}）"],
        ["缺省开局", f"{_start_text(start)}（可用配置里的 `start_*` 逐项覆盖）"],
    ])
    blocked = _huns_blocked()
    block_txt = ("\n\n**外交限制（硬性）**：匈奴不参与结盟那套关系网。被禁的工具："
                 + "、".join(f"`{t}`" for t in blocked)
                 + "。可用的只剩：`send_letter`（写信勒索）、`spy`（刺探）、`declare_war`（宣战）、"
                   "`offer_peace` / `accept_peace` / `reject_peace`（求和与议和）。")
    return params + block_txt


# ---- 命令表（工具）：名字集必须与 TOOL_SCHEMAS 严格一致，多一个少一个都炸 ----

TOOL_GROUPS: list[tuple[str, dict[str, str]]] = [
    ("查询", {
        "query": "取面板（国库 / 国策 / 地皮逐格明细(可翻页·按建筑或资源过滤) / 单格明细 / 军队 / 市场 / 经济核算 / 情报 / 间谍报告 / 信箱 / 外交对象 / 外交（含【公开条约与战线】）/ 近讯 / 视野内敌军）",
        "report": "读本国经济报表（历史期与跨期趋势）",
        "countries": "列出可选外交对象（关系 / 是否接壤 / 有无来信）",
        "rules": "按主题查规则全文（不带主题就返回全部）",
        "econ": "按当前市价核算建筑回本",
        "memory_search": "按关键词检索本国历史正文（返回回合号与相邻上下文）",
    }),
    ("内政", {
        "build": "在自己的一块地上动工一座建筑",
        "recruit": "在有兵营的地块征召军队（民兵在军屯）",
        "buy": "在世界市场买入物资",
        "sell": "在世界市场卖出物资",
        "plan": "制定 / 修订国策（常驻上下文）",
    }),
    ("军事", {
        "move": "在本回合移动力内挪动军队（不占地）",
        "attack": "冲入目标格交战（无守军则直接进驻占领）",
        "retreat": "从交战格撤出（只能退相邻格）",
    }),
    ("外交", {
        "send_letter": "写信（按字数计价）",
        "gift": "馈赠资源或黄金",
        "share_map": "把整张已知地图发给对方",
        "spy": "派间谍，若干回合后拿回对方经济底细与粗略军情",
        "propose": "发起提议（结盟 / 共同防御等）",
        "respond_proposal": "回应收到的提议",
        "break_defense": "单方面解除共同防御",
        "guarantee": "保障某国独立（别人打它你参战）",
        "cancel_guarantee": "单方面撤回保障",
        "declare_war": "宣战（对方必须应战）",
        "offer_peace": "求和（赔款 / 索款 / 白和，可附休战回合数）",
        "accept_peace": "接受议和",
        "reject_peace": "拒绝议和",
    }),
    ("联盟", {
        "bloc_found": "发起立盟（必须起名并邀请创始成员）",
        "bloc_join": "申请加入某个联盟（现成员投票）",
        "bloc_leave": "单方面退盟（盟主不能退）",
        "bloc_rename": "改联盟名（盟主）",
        "bloc_transfer": "移交盟主（盟主）",
        "bloc_dissolve": "解散联盟（盟主）",
        "vote": "对联盟投票表态（可改票；不投算弃权）",
    }),
    ("收尾", {
        "end_turn": "结束本回合（必须带一句回合小结；以引擎回执为准）",
    }),
]


def _tool_names() -> list[str]:
    try:
        import mp_ai
    except Exception as e:  # pragma: no cover
        raise SyncError(f"读不到 mp_ai.TOOL_SCHEMAS（{type(e).__name__}: {e}）") from e
    names = [t["function"]["name"] for t in mp_ai.TOOL_SCHEMAS]
    mapped = [n for _, g in TOOL_GROUPS for n in g]
    if sorted(names) != sorted(mapped):
        only_code = sorted(set(names) - set(mapped))
        only_doc = sorted(set(mapped) - set(names))
        raise SyncError("命令表与 TOOL_SCHEMAS 对不上："
                        f"代码里多出 {only_code or '—'}；文档里多出 {only_doc or '—'}"
                        "（新增工具请在 TOOL_GROUPS 里登记一句话用途）")
    return names


def _tool_aliases() -> dict[str, list[str]]:
    """从 `mp_ai.execute` 的分派表里抓别名（`if tool in ("move", "mv", "移动")`）。"""
    canon = set(_tool_names())
    src = (ROOT / "mp_ai.py").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"if tool in \(([^)]*)\):", src):
        names = re.findall(r'"([^"]+)"', m.group(1))
        if not names or names[0] not in canon:
            continue   # 分派表里的历史别名（如 expand/拓荒、break_alliance），不是面向玩家的工具
        bucket = out.setdefault(names[0], [])
        for a in names[1:]:
            if a not in canon and a not in bucket:
                bucket.append(a)
    return out


def blk_tools() -> str:
    _tool_names()          # 先做一次集合校验
    alias = _tool_aliases()
    rows = []
    for label, group in TOOL_GROUPS:
        for name, use in group.items():
            al = alias.get(name) or []
            shown = f"`{name}`" + (f"（{'、'.join(al)}）" if al else "")
            rows.append([label, shown, use])
    body = _table(["类别", "工具名（别名）", "用途"], rows)
    return (body + "\n\n工具名**中英文等价**（`build` = `建造`、`attack` = `atk` = `进攻`…），"
            "上表别名一列由 `mp_ai.execute` 的分派表现算。"
            "参数非法或内部异常只记一次失败返回给玩家，不会炸掉整局。")

def _settle_mod():
    try:
        import settlement
    except Exception as e:  # pragma: no cover
        raise SyncError(f"读不到 settlement.py 的结算口径（{type(e).__name__}: {e}）") from e
    return settlement


def blk_settle() -> str:
    """终局结算口径（`settlement.py`；这几项**刻意**不在 `balance.py` 里，属结算口径非游戏规则）。"""
    S = _settle_mod()
    weight = "、".join(f"{k} {v:g}" for k, v in S.UNIT_WEIGHT.items())
    spend = " + ".join(S.SPEND_LABELS.values())
    return _table(["结算项", "口径"], [
        ["排名依据", f"**总消费 = {spend}**（全部按**当时市价**折金）"],
        ["不计入总消费", "市场买卖、馈赠（买来的物资在被消耗时才入账）、**存货**——囤而不用的垫底"],
        ["已亡国", "同样上榜（按累计消费排名，四维现状记 0）"],
        ["四维现状（只列示、不参与排名）",
         f"GDP（生产法推算）· 军力（Σ 当前HP/满血 × 兵种权重 {weight}）· 领土（地块数）· "
         "资产（建筑重置成本）"],
        ["GDP 与资产的计价", f"按**基准价**结算（不用市价：市价随回合波动，基准价才是恒定标尺）"],
        ["结算厅轮数", f"{S.CHAT_ROUNDS} 轮（`--rounds N` 可改；亡国者不进聊天室）"],
    ])


BLOCKS: dict[str, object] = {
    "start": blk_start,
    "buildings": blk_buildings,
    "units": blk_units,
    "move": blk_move,
    "combat": blk_combat,
    "diplo": blk_diplo,
    "market": blk_market,
    "bank": blk_bank,
    "rhythm": blk_rhythm,
    "polity": blk_polity,
    "settle": blk_settle,
    "tools": blk_tools,
}


# --------------------------------------------------------------------------
# 读写与校验
# --------------------------------------------------------------------------


def block_re(block_id: str) -> re.Pattern:
    return re.compile(
        rf"(?P<head><!-- AUTO:{block_id} BEGIN[^\n]*-->\n)(?P<body>.*?)"
        rf"(?P<tail>\n<!-- AUTO:{block_id} END -->)",
        re.DOTALL,
    )


def render(doc: str) -> str:
    """把 doc 里所有 AUTO 块刷新成现算内容（不改块外一个字）。"""
    for block_id, fn in BLOCKS.items():
        m = block_re(block_id).search(doc)
        if not m:
            raise SyncError(f"文档里找不到 AUTO 块：{block_id}")
        doc = doc[:m.start("body")] + "\n" + fn() + doc[m.end("body"):]
    return doc


def lint_prose(doc: str) -> list[str]:
    """列出 AUTO 块外正文里带数字的行（建议挪进数值表；行内含 lint-ok 的跳过）。

    「带数字」指**光秃秃的数字**：代码块、行内 `code`、Markdown 链接（`[名](路径)`）
    里的数字不算（那些是提交号 / 文件名 / 标识符，本来就不该进数值表）。
    """
    stripped = doc
    for block_id in BLOCKS:
        stripped = block_re(block_id).sub(
            lambda m: m.group("head") + "\n" + m.group("tail"), stripped)
    stripped = re.sub(r"```.*?```", "", stripped, flags=re.DOTALL)   # 代码块
    stripped = re.sub(r"`[^`]*`", "", stripped)                       # 行内代码
    stripped = re.sub(r"\[[^\]]*\]\([^)]*\)", "", stripped)           # 链接
    hits = []
    for i, line in enumerate(stripped.splitlines(), 1):
        if "lint-ok" in line or not re.search(r"\d", line):
            continue
        hits.append(f"  第 {i} 行：{line.strip()[:96]}")
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="刷新/校验《游戏说明书》里的 AUTO 数值块")
    ap.add_argument("--check", action="store_true", help="只校验，不写盘；漂移则退出码 1")
    ap.add_argument("--lint", action="store_true", help="额外列出正文里的数字行（建议项）")
    ap.add_argument("--doc", default=str(DOC_PATH), help="文档路径（默认 docs/游戏说明书.md）")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.doc)
    if not path.exists():
        print(f"找不到文档：{path}", file=sys.stderr)
        return 2
    old = path.read_text(encoding="utf-8")
    try:
        new = render(old)
    except SyncError as e:
        print(f"✗ 同步失败：{e}", file=sys.stderr)
        return 1

    if args.check:
        if old == new:
            print(f"✓ AUTO 块与 balance.py 一致（{len(BLOCKS)} 块：{'、'.join(BLOCKS)}）")
        else:
            print("✗ AUTO 块已漂移（跑 python3 docs/sync_manual.py 刷新）：", file=sys.stderr)
            sys.stderr.writelines(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True),
                fromfile="文档现状", tofile="balance.py 现算"))
        if args.lint:
            hits = lint_prose(old)
            print("⚠ 正文里的数字（建议挪进 AUTO 块）：" if hits else "✓ 正文无游离数字")
            print("\n".join(hits))
        return 0 if old == new else 1

    if old == new:
        print("已是最新，未改动。")
    else:
        path.write_text(new, encoding="utf-8")
        print(f"✓ 已刷新 {path.relative_to(ROOT)}（{len(BLOCKS)} 个 AUTO 块）")
    if args.lint:
        hits = lint_prose(new)
        print("⚠ 正文里的数字（建议挪进 AUTO 块）：" if hits else "✓ 正文无游离数字")
        print("\n".join(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
