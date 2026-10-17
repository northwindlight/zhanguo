"""Bank toolcalls: credit, position and report purchases."""
from engine.runtime import Action, Tool

from .economy import _reports


def _num(value):
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def tools(runtime):
    def borrow(world, actor):
        return runtime.player_api(world, 'economy:bank').borrow(actor)

    def bank(world, actor):
        return runtime.player_api(world, 'economy:bank').position(actor)

    def buy_report(world, actor, target: str, turn: int | None = None):
        return runtime.player_api(world, 'economy:bank').buy_report(actor, target, turn)

    return (
        Action('borrow', borrow, owner='economy:bank', api=('borrow',),
             description='借入最近已结算 GDP×5 的额度，借款期限5周期，利率为储蓄利率+2%。成功时收外交费（基准10金、外交中心逐座减半）；不能自选金额。'),
        Tool('bank', bank, owner='economy:bank', api=('position',), read_only=True, fact=True, panel=True,
             description='本国现金、信用额度、负债与贷款剩余；无需参数。'),
        Action('buy_report', buy_report, owner='economy:bank', api=('buy_report',),
             description='付费购买目标国家已公布经济报表；turn 指定结束周期，省略取最新。不包含未完成收益。'),
    )


def _borrow(args, receipt, actor, ctx):
    data = receipt.get('data')
    if not isinstance(data, dict):
        return None
    return ctx.text('tool.borrow.done', principal=data['principal'], remaining=data['remaining'], due=data['due'], rate=data['initial_rate'])


def _bank(data, args, actor, ctx):
    if not isinstance(data, dict):
        return None
    loan = data.get('loan')
    result = ctx.text('tool.bank.head', cash=ctx.money(data['cash']), credit=ctx.money(data['credit_limit']))
    if data.get('gdp') is not None:
        result += ctx.text('tool.bank.gdp', gdp=ctx.money(data['gdp']), multiple=_num(data.get('credit_multiple', 5)))
    result += ctx.text('tool.bank.liabilities', value=ctx.money(data['liabilities']))
    if 'loan_rate' in data:
        result += ctx.text('tool.bank.rates', savings=_num(data.get('savings_rate', 0) * 100), loan=_num(data['loan_rate'] * 100),
                           spread=_num((data['loan_rate'] - data.get('savings_rate', 0)) * 100),
                           term=_num(data.get('loan_term', 5)))
    if loan:
        result += ctx.text('tool.bank.loan', remaining=loan['remaining'], due=ctx.money(loan['due']))
    for purchase in reversed(data.get('purchases', [])):
        report = purchase['report']
        result += '\n' + ctx.text('tool.bank.purchase', name=ctx.name(report['owner'], ctx._names), period=purchase['period'], fee=ctx.money(purchase['fee'])) + '\n' + _reports([report], args, actor, ctx)
    return result


def _buy_report(args, receipt, actor, ctx):
    data = receipt.get('data')
    if not isinstance(data, dict) or 'report' not in data:
        return None
    return ctx.text('tool.bank.purchase', name=ctx.name(data['report']['owner'], ctx._names), period=data['period'], fee=ctx.money(data['fee'])) + '\n' + _reports([data['report']], args, actor, ctx)


PANELS = {'bank': _bank}
RECEIPTS = {'borrow': _borrow, 'buy_report': _buy_report}
CAPTIONS = {
    'borrow': lambda args, actor, receipt, ctx: ctx.text('tool.caption.borrow'),
    'bank': lambda args, actor, receipt, ctx: ctx.text('tool.caption.bank'),
    'buy_report': lambda args, actor, receipt, ctx: ctx.text('tool.caption.buy_report'),
}
