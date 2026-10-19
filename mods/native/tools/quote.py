"""Read-only build/recruit/letter/borrow previews, exposed as query(panel=quote)."""
from engine.building import integer
from engine.runtime import Tool
from engine.state import unit_context

from ..locations import site_arguments


def _read(fn, *args, **kwargs):
    with unit_context(None):
        return fn(*args, **kwargs)


def _fmt(value, ctx):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.2f}'.rstrip('0').rstrip('.')
    return str(value)


def _costs(costs, ctx):
    return '、'.join(f'{ctx.label("content", key)}×{_fmt(value, ctx)}'
                     for key, value in sorted((costs or {}).items()))


def tools(runtime):
    def site_rows(world, actor):
        return [s for s in _read(world.buildings.snapshot).sites if s.owner == actor]

    def quote(world, actor, action, site=None, place=None, x=None, y=None,
              kind=None, count=1, target=None, text='', text_length=None):
        if not hasattr(world.buildings, 'quote') or not hasattr(world.military, 'recruit_quote'):
            raise ValueError('本发行版没有 quote 预览实现')
        if action not in ('build', 'recruit', 'letter', 'borrow'):
            raise ValueError('action 须为 build/recruit/letter/borrow')
        if action == 'build':
            if not kind:
                raise ValueError('建造预览需要 kind（建筑短键或中文名）')
            try:
                if site is not None or x is not None or y is not None:
                    site_id = _read(world.buildings.resolve_site, actor, site, x, y)
                    data = _read(world.buildings.quote, actor, site_id, kind,
                                                 policy_pct=runtime.parameter(world, actor, 'build_cost_pct', 100))
                    data['position'] = list(_read(world.buildings.site, site_id).position)
                    return {'action': 'build', **data}
                rows = []
                for row in site_rows(world, actor):
                    try:
                        data = _read(world.buildings.quote, actor, row.id, kind,
                                                     policy_pct=runtime.parameter(world, actor, 'build_cost_pct', 100))
                        rows.append({'site': row.id, 'position': list(row.position),
                                     **{k: v for k, v in data.items() if k != 'site'}})
                    except ValueError as exc:
                        rows.append({'site': row.id, 'position': list(row.position),
                                     'error': str(exc)})
                return {'action': 'build', 'kind': kind, 'sites': rows}
            except ValueError as exc:
                return {'action': 'build', 'kind': kind, 'error': str(exc)}
        if action == 'recruit':
            integer(count, 1)
            kinds = [kind] if kind else ['native:infantry', 'native:militia']
            if site is not None or x is not None or y is not None:
                try:
                    site_id = _read(world.buildings.resolve_site, actor, site, x, y)
                    return {'action': 'recruit',
                            **_read(world.military.recruit_quote, actor, site_id, kinds[0], count,
                                                           energy_short=_read(world.military.grid_short, actor))}
                except ValueError as exc:
                    return {'action': 'recruit', 'kind': kinds[0], 'error': str(exc)}
            rows = []
            for row in site_rows(world, actor):
                for unit_kind in kinds:
                    try:
                        rows.append({'site': row.id, 'position': list(row.position),
                                     **_read(world.military.recruit_quote, actor, row.id, unit_kind, count,
                                                                    energy_short=_read(world.military.grid_short, actor))})
                    except ValueError as exc:
                        rows.append({'site': row.id, 'position': list(row.position),
                                     'kind': unit_kind, 'error': str(exc)})
            return {'action': 'recruit', 'count': count, 'sites': rows}
        if action == 'letter':
            if not target:
                raise ValueError('信件预览需要 target（国家名或国家级 ID）')
            length = text_length if text_length is not None else len(text or '')
            try:
                data = _read(world.intelligence.letter_fee, actor, target, text, length=length)
                data['action'] = 'letter'
                data['cash'] = data.get('budget', {}).get(data.get('currency'), 0)
                data['can_send'] = data['cash'] >= data['fee']
                return data
            except ValueError as exc:
                return {'action': 'letter', 'target': target, 'error': str(exc)}
        return {'action': 'borrow', **_read(world.bank.borrow_quote, actor)}

    return (
        Tool('quote', quote, owner='native:buildings', read_only=True, panel=True, hidden=True,
             adapter=site_arguments, content_parameters=('kind',),
             parameters={'type': 'object', 'properties': {
                 'action': {'type': 'string', 'enum': ['build', 'recruit', 'letter', 'borrow']},
                 'site': {'type': 'string', 'description': '地名、坐标串或 名字(x, y)'},
                 'place': {'type': 'string', 'description': 'site 同义'},
                 'x': {'type': 'integer', 'minimum': 0},
                 'y': {'type': 'integer', 'minimum': 0},
                 'kind': {'type': 'string', 'description': '建筑/兵种短键或中文显示名'},
                 'count': {'type': 'integer', 'minimum': 1},
                 'target': {'type': 'string', 'description': '信件目标国家名或 ID'},
                 'text': {'type': 'string', 'description': '信件正文（仅用于计费）'},
                 'text_length': {'type': 'integer', 'minimum': 0},
             }, 'required': ['action'], 'additionalProperties': False},
             description='只读预览：action=build 看地块实付造价；action=recruit 看可征站点/材料/额度；action=letter 看信件费用；action=borrow 看授信/手续费/是否可借。不改变世界。'),
    )


