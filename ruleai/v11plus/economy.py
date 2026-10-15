"""v11plus economic plan, ported from the legacy rule AI to PlayerSession.

The planner keeps the old one-turn ledger: calculate real needs, value each
legal site, reserve materials for the affordable construction plan, trade the
surplus, then build and recruit. Native modules still validate every action.
"""
import math

from .parameters import BARRACKS_CAP, MIL_SHARE, SUPPLY_PERIODS

STOCK_FACTORY_DISCOUNT = 0.85
STOCK_OUTPUT_FACTORIES = {'native:equipment-factory'}
SKIP_BUILDINGS = {'native:militia-camp'}


def _stocks(player):
    return {row['id']: row['amount'] for row in player.query('econ')['stocks']}


def _amounts(value):
    return dict(value or {})


def _cost(kind, level, site, existing, catalog):
    """Reproduce native construction cost rounding for a public site quote."""
    row = catalog[kind]
    base = _amounts(row['costs_by_level'][min(level, len(row['costs_by_level'])) - 1])
    discount = max((catalog[b['kind']]['build_discount'] for b in existing
                    if b['remaining'] == 0), default=0)
    if row.get('build_discount'):
        discount = 0
    gold = base.get('native:gold', 0)
    gold = gold * (100 + site['penalty']) // 100
    gold = gold * (100 - discount) // 100
    gold = gold * row.get('policy_pct', 100) // 100
    if gold:
        base['native:gold'] = gold
    return base


def _legal(site, kind, row, existing, facts):
    count = len(existing)
    same = sum(b['kind'] == kind for b in existing)
    if len(row['costs_by_level']) > 1 and same >= len(row['costs_by_level']):
        return False
    if count >= site['capacity'] or count < row['min_slots']:
        return False
    if row['max_per_site'] is not None and same >= row['max_per_site']:
        return False
    resource = row['site_resource']
    if resource and same >= dict(site['resources']).get(resource, 0):
        return False
    if row['self_build_limit'] is not None and facts['self_built'].get(kind, 0) >= row['self_build_limit']:
        return False
    if any(item['site'] == site['id'] for item in facts['construction']):
        return False
    return True


def _estimate(player, kind, other_slots):
    return player.query('building_economics', {'kind': kind, 'other_slots': other_slots})


def _payback(estimate, kind):
    if kind not in STOCK_OUTPUT_FACTORIES:
        return estimate['payback_periods']
    outputs = estimate['outputs_sell']
    inputs = estimate['inputs']
    energy = estimate['energy_used'] * (estimate['energy_unit_cost'] or 0)
    net = (outputs['total'] - inputs['total'] - energy) * STOCK_FACTORY_DISCOUNT
    capex = estimate['construction']['total']
    return capex / net if net > 0 else None


def _buy_cost(player, resource, quantity):
    price = dict(player.query('market')['buy_prices']).get(resource)
    return None if price is None else math.ceil(price * math.ceil(quantity))


def _ensure(ledger, recipe, reserve=0):
    inventory = _stocks(ledger.player)
    missing = {key: math.ceil(amount - inventory.get(key, 0))
               for key, amount in recipe.items()
               if key != 'native:gold' and amount > inventory.get(key, 0)}
    total = recipe.get('native:gold', 0) + reserve
    for resource, amount in missing.items():
        price = _buy_cost(ledger.player, resource, amount)
        if price is None:
            return False
        total += price
    if inventory.get('native:gold', 0) < total:
        return False
    for resource, amount in missing.items():
        receipt = ledger.do('buy', resource=resource, quantity=amount)
        if not receipt:
            return False
    return True


def _sell(ledger, resource, quantity):
    amount = int(quantity)
    if amount > 0:
        ledger.do('sell', resource=resource, quantity=amount)


