"""Army toolcalls: actions, movement, combat, catalogs and per-tile facts."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from engine.runtime import Tool
from mods.native.locations import location_parameters, place_arguments, site_arguments


def _num(value):
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def _api(runtime, world):
    return runtime.player_api(world, 'native:army')


def _movement(tool, description):
    params = location_parameters(tool.schema()['function']['parameters'])
    params['properties'].update(aid={'type': 'integer', 'minimum': 1},
                                ids={'type': 'array', 'items': {'type': 'integer', 'minimum': 1},
                                     'minItems': 1, 'uniqueItems': True})
    params['required'] = [key for key in params.get('required', []) if key not in ('aid', 'ids')]
    params['anyOf'] = [{'required': ['aid']}, {'required': ['ids']}]
    return replace(tool, parameters=params, adapter=place_arguments,
                   description=description + ' 军队选择统一为 aid 单个正整数或 ids 非空、不重复的正整数数组；双填仅允许 ids=[aid]。批量行动先校验整批，批量可达查询按 aid 分组返回。')


def tools(runtime):
    def recruit(world, actor, site: str | None = None, kind: str = 'native:infantry',
                count: int = 1, x: int | None = None, y: int | None = None):
        return _api(runtime, world).recruit(actor, site=site, kind=kind, count=count, x=x, y=y)

    def move(world, actor, aid: int | None = None, x: int | None = None, y: int | None = None,
             place: str | None = None, ids: list[int] | None = None):
        return _api(runtime, world).move(actor, aid=aid, ids=ids, x=x, y=y, place=place)

    def attack(world, actor, aid: int | None = None, x: int | None = None, y: int | None = None,
               place: str | None = None, ids: list[int] | None = None):
        return _api(runtime, world).attack(actor, aid=aid, ids=ids, x=x, y=y, place=place)

    def retreat(world, actor, aid: int | None = None, x: int | None = None, y: int | None = None,
                place: str | None = None, ids: list[int] | None = None):
        return _api(runtime, world).retreat(actor, aid=aid, ids=ids, x=x, y=y, place=place)

    def disband(world, actor, aid: int | None = None, ids: list[int] | None = None):
        return _api(runtime, world).disband(actor, aid=aid, ids=ids)

    def army(world, actor):
        return _api(runtime, world).view(actor)

    def reachable(world, actor, aid: int | None = None, attack: bool = False, ids: list[int] | None = None):
        return _api(runtime, world).reachable(actor, aid=aid, attack=attack, ids=ids)

    def frontier(world, actor):
        return _api(runtime, world).frontier(actor)

    def logistics(world, actor):
        return _api(runtime, world).logistics(actor)

    def unit_catalog(world, actor):
        return _api(runtime, world).unit_catalog(actor)

    def threats(world, actor):
        return _api(runtime, world).threats(actor)

    def battle(world, actor):
        return _api(runtime, world).battle(actor)

    def tile(world, actor, x: int | None = None, y: int | None = None, place: str | None = None):
        return _api(runtime, world).tile(actor, x=x, y=y, place=place)

    recruit_tool = Tool('recruit', recruit, owner='native:army', api=('recruit',),
                        content_parameters=('kind',),
                        description='在本国地块征召（site 传地名或 "x y" 坐标串，或传两个整数 x、y），直接返回实际扣款和军队编号。新兵本回合不能行动；兵营与能源门槛查规则，各兵种的征召材料、上限和速度查 unit_catalog。')
    parameters = location_parameters(recruit_tool.schema()['function']['parameters'])
    kinds = sorted(key for key, value in runtime.content.definitions()
                   if isinstance(value, Mapping) and value.get('category') == 'unit')
    parameters['properties']['kind']['enum'] = kinds
    recruit_tool = replace(recruit_tool, parameters=parameters, adapter=site_arguments)

    def schema_for_actor(world, actor, schema):
        result = deepcopy(schema)
        if result['function']['name'] == 'recruit':
            result['function']['parameters']['properties']['kind']['enum'] = list(_api(runtime, world).kinds(actor))
        return result

    recruit_tool = replace(recruit_tool, schema_for_actor=schema_for_actor)

    move_tool = _movement(Tool('move', move, owner='native:army', api=('move',),
                               description='移动本国军队至同一目标格：place 给地名或 "x y" 坐标串，或两个整数 x、y。纯移动不占地。新兵、已移动或交战部队不能普通移动；与异地进攻和撤退共用额度。敌国领土须 attack，路线按地形与兵种速度校验。'),
                          '移动本国军队至同一目标格：place 给地名或 "x y" 坐标串，或两个整数 x、y。纯移动不占地。新兵、已移动或交战部队不能普通移动；与异地进攻和撤退共用额度。敌国领土须 attack，路线按地形与兵种速度校验。')
    attack_tool = _movement(Tool('attack', attack, owner='native:army', api=('attack',),
                                 description='本国军队向同一目标格进攻（place 给地名或 "x y"，或两个整数 x、y）；均须能抵达且有额度。无敌军可直接占领，有守军进入交战并等待结算；不能把提交当战果。他国须已生效战争。新兵不能进攻，原地进攻可保留移动额度。'),
                            '本国军队向同一目标格进攻（place 给地名或 "x y"，或两个整数 x、y）；均须能抵达且有额度。无敌军可直接占领，有守军进入交战并等待结算；不能把提交当战果。他国须已生效战争。新兵不能进攻，原地进攻可保留移动额度。')
    retreat_tool = _movement(Tool('retreat', retreat, owner='native:army', api=('retreat',),
                                  description='交战部队 aid 退往相邻合法格（place 给地名或 "x y"，或两个整数 x、y），与移动共用额度。提交后仍在原地参加本期战斗，结算后才撤离；撤退降低输出，防守撤退有承伤减免。'),
                             '交战部队 aid 退往相邻合法格（place 给地名或 "x y"，或两个整数 x、y），与移动共用额度。提交后仍在原地参加本期战斗，结算后才撤离；撤退降低输出，防守撤退有承伤减免。')
    disband_tool = Tool('disband', disband, owner='native:army', api=('disband',),
                        parameters={'type': 'object', 'properties': {
                            'aid': {'type': 'integer', 'minimum': 1},
                            'ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'uniqueItems': True}},
                            'required': [], 'anyOf': [{'required': ['aid']}, {'required': ['ids']}], 'additionalProperties': False},
                        description='免费遣散本国军队，aid 单支或 ids 数组批量；交战中不可遣散。立即停止维持并释放编制，不返还粮食、装备或金币。 军队选择统一为 aid 单个正整数或 ids 非空、不重复的正整数数组；双填仅允许 ids=[aid]。批量行动先校验整批，批量可达查询按 aid 分组返回。')
    reachable_tool = Tool('reachable', reachable, owner='native:army', api=('reachable',), read_only=True, panel=True,
                          description='按本国军队 aid 列出可见合法移动目的地和路径代价；attack=true 列攻击可达地。只接军队编号，无需目标坐标，不透露迷雾路径。')
    params = reachable_tool.schema()['function']['parameters']
    params['properties'].update(aid={'type': 'integer', 'minimum': 1},
                                ids={'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'uniqueItems': True})
    params['required'] = [key for key in params.get('required', []) if key not in ('aid', 'ids')]
    params['anyOf'] = [{'required': ['aid']}, {'required': ['ids']}]
    reachable_tool = replace(reachable_tool, parameters=params,
                             description=reachable_tool.description + ' 军队选择统一为 aid 单个正整数或 ids 非空、不重复的正整数数组；双填仅允许 ids=[aid]。批量行动先校验整批，批量可达查询按 aid 分组返回。')

    tile_tool = Tool('tile', tile, owner='native:army', api=('tile',), read_only=True, panel=True,
                     description='唯一按单格查询的工具：place 给地名或 "x y"，或两个整数 x、y；返回可见单格详情（有主地块显示地名，无主只显示坐标）。本国及物化无主遗址显示资源、容量、施工与建筑；他国仅显示地形、归属、公开城堡和市政厅及可见军队。迷雾不可查。')
    tile_params = location_parameters(tile_tool.schema()['function']['parameters'])
    tile_tool = replace(tile_tool, parameters=tile_params, adapter=place_arguments)

    return (
        recruit_tool,
        move_tool,
        attack_tool,
        retreat_tool,
        disband_tool,
        Tool('army', army, owner='native:army', api=('view',), read_only=True, fact=True, panel=True,
             description='一次列出本国及视野内军队：国家、整数编号、兵种、位置、HP和行动/交战/撤退状态；无需坐标。军队编号各国独立。'),
        reachable_tool,
        Tool('frontier', frontier, owner='native:army', api=('frontier',), read_only=True, fact=True, panel=True,
             description='全国可见无主地总数、本回合可行动军队逐支可及格数（units）、并集目的地与可拓荒地；无需坐标。'),
        Tool('logistics', logistics, owner='native:army', api=('logistics',), read_only=True, panel=True,
             description='按本国当前军队、军屯与库存只读预演补给：需求、可用、缺口、免供支数和断粮损伤；不扣资源，不含后续生产与行动，无需参数。'),
        Tool('unit_catalog', unit_catalog, owner='native:army', api=('unit_catalog',), read_only=True, fact=True, panel=True,
             description='全部兵种目录：征召材料、满编HP、攻击、速度、地形移动代价与补给；应用本国政策，无需坐标。'),
        Tool('threats', threats, owner='native:army', api=('threats',), read_only=True, fact=True, panel=True,
             description='全视野内其他国家的军队和当前关系，排除本国与野人；无需坐标。'),
        Tool('battle', battle, owner='native:army', api=('battle',), read_only=True, fact=True, panel=True,
             description='当前全部可见或本国参与交战：各方兵种/逐支数量、输出基数、地形/城堡减伤与撤退。盲战仅列本国，不泄露敌军；不是按目标预测战果。'),
        tile_tool,
    )


# ---- receipts / panels -------------------------------------------------------

def _army_line(ctx, unit):
    state = 'newborn' if unit.get('born_turn') == ctx._period else 'engaged' if unit.get('engaged') else 'moved' if unit.get('moved_turn') == ctx._period else 'ready'
    status = '｜' + ctx.text('tool.army.' + state)
    catalog = ctx._facts.get(ctx._actor, {}).get('unit_catalog', {})
    if unit['owner'] == ctx._actor and unit['kind'] in catalog:
        status += '｜' + ctx.text('tool.army.speed', speed=catalog[unit['kind']]['speed'])
    reach = {row['id']: row.get('reach', 0) for row in (ctx._facts.get(ctx._actor, {}).get('frontier') or {}).get('units', [])}
    if unit['owner'] == ctx._actor and state == 'ready' and unit['id'] in reach:
        status += '｜' + ctx.text('tool.army.reach', count=reach[unit['id']])
    if unit.get('retreat_to') is not None:
        status += '｜' + ctx.text('tool.army.retreat_to', place=ctx.pos(unit['retreat_to']), cover=_num(unit.get('retreat_cover')))
    return ctx.text('tool.army.line', owner=ctx.name(unit['owner'], ctx._names),
                    kind=ctx.label('content', unit['kind']), id=unit['id'],
                    place=ctx.pos([unit['x'], unit['y']], actor=unit['owner']), hp=_num(unit['hp']), status=status)


def _relation(value, ctx):
    return ctx.text('tool.relation.' + value)


def _army_list(data, args, actor, ctx):
    own = sorted((u for u in data if u['owner'] == actor), key=lambda u: u['id'])
    foreign = sorted((u for u in data if u['owner'] not in (actor, '野人')), key=lambda u: (u['owner'], u['id']))
    guards = [u for u in data if u['owner'] == '野人']
    lines = [ctx.text('tool.army.head', own=len(own), foreign=len(foreign), guards=len(guards))]
    lines.extend(_army_line(ctx, u) for u in own)
    lines.extend(_army_line(ctx, u) for u in foreign)
    if guards:
        lines.append(ctx.text('tool.army.guard_hint', count=len(guards)))
    return '\n'.join(lines)


def _battles(data, args, actor, ctx):
    lines = [ctx.text('tool.battle.head')]
    for row in data:
        lines.append(ctx.pos(row['position']) + ('｜' + ctx.text('tool.battle.blind') if row['blind'] else '｜' + ctx.label('content', row['terrain'])))
        for side in row['sides']:
            role = ctx.text('tool.battle.role_' + side['role'])
            lines.append(ctx.text('tool.battle.side', owner=ctx.name(side['owner'], ctx._names), role=role,
                                  count=side['count'], output=_num(side['output_base']),
                                  terrain=_num(side['terrain_reduction']), castle=_num(side['castle_reduction']),
                                  reduction=_num(side['combined_reduction'])))
            lines.extend('    ' + _army_line(ctx, unit) for unit in side['units'])
    if not data:
        lines.append(ctx.text('tool.battle.none'))
    return '\n'.join(lines)


def _reachable(data, args, actor, ctx):
    if not data:
        return ctx.text('tool.reachable.empty')
    if isinstance(data[0], dict) and 'destinations' in data[0]:
        return '\n\n'.join(ctx.unit_ref(row['aid'], actor) + '\n' + _reachable(row['destinations'], args, actor, ctx) for row in data)
    lines = [ctx.text('tool.reachable.header', count=len(data))]
    visible = {tuple(tile['position']): tile for tile in ctx._facts.get(actor, {}).get('view', [])}
    for position, cost in data:
        tile = visible.get(tuple(position), {})
        line = ctx.text('tool.reachable.destination', place=ctx.pos(position, actor=actor), cost=_num(cost),
                        terrain=ctx.label('content', tile.get('terrain', '—')))
        buildings = dict(tile.get('buildings', []))
        for kind in ('native:town-hall', 'native:castle'):
            if buildings.get(kind):
                line += '｜' + ctx.label('content', kind) + (' L' if kind == 'native:castle' else '×') + str(buildings[kind])
        if cost == 0:
            line += '｜' + ctx.text('tool.reachable.current')
        lines.append(line)
    lines.append(ctx.text('tool.reachable.assumptions'))
    return '\n'.join(lines)


def _logistics(data, args, actor, ctx):
    lines = [ctx.text('tool.logistics.supply', **{key: _num(data[key]) for key in ('period', 'need', 'available', 'used', 'shortfall')}),
             ctx.text('tool.logistics.damage', damage=_num(data['starvation_damage_per_unit']), exempt=_num(data['exempt_count']))]
    if data['starving_units']:
        lines.append(ctx.text('tool.logistics.units', units='、'.join(ctx.unit_ref(uid, actor) for uid in data['starving_units'])))
    lines.append(ctx.text('tool.logistics.assumptions'))
    return '\n'.join(lines)


def _tile(data, args, actor, ctx):
    title = f'{ctx.pos(data, actor=actor)} {ctx.label("content", data["terrain"])} · ' + ctx.name(data.get('owner') or ctx.text('tool.unowned'), ctx._names)
    details = []
    if 'resources' in data:
        details.append(ctx.text('tool.tile.resources', resources=ctx.resources(data['resources']), capacity=data['capacity']))
    details.append(ctx.text('tool.tile.buildings', names=ctx.building_names([b for b in data['buildings'] if not b['remaining']])))
    pending = [b for b in data['buildings'] if b['remaining']]
    if pending:
        details.append(ctx.text('tool.tile.pending', names=ctx.building_names(pending)))
    if data.get('army'):
        details.append(ctx.text('tool.tile.army', units='、'.join(ctx.name(u['owner'], ctx._names) + ' #' + str(u['id']) + ' ' + ctx.label('content', u['kind']) + ' HP' + _num(u['hp']) for u in data['army'])))
    return title + '\n' + '\n'.join(details)


def _frontier(data, args, actor, ctx):
    lines = [ctx.text('tool.frontier.head', seen=data['visible_unowned'], reachable=len(data['reachable']), frontier=len(data['frontier']))]
    for warning in data.get('capital_warning', []):
        enemies = '、'.join(ctx.name(u['owner'], ctx._names) + ' ' + ctx.label('content', u['kind']) + '#' + str(u['id']) for u in warning['enemies'])
        lines.append(ctx.text('tool.frontier.warning', place=ctx.pos(warning, actor=actor), enemies=enemies))
    for key in ('frontier', 'reachable'):
        lines.append(ctx.text('tool.frontier.' + key, places='、'.join(ctx.pos(p, actor=actor) for p in data[key]) or ctx.text('tool.none')))
    return '\n'.join(lines)


def _unit_catalog(data, args, actor, ctx):
    return ctx.text('tool.unit_catalog.head') + '\n' + '\n'.join(
        ctx.text('tool.unit_catalog.row', name=ctx.label('content', key), recruit=ctx.resources(value['recruit']),
                 hp=value['hp'], atk=value['atk'], speed=value['speed'], supply=value['supply']) +
        ('｜' + ctx.text('tool.move_costs', values='、'.join(ctx.label('content', terrain) + ' ' + _num(cost) for terrain, cost in value['move_cost'].items())) if value.get('move_cost') else '')
        for key, value in data.items())


def _threats(data, args, actor, ctx):
    return ctx.text('tool.threats.head') + '\n' + ('\n'.join(_relation(row['relation'], ctx) + '｜' + _army_line(ctx, row['unit']) for row in data) or ctx.text('tool.none'))


def _recruit(args, receipt, actor, ctx):
    data = receipt.get('data')
    if not isinstance(data, list):
        return None
    if not data:
        return ctx.text('tool.army.empty')
    return ctx.text('tool.recruit.done') + '\n' + '\n'.join(_army_line(ctx, u) for u in data)


def _movement_receipt(action):
    def render(args, receipt, actor, ctx):
        data = receipt.get('data')
        if not isinstance(data, dict):
            return None
        if data.get('status') == 'failed':
            return '❌ ' + data.get('message', '')
        units = '、'.join(ctx.unit_ref(i, actor) for i in data.get('units', []))
        where = ctx.pos(args=args, actor=actor)
        if action == 'disband':
            return ctx.text('tool.disband.done', units=units)
        if action == 'retreat':
            return ctx.text('tool.retreat.done', units=units, place=where)
        if action == 'attack':
            if data.get('occupied'):
                return ctx.text('tool.attack.occupied', units=units, place=where)
            if data.get('engaged'):
                return ctx.text('tool.attack.engaged', units=units, place=where)
            return ctx.text('tool.attack.none', units=units, place=where)
        return ctx.text('tool.move.done', units=units, place=where)
    return render


PANELS = {
    'army': _army_list,
    'frontier': _frontier,
    'logistics': _logistics,
    'unit_catalog': _unit_catalog,
    'threats': _threats,
    'battle': _battles,
    'reachable': _reachable,
    'tile': _tile,
}
RECEIPTS = {
    'recruit': _recruit,
    'move': _movement_receipt('move'),
    'attack': _movement_receipt('attack'),
    'retreat': _movement_receipt('retreat'),
    'disband': _movement_receipt('disband'),
}


def _caption(action):
    def render(args, actor, receipt, ctx):
        if action == 'reachable':
            ids = args.get('ids', [args.get('aid', '?')])
            return ctx.text('tool.reachable.caption', title=ctx.text('tool.caption.reachable'),
                            unit='、'.join(ctx.unit_ref(aid, actor) for aid in ids),
                            mode=ctx.text('tool.reachable.attack' if args.get('attack', False) else 'tool.reachable.move'))
        if action == 'recruit':
            parts = [ctx.text('tool.caption.recruit'), ctx.label('content', args.get('kind', ''))]
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
        if action in ('move', 'retreat', 'attack'):
            units = '、'.join(ctx.unit_ref(n, actor) for n in args['ids']) if 'ids' in args else ctx.unit_ref(args.get('aid', '?'), actor)
            return ctx.text('tool.caption.' + action, units=units, place=ctx.pos(args=args, actor=actor))
        if action == 'disband':
            units = '、'.join(ctx.unit_ref(n, actor) for n in args['ids']) if 'ids' in args else ctx.unit_ref(args.get('aid', '?'), actor)
            return ctx.text('tool.caption.disband', units=units)
        return ctx.text('tool.caption.' + action)
    return render


CAPTIONS = {name: _caption(name) for name in ('recruit', 'move', 'retreat', 'attack', 'disband', 'reachable',
                                              'army', 'frontier', 'logistics', 'unit_catalog', 'threats', 'battle', 'tile')}
