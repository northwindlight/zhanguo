from __future__ import annotations
from ..core import *
from engine.runtime import Tool

def tool_econ(world, actor, **args):
    from ..presentation import BUILDINGS, _econ_building, _fmt_econ
    tool = 'econ'
    b = str(args.get('building', '') or '')
    if b in BUILDINGS:
        return _econ_building(world, b)
    return _fmt_econ(world, actor)

def tool_report(world, actor, **args):
    from ..presentation import _fmt_report
    tool = 'report'
    raw_turn = args.get('turn')
    try:
        turn = int(raw_turn) if raw_turn not in (None, '', 0, '0') else None
    except (TypeError, ValueError):
        turn = None
    all_ = str(args.get('all', '')).strip().lower() in ('1', 'true', 'yes', 'y', '是', '全部')
    return _fmt_report(world, actor, turn, all_)

def tool_loan(world, actor, **args):
    from ..presentation import BANK_LOAN_GDP_MULT, BANK_LOAN_TURNS, _charge, _diplo_cost
    tool = 'loan'
    msg = _charge(world, actor, _diplo_cost(world, actor), world.bank_loan, actor)
    given = [k for k in ('amount', 'qty', 'turns', '期', '额') if args.get(k)]
    if given:
        msg += f"（你给的 {'、'.join(given)} 已被忽略：贷款额与期限都不能自选，一律按 当时 GDP×{BANK_LOAN_GDP_MULT} / {BANK_LOAN_TURNS} 回合放款）"
    return msg

def tool_buy_report(world, actor, **args):
    from ..presentation import _fmt_report_one, _report_of
    tool = 'buy_report'
    to = str(args.get('to', '')).strip()
    turn = args.get('turn', args.get('期', None))
    if turn not in (None, '', 0, '0'):
        try:
            turn = int(turn)
        except (TypeError, ValueError):
            return '报表回合（turn）要写数字，如 turn=21；省略=最新一期'
    else:
        turn = None
    rep = _report_of(world, to, turn)
    if isinstance(rep, str):
        return rep
    return world.bank_sell_report(actor, to, rep['report_turn'], _fmt_report_one(rep))[1]

def tool_buy(world, actor, **args):
    from ..presentation import GOODS_DISPLAY, GOOD_ALIAS
    tool = 'buy'
    g = GOOD_ALIAS.get(str(args.get('good', '')).lower())
    if g is None:
        return '物资名无效：' + '、'.join(GOODS_DISPLAY)
    ok, msg = world.buy(actor, g, int(args.get('qty', args.get('amount', 0))))
    return msg

def tool_sell(world, actor, **args):
    from ..presentation import GOODS_DISPLAY, GOOD_ALIAS
    tool = 'sell'
    g = GOOD_ALIAS.get(str(args.get('good', '')).lower())
    if g is None:
        return '物资名无效：' + '、'.join(GOODS_DISPLAY)
    ok, msg = world.sell(actor, g, int(args.get('qty', args.get('amount', 0))))
    return msg

def definitions():
    return (
        Tool('econ', tool_econ, aliases=('核算', '经济核算', '预算', '回本'), argument_types={'building': str}),
        Tool('report', tool_report, aliases=('报表', '经济报表'), argument_types={'turn': int, 'all': bool}),
        Tool('loan', tool_loan, aliases=('贷款', '借款'), argument_types={}),
        Tool('buy_report', tool_buy_report, aliases=('买报表', '购买报表', '央行报表'), argument_types={}),
        Tool('buy', tool_buy, aliases=('买',), argument_types={'good': str, 'qty': int}),
        Tool('sell', tool_sell, aliases=('卖',), argument_types={'good': str, 'qty': int}),
    )
