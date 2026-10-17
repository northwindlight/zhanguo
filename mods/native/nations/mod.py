from engine.runtime import GameMod
from .api import NationAPI
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
    def player_api(self, world):return NationAPI(world)
