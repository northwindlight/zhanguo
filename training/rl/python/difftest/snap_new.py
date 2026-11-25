import json, re
SHORT = lambda s: s.split(':')[-1]
def snap(host):
    out = {'actors': {}}
    for a in host.actors():
        f = host.player(a).observe()['facts']
        e = f['econ']
        inst = f['building_sites']['instances']
        out['actors'][a] = dict(
            stocks=json.loads(json.dumps(e['stocks'], default=str)), gross=e['gross_assets'],
            cash=f['bank']['cash'],
            land=len(f['building_sites']['sites']),
            bid={b['id']: [SHORT(b['kind']), *map(int, re.search(r'(\d+)-(\d+)$', b['site']).groups()), b.get('remaining'), b.get('level')] for b in inst},
            bld=sorted((SHORT(b['kind']), tuple(re.search(r'(\d+)-(\d+)$', b['site']).groups())) for b in inst),
            army=sorted((SHORT(u['kind']), u['x'], u['y'], u['hp']) for u in f['army'] if u['owner'] == a))
    m = host.player(host.actors()[0]).observe()['facts']['market']
    try:
        merged = None
        for a in host.actors():
            d = json.loads(json.dumps(host.player(a).call_api('native:diplomacy', 'snapshot', {}), default=str))
            if merged is None: merged = d
            else:
                for key in ('offers', 'votes'):
                    ids = {x['id'] for x in merged[key]}
                    merged[key] += [x for x in d[key] if x['id'] not in ids]
        out['diplo'] = merged
    except Exception as e: out['diplo'] = str(e)
    for a in host.actors():
        try: out['actors'][a]['bank'] = json.loads(json.dumps(host.player(a).observe()['facts']['bank'], default=str))
        except Exception: pass
    wild = {}
    for a in host.actors():
        for u in host.player(a).observe()['facts']['army']:
            if u['owner'] == '野人' and u['hp'] != 100: wild[(u['x'], u['y'], u['id'])] = u['hp']
    out['wild'] = sorted([k[0], k[1], k[2], v] for k, v in wild.items())
    out['market'] = {k: {SHORT(g): v for g, v in m[k]} for k in ('prices', 'inventory', 'reference')}
    return out
