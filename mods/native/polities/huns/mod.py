from ...base import DomainMod

class HunsMod(DomainMod):
    package = "mods.native.polities.huns"
    logic_class = ""

    def parameter_overrides(self):
        from .parameters import POLITY
        return POLITY['huns']
    def activate_actor(self, world, actor, **kwargs):
        from .activation import activate
        return activate(world, actor, **kwargs)
    def prompt(self, world, actor):
        from .views import _huns_prompt
        return _huns_prompt(world, actor)
    def appearance_description(self, world, actor, start=None):
        params=self.parameter_overrides()
        initial={**params['start'], **(start or {})}
        return f"匈奴 {actor} 登场：开局 {initial['骑']} 骑兵·金{initial['黄金']}·补给{initial['补给']}；建筑+{params['build_cost_pct']-100}%惩罚"
