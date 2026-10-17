"""Economy toolcalls: stocks, market, reports and building economics."""
from engine.building import Building
from engine.runtime import Action, Tool

from .buildings import _behavior, _constraints


def _num(value):
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def tools(runtime):
    kinds = sorted(key for key, value in runtime.content.definitions()
                   if isinstance(value, type) and issubclass(value, Building))
    estimate_parameters = {'type': 'object', 'properties': {
        'kind': {'type': 'string', 'enum': kinds}, 'level': {'type': 'integer', 'minimum': 1},
        'other_slots': {'type': 'integer', 'minimum': 0, 'description': '该格已落成的其他建筑数（市政厅按每座加成；市政厅另加3座乐观密度假设，非保证收益）'}},
        'additionalProperties': False}

    def econ(world, actor):
        return runtime.player_api(world, 'economy:queries').balance(actor)

    def report(world, actor, period: int | None = None, all: bool = False):
        return runtime.player_api(world, 'economy:queries').report(actor, period=period, all=all)

    def building_economics(world, actor, kind: str | None = None, level: int = 1, other_slots: int = 0):
        return runtime.player_api(world, 'economy:queries').estimate(actor, kind=kind, level=level, other_slots=other_slots)

    def market(world, actor):
        return runtime.player_api(world, 'economy:queries').market(actor)

    def buy(world, actor, resource: str, quantity: int):
        return runtime.player_api(world, 'economy:queries').buy(actor, resource, quantity)

    def sell(world, actor, resource: str, quantity: int):
        return runtime.player_api(world, 'economy:queries').sell(actor, resource, quantity)

    return (
        Tool('econ', econ, owner='economy:queries', api=('balance',), read_only=True, fact=True, panel=True,
             description='本国库存、库存折金、建筑重置值和总资产，无法估值的内容明确列出；不是本期现金收入。'),
        Tool('report', report, owner='economy:queries', api=('report',), read_only=True, panel=True,
             description='本国免费已发布经济报表；缺省仅最新一期，period 查以该周期结束的报表，all=true 返回全部跨期。报表自动结算、有日期，不是当前库存或未完成收益。'),
        Tool('building_economics', building_economics, owner='economy:queries', api=('estimate',), read_only=True, panel=True,
             parameters=estimate_parameters, content_parameters=('kind',),
             description='优先不带参数查询：一次返回全部建筑的造价、产销、能源、效果、门槛与按当前市价算的净收益和回本，不必逐个查询。无需坐标，kind 可选筛选。收益口径写在报告首行（采集按卖价、工厂按买价替代、电耗按木电燃料）；满负荷口径，不代表本回合实收，也不判定建造资格。'),
        Tool('market', market, owner='economy:queries', api=('market',), read_only=True, fact=True, panel=True,
             description='世界市场的现价与买卖单价、周期；无需参数。本期单价对任何数量和任何国家都一样，买入的实际支出与取整以 buy 回执为准。'),
        Action('buy', buy, owner='economy:queries', api=('buy',), content_parameters=('resource',),
             description='从市场买入正整数数量的资源，resource 使用短键；成功返回实际支付、成交数量与价格，不把单价当成交。'),
        Action('sell', sell, owner='economy:queries', api=('sell',), content_parameters=('resource',),
             description='卖出自己持有的正整数数量资源，返回实际收入与成交价格。不能卖不存在的货；成交以回执为准。'),
    )


def _econ(data, args, actor, ctx):
    return ctx.text('tool.econ.head') + '\n' + ctx.text('tool.econ.stocks', values=ctx.resources({r['id']: r['amount'] for r in data['stocks']}, gold_short=False)) + '\n' + \
        ctx.text('tool.econ.assets', inventory=ctx.money(data['inventory']), buildings=ctx.money(data['buildings']), total=ctx.money(data['gross_assets']))


