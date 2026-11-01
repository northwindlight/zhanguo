"""Actor-bound observations and parameterized economic actions.

No World access and no teacher scores/phase/selected action in observations.
Unaffordable buildings remain visible as context; only execution is masked.
Trade quantities have support on every integer in the actual executable range.
"""
from collections import Counter
import hashlib
import json
import math

import numpy as np

from ruleai.v13.market import MarketModel
from ruleai.v13.profiles import PROFILES

VERSION = 'economy-bc-1'
ACTIONS = ('finish', 'build', 'recruit', 'buy', 'sell')
RESOURCES = tuple('native:'+r for r in ('gold','food','wood','ore','oil','equipment','supply'))
BUILDINGS = tuple('native:'+r for r in ('castle','lumber-camp','farm','mine','oil-well',
    'gold-mine','wood-power-plant','oil-power-plant','supply-factory','equipment-factory',
    'barracks','town-hall','watchtower','diplomacy-center','academy','militia-camp'))
UNITS = ('native:infantry','native:cavalry','native:militia')
PROFILES_ORDER = tuple(PROFILES)
WIDTH = 96
# Columns: group 0:5, action 5:10, resource 10:17, kind 17:36,
# position 36:38, scalar attributes 38:59, costs 59:66, inputs 66:73,
# outputs 73:80, profile 80:85. Each token group defines its scalar attributes.
FINGERPRINT = hashlib.sha256(json.dumps([VERSION,ACTIONS,RESOURCES,BUILDINGS,UNITS,
    PROFILES_ORDER,WIDTH],ensure_ascii=True).encode()).hexdigest()
PROJECT_FINGERPRINT = hashlib.sha256((FINGERPRINT+':funded-projects-1').encode()).hexdigest()


def scale(value):
    return math.copysign(math.log1p(abs(float(value)))/5, float(value))


def row(group, *, action=None, resource=None, kind=None, position=None,
        values=(), costs=None, inputs=None, outputs=None, profile=None):
    x=np.zeros(WIDTH,dtype=np.float32)
    x[group]=1
    if action is not None: x[5+ACTIONS.index(action)]=1
    if resource is not None: x[10+RESOURCES.index(resource)]=1
    if kind is not None: x[17+(BUILDINGS+UNITS).index(kind)]=1
    # Coordinates have the same physical scale in context and candidates.
    if position is not None: x[36:38]=np.asarray(position)/32
    if len(values)>21: raise ValueError('Too many scalar features')
    x[38:38+len(values)]=[scale(v) for v in values]
    for offset,data in ((59,costs),(66,inputs),(73,outputs)):
        if data:
            x[offset:offset+7]=[scale(data.get(r,0)) for r in RESOURCES]
    if profile is not None: x[80+PROFILES_ORDER.index(profile)]=1
    return x


def max_buy(player, resource, inventory, cash):
    """Use the formal whole-order quote including coin carry, not unit price."""
    low,high=0,int(inventory)
    while low<high:
        mid=(low+high+1)//2
        quote=player.call_api('economy:queries','trade_quote',
            {'resource':resource,'quantity':mid,'side':'buy'})
        if quote['gold']<=cash: low=mid
        else: high=mid-1
    return low


