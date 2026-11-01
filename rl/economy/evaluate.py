"""Paired-seat heterogeneous markets with the unchanged v11plus military."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import time
from unittest.mock import patch

import torch
from host import GameHost
from .client import LearnedClient
from .model import load_policy
from .scenarios import config,source_hash,snapshot


def run(seed,turns,*,size=24,swap=False,model=None,candidate='v13plus',deadline=float('inf')):
    from .online import consumption_increment,consumption_total,check_deadline
    source=source_hash()
    cfg=config(seed,size=size,swap=swap,candidate=candidate)
    host=GameHost.new(cfg);failures=defaultdict(Counter)
    if model is not None:
        from .projects import LearnedProjects
        learner=LearnedProjects if model.action_mode=='projects' else LearnedClient
        for actor,client in list(host.clients.items()):
            if client.version==candidate:host.clients[actor]=learner(state=client.state,model=model)
    initial=snapshot(host,failures);history=[];production=defaultdict(Counter)
    flows=defaultdict(lambda:dict(inputs=Counter(),outputs=Counter()))
    supply=Counter();actions=defaultdict(Counter);milestones=defaultdict(dict)
    consumption=Counter();previous=Counter();admin_previous=Counter();tail=defaultdict(list)
    economy_trace=defaultdict(list);late_production={};late_supply={}
    prices=dict(host.player(host.actors()[0]).call_api('economy:queries','market')['prices'])
    prices['native:gold']=1
    original_produce,original_supply=host.world.buildings.produce,host.world.military.supply
    def produce(*args,**kwargs):
        result=original_produce(*args,**kwargs)
        for r in result.records:
            production[r.owner][r.status]+=1
            flows[r.owner]['inputs'].update(dict(r.inputs));flows[r.owner]['outputs'].update(dict(r.outputs))
        return result
    def feed(*args,**kwargs):
        result=original_supply(*args,**kwargs)
        for owner,need,used,short,healed in result[0]:supply[owner]+=short
        return result
    def receipt(actor,action,args,result):
        if not result['ok']:failures[actor][action+':'+result['message']]+=1
        else:
            actions[actor][action]+=1
            if action=='recruit':milestones[actor].setdefault('first_recruit',turn+1)
            if action=='build' and args['kind']=='native:barracks':
                milestones[actor].setdefault('first_barracks',turn+1)
    start=time.monotonic()
    with patch.object(host.world.buildings,'produce',side_effect=produce),patch.object(host.world.military,'supply',side_effect=feed):
        for turn in range(turns):
            check_deadline(deadline)
            if turn==max(0,turns-50):
                late_production={a:production[a].copy() for a in initial}
                late_supply=dict(supply)
            host.cycle(on_result=receipt)
            for actor in initial:
                value,consumption[actor]=consumption_increment(host,actor,previous[actor])
                previous[actor]=consumption[actor];tail[actor].append(value)
                admin_total=host.world.economy.spending(actor)['administration'].known
                admin_step=admin_total-admin_previous[actor];admin_previous[actor]=admin_total
                rows=host.world.economy.turns(actor,period=turn)
                economy_trace[actor].append(dict(
                    consumption_step=value,consumption=consumption[actor],
                    gdp=sum(r.gdp.known for r in rows),military=sum(r.military.known for r in rows),
                    administration=admin_step,
                    cash=host.world.resources.balance(actor,'native:gold'),
                    land=len(host.world.territory.owned(actor)),
                    assets=rows[-1].net_assets.known if rows else 0))
                if len(host.world.territory.owned(actor))>initial[actor]['land']:
                    milestones[actor].setdefault('first_expansion',turn+1)
            if (turn+1)%10==0 or turn==turns-1:
                print(json.dumps(dict(seed=seed,swap=swap,candidate='bc' if model else candidate,turn=turn+1,seconds=round(time.monotonic()-start,1))),flush=True)
                history.append(dict(turn=turn+1,countries=snapshot(host,failures)))
    if source_hash()!=source:raise RuntimeError('Source changed during evaluation; discard this world')
    late_economy={}
    for actor,series in economy_trace.items():
        window=series[-50:];before=series[-51] if len(series)>50 else dict(land=initial[actor]['land'],assets=0)
        late_economy[actor]=dict(window=len(window),start_period=turns-len(window)+1,
            mean_consumption=sum(r['consumption_step']for r in window)/len(window),
            final_consumption=window[-1]['consumption'],
            mean_gdp=sum(r['gdp']for r in window)/len(window),
            military=sum(r['military']for r in window),administration=sum(r['administration']for r in window),
            mean_cash=sum(r['cash']for r in window)/len(window),final_cash=window[-1]['cash'],
            land_gained=window[-1]['land']-before['land'],asset_change=window[-1]['assets']-before['assets'],
            missing_inputs=production[actor]['missing-input']-late_production[actor]['missing-input'],
            energy_shortages=production[actor]['energy-shortage']-late_production[actor]['energy-shortage'],
            supply_shortfalls=supply[actor]-late_supply.get(actor,0))
    return dict(seed=seed,swap=swap,turns=turns,config=cfg,source=source,initial=initial,
        history=history,seconds=time.monotonic()-start,production=dict(production),supply_shortfalls=dict(supply),
        actions=dict(actions),milestones=dict(milestones),
        consumption=dict(consumption),tail_consumption={a:sum(v[-50:])/min(50,len(v)) for a,v in tail.items()},
        tail_window=50,late_economy=late_economy,
        net_production_at_initial_prices={actor:sum((f['outputs'][r]-f['inputs'][r])*p for r,p in prices.items()) for actor,f in flows.items()})


def main():
    p=argparse.ArgumentParser();p.add_argument('--seeds',type=int,nargs='+',required=True)
    p.add_argument('--turns',type=int,default=150);p.add_argument('--size',type=int,default=24)
    p.add_argument('--checkpoint',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();torch.set_num_threads(1)
    model,saved=load_policy(a.checkpoint) if a.checkpoint else (None,None)
    if saved and set(a.seeds)&set(saved.get('seen_seeds',saved['train_seeds']+saved['valid_seeds'])):
        raise ValueError('Rollout holdout seeds must be independent of train/validation')
    a.out.parent.mkdir(parents=True,exist_ok=True);results=[]
    for seed in a.seeds:
        for swap in (False,True):
            # Same focal seats/environment distribution for teacher and BC.
            results.append(run(seed,a.turns,size=a.size,swap=swap))
            if model:results.append(run(seed,a.turns,size=a.size,swap=swap,model=model))
            a.out.write_text(json.dumps(dict(checkpoint=str(a.checkpoint),results=results),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
