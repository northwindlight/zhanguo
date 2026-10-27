"""Material, fuel and cash planning with market scenarios and durable preferences."""
from collections import Counter
import hashlib
import math

from ruleai.v11plus import apimap
from ruleai.v11plus.economy import _cost, _legal, supply_capacity
from ruleai.v11plus.parameters import BARRACKS_CAP, MIL_SHARE
from .market import MarketModel
from .profiles import PROFILES

GOLD = 'native:gold'
WOOD = 'native:wood'
SUPPLY = 'native:supply'
EQUIPMENT = 'native:equipment'


def stocks(ledger):
    return {r['id']:r['amount'] for r in ledger.query('econ')['stocks']}


def quote(ledger, resource, quantity, side):
    if quantity <= 0:
        return 0
    market = ledger.query('market')
    if resource not in dict(market[side+'_prices']):
        return None
    if side == 'buy' and quantity > dict(market['inventory']).get(resource, 0):
        return None
    return ledger.player.call_api('economy:queries', 'trade_quote',
        {'resource':resource, 'quantity':int(quantity), 'side':side})['gold']


def fund(ledger, targets, reserve=0):
    """Buy a complete recipe only after checking depth and total cash. No stranded buys."""
    inventory = stocks(ledger)
    gaps = {k:math.ceil(v-inventory.get(k,0)) for k,v in targets.items()
            if k != GOLD and v > inventory.get(k,0)}
    sold = {r['data']['resource'] for r in ledger.acts
            if r['ok'] and r['action']=='sell' and isinstance(r.get('data'),dict)}
    if sold.intersection(gaps):
        return False
    cost = targets.get(GOLD,0) + reserve
    for key, amount in gaps.items():
        price = quote(ledger, key, amount, 'buy')
        if price is None:
            return False
        cost += price
    # Quotes share account carry: at most one rounding coin per additional leg.
    if inventory.get(GOLD,0) < cost + max(0, len(gaps)-1):
        return False
    for key, amount in gaps.items():
        if not ledger.do('buy', resource=key, quantity=amount):
            return False
    return True


def cash_cost(ledger, recipe):
    inventory = stocks(ledger)
    cost = recipe.get(GOLD,0)
    for key, amount in recipe.items():
        if key == GOLD:
            continue
        gap = max(0, math.ceil(amount-inventory.get(key,0)))
        value = quote(ledger, key, gap, 'buy')
        if value is None:
            return math.inf
        cost += value
    return cost


def net_recipe(catalog, buildings, military=None):
    """Steady-state material balance, counting power-plant fuel and hall density."""
    net = Counter()
    for b in buildings:
        row = catalog[b['kind']]
        net.subtract(row['inputs'])
        net.update(row['outputs'])
        if 'gold_base' in row['effects']:
            others = sum(o['site']==b['site'] and o['kind']!=b['kind'] for o in buildings)
            net[GOLD] += row['effects']['gold_base'] + others*row['effects'].get('gold_per_slot',0)
    if military:
        net[SUPPLY] -= military.get('need',0)
        net[EQUIPMENT] -= military.get('equipment_need',0)
    return dict(net)


def operating_targets(catalog, buildings, military, prices, periods):
    """Full fuel first; unprofitable industrial recipes receive no speculative refill.

    There is no per-building on/off action. Shared inputs may still let such a
    factory run; withholding purchases is a spending decision, not a shutdown.
    """
    target = Counter({SUPPLY: military.get('need',0)*periods,
                      EQUIPMENT: military.get('equipment_need',0)*periods})
    for b in buildings:
        row = catalog[b['kind']]
        output = sum(v*(1 if k==GOLD else prices['sell'].get(k,0)) for k,v in row['outputs'].items())
        inputs = sum(v*prices['buy'].get(k,math.inf) for k,v in row['inputs'].items())
        consumed = any(target.get(k,0)>0 for k in row['outputs'])
        if row['energy_output'] or not row['inputs'] or output >= inputs or consumed:
            for key, amount in row['inputs'].items():
                target[key] += amount*periods
    return dict(target)


