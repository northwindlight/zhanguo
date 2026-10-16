"""Candidates derive exclusively from the current player observation."""
def relations(observation):
    actor=observation['actor'];relation=observation['facts']['diplomacy']
    allies={actor};enemies=set()
    for bloc in relation['alliances']:
        if actor in bloc['members']:allies.update(bloc['members'])
    for war in relation['wars']:
        if actor in war['attackers']:enemies.update(war['defenders'])
        if actor in war['defenders']:enemies.update(war['attackers'])
    return allies,enemies

def candidates(observation):
    allies,enemies=relations(observation);facts=observation['facts'];result=[]
    engaged={}
    for u in facts['army']:
        if u['engaged'] and u['owner'] not in allies:engaged.setdefault((u['x'],u['y']),[]).append(u)
    for tile in facts['view']:
        position=tuple(tile['position']);owner=tile['owner']
        if owner is not None and owner not in enemies:continue
        busy=engaged.get(position,())
        if owner is None and busy and not any(u['owner'] in enemies for u in busy):continue
        result.append(position)
    return sorted(result)
