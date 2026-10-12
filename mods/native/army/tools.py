from __future__ import annotations
from ..core import *
from engine.runtime import Tool

def tool_recruit(world, actor, **args):
    from ..presentation import _tile_xy
    tool = 'recruit'
    ref = str(args.get('tile', ''))
    xy = _tile_xy(world, actor, ref)
    if xy is None:
        return f'地块引用无效：{ref}'
    ok, msg = world.recruit(actor, xy[0], xy[1], int(args.get('n', 1)), str(args.get('kind', '步') or '步'))
    return msg

def tool_move(world, actor, **args):
    tool = 'move'
    aid = int(args.get('army_id', 0))
    try:
        x, y = (int(args['x']) - 1, int(args['y']) - 1)
    except (KeyError, TypeError, ValueError):
        return '需要目标坐标 x y（1-based）'
    ok, msg = world.move(actor, aid, x, y)
    return msg

def tool_attack(world, actor, **args):
    tool = 'attack'
    aids = args.get('army_ids', args.get('army_id'))
    if isinstance(aids, int):
        aids = [aids]
    aids = [int(a) for a in aids] if isinstance(aids, list) else []
    try:
        x, y = (int(args['x']) - 1, int(args['y']) - 1)
    except (KeyError, TypeError, ValueError):
        return '需要目标坐标 x y'
    ok, msg = world.attack(actor, aids, x, y)
    return msg

def tool_retreat(world, actor, **args):
    tool = 'retreat'
    aid = int(args.get('army_id', 0))
    try:
        x, y = (int(args['x']) - 1, int(args['y']) - 1)
    except (KeyError, TypeError, ValueError):
        return '需要撤退目标坐标 x y'
    ok, msg = world.retreat(actor, aid, x, y)
    return msg

def tool_disband(world, actor, **args):
    tool = 'disband'
    aids = args.get('army_ids', args.get('army_id'))
    if isinstance(aids, int):
        aids = [aids]
    aids = [int(a) for a in aids] if isinstance(aids, list) else []
    ok, msg = world.disband(actor, aids)
    return msg

def definitions():
    return (
        Tool('recruit', tool_recruit, aliases=('征兵', 'r'), argument_types={'tile': str, 'n': int, 'kind': str}),
        Tool('move', tool_move, aliases=('mv', '移动'), argument_types={'army_id': int, 'x': int, 'y': int}),
        Tool('attack', tool_attack, aliases=('atk', '进攻'), argument_types={'army_ids': list[int], 'x': int, 'y': int}),
        Tool('retreat', tool_retreat, aliases=('撤', '撤出'), argument_types={'army_id': int, 'x': int, 'y': int}),
        Tool('disband', tool_disband, aliases=('遣散', '裁军', '复员', '解散军队'), argument_types={'army_ids': list[int]}),
    )
