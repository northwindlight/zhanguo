from engine.runtime import GameMod, Panel, RuleSection, Tool
from .objects import Territory, Terrain
from engine.resource import Resource


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
    def tools(self):return (Tool('land',self._land,read_only=True,content_parameters=('terrain','resource'),description='本国土地、地形、资源容量与核心；无参查全部，可按 terrain/resource 短键筛选，再按 offset/limit 分页；无需坐标，站点建筑详见 building_sites。'),
                             Tool('terrain_catalog',lambda w,a:tuple(t for _,t in self.runtime.content.definitions() if isinstance(t,Terrain)),read_only=True,description='全部地形的防御、建造惩罚与骑兵移动代价，无需坐标。'))
    @staticmethod
    def _land(world,actor,terrain: str | None=None,resource: str | None=None,offset: int=0,limit: int | None=None):
        if offset<0 or limit is not None and limit<1:raise ValueError('offset 须非负，limit 须为正整数')
        if terrain is not None and terrain not in {t.id for _,t in world.runtime.content.definitions() if isinstance(t,Terrain)}:raise ValueError('未知地形')
        if resource is not None:
            definition=world.runtime.content.definition(resource)
            if not isinstance(definition,type) or not issubclass(definition,Resource):raise ValueError('未知资源')
        rows=tuple(t for t in world.territory.owned(actor) if (terrain is None or t.tile.terrain==terrain) and (resource is None or dict(t.resources).get(resource,0)>0))
        return rows[offset:] if limit is None else rows[offset:offset+limit]
    def panels(self):return (Panel('land',lambda w,a:w.territory.owned(a)),)
    def rule_sections(self, world=None):return (RuleSection('领土','地块保存地形和核心，当前归属由建筑站点维护。查询未物化地块不改变世界；部队调动不改变土地归属。坐标使用 0-based。',keywords=('地形','terrain','领土')),)
