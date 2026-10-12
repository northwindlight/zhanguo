from __future__ import annotations
from ..core import *
from engine.runtime import Tool

def tool_build(world, actor, **args):
    from ..presentation import _tile_xy
    tool = 'build'
    ref = str(args.get('tile', ''))
    xy = _tile_xy(world, actor, ref)
    if xy is None:
        return f"地块引用无效：{ref}（用坐标如 '5 6' 或自家地块名）"
    ok, msg = world.build(actor, xy[0], xy[1], str(args.get('building', '')))
    return msg

def definitions():
    return (
        Tool('build', tool_build, aliases=('建', '建造'), argument_types={'tile': str, 'building': str}),
    )