def observation(ledger, state, *, include_trades=True):
    player=ledger.player
    obs=player.observe()
    facts=obs['facts']; econ=facts['econ']; sites=facts['building_sites']
    catalog={b['id']:b for b in facts['building_catalog']}
    if set(catalog)!=set(BUILDINGS):
        raise ValueError('Building vocabulary changed; regenerate data and checkpoint')
    stocks={r['id']:r['amount'] for r in econ['stocks']}
    market=facts['market']; buy=dict(market['buy_prices']); sell=dict(market['sell_prices'])
    inventory=dict(market['inventory']); reference=dict(market['reference'])
    nations=facts['foreign'].get('nations',[])
    model=MarketModel(getattr(ledger,'economy_market',market),state.market,
                      sum(n.get('alive',True) for n in nations))
    own_net=Counter(); bought=set(); sold=set()
    for receipt in ledger.acts:
        if receipt['ok'] and receipt['action'] in ('buy','sell'):
            d=receipt['data']; side=receipt['action']
            own_net[d['resource']]+=d['quantity']*(1 if side=='sell' else -1)
            (bought if side=='buy' else sold).add(d['resource'])
    preview=econ['preview']; energy=preview['energy']; military=preview['military']
    own_army=[u for u in facts['army'] if u['owner']==player.actor]
    profile=PROFILES[state.profile]
    context=[row(0,profile=state.profile,values=(obs['period'],len(ledger.acts),
        len(sites['sites']),len(sites['instances']),len(own_army),len(nations),
        energy['capacity'],energy['demand'],energy['generated'],energy['shortfall'],
        military['need'],military['equipment_need'],military['shortfall'],
        len(facts['threats']),profile.horizon,profile.reserve,profile.cash_periods,
        profile.self_supply,profile.export,profile.gold,profile.release))]
    flows=econ['latest_flows']
    for resource in RESOURCES:
        context.append(row(1,resource=resource,values=(stocks.get(resource,0),
            buy.get(resource,1),sell.get(resource,1),inventory.get(resource,0),
            reference.get(resource,0),model.external.get(resource,0),model.flow,
            own_net[resource],resource in bought,resource in sold,
            dict(flows['inputs']).get(resource,0),dict(flows['outputs']).get(resource,0)),
            inputs={resource:preview['inputs'].get(resource,0)},
            outputs={resource:preview['outputs'].get(resource,0)}))
    for kind,definition in catalog.items():
        completed=sum(b['kind']==kind and not b['remaining'] for b in sites['instances'])
        pending=sum(b['kind']==kind and bool(b['remaining']) for b in sites['instances'])
        context.append(row(2,kind=kind,values=(completed,pending,definition['energy_use'],
            definition['energy_output'],definition['min_slots'],
            definition['effects'].get('gold_base',0),definition['effects'].get('gold_per_slot',0),
            definition['build_discount']),costs=definition['costs_by_level'][0],
            inputs=definition['inputs'],outputs=definition['outputs']))
    actions=[('finish',{})]; candidates=[row(4,action='finish')]; legal=[True]; limits=[0]
    for site in sites['sites']:
        args=dict(zip(('x','y'),site['position']))
        plan=player.call_api('native:buildings','planning',args)
        space=plan['space']
        context.append(row(3,position=site['position'],values=(space['occupied'],
            space['capacity'],space['completed'],space['construction'],space['pending'],
            space['order_remaining'],space['penalty']),outputs=dict(site['resources'])))
        for kind in catalog:
            instances=[b for b in sites['instances'] if b['site']==site['id'] and b['kind']==kind]
            if instances:
                context.append(row(2,kind=kind,position=site['position'],values=(
                    sum(not b['remaining'] for b in instances),sum(bool(b['remaining']) for b in instances))))
        for choice in plan['buildings']:
            definition=catalog[choice['kind']]
            features=row(4,action='build',kind=choice['kind'],position=site['position'],
                values=(choice['available'],choice['reason'] is None,space['free'],
                    space['order_remaining'],definition['energy_use'],definition['energy_output'],
                    choice['price']['level'] if choice['price'] else 0,
                    definition['effects'].get('gold_base',0),definition['effects'].get('gold_per_slot',0)),
                costs=choice['price']['costs'] if choice['price'] else {},
                inputs=definition['inputs'],outputs=definition['outputs'])
            # Candidate-to-candidate attention sees unaffordable projects too.
            actions.append(('build',{'site':site['id'],'kind':choice['kind']}))
            candidates.append(features);legal.append(choice['available']);limits.append(0)
        for choice in player.call_api('native:army','planning',args):
            definition=facts['unit_catalog'][choice['kind']]
            features=row(4,action='recruit',kind=choice['kind'],position=site['position'],
                values=(choice['available'],choice['quota'],choice['has_facility'],definition['supply']),
                costs=choice['costs'])
            actions.append(('recruit',{'site':site['id'],'kind':choice['kind'],'count':1}))
            candidates.append(features);legal.append(choice['available']);limits.append(0)
    for side,prices,opposite in (('buy',buy,sold),('sell',sell,bought)):
        if not include_trades:
            break
        for resource in RESOURCES[1:]:
            cap=(max_buy(player,resource,inventory.get(resource,0),stocks.get(RESOURCES[0],0))
                 if side=='buy' else int(stocks.get(resource,0)))
            # Same no-roundtrip constraint as the teacher, independent of labels.
            available=cap>0 and resource not in opposite
            total=player.call_api('economy:queries','trade_quote',{
                'resource':resource,'quantity':cap,'side':side})['gold'] if cap else 0
            features=row(4,action=side,resource=resource,values=(available,cap,total,
                prices.get(resource,0),stocks.get(resource,0),inventory.get(resource,0)))
            actions.append((side,{'resource':resource}));candidates.append(features)
            legal.append(available);limits.append(cap)
    return {'context':np.stack(context),'candidates':np.stack(candidates),
            'legal':np.asarray(legal,dtype=bool),'limits':np.asarray(limits,dtype=np.int64),
            'actions':actions}


def label(encoded, action, arguments):
    quantity=arguments.get('quantity',0)
    key=(action,{k:v for k,v in arguments.items() if k!='quantity'})
    try: index=encoded['actions'].index(key)
    except ValueError as error: raise ValueError(f'Teacher action absent: {key}') from error
    if not encoded['legal'][index]: raise ValueError(f'Teacher action masked: {key}')
    if action in ('buy','sell') and not 1<=quantity<=encoded['limits'][index]:
        raise ValueError(f'Teacher quantity outside executable support: {quantity}')
    return index,math.log1p(quantity)


def decode(encoded, index, log_quantity):
    action,args=encoded['actions'][index]; args=dict(args)
    if not encoded['legal'][index]: raise ValueError('Masked action selected')
    if action in ('buy','sell'):
        cap=int(encoded['limits'][index])
        # Continuous log-quantity head, rounded only on execution, no bucket bias.
        args['quantity']=min(cap,max(1,round(math.expm1(min(math.log1p(cap),max(0,log_quantity))))))
    return action,args
