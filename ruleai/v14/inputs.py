"""Detached, actor-scoped inputs for the economic optimization model."""
from collections import Counter
from ruleai.v11plus import apimap


def collect(player, *, manage_military=True):
    observed = player.observe()
    sites = apimap.query(player,'building_sites')
    catalog = apimap.query(player,'building_catalog')
    plans = {s['id']: player.call_api('native:buildings','planning', {
        'x':s['position'][0],'y':s['position'][1]}) for s in sites['sites']}
    units = apimap.query(player,'unit_catalog')
    recruitment = {s['id']:player.call_api('native:army','planning', {
        'x':s['position'][0],'y':s['position'][1],'count':1}) for s in sites['sites']}
    econ = apimap.query(player,'econ')
    market=apimap.query(player,'market')
    # This is a growth ceiling, not a snapshot-based recruitment commitment.
    # Actual post-growth military affordability belongs in the joint model.
    desired=int(manage_military and 'native:infantry' in units)
    return {'actor':player.actor,'period':observed['period'],'sites':sites,'catalog':catalog,'plans':plans,
        'recruitment':recruitment,'units':units,'econ':econ,
        'stocks':{r['id']:r['amount'] for r in econ['stocks']},
        'market':market,'desired_recruits':desired,
        'orders':player.call_api('native:buildings','orders'),
        'recruit_orders':player.call_api('native:army','recruit_orders'),
        'army':[r for r in observed['facts']['army'] if r['owner']==player.actor],
        'manage_military':manage_military,
        'bank':player.call_api('economy:bank','position')}


def current_counts(data):
    result = Counter()
    for row in data['sites']['instances']:
        result[row['site'],row['kind'],int(row['remaining'])] += 1
    return result
