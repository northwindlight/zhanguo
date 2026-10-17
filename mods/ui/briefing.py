"""模型侧战情总纲：按紧迫度分节的中文简报。

与观察者共用 ConsoleUI / ReceiptPresentation 的全部渲染器，差别只在排序与裁剪口径：
模型拿到的是**分节文本**（越界可整节丢弃，并写明去哪查），不是字段字典。
排序沿用旧版 `full_state` 的紧迫度：威胁 → 国祚 → 领土 → 国力 → 国策 → 国土/视野
→ 军队 → 市场 → 央行 → 外交 → 信箱 → 在途。
"""
from .receipts import num
from .facts import flatten
from .views import player_atlas

# 观察者已有的专用面板渲染，直接复用；键是面板名，值是事实里的字段。
PANELS = (('diplomacy', 'brief.diplomacy_head'), ('market', 'brief.market_head'),
          ('bank', 'brief.bank_head'), ('intel', 'brief.intel_head'),
          ('dispatches', 'brief.dispatch_head'))


def stocks_of(facts):
    return {s['id']: s['amount'] for s in facts.get('econ', {}).get('stocks', [])}


def resources_line(ui, stocks, key):
    """模型侧物资行：键值走模型口径（zh=粮食，en=food），与工具 enum 同一套。"""
    return ' '.join(f'{num(amount)}{key(ident)}' for ident, amount in stocks.items() if amount) or ui.text('query.none')


def panel(ui, action, data, actor):
    """把一个公开事实交给同一套观察者渲染器成文；没有专用格式就返回 None。

    专用渲染器只认完整事实，缺字段时退回通用可读格式：简报里少一节可以忍，
    整回合因为一个可选面板缺字段而崩掉不行。
    """
    if data is None:
        return None
    try:
        return ui.receipt_body(action, {}, {'ok': True, 'data': data, 'message': '', 'action': action}, actor)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return ui.format(data, ui._names)


def _power(ui, facts, cues, observation, key):
    stocks = stocks_of(facts)
    lines = [ui.text('brief.power_head') + ' ' + resources_line(ui, stocks, key)]
    energy = cues.get('energy_capacity') or {}
    lines.append(ui.text('brief.energy', capacity=energy.get('capacity', 0), demand=energy.get('demand', 0),
                         status=ui.text('brief.energy_short') if energy.get('capacity_gap') else ui.text('brief.energy_ok')))
    inputs = cues.get('planned_regular_inputs') or {}
    if inputs:
        lines.append(ui.text('brief.industry', inputs=ui.resources(inputs, gold_short=False)))
    actor = observation['actor']
    capitals = [row['id'] for row in facts.get('building_catalog') or [] if 'capital' in (row.get('roles') or ())]
    halls = sum(1 for row in facts.get('view', []) if row.get('owner') == actor
                and any(dict(row.get('buildings') or ()).get(kind) for kind in capitals))
    lines.append(ui.text('brief.halls', count=halls))
    shortfalls = cues.get('stock_shortfalls_before_production') or {}
    if shortfalls:
        lines.append(ui.text('brief.shortfall', values=ui.resources(shortfalls, gold_short=False)))
    own = [u for u in facts.get('army', []) if u.get('owner') == actor and u.get('hp', 0) > 0]
    lines.append(ui.text('brief.army_count', count=len(own)))
    # 上一回合结算：旧版挂在【国力】末尾，让模型一眼看到钱粮是涨是跌。
    settled = _turn_report(facts)
    if settled:
        flows = (facts.get('econ') or {}).get('latest_flows') or {}
        outputs = dict(flows.get('outputs') or ())
        if outputs:
            lines.append(ui.text('brief.settled_flows', period=flows.get('period', settled.get('period', 0)),
                                 values=resources_line(ui, outputs, key), gold=ui.money(settled['production']),
                                 cash=num(settled.get('cash_production', 0)), inputs=ui.money(settled['inputs']),
                                 gdp=ui.money(settled['gdp'])))
        else:
            lines.append(ui.text('brief.settled', gold=ui.money(settled['production']),
                                 cash=num(settled.get('cash_production', 0)), inputs=ui.money(settled['inputs']),
                                 gdp=ui.money(settled['gdp'])))
    return '\n'.join(lines)


def _turn_report(facts):
    """最近一期结算/报表事实；econ 快照自带，不必另开查询。"""
    report = (facts.get('econ') or {}).get('latest_turn')
    return report if isinstance(report, dict) else None


def _report(ui, facts):
    report = _turn_report(facts)
    if not report:
        return None
    return ui.text('brief.report_head') + '\n' + ui.text('brief.report_row',
        period=report.get('period', 0), gdp=ui.money(report.get('gdp')),
        military=ui.money(report.get('military')), investment=ui.money(report.get('investment')),
        assets=ui.money(report.get('total_assets')), liabilities=num(report.get('liabilities', 0)),
        net=ui.money(report.get('net_assets')))


def _capital(ui, cues, actor):
    """国祚警报：真实路径与行动资格由发行版 frontier 事实判定，UI 不复制规则。"""
    warnings = cues.get('capital_warning') or []
    if not warnings:
        return None
    lines = [ui.text('brief.capital_head')]
    for warning in warnings:
        enemies = '、'.join(f"{ui.name(u['owner'], ui._names)} {ui.label('content', u['kind'])}#{u['id']}"
                            for u in warning['enemies'])
        lines.append(ui.text('query.capital_warning', place=ui.pos(warning, actor=actor), enemies=enemies))
    return '\n'.join(lines)


