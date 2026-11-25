"""Read-only building view: main prepaid queues -> legacy completed/pending counts.

Input must come from the bound player's native:buildings.sites API. This does not
start orders, charge resources, grant energy, or change main execution/refunds.
"""
from collections import Counter


def project_buildings(public_sites):
    result={}
    for site in public_sites['sites']:
        result[site['id']]=dict(position=list(site['position']),completed={},pending={})
    actual_ids=set()
    for item in public_sites['instances']:
        row=result[item['site']];key='completed' if item['remaining']==0 else 'pending'
        row[key][item['kind']]=row[key].get(item['kind'],0)+1
        actual_ids.add(item['id'])
    for order in public_sites['queued']:
        if order['id'] in actual_ids:continue
        row=result[order['site']]
        row['pending'][order['kind']]=row['pending'].get(order['kind'],0)+1
    for capacity in public_sites['capacities']:
        row=result[capacity['site']]
        row.update(occupied=capacity['occupied'],free=capacity['free'],capacity=capacity['capacity'],
                   resource_reservations=[dict(r) for r in capacity['resources']])
        assert sum(row['completed'].values())+sum(row['pending'].values())==row['occupied']
    return result
