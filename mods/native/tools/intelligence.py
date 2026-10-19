"""Intelligence toolcalls: letters, gifts, spies, intel, news, dispatches and countries."""
from engine.runtime import Action, Tool


def _num(value):
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def tools(runtime):
    unit = runtime.units.get('native:intelligence')
    if unit is None:
        return ()
    cfg = unit.mod.declaration['Intelligence']

    def send_letter(world, actor, target: str, text: str):
        return runtime.player_api(world, 'native:intelligence').letter(actor, target, text)

    def gift(world, actor, target: str, resource: str, quantity: int):
        return runtime.player_api(world, 'native:intelligence').gift(actor, target, resource, quantity)

    def share_map(world, actor, target: str):
        return runtime.player_api(world, 'native:intelligence').map(actor, target)

    def spy(world, actor, target: str):
        return runtime.player_api(world, 'native:intelligence').spy(actor, target)

    def intel(world, actor, kind: str | None = None):
        return runtime.player_api(world, 'native:intelligence').intel(actor, kind)

    def news(world, actor):
        return runtime.player_api(world, 'native:intelligence').news(actor)

    def dispatches(world, actor):
        return runtime.player_api(world, 'native:intelligence').dispatches(actor)

    return (
        Action('send_letter', send_letter, owner='native:intelligence', api=('letter',), groups=('communication.letter',),
             description=f'向 query(panel=foreign) 中的别国发送正文；提交时扣费，结算派发，下一周期送达 intel。起步价非盟友{cfg["LetterBase"]}金、盟友{cfg["LetterAlly"]}金，外交中心可减免；前{cfg["FreeCharacters"]}字免费，超出每{cfg["CharactersPerGold"]}字加1金，下限{cfg["LetterMinimum"]}金，实际费用看回执。'),
        Action('gift', gift, owner='native:intelligence', api=('gift',), content_parameters=('resource',), groups=('communication.gift',),
             description='向别国赠送正整数数量的本国资源；先预留物资和费用，结算派发，下周期到账。不能向自己赠送，submitted 不等于对方已收到。'),
        Action('share_map', share_map, owner='native:intelligence', api=('map',), groups=('communication.map',),
             description='把本国可见地图发给别国；结算采样，下一周期对方在 intel 收到带日期情报。盟友免基础费用，不能给自己发图。'),
        Action('spy', spy, owner='native:intelligence', api=('spy',), groups=('communication.spy',),
             description=f'派间谍刺探别国，费用{cfg["SpyFee"]}金，延迟{cfg["SpyDelay"]}周期在 intel 返回有日期的库存、已知地图、外交和兵种数量，不泄露军队位置与编号。'),
        Tool('intel', intel, owner='native:intelligence', api=('intel',), read_only=True, fact=True, panel=True,
             parameters={'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['letter', 'gift', 'map', 'spy', 'observer-letter']}}, 'additionalProperties': False},
             description='本国收到的信件、物资通知、共享地图、间谍情报和神秘人来信；可用 kind=letter/gift/map/spy/observer-letter 筛选，省略查全部。注意 sampled 日期，不把旧情报当当前事实。'),
        Tool('news', news, owner='native:intelligence', api=('news',), read_only=True, fact=True, panel=True,
             description='本国可见的最近10周期近讯、外交、战况和观察者公告；旧事件保存于存档旁的历史归档；player_events(cursor) 可只取游标后的新增事件。'),
        Tool('dispatches', dispatches, owner='native:intelligence', api=('dispatches',), read_only=True, fact=True, panel=True,
             description='本国所有待结算和在途通信/馈赠/间谍，包含状态与预计送达周期；无需目标或坐标。'),
    )


def _letter(action):
    def render(args, receipt, actor, ctx):
        data = receipt.get('data')
        if not isinstance(data, dict):
            return None
        target = ctx.name(data['target'], ctx._names)
        fee = data['fee']
        cost = ctx.text('tool.intel.cost', value=_num(fee)) if fee else ctx.text('tool.intel.free')
        if action == 'spy':
            return ctx.text('tool.intel.spy', target=target, cost=cost, arrive=data['arrive'])
        if action == 'send_letter':
            chars = len(str(data.get('text') or args.get('text') or ''))
            return ctx.text('tool.intel.letter', target=target, arrive=data['arrive'], chars=chars, cost=cost)
        what = ctx.text('tool.intel.gift' if action == 'gift' else 'tool.intel.map')
        result = ctx.text('tool.intel.send', what=what, target=target, cost=cost, arrive=data['arrive'])
        if data.get('text'):
            result += '\n' + data['text']
        if data.get('resource'):
            result += ' ' + ctx.resources({data['resource']: data['quantity']})
        return result
    return render


def _intel(data, args, actor, ctx):
    if not data:
        return ctx.text('tool.intel.empty')
    ordered = list(reversed(data))
    lines = [ctx.text('tool.intel.head', count=len(data)), ctx.message(ordered[0], ctx._names)]
    for row in ordered[1:]:
        kind = ctx.text.resolve('tool.intel.kind.' + row['kind'], row['kind'])
        lines.append(ctx.text('tool.intel.index', identity=row['id'], period=row['sampled'], kind=kind,
                              source=ctx.name(row['source'], ctx._names)))
    lines.extend(ctx.message(row, ctx._names) for row in ordered[1:])
    return '\n'.join(lines)


def _news(data, args, actor, ctx):
    data = [row for row in data if not (row.get('period', 0) >= ctx._period and
            isinstance(row.get('details'), dict) and row['details'].get('source') == 'action_receipt')]
    recent = data[-12:]
    older = data[:-12] if len(data) > 12 else []
    lines = [ctx.event(row, ctx._names) for row in reversed(recent)]
    if older:
        lines.append(ctx.text('tool.news.more', count=len(older), first=older[0].get('period', 0), last=older[-1].get('period', 0)))
    return '\n'.join(lines) or ctx.text('tool.news.empty')


def _dispatches(data, args, actor, ctx):
    if not data:
        return ctx.text('tool.intel.no_dispatches')
    lines = [ctx.text('tool.intel.dispatches_head', count=len(data))]
    for row in sorted(data, key=lambda r: (r['arrive'] if r['arrive'] is not None else float('inf'), r['id'])):
        kind = ctx.text.resolve('tool.intel.kind.' + row['kind'], row['kind'])
        lines.append(ctx.text('tool.intel.dispatch_row', identity=row['id'], kind=kind, target=ctx.name(row['target'], ctx._names),
                              status=ctx.label('status', row['status']), submitted=row['submitted'], arrive=row['arrive'], fee=_num(row['fee'])))
        if row.get('resource'):
            lines.append('  ' + ctx.resources({row['resource']: row['quantity']}, gold_short=False))
        if row.get('text'):
            lines.append('  ' + row['text'])
    return '\n'.join(lines)


def _countries(data, args, actor, ctx):
    lines = [ctx.text('tool.intel.countries_head')]
    for row in data:
        tags = [ctx.text('tool.relation.' + row['relation'])]
        for pact in row.get('treaties', []):
            if pact['kind']=='defense':tags.append(ctx.text('tool.intel.defense'))
            else:
                own=ctx.name(actor,ctx._names);other=row['name']
                outgoing=pact['direction']=='outgoing'
                tags.append(ctx.text('tool.diplomacy.guarantee_direction',
                                     protector=own if outgoing else other, protected=other if outgoing else own))
        if row['border']:
            tags.append(ctx.text('tool.intel.border'))
        if row.get('letter_count'):
            tags.append(ctx.text('tool.intel.letters', count=row['letter_count']))
        elif row['has_letter']:
            tags.append(ctx.text('tool.intel.has_letter'))
        if row.get('entity', '').startswith('bloc/'):
            bloc = next((b['name'] for b in ctx._facts.get(ctx._actor, {}).get('diplomacy', {}).get('alliances', []) if 'bloc/' + b['id'] == row['entity']), row['entity'])
            tags.append(ctx.text('tool.intel.bloc', name=bloc, chief=ctx.name(row['chief'], ctx._names)))
        if row.get('war_representative'):
            tags.append(ctx.text('tool.intel.peace_rep', name=ctx.name(row['war_representative'], ctx._names)))
        lines.append(ctx.text('tool.intel.country_row', name=row['name'], id=row['id'], tags='｜'.join(tags)))
    return '\n'.join(lines) if data else ctx.text('tool.intel.no_countries')


PANELS = {'intel': _intel, 'news': _news, 'dispatches': _dispatches, 'countries': _countries}
RECEIPTS = {
    'send_letter': _letter('send_letter'),
    'gift': _letter('gift'),
    'share_map': _letter('share_map'),
    'spy': _letter('spy'),
}
CAPTIONS = {
    'send_letter': lambda args, actor, receipt, ctx: ctx.text('tool.caption.send_letter'),
    'gift': lambda args, actor, receipt, ctx: ctx.text('tool.caption.gift'),
    'share_map': lambda args, actor, receipt, ctx: ctx.text('tool.caption.share_map'),
    'spy': lambda args, actor, receipt, ctx: ctx.text('tool.caption.spy'),
    'intel': lambda args, actor, receipt, ctx: ctx.text('tool.caption.intel'),
    'news': lambda args, actor, receipt, ctx: ctx.text('tool.caption.news'),
    'dispatches': lambda args, actor, receipt, ctx: ctx.text('tool.caption.dispatches'),
}
