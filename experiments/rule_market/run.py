"""Offline, synthetic homogeneous-population market regression. No live saves/API."""
import argparse
from collections import Counter
from contextlib import ExitStack
import json
import hashlib
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from host import GameHost

_rule_root=Path(__file__).resolve().parents[2]/'ruleai'
_source=hashlib.sha256()
for _path in sorted(_rule_root.rglob('*.py')):
    _source.update(_path.relative_to(_rule_root).as_posix().encode())
    _source.update(_path.read_text(encoding='utf-8').encode())
PLANNER_SOURCE_SHA256=_source.hexdigest()


def run(version, seed, turns, *, economy_only=False, experimental=False, no_recruitment=False):
    if no_recruitment and version not in ('v13','v14'):
        raise ValueError('Pure economic comparison currently supports v13/v14')
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
    actual_inputs,actual_outputs = Counter(),Counter()
    first_market=host.player(host.actors()[0]).call_api('economy:queries','market')
    fixed_prices={**dict(first_market['prices']),'native:gold':1}
    history = []
    optimization=Counter();modes=Counter();rejected=Counter();gaps=[];shortfall=Counter()
    military=Counter();last_starved=0
    started = time.monotonic()
    def receipt(actor, action, arguments, result):
        if not result['ok']:
            errors[(action, result['message'])] += 1
    original_produce = host.world.buildings.produce
    original_supply=host.world.military.supply
    def observe_supply(*args,**kwargs):
        nonlocal last_starved
        result=original_supply(*args,**kwargs)
        last_starved=sum(short>0 for owner,need,used,short,healed in result[0])
        military['starving_nation_periods']+=last_starved
        for owner,need,used,short,healed in result[0]:
            military['need']+=need;military['used']+=used;military['shortfall']+=short
        return result
    def observe_production(*args,**kwargs):
        result=original_produce(*args,**kwargs)
        production.update(r.status for r in result.records)
        for row in result.records:
            actual_inputs.update(dict(row.inputs));actual_outputs.update(dict(row.outputs))
        return result
    context=ExitStack()
    if economy_only:context.enter_context(patch('ruleai.v11plus.entry.military.run'))
    if no_recruitment:
        from ruleai.v14 import economy as joint_economy
        def economic_client(self,ledger):
            self.last_economy=joint_economy.run(ledger,self.state,manage_military=False)
        context.enter_context(patch('ruleai.v13.economy.Planner.recruit'))
        context.enter_context(patch('ruleai.v14.entry.RuleClient.run_economy',economic_client))
    with context, patch.object(host.world.buildings,'produce',side_effect=observe_production),patch.object(host.world.military,'supply',side_effect=observe_supply):
        for turn in range(turns):
            host.cycle(on_result=receipt)
            for client in host.clients.values():
                if client.version!='v14' or not hasattr(client,'last_economy'):continue
                row=client.last_economy;cert=row['certificate']
                optimization[cert['status']]+=1
                modes[cert['mode']]+=1
                if cert['gap'] is not None:gaps.append(cert['gap'])
                if not row['result']['executed']:rejected[row['result']['reason']]+=1
                shortfall['initial' if turn==0 else 'later']+=cert['service_shortfall'] or 0
                shortfall['current_initial' if turn==0 else 'current_later']+=cert['current_shortfall'] or 0
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
                                'kinds':dict(kinds),'army':army,'starving_nations':last_starved,
                                'next_preview_short_nations':starved,'military':dict(military),
                                'production_statuses':dict(production),
                                'production_inputs':dict(actual_inputs),'production_outputs':dict(actual_outputs),
                                'net_production_at_initial_prices':sum((actual_outputs[k]-actual_inputs[k])*fixed_prices[k] for k in fixed_prices),
                                'inventory':dict(market['inventory']), 'prices':dict(market['prices'])})
                print(f'{version} seed={seed} turn={turn+1} cash={cash} buildings={buildings} army={army}', flush=True, file=sys.stderr)
    return {'version':version, 'seed':seed, 'turns':turns, 'economy_only':economy_only,
            'planner_source_sha256':PLANNER_SOURCE_SHA256,
            'no_recruitment':no_recruitment,
            'experimental':experimental, 'seconds':round(time.monotonic()-started,2),
            'fixed_prices':fixed_prices,
            'profiles':{a:getattr(c.state,'profile',None) for a,c in host.clients.items()},
            'errors':[{'action':a,'message':m,'count':n} for (a,m),n in errors.items()],
            'optimization':{'statuses':dict(optimization),'modes':dict(modes),'rejected':dict(rejected),
                            'service_shortfall':dict(shortfall),
                            'gap_mean':sum(gaps)/len(gaps) if gaps else None,'gap_max':max(gaps) if gaps else None},
            'history':history}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--versions', nargs='+', default=['v11plus','v13'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[73,20261023,42])
    parser.add_argument('--turns', type=int, default=100)
    parser.add_argument('--economy-only', action='store_true')
    parser.add_argument('--no-recruitment',action='store_true',help='Compare the economic layer without new troops (v13/v14)')
    parser.add_argument('--experimental', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    results=[]
    for version in args.versions:
        for seed in args.seeds:
            results.append(run(version, seed, args.turns, economy_only=args.economy_only,
                               experimental=args.experimental,no_recruitment=args.no_recruitment))
            args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
