"""Building toolcalls: construction, sites and the building catalog."""
from engine.building import Building
from engine.runtime import Tool
from mods.native.locations import location_parameters, site_arguments

from .territory import _land as _land_panel


def _num(value):
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def tools(runtime):
    kinds = sorted(key for key, value in runtime.content.definitions()
                   if isinstance(value, type) and issubclass(value, Building))

    def build(world, actor, kind: str, site: str | None = None, x: int | None = None, y: int | None = None):
        return runtime.player_api(world, 'native:buildings').build(actor, kind, site=site, x=x, y=y)

    def sites(world, actor):
        return runtime.player_api(world, 'native:buildings').sites(actor)

    def catalog(world, actor, kind: str | None = None):
        rows = runtime.player_api(world, 'native:buildings').catalog(actor)
        return [row for row in rows if kind is None or row['id'] == kind]

    parameters = {'type': 'object', 'properties': {
        'site': {'type': 'string', 'description': '站点 ID、地名或坐标串（如 "5 6"）'},
        'x': {'type': 'integer', 'minimum': 0}, 'y': {'type': 'integer', 'minimum': 0},
        'kind': {'type': 'string', 'enum': kinds}},
        'required': ['kind'], 'additionalProperties': False,
        'oneOf': [{'required': ['site'], 'not': {'anyOf': [{'required': ['x']}, {'required': ['y']}]}},
                  {'required': ['x', 'y'], 'not': {'required': ['site']}}]}
    parameters = location_parameters(parameters)
    return (
        Tool('build', build, owner='native:buildings', api=('build',), parameters=parameters,
             adapter=site_arguments, content_parameters=('kind',),
             description='在本国地块动工：site 传地名或 "x y" 坐标串，或直接传两个整数 x、y。直接返回实际扣款和施工回执；参数或材料不符时返回原因。每格每回合一次，结算落成。造价、门槛查 building_catalog，全部收益查 building_economics。'),
        Tool('building_sites', sites, owner='native:buildings', api=('sites',), read_only=True, fact=True,
             description='一次查看全部本国站点：坐标、资源容量、建筑位、已建/在建、当期施工与自建计数。无需地块参数。'),
        Tool('building_catalog', catalog, owner='native:buildings', api=('catalog',), read_only=True, fact=True, panel=True,
             content_parameters=('kind',),
             parameters={'type': 'object', 'properties': {
                 'kind': {'type': 'string', 'enum': kinds,
                          'description': '建筑短键/中文显示名（可选）；省略列全部建筑'}},
                 'additionalProperties': False},
             description='一次列齐全部建筑：各级造价、产销配方、能源、效果与门槛。可选 kind 只看一种建筑（短键或中文显示名均可）。无需地块或坐标。'),
    )


def _constraints(row, ctx):
    pieces = []
    if row.get('min_slots'):
        pieces.append(ctx.text('tool.build.constraint_slots', count=row['min_slots']))
    if row.get('site_resource'):
        pieces.append(ctx.text('tool.build.constraint_site', resource=ctx.label('content', row['site_resource'])))
    if row.get('max_per_site') is not None:
        pieces.append(ctx.text('tool.build.constraint_per_site', count=row['max_per_site']))
    if row.get('self_build_limit') is not None:
        pieces.append(ctx.text('tool.build.constraint_self', count=row['self_build_limit']))
    return '；'.join(pieces)


def _behavior(row, ctx):
    effects = row.get('effects', {})
    pieces = []
    if effects.get('gold_base') is not None:
        pieces.append(ctx.text('tool.build.effect_gold', base=effects['gold_base'], per_slot=effects.get('gold_per_slot', 0)))
    elif row.get('outputs'):
        pieces.append(ctx.text('tool.build.effect_output', values=ctx.resources(row['outputs'])))
    if row.get('inputs'):
        pieces.insert(0, ctx.text('tool.build.effect_input', values=ctx.resources(row['inputs'])))
    if row.get('energy_output'):
        pieces.append(ctx.text('tool.build.effect_energy_out', count=row['energy_output']))
    if row.get('energy_use'):
        pieces.append(ctx.text('tool.build.effect_energy_in', count=row['energy_use']))
    if effects.get('defense_per_level'):
        pieces.append(ctx.text('tool.build.effect_defense', value=effects['defense_per_level']))
    if effects.get('build_discount'):
        pieces.append(ctx.text('tool.build.effect_discount', value=effects['build_discount']))
    if effects.get('vision_radius'):
        pieces.append(ctx.text('tool.build.effect_vision', value=effects['vision_radius']))
    if effects.get('recruit_cap'):
        pieces.append(ctx.text('tool.build.effect_recruit', count=effects['recruit_cap']))
    if effects.get('militia_cap'):
        pieces.append(ctx.text('tool.build.effect_militia', cap=effects['militia_cap'], per_turn=effects.get('militia_per_turn', 1)))
    return '，'.join(pieces) or ctx.text('tool.build.effect_none')


def _catalog(data, args, actor, ctx):
    lines = [ctx.text('tool.build.catalog_head', count=len(data))]
    for row in data:
        key = row.get('id', row.get('kind'))
        ctx._catalog[key] = dict(row)
        costs = row.get('costs_by_level', [])
        if costs and len(costs) > 1:
            cost = '→'.join(_num(c.get('native:gold', 0)) for c in costs) + '金'
            rest = {k: v for k, v in costs[0].items() if k != 'native:gold'}
            if rest:
                cost += ' ' + ctx.resources(rest)
        else:
            cost = ctx.resources(costs[0] if costs else row['costs']) if costs or 'costs' in row else ctx.text('tool.build.no_cost')
        rule = _constraints(row, ctx)
        lines.append(ctx.text('tool.build.catalog_row', name=ctx.label('content', key), cost=cost,
                              behavior=_behavior(row, ctx), rule=('｜' + rule) if rule else ''))
    return '\n'.join(lines)


def _build_receipt(args, receipt, actor, ctx):
    data = receipt.get('data')
    if not isinstance(data, dict) or 'costs' not in data:
        return None
    return ctx.text('tool.build.done', name=ctx.label('content', data['kind']), place=ctx.pos(data, args, actor),
                    costs=ctx.resources(data['costs'], sign='−'))


def _build_caption(args, actor, receipt, ctx):
    parts = [ctx.text('tool.caption.build'), ctx.label('content', args.get('kind', ''))]
    if args.get('count', 1) != 1:
        parts.append('×' + _num(args['count']))
    if 'site' in args or 'x' in args or 'place' in args:
        location = ctx.pos(args=args, actor=actor)
        if location == '所选地块':
            location = ctx.pos((receipt or {}).get('data'), actor=actor)
            if location != '所选地块':
                location = '所选地块 ' + location
        parts.append(location)
    return ' '.join(part for part in parts if part)


def _sites(data, args, actor, ctx):
    ctx.remember({'building_sites': data})
    return _land_panel([{'tile': s, 'resources': s['resources']} for s in data['sites']], {}, actor, ctx)


PANELS = {'building_catalog': _catalog, 'building_sites': _sites}
RECEIPTS = {'build': _build_receipt}
CAPTIONS = {
    'build': _build_caption,
    'building_sites': lambda args, actor, receipt, ctx: ctx.text('tool.caption.building_sites'),
    'building_catalog': lambda args, actor, receipt, ctx: ctx.text('tool.caption.building_catalog'),
}
