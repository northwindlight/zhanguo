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
            "name": "query", "description": f"查询接口：随时获取你的各面板。★ 你的**国土与视野已作为「坐标地图」常驻**在每回合的状态里（按势力分段：我 / 野人 / 各国；每行一格：`(x,y)归属地形，[L2城][，地名][，番号…]`），所以这里查的是**细节**。land=地皮逐格明细（可翻页/按建筑或资源过滤） / tile=**单格全明细**（x= y= 或 at=地名） / grid=**网格版地图**（ASCII 格子图，适合想一眼看形状时） / res=国库与储备 / plan=国策规划 / army=军队 / market=世界市场(现价/买价/卖价/均衡价+大单试算) / econ=经济核算(各建筑造价毛利回本) / intel=收到的地图情报(全部坐标) / spy=间谍情报(别国经济底细+外交关系+粗略军情) / mail=信箱 / countries=可选外交对象 / diplomacy=外交 / news=近讯 / threats=视野内他国军队（野人守军不列，见地图标记） / battle=**当前交战**（每方的支数·兵种构成·合计HP·输出基数、逐支 HP、地形/城堡/合计减伤——**仅防御方**吃；视野外的格标「盲战」只报我方） / all=全部。每个行动后状态会变，拿不准就再查一次。",
            "parameters": _props({"panel": {"type": "string", "enum": ["all", "res", "plan", "land", "tile", "grid", "army", "market", "econ", "intel", "spy", "mail", "countries", "diplomacy", "news", "threats", "battle", "overview", "pending", "targets", "results", "state"], "description": "要查询的面板", "required": True},
                                  "cap": {"type": "integer", "description": f"panel=land：本次列几块（默认 {LAND_CAP}）"},
                                  "offset": {"type": "integer", "description": "panel=land：从第几块开始列（翻页用）"},
                                  "filter": {"type": "string", "description": "panel=land：只看含该**建筑**或该**资源**的格（如 兵营 / 耕地 / 军屯）"},
                                  "x": {"type": "integer", "description": "panel=tile：坐标 x（1-based）"},
                                  "y": {"type": "integer", "description": "panel=tile：坐标 y（1-based）"},
                                  "at": {"type": "string", "description": "panel=tile：地名（可代替 x/y）"}})}},
        {"type": "function", "function": {
            "name": "rules", "description": "查询完整游戏规则：建筑造价与上限、地形、电网经济、军队战斗、外交、信箱、市场、回合存档，以及四本讲义——rules(经济手册)「为什么这样搞经济」、rules(基础指南)「基础功：开局怎么下手、电与工业怎么配」、rules(战争手册)「这仗该怎么打」、rules(战略手册)「盘面怎么看：均势/该不该出手/外交/僵持」。可带 topic 只取相关段（如 '兵营'、'外交'、'宣战'、'战争手册'）；**不带或认不出主题则返回章节目录**——照目录里的名字再查一次即可。★ **开战之前先查 rules(战争手册)**：一旦开打它会被强制挂进你的 system prompt，但在你还没开打时就查它，是**你的先手**——先用它算清「这仗几回合能拔几座厅、值不值得打」，再决定要不要开战。★ **rules(战略手册) 是完全按需取的一本**（没人替你挂）：当你面对「谁在坐大、该不该干预、要不要僵持、跟谁外交」这类**盘面**问题时，查它。",
            "parameters": _props({"topic": {"type": "string", "description": "想查的主题（可选）"}})}},
        {"type": "function", "function": {
            "name": "plan", "description": f"制定或修订你的国策（长期战略目标），会永久常驻你的上下文（【国策规划】标记），直到你再次修订。⚠ 结束回合(end_turn)前必须已有国策；且每 {PLAN_MAX_TURNS} 回合必须修订一次，否则 end_turn 会被拦。建议按四方面写：经济发展（粮木矿油/建设/卖买）、军事规划（扩军/攻防/结盟）、情报管理（间谍/换图/来信研判）、外交方向（结盟/宣战/求和/馈赠立场）。",
            "parameters": _props({"content": {"type": "string", "description": "国策内容", "required": True}})}},
        {"type": "function", "function": {
            "name": "memory_search", "description": "检索你自己的历史记忆（只看行动/发言/结果的**正文**，不看思考过程）：按关键词找出相关回合，返回**回合号与相邻上下文**。适合翻旧账——「我之前答应过楚国什么」「哪几场仗烧补给最凶」「谁对我宣过战」。关键词用空格分隔（如 盟约 楚），全部命中优先、部分命中次之；limit 控制返回条数。只搜得到你自己的记忆，不影响他人。免费、只读。",
            "parameters": _props({"query": {"type": "string", "description": "检索关键词（用空格分隔多个词）", "required": True},
                                  "limit": {"type": "integer", "description": "最多返回几条（默认3，最大8）"}})}},
        {"type": "function", "function": {
            "name": "end_turn", "description": "结束本国本回合的行动。⚠ 必填 summary：用一句话总结你这回合做了什么/当前立场（例如：summary=这回合建了两座农场并继续拓荒）。没有这句小结就不算结束本回合。",
            "parameters": _props({"summary": {"type": "string", "description": f"一句话回合小结（必填，>={SUMMARY_MIN_CHARS}字）", "required": True}})}}
    ]
    return result
