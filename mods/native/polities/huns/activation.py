from ...core import *
from .parameters import POLITY

def activate(world, name, home=None, start=None):
    start = start or {}
    _def = POLITY["huns"].get("start", {})
    gold = int(start.get("黄金", _def.get("黄金", 1000)))
    supply = int(start.get("补给", _def.get("补给", 200)))
    cav = int(start.get("骑", _def.get("骑", 6)))
    world.nations[name].res.update({"黄金": gold, "粮食": 0, "木头": 0,
                                   "矿石": 0, "石油": 0, "装备": 0, "补给": supply})
    if home is None:
        own = world.own_tiles(name)
        home = max(own, key=lambda p: sum(1 for n in world.neighbors(*p)
                                          if world.owned_by(*n) == name)) if own else None
    if home:
        cx, cy = home
        for i in range(cav):
            world.runtime.object_call(world,'army.units','spawn',name,'骑',cx,cy)
