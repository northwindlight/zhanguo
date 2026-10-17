from engine.runtime import GameMod, RuleSection
from .api import TerritoryAPI
from .objects import Territory, Terrain


TERRAINS = (Terrain('native:plain',0,0,1),Terrain('native:forest',10,15,2),
            Terrain('native:hill',25,25,1),Terrain('native:mountain',50,50,2),Terrain('native:desert',-10,40,1))


class TerritoryMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('territory',)
    def needs(self):return ('map:generation','native:nation-inspection','native:buildings')
    def provides(self):return ('native:territory',)
    def content_definitions(self):return {t.id:t for t in TERRAINS}
    def initialize_world(self, world):
        types = tuple(t for _,t in self.runtime.content.definitions() if isinstance(t,Terrain))
        world.territory = Territory(types,lambda cap,method,*args,**kwargs:self.runtime.object_call(world,cap,method,*args,**kwargs))
    def objects(self, world):return {'native:territory':world.territory}
    def save_state(self, world):return world.territory.to_save()
    def load_state(self, world, state):world.territory.restore(state)
    def player_api(self, world):return TerritoryAPI(world)
    def rule_sections(self, world=None):return (RuleSection('领土','地块保存地形和核心，当前归属由建筑站点维护。查询未物化地块不改变世界；部队调动不改变土地归属。所有坐标统一 0-based。',keywords=('地形','terrain','领土')),)
