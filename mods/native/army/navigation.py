"""Army rules shared by execution and player-scoped previews.

The caller supplies its map facts. Unknown cells may be optimistic in a
preview; execution always supplies the actual world. No UI-specific rules.
"""
import heapq


def action_reason(unit, period, *, attack=False, current=False, attacked=False):
    if unit.get('born_turn') == period:return '新兵本回合不能行动'
    if attack and current:return ''
    if unit.get('moved_turn') == period:return '本回合已移动或已消耗行动额度'
    if unit.get('engaged'):return '正在交战，须先撤退'
    if not attack and attacked:return '本格正受进攻，须用 retreat 撤出'
    return ''


def retreat_reason(unit, period, attacked):
    if unit.get('born_turn') == period:return '新兵本回合不能撤退'
    if unit.get('moved_turn') == period:return '本回合已移动或已提交撤退'
    if not (unit.get('engaged') or attacked):return '未交战、未受进攻，无需撤退；请用 move 调动'
    return ''


def can_pass(relation, hostile_present):
    # Own/allied territory remains traversable even when under attack.
    return relation in ('own', 'ally') or relation == 'wild' and not hostile_present


def target_reason(relation, owner, passable, movable, busy_relations, attack):
    if not passable:return '高山或海洋不可通行、进攻或占领；请沿可通行陆地绕行'
    if not attack:
        return '' if movable else '移动目标不合法：只能是野地、自家或盟国格；敌国领土要用 attack'
    if relation in ('own', 'ally'):
        return '那是你自己（或盟国）的地块：不能进攻自家领土，占地请选无主地或敌国领土'
    if relation == 'neutral':
        return f'你和{owner.split(":",1)[-1]}之间没有生效的战争：先 declare_war，或改打无主地/已在交战的目标'
    if relation == 'wild' and busy_relations and not any(r in ('war','ally') for r in busy_relations):
        return '该无主地上有与你非敌非盟的一方正在打野：不能抢别人的战斗（可先旁观），换一格或等它打完'
    return ''


def step_allowed(start, target, passable):
    if not passable(target):return False
    x,y=start;tx,ty=target
    return x == tx or y == ty or (passable((x,ty)) and passable((tx,y)))


def reachable(start, speed, neighbors, cost, movable, target_ok, *, attack=False, visible=None, parents=None, goal=None):
    """Weighted army navigation; attack-only endpoints are never transit cells."""
    best={start:0};queue=[(0,start)]
    while queue:
        distance,point=heapq.heappop(queue)
        if distance != best[point]:continue
        if point==goal:break
        for target in neighbors(point):
            if visible is not None and (target not in visible or
                point[0] != target[0] and point[1] != target[1] and
                ((point[0],target[1]) not in visible or (target[0],point[1]) not in visible)):continue
            if not step_allowed(point,target,lambda p:cost(p) is not None) or cost(point) is None:continue
            length=distance+max(cost(point),cost(target))
            if length > speed or length >= best.get(target,speed+1):continue
            transit=movable(target)
            if not transit and not (attack and target_ok(target)):continue
            best[target]=length
            if parents is not None:parents[target]=point
            if transit:heapq.heappush(queue,(length,target))
    return best


def route(start, target, parents):
    points=[target]
    while points[-1] != start:
        if points[-1] not in parents:return []
        points.append(parents[points[-1]])
    return [list(p) for p in reversed(points)]
