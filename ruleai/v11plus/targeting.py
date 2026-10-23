"""Candidates derive exclusively from the current player observation."""
def relations(observation):
    facts=observation['facts'];actor=observation['actor']
    relation=(facts.get('foreign') or facts)['diplomacy']
    allies={actor};enemies=set()
    for bloc in relation['alliances']:
        if actor in bloc['members']:allies.update(bloc['members'])
    for war in relation['wars']:
        if actor in war['attackers']:enemies.update(war['defenders'])
        if actor in war['defenders']:enemies.update(war['attackers'])
    return allies,enemies

def candidates(observation):
    allies,enemies=relations(observation);facts=observation['facts'];result=[]
    terrains={t['id']:t for t in facts.get('terrain_catalog') or ()}
    engaged={}
    for u in facts['army']:
        if u['engaged'] and u['owner'] not in allies:engaged.setdefault((u['x'],u['y']),[]).append(u)
    for tile in facts['view']:
        if not terrains.get(tile['terrain'],{}).get('claimable',True):continue
        position=tuple(tile['position']);owner=tile['owner']
        if owner is not None and owner not in enemies:continue
        busy=engaged.get(position,())
        if owner is None and busy and not any(u['owner'] in enemies for u in busy):continue
        result.append(position)
    return sorted(result)
