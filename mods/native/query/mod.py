"""模型侧只读查询的统一入口。

各模块的只读工具仍然注册、仍可按名调用（玩家状态扇出、脚本、规则 AI 都用得到），
但不进模型工具表；模型只看到 `query(panel=…)`。panel 名与原来的工具名一致，
只有整张地图叫 `map`（原先的 view）。
"""
from engine.runtime import GameMod, Tool

PANELS = {
    'map': 'view', 'land': 'land', 'tile': 'tile',
    'army': 'army', 'reachable': 'reachable', 'frontier': 'frontier',
    'threats': 'threats', 'battle': 'battle', 'logistics': 'logistics',
    'market': 'market', 'econ': 'econ', 'bank': 'bank', 'report': 'report',
    'government': 'government', 'diplomacy': 'diplomacy',
    'intel': 'intel', 'news': 'news', 'dispatches': 'dispatches',
    'countries': 'countries', 'nation': 'nation', 'nations': 'nations',
    'unit_catalog': 'unit_catalog', 'building_catalog': 'building_catalog',
    'terrain_catalog': 'terrain_catalog', 'building_economics': 'building_economics',
    'handbooks': 'handbooks',
}

PROPERTIES = {
    'panel': {'type': 'string', 'enum': sorted(PANELS),
              'description': '要查的面板；不传参数的面板只给 panel 即可。'},
    'place': {'type': 'string', 'description': '地名或 "x y" 坐标串（land/tile）。'},
    'site': {'type': 'string', 'description': '与 place 同义（tile）。'},
    'x': {'type': 'integer'}, 'y': {'type': 'integer'},
    'aid': {'type': 'integer', 'minimum': 1},
    'ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1},
            'minItems': 1, 'uniqueItems': True},
    'attack': {'type': 'boolean', 'description': 'reachable：连攻击可达一起算。'},
    'terrain': {'type': 'string'}, 'resource': {'type': 'string'},
    'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1},
    'kind': {'type': 'string'}, 'level': {'type': 'integer', 'minimum': 1},
    'other_slots': {'type': 'integer', 'minimum': 0},
    'period': {'type': 'integer', 'minimum': 0}, 'all': {'type': 'boolean'},
}
PARAMETERS = {'type': 'object', 'properties': PROPERTIES, 'required': ['panel'],
              'additionalProperties': False}
TYPES = {'panel': str, 'place': str, 'site': str, 'x': int, 'y': int, 'aid': int, 'ids': list,
         'attack': bool, 'terrain': str, 'resource': str, 'offset': int, 'limit': int,
         'kind': str, 'level': int, 'other_slots': int, 'period': int, 'all': bool}

DESCRIPTION = (
    '只读查询统一入口：panel 选面板，其余参数按面板需要。'
    'land=本国土地（含建筑位、已建/在建、能否下单）、tile=单格明细、map=可见地图、'
    'army=全部可见军队、reachable=某军合法目的地、frontier=可及/可拓荒、threats=近邻敌军、'
    'battle=当前交战、logistics=补给预演；market/econ/bank/report=市场、库存与资产、央行、经济报表；'
    'government/diplomacy/intel/news/dispatches=国策、外交、情报、近讯、在途；'
    'countries/nation/nations=外交对象/本国/全部国家；'
    'unit_catalog/building_catalog/terrain_catalog/building_economics=兵种、建筑、地形、建筑收益；'
    'handbooks=策略指南目录（正文用 player_rules）。'
    '同一回合内可一次查询多块面板；已有资料不必重复查询。'
)


class QueryMod(GameMod):
    def mixins(self): return ()
    def provides(self): return ('native:query',)

    def tools(self):
        return (Tool('query', self._query, read_only=True, parameters=PARAMETERS,
                     argument_types=TYPES, description=DESCRIPTION),)

    @staticmethod
    def _query(world, actor, **arguments):
        panel = arguments.pop('panel', None)
        target = PANELS.get(panel)
        if target is None:
            raise ValueError('未知面板：' + str(panel) + '；可用：' + '、'.join(sorted(PANELS)))
        return world.runtime.call(world, actor, target, arguments)