def _quote(data, args, actor, ctx):
    if not isinstance(data, dict):
        return None
    action = data.get('action')
    if action == 'build':
        if 'sites' in data:
            lines = [f'【建造预览】{ctx.label("content", data.get("kind", ""))}｜各站点']
            for row in data['sites']:
                place = ctx.pos(row.get('position'))
                if row.get('error'):
                    lines.append(f'{place}｜不可：{ctx.label("receipt", row["error"])}')
                else:
                    lines.append(f'{place}｜实付 {_costs(row.get("costs"), ctx)}｜'
                                 f'地形惩罚 {_fmt(row.get("penalty"), ctx)}%｜'
                                 f'折扣 {_fmt(row.get("discount"), ctx)}%')
            return '\n'.join(lines)
        if data.get('error'):
            return '【建造预览】不可：' + ctx.label('receipt', data['error'])
        return (f'【建造预览】{ctx.label("content", data.get("kind", ""))} @ {ctx.pos(data.get("position"))}｜'
                f'实付 {_costs(data.get("costs"), ctx)}｜惩罚 {_fmt(data.get("penalty"), ctx)}%｜'
                f'折扣 {_fmt(data.get("discount"), ctx)}%')
    if action == 'recruit':
        if 'sites' in data:
            lines = ['【征兵预览】可征站点']
            for row in data['sites']:
                place = ctx.pos(row.get('position'))
                if row.get('error'):
                    lines.append(f'{place}｜{ctx.label("content", row.get("kind", ""))}｜不可：{ctx.label("receipt", row["error"])}')
                else:
                    lines.append(f'{place}｜{ctx.label("content", row.get("kind", ""))}｜'
                                 f'材料 {_costs(row.get("costs"), ctx)}｜本回合余 {row.get("quota")}')
            return '\n'.join(lines)
        if data.get('error'):
            return '【征兵预览】不可：' + ctx.label('receipt', data['error'])
        return (f'【征兵预览】{ctx.label("content", data.get("kind", ""))} @ {ctx.pos(data.get("position"))}｜'
                f'材料 {_costs(data.get("costs"), ctx)}｜本回合余 {data.get("quota")}')
    if action == 'letter':
        if data.get('error'):
            return '【信件预览】不可：' + ctx.label('receipt', data['error'])
        return (f'【信件预览】→{ctx.name(data.get("target"), ctx._names)}｜{data.get("length")} 字｜'
                f'费用 {data.get("fee")}｜现金 {data.get("cash")}｜'
                f'{"可发" if data.get("can_send") else "现金不足"}')
    if action == 'borrow':
        loan = data.get('loan')
        status = '可借' if data.get('can_borrow') else ('已有未清贷款' if loan else '现金不足或额度为 0')
        return (f'【借款预览】额度 {_fmt(data.get("credit"), ctx)}｜手续费 {data.get("fee")}｜'
                f'现金 {_fmt(data.get("cash"), ctx)}｜{status}｜期限 {data.get("loan_term")}｜'
                f'利率 {_fmt(data.get("loan_rate"), ctx)}')
    return None


PANELS = {'quote': _quote}
RECEIPTS = {}
CAPTIONS = {}
