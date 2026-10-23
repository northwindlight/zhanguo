"""Offline, synthetic homogeneous-population market regression. No live saves/API."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from host import GameHost


def run(version, seed, turns, *, economy_only=False, experimental=False):
    cfg = {'map_size':24, 'seed':seed,
           'nations':[{'id':f'scenario:n{i}', 'name':f'国{i}', 'client':'rule',
                       'rule_ai':version, 'rule_seed':seed+i} for i in range(8)]}
    if experimental:
        cfg.update(mods='mods.experimental.toml', parliament={'reviewer':{
            'model':'offline-fixture', 'api_key':'offline-fixture'}})
    host = GameHost.new(cfg)
    if experimental:
        from tests.test_parliament import CouncilBackend
        for services in host.services.values():
            services['council']._review_backend = CouncilBackend([True] * turns)
    errors = Counter()
    production = Counter()
    history = []
    started = time.monotonic()
    def receipt(actor, action, arguments, result):
        if not result['ok']:
            errors[(action, result['message'])] += 1
    original_produce = host.world.buildings.produce
    def observe_production(*args,**kwargs):
        result=original_produce(*args,**kwargs)
        production.update(r.status for r in result.records)
        return result
    context = patch('ruleai.v11plus.entry.military.run') if economy_only else patch.dict({}, {})
    with context, patch.object(host.world.buildings,'produce',side_effect=observe_production):
        for turn in range(turns):
            host.cycle(on_result=receipt)
            if (turn+1) % 10 == 0 or turn == turns-1:
                cash = buildings = army = starved = 0
                kinds = Counter()
                for actor in host.actors():
                    facts = host.player(actor).observe()['facts']
                    cash += next(s['amount'] for s in facts['econ']['stocks'] if s['id']=='native:gold')
                    instances = facts['building_sites']['instances']
                    buildings += len(instances)
                    kinds.update(b['kind'] for b in instances)
                    army += sum(u['owner']==actor for u in facts['army'])
                    starved += facts['econ']['preview']['military']['shortfall'] > 0
                market = host.player(host.actors()[0]).call_api('economy:queries','market')
                history.append({'turn':turn+1,'gold':cash,'buildings':buildings,
                                'kinds':dict(kinds),'army':army,'starving_nations':starved,
                                'production_statuses':dict(production),
                                'inventory':dict(market['inventory']), 'prices':dict(market['prices'])})
                print(f'{version} seed={seed} turn={turn+1} cash={cash} buildings={buildings} army={army}', flush=True, file=sys.stderr)
    return {'version':version, 'seed':seed, 'turns':turns, 'economy_only':economy_only,
            'experimental':experimental, 'seconds':round(time.monotonic()-started,2),
            'profiles':{a:getattr(c.state,'profile',None) for a,c in host.clients.items()},
            'errors':[{'action':a,'message':m,'count':n} for (a,m),n in errors.items()],
            'history':history}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--versions', nargs='+', default=['v11plus','v13'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[73,20261023,42])
    parser.add_argument('--turns', type=int, default=100)
    parser.add_argument('--economy-only', action='store_true')
    parser.add_argument('--experimental', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    results=[]
    for version in args.versions:
        for seed in args.seeds:
            results.append(run(version, seed, args.turns, economy_only=args.economy_only,
                               experimental=args.experimental))
            args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
