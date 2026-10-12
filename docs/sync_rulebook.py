#!/usr/bin/env python3
"""生成正规化规则册的数值表；数值源为 balance.py。"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import balance as B
from docs import sync_manual as M

PATH = ROOT / "docs" / "规则纲要.md"


def table(headers, rows):
    return M._table(headers, [[str(v) for v in row] for row in rows])


def buildings():
    rows = []
    for name, b in B.BUILDINGS.items():
        kind = b["kind"]
        cost = "/".join(map(str, b["cost"])) if kind == "castle" else b["cost"]
        limits = []
        if b.get("cap_resource"):
            limits.append("座数≤本地" + b["cap_resource"] + "资源点")
        if b.get("min_slots"):
            limits.append(f"已用位≥{b['min_slots']}")
        if b.get("limit"):
            limits.append(f"每格≤{b['limit']}")
        if b.get("limit_nation"):
            limits.append(f"全国自建≤{b['limit_nation']}")
        if kind == "castle":
            limits.append(f"等级≤{b['max_level']}")
        fx = b.get("effects", {})
        if kind == "extract":
            effect = "产" + M._cost_dict(b["outputs"]) + "/回合"
        elif kind == "gold":
            effect = f"产{b['outputs']['黄金'] * B.MARKET['黄金']:g}金/回合"
        elif kind == "energy":
            effect = M._cost_dict(b["fuel"]) + f"→{b['energy_out']}电/回合"
        elif kind == "factory":
            effect = M._cost_dict(b["inputs"]) + "→" + M._cost_dict(b["outputs"]) + f"；耗{b['energy']}电"
        elif kind == "castle":
            effect = f"每级防御{fx['defense_per_level']}%；每级占一位"
        elif kind == "barracks":
            effect = f"征召{fx['recruit_cap']}支/回合；耗{b['energy']}电"
        elif kind == "townhall":
            effect = f"国祚；产金={fx['gold_base']}+本格其他建筑位×{fx['gold_per_slot']}；耗{b['energy']}电"
        elif kind == "tower":
            effect = f"视野半径{fx['vision_radius']}；不耗电"
        elif kind == "diplomat":
            effect = "外交费减免；不耗电"
        elif kind == "academy":
            effect = f"本格其他建筑金价−{fx['build_discount']}%；不耗电"
        elif kind == "militia_camp":
            effect = f"征民{fx['militia_per_turn']}支/回合；编制及本格免供各{fx['militia_cap']}支；不产粮、不耗电"
        else:
            raise ValueError(f"未登记建筑类型：{kind}")
        rows.append([name, cost, b["wood"], "；".join(limits) or "—", effect])
    return table(["建筑", "金价", "木材", "限制（含在建）", "每座效果"], rows)


def units():
    return table(["兵种", "征召耗材", "满血HP", "攻击", "补给/回合", "移动力"], [
        [u["label"], M._cost_dict(u["recruit"]), u["hp"], u["atk"], u["supply"], u["speed"]]
        for u in B.UNIT_TYPES.values()
    ])


def combat():
    return table(["参数", "值"], [
        ["骰面→输出修正", "；".join(f"{k}→{v:+d}%" for k, v in B.COMBAT_DIE_MOD.items())],
        ["断粮扣血上限", B.ARMY_STARVE_DAMAGE],
        ["回血/回合", B.ARMY_HEAL_PER_TURN],
        ["每支正规军回血耗装备", B.HEAL_EQUIP_COST],
        ["撤退距离", B.RETREAT_RANGE],
        ["撤退输出削减", f"{B.RETREAT_ATK_PENALTY}%"],
        ["防御方撤退承伤比例", f"{B.RETREAT_DEF_COVER}%（100%=全额；0%=免伤）"],
    ])


def opening():
    return table(["资源", "标准开局库存"], B.START_RES.items()) + "\n\n" + table(["参数", "值"], [
        ["标准开局地块", "中心及其上下左右，共5格"],
        ["中心格", "平原；已落成市政厅1座"],
        ["每格建筑位", B.MAX_SLOTS],
        ["每格每回合动工", "1座或城堡1级"],
        ["建设工期", "当轮结算落成，下轮起生效"],
    ])


def movement():
    rows = []
    for kind, u in B.UNIT_TYPES.items():
        rows.append([u["label"], *[B.MOVE_COST[kind][t] for t in B.TERRAIN_STATS], u["speed"]])
    return table(["兵种", *B.TERRAIN_STATS, "移动力"], rows)


def tools():
    import mp_ai
    aliases = M._tool_aliases()
    groups = {
        "查询": "query report countries rules econ memory_search buy_report".split(),
        "经济与建设": "build recruit buy sell loan".split(),
        "军事": "move attack retreat disband".split(),
        "国家间外交": "send_letter gift share_map spy propose respond_proposal break_defense guarantee cancel_guarantee declare_war offer_peace accept_peace reject_peace".split(),
        "联盟": "bloc_found bloc_join bloc_leave bloc_rename bloc_transfer bloc_dissolve vote".split(),
        "玩家收尾": "plan end_turn".split(),
    }
    declared = {t["function"]["name"] for t in mp_ai.TOOL_SCHEMAS + list(mp_ai.BANK_TOOL_SCHEMAS)}
    listed = {name for names in groups.values() for name in names}
    if declared != listed:
        raise ValueError(f"命令表未同步：{declared ^ listed}")
    bank_names = {t["function"]["name"] for t in mp_ai.BANK_TOOL_SCHEMAS}
    return table(["类别", "命令", "别名"], [[group, f"`{name}`", "、".join(f"`{a}`" for a in aliases.get(name, []) if a != name) or "—"]
                 for group, names in groups.items() for name in names if name not in bank_names])


def settlement():
    import settlement as S
    return table(["结算项", "口径"], [
        ["排名", "累计建造＋征兵＋补给军费；按实际记账时市价折金"],
        ["不计分", "市场买卖、馈赠、存货"],
        ["亡国者", "保留累计得分；现状指标记零"],
        ["现状展示", "GDP、军力、领土、资产；不参与排名"],
        ["终局GDP与资产", "按基准价折算；区别于局内市价报表"],
        ["军力", "Σ（当前HP÷满血HP×兵种权重）；" + "、".join(f"{k}={v:g}" for k, v in S.UNIT_WEIGHT.items())],
        ["结算厅默认轮数", f"{S.CHAT_ROUNDS}；亡国者也列入名单"],
    ])


def bank():
    return table(["央行参数", "值"], [
        ["储蓄利率区间", f"{B.BANK_RATE_MIN:+.0%}～{B.BANK_RATE_MAX:+.0%}"],
        ["贷款利率", f"储蓄利率＋{B.BANK_SPREAD:.0%}"],
        ["授信倍数", B.BANK_LOAN_GDP_MULT],
        ["贷款期限", f"{B.BANK_LOAN_TURNS}回合"],
        ["借款手续费", f"基准{B.DIPLO_COST}金；适用外交中心减免"],
        ["报表购买费", f"{B.BUY_REPORT_COST}金/份"],
    ])


def terrain():
    return table(["地形", "防御", "建设金价修正"], [
        [name, f"{stats['defense']:+d}%", f"{stats['build_penalty']:+d}%"]
        for name, stats in B.TERRAIN_STATS.items()
    ])


def blocks():
    return {
        "start": opening(), "buildings": buildings(), "units": units(),
        "terrain": terrain(), "move": movement(), "combat": combat(),
        "diplo": M.blk_diplo(), "market": M.blk_market(), "bank": bank(),
        "rhythm": M.blk_rhythm(), "polity": M.blk_polity(),
        "tools": tools(), "settle": settlement(),
    }


def render(text):
    values = blocks()
    found = re.findall(r"<!-- AUTO:(\w+) BEGIN -->", text)
    if len(found) != len(set(found)) or set(found) != set(values):
        raise ValueError("数值表标记缺失、重复或未登记")
    for name, value in values.items():
        pattern = rf"(<!-- AUTO:{name} BEGIN -->\n).*?(<!-- AUTO:{name} END -->)"
        text, n = re.subn(pattern, lambda m: m[1] + "\n" + value + "\n" + m[2], text, flags=re.S)
        if n != 1:
            raise ValueError(f"数值表边界错误：{name}")
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    old = PATH.read_text(encoding="utf-8")
    new = render(old)
    if args.check:
        if old != new:
            print("规则册数值表漂移；运行 python3 docs/sync_manual.py")
            return 1
        print("规则册：全部数值表与 balance.py 一致")
    else:
        PATH.write_text(new, encoding="utf-8")
        print("规则册数值表已同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
