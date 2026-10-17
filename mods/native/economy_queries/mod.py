from engine.runtime import GameMod
from .api import EconomyAPI
from .objects import EconomicQueries


class EconomicActors:
    @property
    def economy_actors(self):
        return self.runtime.object_call(self,'native:resource-inspection','owners')


class QueriesMod(GameMod):
    def mixins(self):return (EconomicActors,)
    def needs(self):return ('native:resource-inspection','native:buildings','native:economy','native:market','economy:valuation')
    def provides(self):return ('economy:queries',)
    def objects(self, world):
        return {'economy:queries':EconomicQueries(self.runtime,lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))}
    def player_api(self, world):return EconomyAPI(world)
