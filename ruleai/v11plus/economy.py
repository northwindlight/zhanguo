"""v11plus economic layer: shared inventory, ROI construction and recruitment.

Consumes public catalogs and observed production. Strategy tuning is local;
construction prerequisites, policy costs and quotas remain native decisions.

库存市场面向多国对局；单机规则局不在它的目标场景里，本层不跟着它调参。
"""
import math
from .parameters import BARRACKS_CAP, MIL_SHARE, SUPPLY_PERIODS

def stocks(player):return {s['id']:s['amount'] for s in player.query('econ')['stocks']}

# 市场每期只给一个公开报价，先后手不影响它，行情里的买价就是这期的边际价。
def buy_prices(player):return dict(player.query('market')['buy_prices'])

def ensure(ledger, recipe, reserve=0):
    inventory=stocks(ledger.player);prices=buy_prices(ledger.player)
    purchases=[];gold=recipe.get('native:gold',0)+reserve
    for resource,amount in recipe.items():
        if resource=='native:gold':continue
        missing=max(0,amount-inventory.get(resource,0))
        if missing:
            price=prices.get(resource)
            if price is None:return False
            gold+=math.ceil(price*math.ceil(missing));purchases.append((resource,math.ceil(missing)))
    if inventory.get('native:gold',0)<gold:return False
    for resource,quantity in purchases:
        if not ledger.do('buy',resource=resource,quantity=quantity):return False
    return True

def candidate_cost(site, kind, buildings, catalog, facts):
    """Client-side planning from public facts; build remains authoritative."""
    existing=[b for b in buildings if b['site']==site['id']]
    same=sum(b['kind']==kind['id'] for b in existing)
    if len(existing)>=site['capacity'] or len(existing)<kind['min_slots']:return None
    if kind['max_per_site'] is not None and same>=kind['max_per_site']:return None
    if kind['site_resource'] and same>=dict(site['resources']).get(kind['site_resource'],0):return None
    if kind['self_build_limit'] is not None and facts.get('self_built',{}).get(kind['id'],0)>=kind['self_build_limit']:return None
    if any(r['site']==site['id'] for r in facts.get('construction',[])):return None
    level=same+1 if len(kind['costs_by_level'])>1 else 1
    if level>len(kind['costs_by_level']):return None
    discount=0 if kind.get('build_discount',0) else max((catalog[b['kind']].get('build_discount',0) for b in existing if not b['remaining']),default=0)
    costs=dict(kind['costs_by_level'][level-1])
    if 'native:gold' in costs:
        costs['native:gold']=costs['native:gold']*(100+site['penalty'])//100
        costs['native:gold']=costs['native:gold']*(100-discount)//100
        costs['native:gold']=costs['native:gold']*kind.get('policy_pct',100)//100
    return {'level':level,'other_slots':len(existing),'costs':costs}

def run(ledger):
    player=ledger.player;obs=player.observe();facts=obs['facts']
    catalog=player.query('building_catalog');units=player.query('unit_catalog')
    buildings=facts['building_sites']['instances'];sites=facts['building_sites']['sites']
    army=[u for u in facts['army'] if u['owner']==player.actor]
    supply_need=sum(units[u['kind']]['supply'] for u in army)
    supply=max(0,supply_need*SUPPLY_PERIODS-stocks(player).get('native:supply',0))
    if supply_need:ensure(ledger,{'native:supply':supply_need})
    if supply:ensure(ledger,{'native:supply':supply})
    barracks=sum(b['kind']=='native:barracks' and b['remaining']==0 for b in buildings)
    inventory=stocks(player)
    latest=facts['econ']['latest_turn']
    income=latest['gdp']['total'] if latest else inventory.get('native:gold',0)
    # Sell surplus against observed next-period inputs and military requirements.
    prices=dict(facts['market']['prices']);required={}
    for u in army:
        if u['hp']<units[u['kind']]['hp'] and u['kind']!='native:militia':required['native:equipment']=required.get('native:equipment',0)+1
    required['native:supply']=supply_need*SUPPLY_PERIODS
    for resource,amount in units.get('native:infantry',{}).get('recruit',{}).items():
        if resource!='native:gold':required[resource]=required.get(resource,0)+amount
    estimates={row['kind']:row for row in player.query('building_economics')}
    recipes=[(kind,estimates[kind['id']]) for kind in catalog if kind['id'] in estimates]
    for b in buildings:
        if b['remaining']:continue
        # Resource amounts are provided separately in the catalog below.
        cls=next(k for k in catalog if k['id']==b['kind'])
        for resource,amount in cls['inputs'].items():required[resource]=required.get(resource,0)+amount
    for resource in prices:
        if resource=='native:wood':continue
        extra=max(0,stocks(player).get(resource,0)-required.get(resource,0))
        if extra:ledger.do('sell',resource=resource,quantity=int(extra))
    ensure(ledger,required)
    generated=sum(next(k for k in catalog if k['id']==b['kind'])['energy_output'] for b in buildings if not b['remaining'])
    demand=sum(next(k for k in catalog if k['id']==b['kind'])['energy_use'] for b in buildings if not b['remaining'])
    reserve=0
    if barracks:
        inventory=stocks(player)
        recruitment_budget=0;prices=buy_prices(player)
        for resource,amount in units.get('native:infantry',{}).get('recruit',{}).items():
            missing=max(0,amount-inventory.get(resource,0))
            if resource=='native:gold':recruitment_budget+=amount
            elif missing and prices.get(resource) is not None:
                recruitment_budget+=math.ceil(prices[resource]*missing)
        reserve=max(recruitment_budget,max(0,income or 0)*MIL_SHARE)
    remaining={site['id']:site for site in sites}
    while remaining and not ledger.full():
        choices=[]
        for site in remaining.values():
            for kind,base in recipes:
                if kind['id']=='native:barracks' and barracks>=BARRACKS_CAP:continue
                quote=candidate_cost(site,kind,buildings,{k['id']:k for k in catalog},facts['building_sites'])
                if quote is None:continue
                estimate=player.query('building_economics',{'kind':kind['id'],'level':quote['level'],'other_slots':quote['other_slots']})
                payback=estimate['payback_periods'];priority=1
                if generated<demand and kind['energy_output']>0:priority=0
                elif not barracks and kind['id']=='native:barracks':priority=0
                elif kind['id']=='native:supply-factory' and supply_need>0 and stocks(player).get('native:supply',0)<supply_need*SUPPLY_PERIODS:priority=0
                elif payback is None:continue
                choices.append((priority,payback if payback is not None else float('inf'),site['id'],kind['id'],quote))
        constructed=False
        for _,_,site_id,kind,quote in sorted(choices):
            if ensure(ledger,quote['costs'],reserve) and ledger.do('build',site=site_id,kind=kind):
                if kind=='native:barracks':barracks+=1
                definition=next(k for k in catalog if k['id']==kind)
                generated+=definition['energy_output'];demand+=definition['energy_use']
                del remaining[site_id];constructed=True;break
        if not constructed:break
    # The economic layer alone decides production of troops.
    for site in sites:
        if ledger.full():break
        for kind in ('native:infantry','native:cavalry'):
            if kind not in units:continue
            if not any(b['site']==site['id'] and b['kind']=='native:barracks' and not b['remaining'] for b in buildings):continue
            if ensure(ledger,units[kind]['recruit']):ledger.do('recruit',site=site['id'],kind=kind)
            break
