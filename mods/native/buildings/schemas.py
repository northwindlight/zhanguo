from ..core import *
from ..rules import *
from ..ui_constants import *
from ..presentation import _cost_text,_move_rule_text,_reach_brief,_recruit_desc

def _props(schema: dict) -> dict:
    """把每个属性里的 `required: True` 提到顶层（属性内不允许出现 required）。"""
    props, req = ({}, [])
    for k, v in schema.items():
        pv = dict(v)
        if pv.pop('required', False):
            req.append(k)
        props[k] = pv
    d = {'type': 'object', 'properties': props}
    if req:
        d['required'] = req
    return d

def definitions():
    result = [
        {"type": "function", "function": {
            "name": "build", "description": f"在自己的一块地上建一座建筑。每地块每回合限建1座。建筑: 城堡/林场/农场/矿场/黄金矿场/石油厂/木材能源厂/石油能源厂/补给厂/装备厂/兵营/市政厅/瞭望塔/外交中心/工程院/军屯。采集类上限=本地资源量；补给厂/装备厂/能源厂任地可建（工业不挑地）；兵营需本地已用建筑位≥{BUILDINGS['兵营']['min_slots']}；瞭望塔=事件视野+{building_effect('瞭望塔', 'vision_radius')}圆；市政厅需本地已用位≥{BUILDINGS['市政厅']['min_slots']}且每地块限{BUILDINGS['市政厅']['limit']}（★**它是国祚：市政厅尽失即亡国**，余土沦为无主之地、建筑留原地；开局核心白送1座）；外交中心=外交费减半可叠加但自建全国限{BUILDINGS['外交中心']['limit_nation']}（第2座只能抢）；工程院=本地建造费-{building_effect('工程院', 'build_discount')}%需本地位≥{BUILDINGS['工程院']['min_slots']}；军屯=**不产粮**的民兵编制、可征民兵({_cost_text(UNIT_TYPES['民']['recruit'])}/支、每座每回合{building_effect('军屯', 'militia_per_turn')}支、可驻{building_effect('军屯', 'militia_cap')}支、全国民兵总数≤军屯数×{building_effect('军屯', 'militia_cap')})且民兵驻本格不耗补给（每座覆盖{building_effect('军屯', 'militia_cap')}支；需本地{BUILDINGS['军屯']['cap_resource']}≥1、每地块限{BUILDINGS['军屯']['limit']}座）。",
            "parameters": _props({"tile": {"type": "string", "description": "地块：坐标如 '5 6' 或自家地块名（land 面板有）", "required": True},
                                  "building": {"type": "string", "enum": BUILD_NAMES, "description": "建筑名", "required": True}})}}
    ]
    return result