def run(ledger):
    player = ledger.player
    actor = player.actor
    obs = player.observe()
    facts = obs['facts']
    site_data = player.query('building_sites')
    sites = sorted(site_data['sites'], key=lambda row: tuple(row['position']))
    buildings = site_data['instances']
    catalog_rows = player.query('building_catalog')
    catalog = {row['id']: row for row in catalog_rows}
    units = player.query('unit_catalog')
    inventory = _stocks(player)
    market = player.query('market')
    buy_prices = dict(market['buy_prices'])
    sell_prices = dict(market['sell_prices'])
    army = [unit for unit in facts['army'] if unit['owner'] == actor]
    supply_need = sum(units[unit['kind']]['supply'] for unit in army)
    latest = facts['econ']['latest_turn']
    income = latest['gdp']['total'] if latest else inventory.get('native:gold', 0)
    if income is None:
        income = inventory.get('native:gold', 0)
    supply_buildings = sum(b['kind'] == 'native:supply-factory' and not b['remaining'] for b in buildings)
    supply_cap = supply_buildings * 2
    barracks = sum(b['kind'] == 'native:barracks' and not b['remaining'] for b in buildings)
    want_barracks = max(2, min(supply_cap // 2 + 2, BARRACKS_CAP))
    upkeep = supply_need * buy_prices.get('native:supply', 0)
    mil_ok = income <= 0.5 or upkeep / income < MIL_SHARE
    army_cap = max(len(army) + 1, supply_cap) if mil_ok else len(army)

    completed = [b for b in buildings if b['remaining'] == 0]
    power = sum(catalog[b['kind']]['energy_output'] for b in completed)
    power_need = sum(catalog[b['kind']]['energy_use'] for b in completed)
    power_plants = catalog.get('native:wood-power-plant')
    plant_count = max(0, math.ceil((power_need - power) / power_plants['energy_output'])) if power_plants and power < power_need else 0
    planned_recruits = min(max(0, army_cap - len(army)), sum(
        max(0, sum(b['kind'] == 'native:barracks' and b['site'] == s['id'] for b in buildings))
        for s in sites))

    # Rank legal site/building pairs by their actual site-specific return.
    roi = []
    existing_by_site = {site['id']: [b for b in buildings if b['site'] == site['id']] for site in sites}
    for site in sites:
        existing = existing_by_site[site['id']]
        if any(b['site'] == site['id'] for b in site_data['construction']):
            continue
        for kind, row in catalog.items():
            if kind in SKIP_BUILDINGS or not _legal(site, kind, row, existing, site_data):
                continue
            same = sum(b['kind'] == kind for b in existing)
            level = same + 1 if len(row['costs_by_level']) > 1 else 1
            estimate = _estimate(player, kind, len(existing) - same)
            payback = _payback(estimate, kind)
            if payback is None:
                continue
            costs = _cost(kind, level, site, existing, catalog)
            # Estimate construction with site penalty and local academy discount.
            base_gold = costs.get('native:gold', 0)
            nominal = estimate['construction']['total']
            if nominal > 0:
                payback *= base_gold / nominal
            roi.append((payback, -len(existing), tuple(site['position']), site['id'], kind, costs))
    roi.sort()
    best = {}
    for item in roi:
        best.setdefault(item[3], item)

    # Build the exact material plan that can be funded by current cash and saleable stock.
    need = {}
    def want(resource, amount):
        if amount > 0:
            need[resource] = need.get(resource, 0) + math.ceil(amount)
    want('native:supply', supply_need)
    for kind in ('native:supply-factory', 'native:equipment-factory', 'native:wood-power-plant', 'native:oil-power-plant'):
        count = sum(b['kind'] == kind and not b['remaining'] for b in buildings)
        for resource, amount in catalog.get(kind, {}).get('inputs', {}).items():
            want(resource, count * amount)
    wood_plant_cost = catalog.get('native:wood-power-plant', {}).get('costs_by_level', [{}])[0]
    barracks_cost = catalog.get('native:barracks', {}).get('costs_by_level', [{}])[0]
    want('native:wood', plant_count * wood_plant_cost.get('native:wood', 0))
    barracks_to_build = min(max(0, want_barracks - barracks), sum(len(existing_by_site[s['id']]) >= 3 and not any(b['site'] == s['id'] for b in site_data['construction']) for s in sites))
    want('native:wood', barracks_to_build * barracks_cost.get('native:wood', 0))
    # Keep one-turn recruitment affordable under the legacy income and supply gates.
    infantry = units.get('native:infantry', {})
    recruit_cost = _amounts(infantry.get('recruit', {}))
    for _ in range(planned_recruits):
        for resource, amount in recruit_cost.items():
            want(resource, amount)
    budget = inventory.get('native:gold', 0)
    for resource, quantity in need.items():
        if resource not in ('native:wood', 'native:gold'):
            budget += max(0, inventory.get(resource, 0) - quantity) * sell_prices.get(resource, 0) * 0.9
    wood_price = max(1, buy_prices.get('native:wood', 1))
    affordable = []
    for item in best.values():
        costs = item[5]
        total = costs.get('native:gold', 0) + costs.get('native:wood', 0) * wood_price
        if total <= budget:
            affordable.append(item)
            budget -= total
            want('native:wood', costs.get('native:wood', 0))

    # Sell only stock above this period's production, construction and recruitment needs.
    for resource in sell_prices:
        if resource == 'native:wood':
            continue
        surplus = inventory.get(resource, 0) - need.get(resource, 0)
        if surplus > 0:
            _sell(ledger, resource, surplus)
    for resource, amount in need.items():
        gap = amount - inventory.get(resource, 0)
        if gap > 0:
            _ensure(ledger, {resource: gap})

    # The old economic layer recruited independently of free building slots.
    if mil_ok and len(army) < army_cap:
        for site in sites:
            available = sum(b['kind'] == 'native:barracks' and b['remaining'] == 0 and b['site'] == site['id'] for b in buildings)
            for _ in range(available):
                if len(army) >= army_cap or ledger.full():
                    break
                if not _ensure(ledger, recruit_cost):
                    break
                receipt = ledger.do('recruit', site=site['id'], kind='native:infantry', count=1)
                if receipt:
                    army.append({'kind': 'native:infantry'})

    built_this_turn = set()
    gen_add = demand_add = 0
    barracks_left = max(0, want_barracks - barracks)
    # Conditions first: barracks and missing power, followed by the ROI list.
    order = []
    for site in sorted(sites, key=lambda s: (-len(existing_by_site[s['id']]), tuple(s['position']))):
        current = existing_by_site[site['id']]
        if barracks_left and len(current) >= 3:
            order.append((site, 'native:barracks'))
        if power + gen_add < power_need + demand_add and power_plants:
            order.append((site, 'native:wood-power-plant'))
    order.extend((next(s for s in sites if s['id'] == item[3]), item[4]) for item in affordable)
    for site, kind in order:
        if ledger.full() or site['id'] in built_this_turn:
            continue
        existing = existing_by_site[site['id']]
        if kind == 'native:barracks' and barracks_left <= 0:
            continue
        if kind == 'native:wood-power-plant' and power + gen_add >= power_need + demand_add:
            continue
        # The legacy rule AI always built the academy before its town hall so
        # the hall itself and later buildings received the local 25% discount.
        if kind == 'native:town-hall' and not any(
                b['kind'] == 'native:academy' and b['remaining'] == 0 for b in existing):
            academy = catalog.get('native:academy')
            if academy and _legal(site, 'native:academy', academy, existing, site_data):
                academy_cost = _cost('native:academy', 1, site, existing, catalog)
                if _ensure(ledger, academy_cost) and ledger.do('build', site=site['id'], kind='native:academy'):
                    built_this_turn.add(site['id'])
                    continue
        # Power shortage replaces a planned electricity consumer with a plant.
        replaced_for_power = bool(catalog[kind]['energy_use'] and
                                  power + gen_add < power_need + demand_add + catalog[kind]['energy_use'])
        if replaced_for_power:
            kind = 'native:wood-power-plant'
        same = sum(b['kind'] == kind for b in existing)
        level = same + 1 if len(catalog[kind]['costs_by_level']) > 1 else 1
        costs = _cost(kind, level, site, existing, catalog)
        if not _ensure(ledger, costs):
            if replaced_for_power:
                built_this_turn.add(site['id'])
            continue
        if ledger.do('build', site=site['id'], kind=kind):
            built_this_turn.add(site['id'])
            if kind == 'native:barracks':
                barracks_left -= 1
            gen_add += catalog[kind]['energy_output']
            demand_add += catalog[kind]['energy_use']
