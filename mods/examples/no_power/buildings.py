"""改一样已有的东西：去掉电力系统。

添加内容用 `content_definitions()` 注册新 ID；**替换**已有内容用同一个入口，
注册一个 TYPE_ID 相同的类把它顶掉。这里把两个电厂从内容表里删掉，
并把其余建筑的耗电改成 0。

内容表是累加的，同一个 ID 注册两次会被拒绝，所以替换必须发生在
「别人不再注册原来的那一份」的前提下 —— 也就是换掉建筑模块本身
（见同目录 buildings.toml，Id 保持 `native:buildings` 不变）。
"""
from mods.native.buildings.mod import BuildingsMod
from mods.native.buildings.types import BUILDING_TYPES

POWER_PLANTS = ('native:wood-power-plant', 'native:oil-power-plant')


def _without_energy(cls):
    """同一个 TYPE_ID、同一个名字，只把耗电改 0。"""
    return type(cls.__name__, (cls,), {'ENERGY_USE': 0})


class NoPowerBuildingsMod(BuildingsMod):
    def content_definitions(self):
        return {cls.TYPE_ID: _without_energy(cls)
                for cls in BUILDING_TYPES if cls.TYPE_ID not in POWER_PLANTS}