def _market(data, args, actor, ctx):
    lines = [ctx.text('tool.market.head')]
    inventory = dict(data.get('inventory', []))
    reference = dict(data.get('reference', []))
    buy = dict(data.get('buy_prices', []))
    sell = dict(data.get('sell_prices', []))
    equilibrium = dict(data.get('equilibrium', []))
    trials = {row[0]: row[1:] for row in data.get('trials', []) if len(row) == 3}
    units = data.get('trial_units', 50)
    for key, price in data['prices']:
        line = ctx.text('tool.market.line', name=ctx.label('content', key), price=_num(price))
        if key in equilibrium:
            gap = price - equilibrium[key]
            tone = ctx.text('tool.market.high' if gap > 1e-9 else 'tool.market.low' if gap < -1e-9 else 'tool.market.fair')
            line += ctx.text('tool.market.equilibrium', price=_num(equilibrium[key]), tone=tone)
        line += ctx.text('tool.market.side', buy=_num(buy.get(key)), sell=_num(sell.get(key)))
        if key in trials:
            trial_buy, trial_sell = trials[key]
            line += ctx.text('tool.market.trial_sell', units=units, price=_num(trial_sell))
            if trial_buy is not None:
                line += ctx.text('tool.market.trial_buy', units=units, price=_num(trial_buy))
        if key in inventory:
            line += ctx.text('tool.market.inventory', inventory=_num(inventory[key]), reference=_num(reference.get(key)))
        lines.append(line)
    return '\n'.join(lines)


def _reports(data, args, actor, ctx):
    lines = [ctx.text('tool.report.head')]
    for row in data:
        lines.append(ctx.text('tool.report.row1', start=row['start'], end=row['end'], span=row['span'],
                              gdp=ctx.money(row['gdp']), investment=ctx.money(row['investment']),
                              recruitment=ctx.money(row['recruitment']), military=ctx.money(row['military'])))
        lines.append(ctx.text('tool.report.row2', inventory=ctx.money(row['inventory_assets']), buildings=ctx.money(row['building_assets']),
                              total=ctx.money(row['total_assets']), liabilities=_num(row['liabilities']),
                              net=ctx.money(row['net_assets']), imports=_num(row['imports']), exports=_num(row['exports'])))
        percent = lambda value: '—' if value is None else _num(value * 100) + '%'
        lines.append(ctx.text('tool.report.row3', gdp=percent(row['gdp_growth']), investment=percent(row['investment_growth']),
                              assets=percent(row['assets_growth']), military=percent(row['military_ratio']), trade=percent(row['trade_ratio'])))
    if not data:
        lines.append(ctx.text('tool.report.empty'))
    return '\n'.join(lines)


def _trend(data, args, actor, ctx):
    lines = [ctx.text('tool.trend.head', count=len(data))]
    for row in sorted(data, key=lambda r: r['end'], reverse=True):
        percent = '—' if row['gdp_growth'] is None else _num(row['gdp_growth'] * 100) + '%'
        lines.append(ctx.text('tool.trend.row', start=row['start'], end=row['end'], gdp=ctx.money(row['gdp']),
                              growth=percent, military=ctx.money(row['military']),
                              assets=ctx.money(row['total_assets']), investment=ctx.money(row['investment'])))
    return '\n'.join(lines)


