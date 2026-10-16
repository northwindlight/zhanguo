from engine.runtime import GameMod, RuleSection, Stage, Tool
from .content import building_types
from .objects import Buildings
from .types import BUILDING_TYPES


class BuildingsMod(GameMod):
    def mixins(self):return ()
    def content_definitions(self):return {cls.TYPE_ID:cls for cls in BUILDING_TYPES}
    def numbers(self):return {cls.TYPE_ID:{'costs_by_level':[cls.construction_cost(i) for i in range(1,len(cls.GOLD_COSTS)+1)],'inputs':dict(cls.INPUTS),'outputs':dict(cls.OUTPUTS),'energy_use':cls.ENERGY_USE,'energy_output':cls.ENERGY_OUTPUT,'effects':dict(cls.EFFECTS),'min_slots':cls.MIN_SLOTS,'max_per_site':cls.MAX_PER_SITE,'self_build_limit':cls.SELF_BUILD_LIMIT} for cls in building_types(self.runtime.content)}
    def state_keys(self):return ('buildings',)
    def needs(self):return ('native:resource-exchange',)
    def optional(self):return ('native:territory',)
    def provides(self):return ('native:buildings',)

    def initialize_world(self, world):
        world.buildings = Buildings(building_types(self.runtime.content),
            lambda method,*args:self.runtime.object_call(world,'native:resource-exchange',method,*args),
            lambda text:self.runtime.object_call(world,'native:territory','resolve',text))

    def objects(self, world):return {'native:buildings':world.buildings}
    def save_state(self, world):return world.buildings.to_save()
    def load_state(self, world, state):world.buildings.restore(state)

    def tools(self):
        kinds = sorted(cls.TYPE_ID for cls in building_types(self.runtime.content))
        parameters = {'type':'object','properties':{
            'site':{'type':'string','description':'站点 ID、地名或坐标串（如 "5 6"）'},
            'x':{'type':'integer','minimum':0},'y':{'type':'integer','minimum':0},
            'kind':{'type':'string','enum':kinds}},
            'required':['kind'],'additionalProperties':False,
            'oneOf':[{'required':['site'],'not':{'anyOf':[{'required':['x']},{'required':['y']}]}},
                     {'required':['x','y'],'not':{'required':['site']}}]}
        return (Tool('build',self._build,parameters=parameters,content_parameters=('kind',),
                     description='在本国地块动工：site 传地名或 "x y" 坐标串，或直接传两个整数 x、y。直接返回实际扣款和施工回执；参数或材料不符时返回原因。每格每回合一次，结算落成。造价、门槛查 building_catalog，全部收益查 building_economics。'),
                Tool('building_sites',self._sites,read_only=True,description='一次查看全部本国站点：坐标、资源容量、建筑位、已建/在建、当期施工与自建计数。无需地块参数。'),
                Tool('building_catalog',self._catalog,read_only=True,
                     description='一次列齐全部建筑：各级造价、产销配方、能源、效果与门槛。无需地块或坐标。'))

    @staticmethod
    def _build(world, actor, kind: str, site: str | None=None, x: int | None=None, y: int | None=None):
        site=world.buildings.resolve_site(actor,site,x,y)
        return world.buildings.build(actor,site,kind,policy_pct=world.runtime.parameter(world,actor,'build_cost_pct',100))

    @staticmethod
    def _sites(world, actor):
        snap=world.buildings.snapshot();sites=[s for s in snap.sites if s.owner==actor];ids={s.id for s in sites}
        return {'sites':sites,'instances':[b for b in snap.instances if b.site in ids],
                'construction':[r for r in snap.construction if r.owner==actor],
                'self_built':{kind:count for owner,kind,count in snap.self_built if owner==actor}}

    @staticmethod
    def _catalog(world, actor):
        return [{'id':cls.TYPE_ID,'min_slots':cls.MIN_SLOTS,'max_per_site':cls.MAX_PER_SITE,
                 'self_build_limit':cls.SELF_BUILD_LIMIT,'site_resource':cls.SITE_RESOURCE,
                 'energy_use':cls.ENERGY_USE,'energy_output':cls.ENERGY_OUTPUT,
                 'inputs':dict(cls.INPUTS),'outputs':dict(cls.OUTPUTS),
                 'effects':dict(cls.EFFECTS),'build_discount':cls.BUILD_DISCOUNT,
                 'policy_pct':world.runtime.parameter(world,actor,'build_cost_pct',100),
                 'costs_by_level':[cls.construction_cost(level) for level in range(1,len(cls.GOLD_COSTS)+1)]}
                for cls in building_types(world.runtime.content)]

    def rule_sections(self, world=None):
        return (RuleSection('建筑与能源',
                'building_catalog 一次返回全部建筑的基础造价（各等级）、投入产出、耗电发电、效果和建造门槛。\n'
                'building_economics 不带参数时一次返回所有建筑的完整收益报告，不接受地块参数（需加载经济查询模块）。\n'
                'build 才会扣除材料并动工；每地块每回合只能动工一次，施工占建筑位，结算后落成，从下一周期开始生产。\n'
                '采集建筑受本地资源容量限制；兵营等建筑须满足已有建筑门槛。地形只提高金币造价，已落成工程院减免本格后续建筑金币造价25%，木材不变。\n'
                '电力不储存、不交易，燃料不足的发电厂无法发电，电网不足会使耗电建筑停工；仅有燃料不代表已有电厂。',
                keywords=('build','building','construction','energy','power','能源','电力','建筑','兵营','工程院','军屯','市政厅','城堡','林场','农场','矿场','油电','木电')),)

    def stages(self):
        handlers = {'produce':self._produce,'complete':self._complete}
        return tuple(Stage(row['Id'],handlers[row['Handler']],hook=row['Hook'],
                           needs=tuple(row.get('Needs',())),provides=tuple(row.get('Provides',())),
                           requires=tuple(row.get('Requires',())),after=tuple(row.get('After',())),
                           before=tuple(row.get('Before',())))
                     for row in self.declaration.get('Stages',()))

    @staticmethod
    def _produce(world, context):
        report = world.buildings.produce(external_energy=context.get('building_energy',{}))
        context['building_production'] = report

    @staticmethod
    def _complete(world, context):
        context['building_completions'] = world.buildings.complete_construction()
