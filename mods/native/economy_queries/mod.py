from engine.runtime import GameMod, Tool
from .objects import EconomicQueries
from mods.native.buildings.types import NativeBuilding


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
    def _queries(self, world):return self.objects(world)['economy:queries']
    def tools(self):
        kinds = sorted(key for key,cls in self.runtime.content.definitions()
                       if isinstance(cls,type) and issubclass(cls,NativeBuilding))
        estimate_parameters = {'type':'object','properties':{
            'kind':{'type':'string','enum':kinds},'level':{'type':'integer','minimum':1},
            'other_slots':{'type':'integer','minimum':0}},'required':['kind'],'additionalProperties':False}
        return (Tool('econ',lambda world,actor:self._queries(world).balance(actor),read_only=True,
                     description='Current inventory and replacement assets; unpriced content stays explicit'),
                Tool('report',lambda world,actor:self._queries(world).report(actor),read_only=True,
                     description='Own published economic reports'),
                Tool('building_economics',self._estimate,read_only=True,parameters=estimate_parameters,
                     description='Estimate full-capacity returns at current valuation; assumes cheapest registered energy'),
                Tool('market',lambda world,actor:self._queries(world).market(),read_only=True),
                Tool('market_quote',self._quote,read_only=True),
                Tool('buy',self._buy),Tool('sell',self._sell))
    def _estimate(self, world, actor, kind: str, level: int=1, other_slots: int=0):
        return self._queries(world).estimate(kind,level,other_slots)
    def _quote(self, world, actor, resource: str, quantity: int, side: str):
        return self.runtime.object_call(world,'native:market','quote',resource,quantity,side,actor)
    def _buy(self, world, actor, resource: str, quantity: int):
        return self.runtime.object_call(world,'native:market','buy',actor,resource,quantity)
    def _sell(self, world, actor, resource: str, quantity: int):
        return self.runtime.object_call(world,'native:market','sell',actor,resource,quantity)
