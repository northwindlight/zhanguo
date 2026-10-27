"""Render only the detached human application state; never inspect a world."""
from collections import Counter, defaultdict
from copy import deepcopy
from mods.native.tools.buildings import _behavior


def flow(state):
    facts=state['facts'];latest=facts['econ'].get('latest_turn')
    period=latest['period'] if latest else -1
    actual=facts['econ'].get('latest_flows',{})
    result={'id':state['actor'],'period':period,
        'production_in':deepcopy(actual.get('outputs',[])),
        'production_out':deepcopy(actual.get('inputs',[])),
        'tribute_in':deepcopy(actual.get('tribute_received',[])),
        'tribute_out':deepcopy(actual.get('tribute_paid',[])),'military_out':[]}
    costs=defaultdict(float)
    for event in facts['news']:
        d=event.get('details') or {}
        if event['period']==period and event['kind']=='maintenance' and d.get('owner')==state['actor']:
            for key,value in d['costs'].items():costs[key]+=value
    result['military_out']=[[k,v] for k,v in costs.items()]
    net=defaultdict(float)
    for name in ('production_in','production_out','tribute_in','tribute_out','military_out'):
        for key,value in result[name]:net[key]+=value*(1 if name.endswith('_in') else -1)
    result['net']=[[k,v] for k,v in net.items() if v]
    return result


def frame(ui,state):
    facts=state['facts'];actor=state['actor'];period=state['period']
    label=lambda ident:ui.console.label('content',ident)
    names={n['id']:n['name'] for n in facts['foreign']['nations']}
    terrain={t['id']:t for t in facts['terrain_catalog']}
    kinds=sorted(b['id'] for b in facts['building_catalog'])
    sites={s['id']:tuple(s['position']) for s in facts['building_sites']['sites']}
    own=defaultdict(list)
    for b in facts['building_sites']['instances']:own[sites[b['site']]].append(b)
    tiles=[];buildings=[];ownership=[];defenses=[]
    for cell in state['map']['tiles']:
        p=cell['position'];visibility=cell['visibility'];fields=cell.get('fields',{})
        t={'position':deepcopy(p),'visibility':visibility,'knowledge':deepcopy(fields)}
        if visibility=='unknown':tiles.append(t);continue
        value=lambda key,default=None:deepcopy(fields.get(key,{}).get('value',default))
        current=cell.get('current',{})
        tid=current.get('terrain',value('terrain'))
        if tid is None:tiles.append({'position':deepcopy(p),'visibility':'unknown'});continue
        owner=current.get('owner',value('owner'));name=current.get('name',value('name',''))
        resources=value('resources',{})
        if visibility=='live' and owner==actor:resources=dict(current.get('resources',[]))
        local=[];sampled=max((v['sampled'] for v in fields.values()),default=period)
        if visibility=='live' and owner==actor:
            local=[{**deepcopy(b),'x':p[0],'y':p[1],'owner':owner,'label':label(b['kind']),
                    'level_known':True,'historical':False,'sampled':period} for b in own[tuple(p)]]
        else:
            counts={k[9:]:deepcopy(v['value']) for k,v in fields.items() if k.startswith('building/')}
            for kind,count in current.get('buildings',[]):counts[kind]={'count':count,'construction':0}
            for kind,row in sorted(counts.items()):
                index=kinds.index(kind) if kind in kinds else len(kinds)
                for i in range(row['count']+row.get('construction',0)):
                    local.append({'id':500000000+(p[1]*state['map']['size']+p[0])*10000+index*100+i,
                        'kind':kind,'level':1,'remaining':int(i>=row['count']),'x':p[0],'y':p[1],
                        'owner':owner,'label':label(kind),'level_known':False,'historical':True,
                        'sampled':period if any(k==kind for k,_ in current.get('buildings',[])) else fields.get('building/'+kind,{}).get('sampled',sampled)})
        t.update(name=name,terrain=tid,terrain_name=label(tid),owner=owner,
            resources=[[k,v] for k,v in resources.items()],passable=terrain[tid]['passable'],
            claimable=terrain[tid]['claimable'],sampled=sampled,
            dynamic=visibility=='live' and owner==actor,
            building_instances=local,buildings=[[b['kind'],b['level']] for b in local],
            building_labels=[f'{b["label"]}'+((f' · {b["level"]}级' if b['kind']=='native:castle' else '') if b['level_known'] else f'（示意 · 第{b["sampled"]+1}期）')+
                (' · 在建' if b['remaining'] else '') for b in local])
        tiles.append(t);buildings.extend(local);ownership.append({'x':p[0],'y':p[1],'owner':owner})
        defenses.append({'id':f'{p[0]},{p[1]}','x':p[0],'y':p[1],'terrain':terrain[tid]['defense'],
            'castle_known':owner==actor or not any(b['kind']=='native:castle' for b in local),
            'castle':sum(next((c['effects'].get('defense_per_level',0) for c in facts['building_catalog'] if c['id']==b['kind']),0) for b in local if b['kind']=='native:castle' and not b['remaining'] and b['level_known'])})
    units=[{**deepcopy(u),'label':label(u['kind'])} for u in facts['army']]
    guards=[u for u in units if u['owner']=='野人']
    armies=[u for u in units if u['owner']!='野人']
    stocks=[{**deepcopy(s),'name':label(s['id'])} for s in facts['econ']['stocks']]
    market=facts['market'];inv=dict(market['inventory']);buys=dict(market['buy_prices']);sells=dict(market['sell_prices'])
    prices=[{'id':k,'name':label(k),'price':v,'inventory':inv[k],'buy':buys[k] if inv[k]>0 else None,'sell':sells[k]}
            for k,v in market['prices']]
    resource_flow=flow(state)
    nation={'id':actor,'name':names[actor],'land':len(facts['land']),'stocks':stocks,
        'resource_flow':resource_flow,'units':dict(Counter(u['label'] for u in armies if u['owner']==actor)),
        'assets':facts['econ']['gross_assets']['total'],'energy':facts['econ']['preview'].get('energy',{})}
    return {'period':period,'map_seed':0,'map_size':state['map']['size'],'player_actor':actor,
        'names':names,'colors':deepcopy(state.get('colors',{})),'tiles':tiles,'armies':armies,'nations':[nation],'market':prices,
        'bank':{'rate':facts['bank']['savings_rate'],'loan_rate':facts['bank']['loan_rate']},
        'events':deepcopy(facts['news']), 'diplomacy':deepcopy(facts['foreign']['diplomacy']),
        'building_help':{b['id']:_behavior(b,ui.console) for b in facts['building_catalog']},
        'labels':{key:label(key) for key in set(kinds)|set(terrain)|set(s['id'] for s in stocks)|set(facts['unit_catalog'])|{k for t in tiles for k,_ in t.get('resources',[])}},
        'clients':[{'actor':actor,'kind':'human','version':'human/1','model':None,'strategy':None}],
        'scene':{'buildings':buildings,'armies':armies,'guards':guards,'tile_defenses':defenses,
            'power':[{'id':actor,**deepcopy(facts['econ']['preview']['energy'])}],
            'ownership':ownership,'accounts':[{'id':actor,'stocks':stocks}],
            'resource_flows':[resource_flow],'production':[],'market':prices,
            'unit_types':[{'id':k,'max_hp':v['hp']} for k,v in facts['unit_catalog'].items()]}}
