"""Standard UI desktop projection. Never gives observer data to players."""
from collections import Counter
from copy import deepcopy
from .desktop_defense import project


def scene(ui, facts):
    facts=deepcopy(facts)
    for account in facts.get('accounts',[]):
        for stock in account['stocks']:stock['name']=ui.console.label('content',stock['id'])
    for row in facts.get('market',[]):row['name']=ui.console.label('content',row['id'])
    for flow in facts.get('resource_flows',[]):
        for source in flow.get('sources') or []:source['label']=ui.console.label('content',source['kind'])
    for b in facts.get('buildings',[]):b['label']=ui.console.label('content',b['kind'])
    for u in facts.get('armies',[])+facts.get('guards',[]):u['label']=ui.console.label('content',u['kind'])
    return project(facts)


def frame(ui, snapshot):
    names = snapshot['names']
    facts = snapshot['map']['facts']
    def label(kind, category):
        return ui.console.label('content', kind)

    tiles = []
    for tile in facts['view']:
        tiles.append({**tile, 'terrain_name': label(tile['terrain'], 'terrain'),
            'building_labels': [f'{label(kind, "building")}' + (f' · {level}级' if kind=='native:castle' else '') for kind, level in tile['buildings']]})
    armies = [{**unit, 'label': label(unit['kind'], 'unit')} for unit in facts['army']]
    nations = []
    flows={row['id']:row for row in snapshot.get('scene',{}).get('resource_flows',[])}
    for observation in snapshot['nations']:
        actor = observation['actor']; data = observation['facts']; econ = data['econ']
        own = [u for u in armies if u['owner'] == actor and u['hp'] > 0]
        nations.append({'id': actor, 'name': names[actor], 'land': len(data['land']),
            'resource_flow':deepcopy(flows.get(actor,{'id':actor,'period':-1,'net':[]})),
            'stocks': [{'id':s['id'],'name': label(s['id'], 'resource'), 'amount': s['amount']} for s in econ['stocks']],
            'units': dict(Counter(u['label'] for u in own)),
            'assets': econ['gross_assets']['total'],
            'plan': data.get('government', {}).get('plan'),
            'energy': econ.get('preview', {}).get('energy', {})})
    market = [{'name': label(kind, 'resource'), 'price': price}
              for kind, price in snapshot['market']['prices']]
    return {'period': snapshot['period'], 'map_seed': snapshot['map'].get('seed',0), 'names': names, 'tiles': tiles, 'armies': armies,
        'scene':scene(ui,snapshot.get('scene',{})),
        'nations': nations, 'market': market, 'bank': snapshot['bank'],
        'diplomacy': facts['diplomacy'], 'events': snapshot['records']['events'][-160:]}
