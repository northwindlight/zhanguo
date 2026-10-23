"""Territory toolcalls: owned land and the terrain catalog."""
from engine.runtime import Tool


def _num(value):
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def tools(runtime):
    def land(world, actor, terrain: str | None = None, resource: str | None = None,
             offset: int = 0, limit: int | None = None):
        return runtime.player_api(world, 'native:territory').land(actor, terrain=terrain, resource=resource, offset=offset, limit=limit)

    def terrain_catalog(world, actor):
        return runtime.player_api(world, 'native:territory').terrain_catalog(actor)

    return (
        Tool('land', land, owner='native:territory', api=('land',), read_only=True, fact=True, panel=True,
             content_parameters=('terrain', 'resource'),
             description='本国土地、地形、资源容量与核心；无参查全部，可按 terrain/resource 短键筛选，再按 offset/limit 分页；无需坐标，站点建筑详见 building_sites。'),
        Tool('terrain_catalog', terrain_catalog, owner='native:territory', api=('terrain_catalog',), read_only=True, fact=True, panel=True,
             description='全部地形的防御、建造惩罚与骑兵移动代价，无需坐标。'),
    )


def _land(data, args, actor, ctx):
    facts = ctx._facts.get(actor, {})
    sites = facts.get('building_sites', {})
    instances = sites.get('instances')
    args = args or {}
    paged = any(key in args for key in ('terrain', 'resource', 'offset', 'limit'))
    if paged:
        lines = [ctx.text('tool.land.page', count=len(data), offset=args.get('offset', 0))]
        if 'land' in facts:
            lines[0] += '｜' + ctx.text('tool.land.total', count=len(facts['land']))
        if args.get('terrain'):
            lines[0] += '｜' + ctx.label('content', args['terrain'])
        if args.get('resource'):
            lines[0] += '｜' + ctx.label('content', args['resource'])
        more = any(args.get(key) for key in ('terrain', 'resource')) or 'land' not in facts or args.get('offset', 0) + len(data) < len(facts['land'])
        if args.get('limit') and len(data) == args['limit'] and more:
            lines.append(ctx.text('tool.land.next', offset=args.get('offset', 0) + len(data)))
    else:
        lines = [ctx.text('tool.land.head', count=len(data))]
    cores = {tuple(r['tile']['position']): r['tile'].get('core') for r in facts.get('land', [])}
    for row in data:
        tile = row.get('tile', row)
        site = ctx._sites.get(tile.get('id'), {})
        completed = [b for b in instances or [] if b['site'] == tile.get('id') and not b['remaining']]
        pending = [b for b in instances or [] if b['site'] == tile.get('id') and b['remaining']]
        buildings = ctx.building_names(completed)
        if pending:
            buildings += '；' + ctx.text('tool.land.pending', names=ctx.building_names(pending))
        resources = ctx.resources(row.get('resources', site.get('resources', [])), gold_short=False)
        cap = '｜' + ctx.text('tool.land.capacity', used=len(completed) + len(pending), capacity=site['capacity']) if 'capacity' in site and instances is not None else ''
        terrain = ctx.label('content', tile.get('terrain') or ctx._terrain.get(tile.get('id')) or '')
        position = tuple(tile.get('position', ()))
        core = tile.get('core', cores.get(position))
        details = []
        if core:
            details.append(ctx.text('tool.land.core', name=ctx.name(core, ctx._names)))
        if 'construction' in sites:
            ordered = any(r['site'] == tile.get('id') and r['period'] == ctx._period for r in sites['construction'])
            details.append(ctx.text('tool.land.ordered' if ordered else 'tool.land.can_build'))
        guards = [u for u in facts.get('army', []) if u['owner'] == actor and (u['x'], u['y']) == position and u['hp'] > 0]
        if guards:
            details.append(ctx.text('tool.land.garrison', units='、'.join(ctx.unit_ref(u['id'], actor) + ' ' + _num(u['hp']) + 'HP' for u in guards)))
        lines.append(f'{ctx.pos(tile, actor=actor)}{terrain}{cap}｜' + ctx.text('tool.land.resources', resources=resources) +
                     ('｜' + ctx.text('tool.land.buildings', names=buildings) if instances is not None else '') +
                     ('｜' + '｜'.join(details) if details else ''))
    return '\n'.join(lines)


def _terrain(data, args, actor, ctx):
    return '\n'.join(ctx.text('tool.terrain.blocked',name=ctx.label('content',row['id'])) if not row.get('passable',True) else
                    ctx.text('tool.terrain.row', name=ctx.label('content', row['id']), defense=row['defense'],
                              penalty=row['build_penalty'], cost=row['cavalry_cost']) for row in data)


PANELS = {'land': _land, 'terrain_catalog': _terrain}
RECEIPTS = {}
CAPTIONS = {
    'land': lambda args, actor, receipt, ctx: ctx.text('tool.caption.land'),
    'terrain_catalog': lambda args, actor, receipt, ctx: ctx.text('tool.caption.terrain_catalog'),
}
