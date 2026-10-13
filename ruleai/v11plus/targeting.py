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
    for tile in facts['view']:
        position=tuple(tile['position']);owner=tile['owner']
        if owner is not None and owner not in enemies:continue
        busy=[u for u in facts['army'] if (u['x'],u['y'])==position and u['engaged'] and u['owner'] not in allies]
        if owner is None and busy and not any(u['owner'] in enemies for u in busy):continue
        result.append(position)
    return sorted(result)
