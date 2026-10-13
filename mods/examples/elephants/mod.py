"""添加一个兵种，并为它添加自己的结算阶段。

兵种用**映射**注册（与资源、建筑的类不同），键固定为八个：
    category  必须是 "unit"
    name_key  展示名键
    hp        整数 ≥ 1
    speed     整数 ≥ 1
    supply    整数 ≥ 0，每期该兵种吃掉的补给
    atk       整数 ≥ 0
    recruit   资源 ID → 正整数，征兵消耗
    move_cost 地形 ID → 整数 ≥ 1，进入该地形的移动力消耗
`move_cost` 的键用 `native:plain` 一类的地形内容 ID。

注册后，兵种自动进入征兵工具的枚举、兵种目录、面板与存档依赖清单，
`mods/native/army/` 无需改动。军费、战斗、移动等既有规则对新兵种同样生效。
"""
from engine.runtime import GameMod, RuleSection, Stage, Tool

WAR_ELEPHANT = {
    'category': 'unit',
    'name_key': 'unit.example:war-elephant.name',
    'hp': 200,
    'speed': 1,
    'supply': 3,
    'atk': 90,
    'recruit': {'native:food': 20, 'native:equipment': 10, 'native:gold': 60},
    'move_cost': {'native:plain': 2, 'native:forest': 3, 'native:hill': 3,
                  'native:mountain': 4, 'native:desert': 2},
}


class ElephantsMod(GameMod):
    def mixins(self):
        return ()

    def content_definitions(self):
        return {'example:war-elephant': dict(WAR_ELEPHANT)}

    def state_keys(self):
        return ()

    def needs(self):
        return ('native:army', 'native:resource-exchange')

    def provides(self):
        return ()

    def tools(self):
        return (Tool('elephant_heads', self._heads, read_only=True,
                     description='本国现有战象头数与本期草料账单'),)

    def stages(self):
        # 挂在 army 的补给挂点上，用 needs 排到它的补给阶段之后。
        # scope 用默认的 'world'：scope='actor' 的参与者是**政体**，不是兵种，
        # "只对某个兵种生效"要在处理函数里按 kind 过滤。
        return (Stage('example:elephants.upkeep', self._upkeep,
                      hook='military.supply', needs=('military.supplied',),
                      provides=('elephants.fed',)),)

    def _read_heads(self, world):
        # 读别的域的数据同样走对象接口；写则只能写自己拥有的状态。
        rows = world.runtime.object_call(world, 'native:army', 'snapshot')
        heads = {}
        for unit in rows:
            if unit.kind == 'example:war-elephant':
                heads[unit.owner] = heads.get(unit.owner, 0) + 1
        return heads

    def _upkeep(self, world, context):
        """战象每期额外消耗粮食；余额不足的国家记入 starved，不改动其库存。"""
        per_head = self.declaration['Elephants']['Upkeep']
        heads = self._read_heads(world)
        fed, starved = {}, {}
        for owner, count in sorted(heads.items()):
            need = per_head * count
            try:
                world.runtime.object_call(world, 'native:resource-exchange', 'exchange',
                                          owner, {'native:food': need}, {})
                fed[owner] = need
            except ValueError:
                starved[owner] = need
        context['elephant_upkeep'] = {'heads': heads, 'fed': fed, 'starved': starved}

    def _heads(self, world, actor):
        per_head = self.declaration['Elephants']['Upkeep']
        count = self._read_heads(world).get(actor, 0)
        return {'heads': count, 'upkeep_per_head': per_head, 'total_food': count * per_head}

    def rule_sections(self, world=None):
        return (RuleSection('战象',
                            f'战象 HP {WAR_ELEPHANT["hp"]}／攻击 {WAR_ELEPHANT["atk"]}／'
                            f'自带补给 {WAR_ELEPHANT["supply"]}，每头每期另耗 '
                            f'{self.declaration["Elephants"]["Upkeep"]} 粮食。',
                            ('战象', '象兵')),)
