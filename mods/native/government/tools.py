from __future__ import annotations
from ..core import *
from engine.runtime import Tool

def tool_query(world,actor,**args):
    panel=str(args.pop('panel','all')).lower()
    panel={'gridmap':'grid','网格':'grid','格子图':'grid'}.get(panel,panel)
    if panel not in world.runtime.panel_map:panel='all'
    return world.runtime.panel(world,actor,panel,args)


def tool_rules(world, actor, **args):
    from ..presentation import rules_text
    tool = 'rules'
    return rules_text(world, str(args.get('topic', '') or ''))

def tool_memory_search(world, actor, **args):
    from ..presentation import _memory_search
    tool = 'memory_search'
    return _memory_search(world, actor, args)

def tool_expand(world, actor, **args):
    tool = 'expand'
    return '没有单独占地命令：占地一律走 atk——派军队 attack 目标格，若那格没有守军/敌军（敌人=0）军队直接进驻占领；有野人/敌军则打赢后自动占地；野地上只有中立/盟友和平驻守时也直接进驻占领（它们不参战、回合末被遣返）。mv 只挪位置、不占地；野地有敌军驻守（含正在打野的）时不能 mv，只能 atk。野地上有与你非敌非盟的一方正在打野时不能 atk 插足；敌人/盟友在打野则可以参战。占地按索取顺序：第一个 atk 者优先，它阵亡则顺位最早入场的同盟者。'

def tool_plan(world, actor, **args):
    from ..presentation import PLAN_MAX_TURNS
    tool = 'plan'
    text = str(args.get('content', args.get('text', ''))).strip()
    if not text:
        return 'plan 需要 content=你的国策一句话（常驻上下文作为长期目标，可随时修订）'
    is_new = actor not in world.plans
    world.plans[actor] = {'text': text, 'turn': world.turn}
    act = '制定' if is_new else '修订'
    return f'✅ 国策已{act}（第{world.turn}回合），常驻你的上下文。每 {PLAN_MAX_TURNS} 回合须再修订一次，否则无法结束回合。建议从 经济发展/军事规划/情报管理/外交方向 四方面写（可后续 plan 随时修订）。'

def tool_end_turn(world, actor, **args):
    from ..presentation import PLAN_MAX_TURNS, SUMMARY_MIN_CHARS
    tool = 'end_turn'
    pl = world.plans.get(actor)
    if not pl or not str(pl.get('text', '')).strip():
        return '本回合还不能结束：还没有国策规划。请先 plan(content=…) 制定国策（常驻上下文作为长期目标；建议涵盖 经济发展/军事规划/情报管理/外交方向）。'
    since = world.turn - pl.get('turn', world.turn)
    if since >= PLAN_MAX_TURNS:
        return f'本回合还不能结束：国策已 {since} 回合未修订（每 {PLAN_MAX_TURNS} 回合必须修订一次），请先用 plan(content=…) 更新国策。'
    summary = str(args.get('summary', '')).strip()
    if len(summary) < SUMMARY_MIN_CHARS:
        return '本回合还没收尾：end_turn 必须带 summary=一句话，总结你这回合做了什么/立场（例如 summary=这回合建了两座农场并继续拓荒）。'
    world.log(f'{actor} 回合小结：{summary}', phase='行动', nation=actor)
    mem = world.summaries.setdefault(actor, [])
    mem.append({'turn': world.turn, 'text': summary})
    return f'✅ 本回合结束（小结已记录：{summary}）'

def definitions():
    return (
        Tool('query', tool_query, aliases=('view', 'panel', '查', '查询', '看', '面板'), argument_types={'panel': str, 'filter': str, 'x': int, 'y': int, 'at': str}),
        Tool('rules', tool_rules, aliases=('规则', 'help', '帮助'), argument_types={'topic': str}),
        Tool('memory_search', tool_memory_search, aliases=('search_memory', '检索记忆', '记忆检索', '回想', '回忆', 'history', '翻旧账'), argument_types={'query': str, 'limit': int}),
        Tool('expand', tool_expand, aliases=('拓荒', 'activate'), argument_types={}),
        Tool('plan', tool_plan, aliases=('国策', '国策规划'), argument_types={'content': str}),
        Tool('end_turn', tool_end_turn, aliases=('结束回合', 'done'), argument_types={'summary': str}),
    )
