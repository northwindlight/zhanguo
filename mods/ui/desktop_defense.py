"""Project shared army cover from supplied facts, never from a live world."""
from collections import defaultdict
from mods.native.army.defense import cover


def project(scene):
    owners={(r['x'],r['y']):r['owner'] for r in scene.get('ownership',[])}
    terrain={(r['x'],r['y']):r for r in scene.get('tile_defenses',[])}
    groups=defaultdict(dict)
    # Staged battle units take priority over an idle lazy garrison.
    for unit in scene.get('guards',[])+scene.get('armies',[]):
        groups[unit['x'],unit['y']][unit['owner'],unit['id']]=unit
    rows=[]
    for (x,y),units in sorted(groups.items()):
        units=[u for u in units.values() if u['hp']>0]
        active={u['owner'] for u in units if u.get('engaged') and u['owner']!='野人'}
        tile=terrain.get((x,y));land=owners.get((x,y))
        for owner in sorted({u['owner'] for u in units}):
            result=cover(owner,land,active,tile['terrain'],tile['castle']) if tile else None
            if result and result['defending'] and owner==land and tile.get('castle_known') is False:
                result={**result,'castle':None,'total':None}
            rows.append({'id':f'{owner}/{x},{y}','owner':owner,'x':x,'y':y,
                         **(result or {'defending':False,'terrain':None,'castle':None,'total':None})})
    scene['squad_defenses']=rows
    return scene
