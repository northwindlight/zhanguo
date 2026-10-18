from engine.runtime import GameMod, RuleSection, Stage
from .api import BuildingsAPI
from .content import building_types
from .objects import Buildings
from .types import BUILDING_TYPES


class BuildingsMod(GameMod):
    def mixins(self):return ()
    def content_definitions(self):return {cls.TYPE_ID:cls for cls in BUILDING_TYPES}
    def numbers(self):return {cls.TYPE_ID:{'roles':list(cls.ROLES),'costs_by_level':[cls.construction_cost(i) for i in range(1,len(cls.GOLD_COSTS)+1)],'inputs':dict(cls.INPUTS),'outputs':dict(cls.OUTPUTS),'energy_use':cls.ENERGY_USE,'energy_output':cls.ENERGY_OUTPUT,'effects':dict(cls.EFFECTS),'min_slots':cls.MIN_SLOTS,'max_per_site':cls.MAX_PER_SITE,'self_build_limit':cls.SELF_BUILD_LIMIT} for cls in building_types(self.runtime.content)}

    def initialize_world(self, world):
        world.buildings = Buildings(building_types(self.runtime.content),
            lambda method,*args:self.runtime.object_call(world,'native:resource-exchange',method,*args),
            lambda text:self.runtime.object_call(world,'native:territory','resolve',text))

    def objects(self, world):return {'native:buildings':world.buildings}
    def save_state(self, world):return world.buildings.to_save()
    def load_state(self, world, state):world.buildings.restore(state)
    def player_api(self, world):return BuildingsAPI(world)

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
