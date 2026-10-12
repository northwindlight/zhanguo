from ..core import *
from ..rules import *
from ..ui_constants import *
from ..presentation import _cost_text,_move_rule_text,_reach_brief,_recruit_desc

def _props(schema: dict) -> dict:
    """把每个属性里的 `required: True` 提到顶层（属性内不允许出现 required）。"""
    props, req = ({}, [])
    for k, v in schema.items():
        pv = dict(v)
        if pv.pop('required', False):
            req.append(k)
        props[k] = pv
    d = {'type': 'object', 'properties': props}
    if req:
        d['required'] = req
    return d

def definitions():
    result = [
        {"type": "function", "function": {
            "name": "report", "description": f"查本国经济报表（免费、只读）。每 {REPORT_EVERY} 回合**自动**结一期，第 {REPORT_EVERY+1}/{2*REPORT_EVERY+1}/{3*REPORT_EVERY+1}… 回合开局可查，**不能手动运行**。内容：市场计价 GDP 及增长率、扣除军费的财政收入、军费占 GDP 比、国家总资产及增长率、本期投资总量及增长率、外贸/内循环占比。不传参数=最新一期；turn=指定报表回合（如 {REPORT_EVERY+1}）；all=true=跨期趋势对比表。",
            "parameters": _props({"turn": {"type": "integer", "description": f"报表回合（{REPORT_EVERY+1}/{2*REPORT_EVERY+1}/{3*REPORT_EVERY+1}…）；省略=最新一期"},
                                  "all": {"type": "boolean", "description": "true=返回全部期的趋势对比表"}})}},
        {"type": "function", "function": {
            "name": "econ", "description": "按当前市价核算建设回报：某建筑的 造价(折金)/每回合毛利/回本时间；不带 building 则输出全部建筑经济表。做建设/买卖决策前先算再定。",
            "parameters": _props({"building": {"type": "string", "enum": BUILD_NAMES, "description": "要核算的建筑名（可选；省则输出全部）"}})}},
        {"type": "function", "function": {
            "name": "buy", "description": f"从世界市场买物资花黄金。买=推高市价；成交按「沿曲线均价」结算并含 {MARKET_SPREAD/2:.0%} 买价差，越急买越贵（试算见 query panel=market）。⚠ 同一回合在**同一个商品**上又卖又买＝**空转**（白付两趟价差），回执里会当场报出倒手量与净亏——先想清楚再下单。",
            "parameters": _props({"good": {"type": "string", "description": "物资：粮食/木头/矿石/石油/装备/补给", "required": True},
                                  "qty": {"type": "integer", "description": "数量", "required": True}})}},
        {"type": "function", "function": {
            "name": "sell", "description": f"向世界市场卖物资赚黄金。卖=压低市价；成交按「沿曲线均价」结算并扣 {MARKET_SPREAD/2:.0%} 卖价差，大单自己砸盘（试算见 query panel=market），分批慢慢卖更划算（**跨回合**分批才划算）。⚠ 同一回合在**同一个商品**上又买又卖＝**空转**（白付两趟价差），回执里会当场报出倒手量与净亏。",
            "parameters": _props({"good": {"type": "string", "description": "物资", "required": True},
                                  "qty": {"type": "integer", "description": "数量", "required": True}})}}
    ]
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
                           f"{REPORT_EVERY} 回合自动结一期，**摘要只通知本国**"
                           "，央行卖该国已生成报表的**明细全文**"
                           "（市场计价 GDP 及增长率、军费占比、总资产及增长、本期投资、外贸/内循环占比）。"
                           "要偷**当下**的国库、储备与每块地的建设，仍得用 spy（100 金、3 回合后到手）。"
                           "买到手进你的情报库（query panel=spy 可重看）。to=卖谁（不能是自己）；"
                           "turn=指定报表回合，省略=它最新一期。",
            "parameters": _props({
                "to": {"type": "string", "description": "买哪一国的报表（别国名）", "required": True},
                "turn": {"type": "integer", "description": "指定报表回合（如 21）；省略=它最新一期"}})}}
    result += [BANK_TOOL_SCHEMA, BUY_REPORT_TOOL_SCHEMA]
    return result
