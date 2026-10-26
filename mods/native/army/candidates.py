"""Optimistic command candidates derived exclusively from a player's facts.

An uncertain route is an attempt, not a path preview. Historical obstacles are
not used as current blockers. This module cannot access the world or session.
"""
import heapq


def candidates(observation, ids, mode, size):
    if mode not in ('move', 'attack', 'retreat'):raise ValueError('未知军令模式')
    if (not isinstance(ids, list) or not ids or any(type(i) is not int or i < 1 for i in ids)
        or len(set(ids)) != len(ids)):raise ValueError('请选择不重复的本国军队编号')
    owner, period, facts = observation['actor'], observation['period'], observation['facts']
    own = {u['id']: u for u in facts['army'] if u['owner'] == owner and u['hp'] > 0}
    if any(i not in own for i in ids):raise ValueError('选中军队已不存在或不属于本国')
    tiles = {tuple(t['position']): t for t in facts['view']}
    catalog = facts['unit_catalog']
    terrain = {t['id']: t for t in facts['terrain_catalog']}
    relations = {n['id']: n['relation'] for n in facts['foreign']['countries']}
    units = facts['army']
    paths, base_reasons = {}, {}

    def passable(p, unit):
        if p not in tiles:return True
        kind = tiles[p]['terrain']
        return terrain[kind]['passable'] and unit['move_cost'].get(kind) is not None

    def relation(p):
        target = tiles.get(p, {}).get('owner')
        return 'own' if target == owner else 'wild' if target is None else relations.get(target, 'neutral')

    def movable(p):
        if p not in tiles:return True
        if relation(p) not in ('own', 'ally', 'wild'):return False
        return not any((u['x'], u['y']) == p and u['hp'] > 0 and
                       u['owner'] not in (owner, '野人') and relations.get(u['owner']) == 'war' for u in units)

    for aid in ids:
        u = own[aid];start = (u['x'], u['y']);definition = catalog[u['kind']]
        newborn = u.get('born_turn') == period
        used = u.get('moved_turn') == period
        engaged = u.get('engaged', False)
        under_attack = any((v['x'], v['y']) == start and v.get('engaged') and
                          v['owner'] not in (owner, '野人') and relations.get(v['owner']) == 'war' for v in units)
        reason = ('新兵本回合不能行动' if newborn else
                  '本回合行动额度已用' if used else
                  '正在交战，须撤退' if engaged and mode != 'retreat' else
                  '本格受到进攻，须撤退' if under_attack and mode == 'move' else '')
        if mode == 'retreat' and not (engaged or under_attack):reason = '本军没有需要撤出的交战'
        base_reasons[aid] = reason
        speed = 1 if mode == 'retreat' else definition['speed']
        costs = definition['move_cost'];minimum = min(c for c in costs.values() if c is not None)
        def cost(p):return costs.get(tiles[p]['terrain']) if p in tiles else minimum
        best = {start: (0, start not in tiles)};queue = [(0, start not in tiles, start)]
        while queue:
            distance, uncertain, p = heapq.heappop(queue)
            if best.get(p) != (distance, uncertain):continue
            if p != start and not movable(p):continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (p[0] + dx, p[1] + dy)
                    if not (dx or dy) or not (0 <= q[0] < size and 0 <= q[1] < size):continue
                    corners = ((p[0], q[1]), (q[0], p[1])) if dx and dy else ()
                    if not all(passable(t, definition) for t in (p, q, *corners)):continue
                    step = 1 if mode == 'retreat' else max(cost(p), cost(q))
                    length = distance + step
                    if length > speed:continue
                    if mode == 'attack':
                        if q in tiles and relation(q) not in ('wild', 'war') and not movable(q):continue
                        # Neutral third-party engagement on unowned land is public only in live view.
                        if q in tiles and any((v['x'], v['y']) == q and v.get('engaged') and v['hp'] > 0 and
                            v['owner'] not in (owner, '野人') and relations.get(v['owner']) not in ('ally', 'war') for v in units):continue
                    elif not movable(q):continue
                    value = (length, uncertain or any(t not in tiles for t in (q, *corners)))
                    if value >= best.get(q, (speed + 1, True)):continue
                    best[q] = value;heapq.heappush(queue, (*value, q))
        # In-place attack is a domain exception even for used/engaged troops.
        if mode == 'attack' and not newborn and (start not in tiles or relation(start) in ('wild', 'war')):
            best[start] = (0, start not in tiles)
        else:best.pop(start, None)
        paths[aid] = {p: value for p, value in best.items()
                      if (not reason or mode == 'attack' and p == start and not newborn)
                      and (mode != 'attack' or p not in tiles or relation(p) in ('wild', 'war'))}

    targets = []
    for point in sorted({p for rows in paths.values() for p in rows}):
        execute = [aid for aid in ids if point in paths[aid]]
        targets.append({'position': list(point), 'ids': execute,
            'uncertain': any(paths[aid][point][1] for aid in execute),
            'excluded': [{'aid': aid, 'reason': base_reasons[aid] or '已知路径或移动力不足'}
                         for aid in ids if aid not in execute]})
    return {'mode': mode, 'selected': list(ids), 'targets': targets,
            'units': [{'aid': aid, 'reason': base_reasons[aid]} for aid in ids]}
