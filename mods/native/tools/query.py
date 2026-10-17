"""The single read-only panel dispatcher, generated from panel tool metadata."""
from engine.runtime import Tool

PROPERTIES = {
    'place': {'type': 'string', 'description': '地名或 "x y" 坐标串（land/tile）。'},
    'site': {'type': 'string', 'description': '与 place 同义（tile）。'},
    'x': {'type': 'integer'}, 'y': {'type': 'integer'},
    'aid': {'type': 'integer', 'minimum': 1},
    'ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'uniqueItems': True},
    'attack': {'type': 'boolean', 'description': 'reachable：连攻击可达一起算。'},
    'terrain': {'type': 'string'}, 'resource': {'type': 'string'},
    'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1},
    'kind': {'type': 'string'}, 'level': {'type': 'integer', 'minimum': 1},
    'other_slots': {'type': 'integer', 'minimum': 0},
    'period': {'type': 'integer', 'minimum': 0}, 'all': {'type': 'boolean'},
}
TYPES = {'panel': str, 'place': str, 'site': str, 'x': int, 'y': int, 'aid': int, 'ids': list,
         'attack': bool, 'terrain': str, 'resource': str, 'offset': int, 'limit': int,
         'kind': str, 'level': int, 'other_slots': int, 'period': int, 'all': bool}

DESCRIPTION = (
    '只读查询统一入口：panel 选面板，其余参数按面板需要。'
    'land=本国土地（含建筑位、已建/在建、能否下单）、tile=单格明细、map=可见地图、'
    'army=全部可见军队、reachable=某军合法目的地、frontier=可及/可拓荒、threats=近邻敌军、'
    'battle=当前交战、logistics=补给预演；market/econ/bank/report=市场、库存与资产、央行、经济报表；'
    'government/intel/news/dispatches=国策、情报、近讯、在途；'
    'foreign=本国与全部对外关系（各国身份、关系、条约、联盟、战争、提议与投票）；'
    'unit_catalog/building_catalog/terrain_catalog/building_economics=兵种、建筑、地形、建筑收益；'
    'handbooks=策略指南目录（正文用 player_rules）。'
    '同一回合内可一次查询多块面板；已有资料不必重复查询。'
)


def build(runtime, specs):
    panels = {('map' if tool.name == 'view' else tool.name): tool.name for tool in specs if tool.panel}

    def query(world, actor, **arguments):
        panel = arguments.pop('panel', None)
        target = panels.get(panel)
        if target is None:
            raise ValueError('未知面板：' + str(panel) + '；可用：' + '、'.join(sorted(panels)))
        return runtime.call(world, actor, target, arguments)

    properties = {**PROPERTIES, 'panel': {'type': 'string', 'enum': sorted(panels),
                                          'description': '要查的面板；不传参数的面板只给 panel 即可。'}}
    return Tool('query', query, owner='native:query', read_only=True, parameters={
        'type': 'object', 'properties': properties, 'required': ['panel'], 'additionalProperties': False},
        argument_types={**TYPES, 'panel': str}, description=DESCRIPTION)


RECEIPTS = {}
CAPTIONS = {'query': lambda args, actor, receipt, ctx: ctx.text('tool.caption.query')}
PANELS = {}
