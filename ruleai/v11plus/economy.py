"""v11plus economic layer: shared inventory, ROI construction and recruitment.

Consumes public quotes and observed production. Strategy tuning is local;
construction prerequisites, policy costs and quotas remain native decisions.
"""
import math
from .parameters import BARRACKS_CAP, MIL_SHARE, SUPPLY_PERIODS

def stocks(player):return {s['id']:s['amount'] for s in player.query('econ')['stocks']}

def ensure(ledger, recipe, reserve=0):
    inventory=stocks(ledger.player)
    purchases=[];gold=recipe.get('native:gold',0)+reserve
    for resource,amount in recipe.items():
        if resource=='native:gold':continue
        missing=max(0,amount-inventory.get(resource,0))
        if missing:
            try:quote=ledger.player.query('market_quote',{'resource':resource,'quantity':math.ceil(missing),'side':'buy'})
            except ValueError:return False
            gold+=quote['gold'];purchases.append((resource,math.ceil(missing)))
    if inventory.get('native:gold',0)<gold:return False
    for resource,quantity in purchases:
        if not ledger.do('buy',resource=resource,quantity=quantity):return False
    return True

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
    recipes=[]
    for kind in catalog:
        try:estimate=player.query('building_economics',{'kind':kind['id']})
        except ValueError:continue
        recipes.append((kind,estimate))
    for b in buildings:
        if b['remaining']:continue
        estimate=player.query('building_economics',{'kind':b['kind'],'level':b['level']})
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
        recruitment_budget=0
        for resource,amount in units.get('native:infantry',{}).get('recruit',{}).items():
            missing=max(0,amount-inventory.get(resource,0))
            if resource=='native:gold':recruitment_budget+=amount
            elif missing:
                try:recruitment_budget+=player.query('market_quote',{'resource':resource,'quantity':missing,'side':'buy'})['gold']
                except ValueError:pass
        reserve=max(recruitment_budget,max(0,income or 0)*MIL_SHARE)
    remaining={site['id']:site for site in sites}
    while remaining and not ledger.full():
        choices=[]
        for site in remaining.values():
            for kind,base in recipes:
                if kind['id']=='native:barracks' and barracks>=BARRACKS_CAP:continue
                try:
                    quote=player.query('build_quote',{'site':site['id'],'kind':kind['id']})
                    estimate=player.query('building_economics',{'kind':kind['id'],'site':site['id']})
                except ValueError:continue
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
            try:quote=player.query('recruit_quote',{'site':site['id'],'kind':kind})
            except ValueError:continue
            if quote['quota'] and ensure(ledger,quote['costs']):ledger.do('recruit',site=site['id'],kind=kind)
            break
