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
            "name": "recruit", "description": _recruit_desc(
                {k: v["recruit"] for k, v in UNIT_TYPES.items()}),
            "parameters": _props({"tile": {"type": "string", "description": "地块：坐标 '5 6' 或名字", "required": True},
                                  "n": {"type": "integer", "description": "征召数量（默认1）"},
                                  "kind": {"type": "string", "enum": ["步", "骑", "民"], "description": "兵种（默认 步；民=民兵，只能在自家军屯格征召）"}})}},
        {"type": "function", "function": {
            "name": "move", "description": "把一支自己的军队挪位置，纯移动不占地。每回合每支限1次。" + _move_rule_text() + " **行军不打野人**：合法移动目标只有三种：**野地（无人荒地）、自家格、盟国格**——野地可直接走进/穿过（行军不打野人，野人只在被 atk 时接战）；★★ **走进 ≠ 占下：mv 走进去不改变归属**，无主地走完**还是无主地**（国土不会多一块）——**要占地必须用 atk**，那是本作唯一的占地动作；自家/盟国格被混战敌军占着也可以 mv 进去（增援，入格即随军参战）。**敌国领土 mv 一律不得进入（空格也是）**：每一步进敌境都是 atk——会交战或直接进占。**野地上有与你交战的敌军驻守（含正在打野的）也不得 mv**——必须 atk 交战；中立/盟友驻守的野地可以 mv 进去（旁观待命，互不干扰）。交战中不能移动，须先 retreat 撤出。",
            "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                                  "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                                  "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
        {"type": "function", "function": {
            "name": "attack", "description": "军队(" + f"按兵种移动力可及：{_reach_brief()}" + ")冲入目标地块并交战——打赢该地守军自动占地；格上**无任何军队**则直接进驻占领；野地上只有中立/盟友和平驻守时也直接进驻占领（它们不参战，回合末自动遣返）；他国领土上有非敌军队则不能进驻。**不抢别人的战斗**：野地上有与你非敌非盟的一方正在打野 → 不能 atk 插足（可 mv 旁观）；敌人/盟友在打野 → 可以参战（同格多方各打各的敌人，互相宣战才互打）。占地按索取顺序：第一个 atk 者优先，它阵亡则顺位最早入场的同盟者。与别国开打需已宣战。★★ **反击无罪**：已经交战的两国之间，atk 夺地/打人**不会再触发任何条约**——保障/共同防御/联盟的自动参战**只在宣战那一刻结算过一次**，此后这条战线的参战名单冻结，不会再拉进任何国家。**放手打，缩手才是亏。**",
            "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"}, "description": "参战本国军队id数组（各国独立从1编号）", "required": True},
                                  "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                                  "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
        {"type": "function", "function": {
            "name": "retreat", "description": f"交战中的军队（含防守方守军）撤出——**固定只能退相邻 {RETREAT_RANGE} 格**（所有人，不按兵种速度、也不看地形代价）。撤退不立刻结算：军队留在战场参与本回合末战斗结算（伤害全场分摊；防御方撤退减伤{RETREAT_DEF_COVER}%；撤退军本回合输出-{RETREAT_ATK_PENALTY}%），结算后自动脱离到目标格。目标限 己方/同盟/无人荒地；四周无合法撤退点则无法撤退。mv 不能从交战地撤离；想脱离战场一律用 retreat。",
            "parameters": _props({"army_id": {"type": "integer", "description": "本国军队id（各国独立从1编号，以 query army 面板为准）", "required": True},
                                  "x": {"type": "integer", "description": "目标x(1-based)", "required": True},
                                  "y": {"type": "integer", "description": "目标y(1-based)", "required": True}})}},
        {"type": "function", "function": {
            "name": "disband", "description": f"**遣散军队**（解甲归田）：把本国军队撤编、就地解散——免费、不限次数、**境内境外野地都行**（孤军深陷敌境时用它止损）。**当回合起就不再吃补给、不再计军费**（军费是每回合的持续支出，裁军是治「军费占 GDP 过高」的正当手段之一）；民兵遣散会**当场释放编制名额**（全国民兵总数 ≤ 军屯数×{building_effect('军屯', 'militia_cap')}）。★ **不返还**：兵员、装备、粮食一概不退——这是明确的代价，别指望靠遣散回血。★ **交战中（含正在挨打的守军）不能遣散**：先用 retreat 撤出战场，下回合再遣散。★ 遣散**不亡国**（亡国只认市政厅全失），但军队没了就是没了——番号不回收、也不能补回来，要兵得重新征兵。",
            "parameters": _props({"army_ids": {"type": "array", "items": {"type": "integer"},
                                              "description": "要遣散的本国军队id数组（各国独立从1编号，以 query panel=army 为准）；一次可遣散多支", "required": True}})}}
    ]
    return result
