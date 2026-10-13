from engine.runtime import GameMod, Tool
from .objects import Nations, NationInspection


class NationActors:
    @property
    def nation_actors(self):
        return self.runtime.object_call(self, 'native:nation-inspection', 'ids')


class NationsMod(GameMod):
    def mixins(self):return (NationActors,)
    def state_keys(self):return ('nations',)
    def needs(self):return ('native:resources', 'native:buildings')
    def provides(self):return ('native:nations', 'native:nation-inspection')

    def initialize_world(self, world):
        world.nations = Nations(
            lambda method, *args: self.runtime.object_call(world, 'native:resources', method, *args),
            lambda method, *args: self.runtime.object_call(world, 'native:buildings', method, *args))

    def objects(self, world):
        return {'native:nations': world.nations,
                'native:nation-inspection': NationInspection(world.nations)}

    def save_state(self, world):return world.nations.to_save()
    def load_state(self, world, state):world.nations.restore(state)

    def tools(self):
        return (Tool('nations', lambda world, actor: world.nations.snapshot(), read_only=True,
                     description='全部国家的公开身份与登场/灭亡记录；无需参数。'),
                Tool('nation', lambda world, actor: world.nations.overview(actor), read_only=True,
                     description='本国身份与当前拥有的站点；无需参数，站点明细见 building_sites。'))
