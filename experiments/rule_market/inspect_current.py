"""Inspect economic strategy assumptions using new, offline synthetic fixtures.

No live files or model calls. This diagnoses the checked-out code; it does not
assert that historical mismatches must remain after a later strategy update.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from host import GameHost
from ruleai.v11plus import apimap
from ruleai.v11plus.economy import _cost, _legal, _payback
from ruleai.v11plus.ledger import Ledger
from ruleai.v13 import RuleState
from ruleai.v13.economy import Planner


PLANT = 'native:wood-power-plant'


def fixture():
    host = GameHost.new({'map_size': 12, 'seed': 73, 'nations': [{
        'id': 'scenario:qin', 'name': '秦', 'client': 'rule',
        'rule_ai': 'v13', 'rule_strategy': 'balanced',
        'resources': {'native:gold': 10000, 'native:wood': 10000}}]})
    player = host.player('scenario:qin')
    ledger = Ledger(player)
    assert ledger.do('plan', text='独立离线经济接口核验')
    data = apimap.query(player, 'building_sites')
    hall = next(b for b in data['instances'] if b['kind'] == 'native:town-hall')
    site = next(s for s in data['sites'] if s['id'] == hall['site'])
    return host, player, ledger, site


def compare_legality(player, site, kind, *, queued=False):
    data = apimap.query(player, 'building_sites')
    catalog = {b['id']: b for b in apimap.query(player, 'building_catalog')}
    existing = [b for b in data['instances'] if b['site'] == site['id']]
    planning = player.call_api('native:buildings', 'planning', {
        'x': site['position'][0], 'y': site['position'][1], 'queued': queued})
    row = next(b for b in planning['buildings'] if b['kind'] == kind)
    return {'kind': kind, 'queued': queued,
        'legacy_legal_used_by_both_versions': _legal(
            site, kind, catalog[kind], existing, data, catalog),
        'official_available': row['available'], 'official_reason': row['reason'],
        'official_space': planning['space'],
        'unmet': [r for r in row['requirements'] if not r['met']]}


def cancelled_construction():
    _, player, ledger, site = fixture()
    assert ledger.do('build', site=site['id'], kind=PLANT)
    identity = ledger.acts[-1]['data']['id']
    assert player.call_api('native:buildings', 'demolish', {'id': identity})['ok']
    return compare_legality(player, site, PLANT)


def reserved_space():
    host, player, ledger, site = fixture()
    data = apimap.query(player, 'building_sites')
    count = sum(b['site'] == site['id'] for b in data['instances'])
    # Explicit fixture setup only, not information given to a decision-maker.
    host.world.buildings.seed(site['id'], PLANT, count=site['capacity']-2-count)
    assert ledger.do('build', site=site['id'], kind=PLANT)
    identity = ledger.acts[-1]['data']['id']
    assert player.call_api('native:buildings', 'enqueue', {
        'site': site['id'], 'kind': PLANT})['ok']
    assert player.call_api('native:buildings', 'demolish', {'id': identity})['ok']
    assert player.call_api('native:buildings', 'enqueue', {
        'site': site['id'], 'kind': PLANT})['ok']
    # queued=True isolates capacity from the already-used current-period quota.
    return compare_legality(player, site, PLANT, queued=True)


def academy_candidate():
    host, player, ledger, site = fixture()
    host.world.buildings.seed(site['id'], PLANT, count=4)
    result = compare_legality(player, site, 'native:academy')
    planner = Planner(ledger, RuleState(profile='balanced'))
    try:
        result['v13_economic_candidate'] = any(
            place['id'] == site['id'] and kind == 'native:academy'
            for place, kind, _ in planner.candidates())
    finally:
        planner.finish()
    return result


def market_quote():
    _, player, _, _ = fixture()
    market = apimap.query(player, 'market')
    price = dict(market['buy_prices'])['native:wood']
    rows = []
    for quantity in (10, 100, 500):
        quote = player.call_api('economy:queries', 'trade_quote', {
            'resource': 'native:wood', 'quantity': quantity, 'side': 'buy'})
        rows.append({'quantity': quantity, 'v11_linear_budget': math.ceil(price*quantity),
                     'official_gold': quote['gold'], 'official_exact_total': quote['exact_total']})
    return rows


def payback_capital():
    _, player, _, site = fixture()
    kind = 'native:farm'
    data = apimap.query(player, 'building_sites')
    catalog = {b['id']: b for b in apimap.query(player, 'building_catalog')}
    existing = [b for b in data['instances'] if b['site'] == site['id']]
    estimate = apimap.query(player, 'building_economics', {'kind': kind})
    costs = _cost(kind, 1, site, existing, catalog)
    # Exactly the rescaling performed by v11plus.run, exposed for inspection.
    ranked = _payback(estimate, kind)*costs.get('native:gold', 0)/estimate['construction']['total']
    return {'kind': kind, 'site_penalty': site['penalty'], 'actual_materials': costs,
        'catalog_capital_value': estimate['construction']['total'],
        'catalog_net_per_period': estimate['net_per_period']['total'],
        'catalog_payback': estimate['payback_periods'], 'v11_ranked_payback': ranked,
        'note': '两者口径不同；此例用于检查木材资本是否在最终排序中保留，不表示实际投资回报。'}


def inspect():
    root = Path(__file__).resolve().parents[2]
    return {'source_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'fixture': 'native, map=12, seed=73, one nation, explicit ample gold/wood; no live data',
        'after_cancel': cancelled_construction(), 'reserved_space': reserved_space(),
        'academy': academy_candidate(), 'market_quote': market_quote(),
        'payback_capital': payback_capital()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = inspect()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