def _economics(data, args, actor, ctx):
    rows = data if isinstance(data, list) else [data]
    lines = [ctx.text('tool.economics.head'), ctx.text('tool.economics.basis')]
    for row in rows:
        name = ctx.label('content', row['kind'])
        net = ctx.money(row['net_per_period'])
        payback = row['payback_periods']
        details = ctx.text('tool.economics.construction', value=ctx.money(row['construction']))
        if row['energy_produced']:
            details += ctx.text('tool.economics.energy', value=ctx.money(row['inputs']), produced=row['energy_produced'])
        else:
            details += ctx.text('tool.economics.net', value=net)
            if row['energy_used']:
                details += ctx.text('tool.economics.energy_used', value=row['energy_used'])
            if row.get('output_basis') == 'buy':
                details += ctx.text('tool.economics.sell', value=ctx.money(row['outputs_sell']))
            details += ctx.text('tool.economics.payback', value=('—' if payback is None else _num(payback) + '回合'))
        lines.append(ctx.text('tool.economics.row', name=name, details=details))
        if row.get('costs_by_level'):
            definition = {**row, 'inputs': dict(row.get('recipe_inputs', [])), 'outputs': dict(row.get('recipe_outputs', [])),
                          'energy_use': row['energy_used'], 'energy_output': row['energy_produced'], 'effects': dict(row.get('effects', []))}
            costs = row['costs_by_level']
            cost = ' → '.join(_num(c.get('native:gold', 0)) for c in costs) + '金 ' + ctx.resources({k: v for k, v in costs[0].items() if k != 'native:gold'})
            lines.append('  ' + ctx.text('tool.economics.levels', cost=cost) + '；' + _behavior(definition, ctx) + ('；' + _constraints(row, ctx) if _constraints(row, ctx) else ''))
            for assumption in row.get('assumptions', []):
                lines.append('  ' + assumption)
    return '\n'.join(lines)


def _report(args, receipt, actor, ctx):
    data = receipt.get('data')
    if isinstance(data, list):
        return _trend(data, args, actor, ctx) if args.get('all') else _reports(data, args, actor, ctx)
    return None


def _economics_receipt(args, receipt, actor, ctx):
    data = receipt.get('data')
    if isinstance(data, list) or isinstance(data, dict) and 'net_per_period' in data:
        return _economics(data, args, actor, ctx)
    return None


def _trade(action):
    def render(args, receipt, actor, ctx):
        data = receipt.get('data')
        if not isinstance(data, dict) or 'exact_total' not in data:
            return None
        quantity = data['quantity']
        label = ctx.label('content', data['resource'])
        cash = ctx.text('tool.trade.paid') if data['side'] == 'buy' else ctx.text('tool.trade.received')
        line = ctx.text('tool.trade.line', verb=ctx.text('tool.caption.' + action), resource=label, quantity=quantity,
                        before=_num(data['price_before']), after=_num(data['price_after']),
                        average=_num(data['exact_total'] / quantity), cash=cash, gold=_num(data['gold']))
        if 'remaining' in data:
            line += ctx.text('tool.trade.remaining', value=_num(data['remaining']))
        return line + _churn(data, ctx)
    return render


def _churn(data, ctx):
    churn = data.get('churn') or {}
    if not churn.get('quantity'):
        return ''
    return ctx.text('tool.market.churn_gain' if churn['loss'] < 0 else 'tool.market.churn',
                           resource=ctx.label('content', data['resource']),
                           buy=_num(churn['buy_quantity']), buy_price=_num(churn['buy_average']),
                           sell=_num(churn['sell_quantity']), sell_price=_num(churn['sell_average']),
                           overlap=_num(churn['quantity']), loss=_num(abs(churn['loss'])))


PANELS = {'econ': _econ, 'market': _market}
RECEIPTS = {'buy': _trade('buy'), 'sell': _trade('sell'), 'report': _report, 'building_economics': _economics_receipt}
CAPTIONS = {
    'econ': lambda args, actor, receipt, ctx: ctx.text('tool.caption.econ'),
    'market': lambda args, actor, receipt, ctx: ctx.text('tool.caption.market'),
    'report': lambda args, actor, receipt, ctx: ctx.text('tool.caption.report'),
    'building_economics': lambda args, actor, receipt, ctx: ctx.text('tool.caption.building_economics') + ((' ' + ctx.label('content', args['kind'])) if args.get('kind') else ' ' + ctx.text('tool.economics.all')),
    'buy': lambda args, actor, receipt, ctx: ctx.text('tool.caption.buy') + ' ' + ctx.label('content', args.get('resource', '')) + '×' + _num(args.get('quantity', '')),
    'sell': lambda args, actor, receipt, ctx: ctx.text('tool.caption.sell') + ' ' + ctx.label('content', args.get('resource', '')) + '×' + _num(args.get('quantity', '')),
}