def reserve_cash(catalog, buildings, military, buy, profile):
    needs = Counter({SUPPLY:military.get('need',0), EQUIPMENT:military.get('equipment_need',0)})
    for b in buildings:
        needs.update(catalog[b['kind']]['inputs'])
    # Gold is a separate operating buffer; keeping physical inputs does not
    # guarantee tomorrow's wages/materials can be financed after construction.
    return math.ceil(20 + profile.cash_periods*sum(v*buy.get(k,0) for k,v in needs.items()))


def sell_surplus(ledger, targets, profile):
    inventory = stocks(ledger)
    for key in dict(ledger.query('market')['sell_prices']):
        excess = max(0, inventory.get(key,0)-targets.get(key,0))
        amount = math.ceil(excess*profile.release)
        if amount and not ledger.full():
            ledger.do('sell', resource=key, quantity=amount)


def _tie(actor, site, kind):
    # Stable local tie breaking prevents catalog/site order from choosing the
    # same investment everywhere; long-term policy is sampled separately.
    return hashlib.sha256(f'{actor}:{site}:{kind}'.encode()).digest()


class Planner:
    def __init__(self, ledger, state):
        self.ledger, self.state = ledger, state
        self.player = ledger.player
        self.profile = PROFILES[state.profile]
        self.obs = self.player.observe()
        self.sites = apimap.query(self.player, 'building_sites')
        self.catalog = {r['id']:r for r in apimap.query(self.player, 'building_catalog')}
        self.market = ledger.query('market')
        self.buy, self.sell = dict(self.market['buy_prices']), dict(self.market['sell_prices'])
        self.prices = {'buy':self.buy, 'sell':self.sell}
        self.military = ledger.query('econ')['preview'].get('military',{})
        nations = self.obs['facts'].get('foreign',{}).get('nations',[])
        self.model = MarketModel(self.market, state.market, sum(n.get('alive',True) for n in nations))
        self.own_net = Counter()
        self.original_do = ledger.on_result
        def record(action, args, receipt):
            if receipt['ok'] and action in ('buy','sell'):
                self.own_net[args['resource']] += args['quantity'] * (1 if action=='sell' else -1)
            if self.original_do:
                self.original_do(action,args,receipt)
        ledger.on_result = record

    def buildings(self):
        return self.sites['instances']

    def operations(self):
        buildings = self.buildings()
        immediate = [b for b in buildings if not b['remaining']]
        # Protect commissioned capacity as well: construction completes before
        # next turn, so its fuel must not be sold as today's "surplus".
        targets = operating_targets(self.catalog, buildings, self.military, self.prices, self.profile.reserve)
        sell_targets = dict(targets)
        # Reserve one legal construction per site before selling building
        # material. Otherwise the opening wood is sold and immediately bought
        # back for the same period's construction, paying both spreads.
        for site in self.sites['sites']:
            existing = [b for b in buildings if b['site']==site['id']]
            wood = max((_cost(kind,1,site,existing,self.catalog).get(WOOD,0)
                        for kind,row in self.catalog.items()
                        if _legal(site,kind,row,existing,self.sites,self.catalog)),default=0)
            sell_targets[WOOD] = sell_targets.get(WOOD,0)+wood
        units = apimap.query(self.player,'unit_catalog')
        infantry = units.get('native:infantry',{})
        recruiters = sum(b['kind']=='native:barracks' and not b['remaining'] for b in buildings)
        for key,value in infantry.get('recruit',{}).items():
            if key!=GOLD:sell_targets[key]=sell_targets.get(key,0)+recruiters*value
        sell_surplus(self.ledger, sell_targets, self.profile)
        # Profiles replenish in different sized batches instead of placing an
        # identical small order every turn. One-period needs remain protected.
        inventory = stocks(self.ledger)
        reorder_periods = max(1,self.profile.reserve//2)
        refill = {k:v for k,v in targets.items()
                  if inventory.get(k,0) < v*reorder_periods/self.profile.reserve}
        # Full reserve can be unaffordable; retry a complete one-period package,
        # then military alone. Never spend the last coins chasing a partial plant recipe.
        if not fund(self.ledger, refill):
            minimal = operating_targets(self.catalog, immediate, self.military, self.prices, 1)
            if not fund(self.ledger, minimal):
                fund(self.ledger, {SUPPLY:self.military.get('need',0),
                                   EQUIPMENT:self.military.get('equipment_need',0)})
        self.model.order_offset = dict(self.own_net)

    def candidates(self):
        result = []
        by_site = {s['id']:[b for b in self.buildings() if b['site']==s['id']]
                   for s in self.sites['sites']}
        for site in self.sites['sites']:
            existing = by_site[site['id']]
            for kind, row in self.catalog.items():
                if not _legal(site,kind,row,existing,self.sites,self.catalog):
                    continue
                if not (row['outputs'] or row['energy_output'] or 'gold_base' in row['effects']):
                    continue
                level = 1 + sum(b['kind']==kind for b in existing) if len(row['costs_by_level'])>1 else 1
                costs = _cost(kind,level,site,existing,self.catalog)
                result.append((site,kind,costs))
        return result

    def choose(self):
        buildings = self.buildings()
        net = net_recipe(self.catalog, buildings, self.military)
        horizon = self.profile.horizon
        base_value = self.model.value(net, net, {}, horizon)
        power = sum(self.catalog[b['kind']]['energy_output'] for b in buildings)
        demand = sum(self.catalog[b['kind']]['energy_use'] for b in buildings)
        reserve = reserve_cash(self.catalog, buildings, self.military, self.buy, self.profile)
        candidate_rows = self.candidates()
        # A fuelled power package takes precedence when existing production is
        # blocked. Choose between wood and oil by both installation and fuel.
        # Leave capacity for the next legal consumer. Waiting for an actual
        # blackout would deadlock expansion when power exactly equals demand.
        can_expand_power = any(self.catalog[k]['energy_use'] for _,k,_ in candidate_rows)
        shortage = power < demand or (power == demand and can_expand_power)
        ranked = []
        for site, kind, costs in candidate_rows:
            row = self.catalog[kind]
            if shortage and not row['energy_output']:
                continue
            if not shortage and row['energy_output']:
                continue
            if row['energy_use'] and power < demand + row['energy_use']:
                continue
            added = {'site':site['id'],'kind':kind,'remaining':1}
            after = net_recipe(self.catalog, buildings+[added], self.military)
            delta = {k:after.get(k,0)-net.get(k,0) for k in after.keys()|net.keys()}
            capex = sum(v*(1 if k==GOLD else self.buy.get(k,math.inf)) for k,v in costs.items())
            if not math.isfinite(capex) or capex <= 0:
                continue
            if shortage:
                # Plants provide no saleable electricity. Their value is the
                # income/industry they unlock; installation plus sustained fuel
                # decides which plant repairs the shortage at lowest total cost.
                fuel_net = {k:-v for k,v in row['inputs'].items()}
                fuel = -self.model.value(fuel_net,net,fuel_net,horizon)
                # Value the full installable capacity; charging an oil plant
                # for eight units but crediting only today's one-unit gap
                # locks every country into fuel-hungry wood power forever.
                score = 1000 / ((capex+horizon*fuel)/row['energy_output'])
            else:
                normal = self.model.value(after, net, delta, horizon)
                crowded = self.model.value(after, net, delta, horizon,
                                           1+.5*(self.model.participants-1))
                # Compare against the same baseline; devaluing existing export
                # capacity is part of an investment's marginal return.
                margin = min(normal,crowded) - base_value
                if not math.isfinite(margin):
                    continue
                if margin <= 0:
                    continue
                score = margin/capex
                if delta.get(GOLD,0)>0:
                    score *= self.profile.gold
                elif any(net.get(k,0)<0 and v>0 for k,v in delta.items()):
                    score *= self.profile.self_supply
                else:
                    score *= self.profile.export
                # Even modest gathering returns unlock hall density; no
                # optimistic phantom future buildings are priced as current gold.
            operation = operating_targets(self.catalog,buildings+[added],self.military,
                                          self.prices,self.profile.reserve)
            recipe = dict(costs)
            for key, value in operation.items():
                recipe[key] = recipe.get(key,0)+value
            if cash_cost(self.ledger, recipe)+reserve > stocks(self.ledger).get(GOLD,0):
                continue
            ranked.append((-score,_tie(self.player.actor,site['id'],kind),site,kind,costs,operation))
        return min(ranked,key=lambda r:r[:2]) if ranked else None

    def invest(self):
        while not self.ledger.full():
            chosen = self.choose()
            if chosen is None:
                break
            _, _, site, kind, costs, operation = chosen
            recipe = dict(costs)
            for key,value in operation.items():
                recipe[key] = recipe.get(key,0)+value
            reserve = reserve_cash(self.catalog,self.buildings(),self.military,self.buy,self.profile)
            if not fund(self.ledger,recipe,reserve):
                break
            if not self.ledger.do('build',site=site['id'],kind=kind):
                break
            # Re-score after every investment; one portfolio plan rather than
            # independently ranking every site at the same pre-investment price.
            self.sites = apimap.query(self.player,'building_sites')
            self.model.order_offset = dict(self.own_net)

    def recruit(self):
        ledger = self.ledger
        buildings = self.buildings()
        units = apimap.query(self.player,'unit_catalog')
        infantry = units.get('native:infantry')
        if not infantry:
            return
        army = [u for u in self.obs['facts']['army'] if u['owner']==self.player.actor]
        net = net_recipe(self.catalog,buildings,self.military)
        income = sum(v*(1 if k==GOLD else self.sell.get(k,0)) for k,v in net.items() if v>0)
        upkeep = sum(units[u['kind']]['supply'] for u in army)*self.buy.get(SUPPLY,0)
        if income<=0 or upkeep >= MIL_SHARE*income:
            return
        supply = supply_capacity(self.catalog,buildings)
        # Retain the legacy budget gate: imports may support another unit.
        # A hard cap at domestic supply capacity would turn a viable importer
        # into a permanently tiny army despite a large operating surplus.
        cap = max(len(army)+1, supply//max(1,infantry['supply']))
        if len(army)>=cap:
            return
        reserve = reserve_cash(self.catalog,buildings,self.military,self.buy,self.profile)
        # Barracks are a military investment, not fictitious economic ROI.
        row = self.catalog.get('native:barracks')
        barracks = sum(b['kind']=='native:barracks' for b in buildings)
        if row and barracks<min(BARRACKS_CAP,max(1,math.ceil(cap/4))):
            power = sum(self.catalog[b['kind']]['energy_output'] for b in buildings)
            demand = sum(self.catalog[b['kind']]['energy_use'] for b in buildings)
            for site in self.sites['sites']:
                existing = [b for b in buildings if b['site']==site['id']]
                if power>=demand+row['energy_use'] and _legal(site,'native:barracks',row,existing,self.sites,self.catalog):
                    costs = _cost('native:barracks',1,site,existing,self.catalog)
                    targets = operating_targets(self.catalog,buildings,self.military,self.prices,self.profile.reserve)
                    recipe = dict(costs)
                    for key,value in targets.items():recipe[key]=recipe.get(key,0)+value
                    if fund(ledger,recipe,reserve):
                        if ledger.do('build',site=site['id'],kind='native:barracks'):
                            self.sites = apimap.query(self.player,'building_sites')
                    break
        recipe = dict(infantry['recruit'])
        target = operating_targets(self.catalog,buildings,self.military,self.prices,self.profile.reserve)
        target[SUPPLY] = target.get(SUPPLY,0)+infantry['supply']*self.profile.reserve
        for key,value in target.items():recipe[key]=recipe.get(key,0)+value
        for site in self.sites['sites']:
            count = sum(b['kind']=='native:barracks' and not b['remaining'] and b['site']==site['id'] for b in buildings)
            for _ in range(count):
                if len(army)>=cap or ledger.full() or not fund(ledger,recipe,reserve):
                    return
                if ledger.do('recruit',site=site['id'],kind='native:infantry',count=1):
                    army.append({'kind':'native:infantry'})
                    recipe[SUPPLY] = recipe.get(SUPPLY,0)+infantry['supply']*self.profile.reserve

    def finish(self):
        self.state.market = self.model.remember(self.own_net)
        self.ledger.on_result = self.original_do


def run(ledger, state):
    planner = Planner(ledger,state)
    try:
        planner.operations()
        planner.recruit()
        planner.invest()
    finally:
        planner.finish()