def _threats(ui, facts):
    rows = facts.get('threats') or []
    if not rows:
        return None
    return ui.text('brief.threats_head') + '\n' + '\n'.join(
        ui.relation(row['relation']) + '｜' + ui.army_line(row['unit']) for row in rows)


def _battle(ui, facts, actor):
    """当前交战：够判断该不该继续打；逐支明细仍留给 battle 面板。

    旧版挂在批间刷新里，现在没有批间消息了，交战属于「正在发生的战况」，
    随战情总纲一起在回合开局给全。
    """
    rows = facts.get('battle') or []
    if not rows:
        return None
    pieces = []
    for row in rows:
        sides = []
        for side in row.get('sides', []):
            who = '你' if side['owner'] == actor else ui._names.get(side['owner'], side['owner'])
            sides.append(f"{who}{side['count']}支{num(sum(u.get('hp', 0) for u in side.get('units', [])))}HP")
        detail = '/'.join(sides)
        defence = next((s for s in row.get('sides', []) if s.get('role') == 'defending'), None)
        if defence is not None and not row.get('blind'):
            detail += f"·守方减伤{num(defence.get('combined_reduction', 0))}%"
        pieces.append(ui.pos(row.get('position'), actor=actor) + detail)
    return ui.text('brief.battle', count=len(rows), detail='；'.join(pieces))


def _territory(ui, events, names):
    """领土得失是大事：只列本回合真实的归属变更，不重播旧事。"""
    rows = [row for row in events if row.get('kind') == 'occupation' and isinstance(row.get('details'), dict)
            and 'previous_owner' in row['details']]
    if not rows:
        return None
    return ui.text('brief.territory_head') + '\n' + '\n'.join(ui.event(row, names) for row in rows)


def _plan(ui, facts):
    government = facts.get('government') or {}
    plan = government.get('plan')
    lines = [ui.text('brief.plan_head')]
    if not plan:
        lines.append(ui.text('query.no_plan'))
        return '\n'.join(lines)
    lines.append(ui.text('query.plan_head', start=plan['period'], end=plan['valid_until'],
                         status=ui.text('query.expired' if plan['valid_until'] < government.get('period', 0)
                                        else 'query.valid')))
    if plan.get('text'):
        lines.append(plan['text'])
    return '\n'.join(lines)


def _map(ui, observation, key, vision):
    """国土视野：默认给逐格坐标地图。

    vision 打开时图片已经带地形与归属，逐格 atlas 必须让位——同一张地图不能付两遍
    （G6.15：0.1 就是图片顶掉 atlas），只留一行计数。
    """
    rows = observation['facts'].get('view') or []
    if vision:
        own = sum(1 for row in rows if row.get('owner') == observation['actor'])
        return ui.text('brief.map_head') + '\n' + ui.text('atlas.footer', own=own,
                                                          seen=len(rows), other=len(rows) - own)
    return ui.text('brief.map_head') + '\n' + player_atlas(observation, ui.text, key)


def _army(ui, facts, actor):
    rows = [u for u in facts.get('army', []) if u.get('hp', 0) > 0]
    lines = [ui.text('brief.army_head'), ui.army_list(rows, actor)]
    # 问路并进军事面板：可及格数与可拓荒地一次给全，省掉单独一次路径查询。
    frontier = facts.get('frontier') or {}
    if frontier:
        lines.append(ui.text('query.frontier_head', seen=frontier.get('visible_unowned', 0),
                             reachable=len(frontier.get('reachable', [])), frontier=len(frontier.get('frontier', []))))
        for key in ('reachable', 'frontier'):
            places = '、'.join(ui.pos(p, actor=actor) for p in frontier.get(key, []))
            lines.append(ui.text('query.' + key, places=places or ui.text('query.none')))
    return '\n'.join(lines)


def sections(ui, observation, events, cues, key=lambda value: value, vision=False):
    """有序 `(键, 正文)` 列表；正文为空的节整节不出现，越界时按此序从后往前丢弃。"""
    facts = flatten(observation.get('facts', {}))
    actor = observation['actor']
    names = {n['id']: n['name'] for n in facts.get('nations', []) if isinstance(n, dict)}
    ui._names = names
    ui._actor = actor
    out = [
        ('capital', _capital(ui, cues, actor)),
        ('threats', _threats(ui, facts)),
        ('battle', _battle(ui, facts, actor)),
        ('territory', _territory(ui, events, names)),
        ('power', _power(ui, facts, cues, observation, key)),
        ('plan', _plan(ui, facts)),
        ('map', _map(ui, observation, key, vision)),
        ('army', _army(ui, facts, actor)),
    ]
    out.append(('report', _report(ui, facts)))
    for key, title in PANELS:
        body = panel(ui, key, facts.get(key), actor)
        if body:
            if key == 'intel':
                rows = facts.get('intel') or []
                if rows:
                    latest = max(rows, key=lambda row: row.get('sampled', 0))
                    body = (ui.text('brief.intel_count', count=len(rows),
                                    source=ui.name(latest.get('source', ''), ui._names),
                                    period=latest.get('sampled', 0)) + '\n' + body)
            out.append((key, ui.text(title) + '\n' + body))
    return [(key, text) for key, text in out if text]
