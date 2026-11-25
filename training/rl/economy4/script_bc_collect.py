"""Collect pure-script economy-then-expansion demonstrations on CPU."""
import argparse
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import pickle
import random
import re
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile,ZIP_DEFLATED

import numpy as np

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'cpp'))
from read_trajectory import episodes
from mixed_bc_run import digest,publish
from expansion_bc_v2 import category

CONTRACT=dict(source='pure_script',economy_turns=[1,30],military_from=31,target_total_land=12,
              map_size=24,seats=4,action_budget=60,observation='budget60-power-v1',no_neural_targets=True,
              no_supply_purchases=True,allow_ore_import=True,allow_capacity_import=True,max_turns=100)
CAPS=dict(end=4,extraction=12,power=8,supply_factory=6,equipment_factory=6,
          supply_inputs=10,fuel=10,recruit=8,attack=20,materials=8,finance=8,setup=8,demobilize=8)


def chunk(sampler,output,offset,count,seed):
    archive=output/'chunks'/f'chunk-{offset:04}.zip'
    if archive.exists():
        with ZipFile(archive) as z:return json.loads(z.read('summary.json'))
    with tempfile.TemporaryDirectory(prefix='script-collect-',dir=output) as temporary:
        work=Path(temporary);tracks=work/'tracks';log=work/'rollout.log';started=time.monotonic()
        with log.open('w') as f:subprocess.run([str(sampler),str(count),str(seed),str(CONTRACT['max_turns']),str(tracks)],stdout=f,stderr=subprocess.STDOUT,check=True)
        outcomes={};turn_rows=defaultdict(list)
        for line in log.read_text().splitlines():
            fields=dict(re.findall(r'(\w+)=(\S+)',line))
            if line.startswith('script_game '):outcomes[int(fields['seed'])]=fields
            elif line.startswith('script_turn '):turn_rows[int(fields['seed'])].append(fields)
        if len(outcomes)!=count:raise ValueError('Missing pure-script worlds')
        summary=dict(games=count,seed=seed,accepted=0,teacher=0,economy=0,military=0,starved=0,shortage=0,
                     early_military=0,supply_buys=0,budget_hits=0,seconds=0,contract=CONTRACT)
        groups=Counter();seen=set()
        with ZipFile(archive.with_suffix('.partial'),'w',ZIP_DEFLATED,compresslevel=3) as z:
            for decisions,_,meta,features,_ in episodes(tracks,mixed_bc=True,mmap_arrays=True):
                game=meta['seed'];seen.add(game);row=outcomes[game];accepted=bool(int(row['reached']) and int(row['land'])>=12 and not int(row['starved']) and not int(row['shortage']))
                for key in ('starved','shortage','early_military','supply_buys','budget_hits'):summary[key]+=int(row[key])
                if int(row['early_military']) or int(row['supply_buys']):raise ValueError('Pure-script curriculum violation')
                if not all(d['teacher'] and d['obs']['cand_exec'][d['a']]>.5 for d in decisions):raise ValueError('Non-script or illegal target')
                buckets=defaultdict(list)
                for d in decisions:
                    turn=d['ti'];obs=d['obs'];kind=int(obs['cand_type'][d['a']]);phase='economy' if turn<30 else 'military'
                    if abs(float(obs['globals'][31])-.6)>.001 or obs['cand'][0,28]!=1:raise ValueError('Wrong observation contract')
                    if phase=='economy' and kind not in (0,1,2,4,5):raise ValueError('Military decision in economic prefix')
                    group=category(obs,d['a'],True);buckets[phase,group].append((obs,d['a'],turn,group))
                selected=[];rng=random.Random(game)
                if accepted:
                    for (phase,group),rows in sorted(buckets.items()):
                        selected.extend(rng.sample(rows,min(len(rows),CAPS[group])))
                    selected.sort(key=lambda r:r[2]);summary['accepted']+=1
                for obs,action,turn,group in selected:
                    summary['teacher']+=1;summary['economy' if turn<30 else 'military']+=1;groups[group]+=1
                item=dict(seed=game,features=np.stack(features),teacher=selected,anchor=[],contract=CONTRACT,
                          outcome=row,nominal_gdp=meta['nominal_gdp'],fixed_gdp=meta['turn_rewards'],
                          original_script_decisions=len(decisions),accepted=accepted)
                z.writestr(f'{game}.pkl',pickle.dumps(item,pickle.HIGHEST_PROTOCOL))
            if seen!=set(outcomes):raise ValueError('Missing demonstration country')
            summary.update(seconds=time.monotonic()-started,groups=dict(groups),
                mean_nominal_gdp=float(np.mean([float(r['nominal_gdp']) for r in outcomes.values()])),
                mean_turn30_gdp=float(np.mean([float(r['gdp']) for rows in turn_rows.values() for r in rows if int(r['turn'])==30])))
            z.writestr('summary.json',json.dumps(summary));z.write(log,'rollout.log')
        archive.with_suffix('.partial').replace(archive)
    return summary


def collect(args):
    output=args.output.resolve();(output/'chunks').mkdir(parents=True,exist_ok=True)
    sampler=args.sampler.resolve();publish(output/'contract.json',dict(CONTRACT,games=args.games,seed=args.seed,sampler_sha256=digest(sampler)))
    total=Counter();inventory={}
    jobs=[(offset,min(args.chunk,args.games-offset)) for offset in range(0,args.games,args.chunk)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending={pool.submit(chunk,sampler,output,offset,count,args.seed+offset):(offset,count) for offset,count in jobs}
        for future in as_completed(pending):
            offset,count=pending[future];row=future.result()
            for key in ('games','accepted','teacher','economy','military','starved','shortage','early_military','supply_buys','budget_hits'):total[key]+=row[key]
            path=output/'chunks'/f'chunk-{offset:04}.zip';inventory[path.name]=dict(sha256=digest(path),games=count,accepted=row['accepted'])
            publish(output/'inventory.json',inventory);publish(output/'progress.json',dict(state='collecting',goal=args.games,**total))
            print(json.dumps(dict(stage='chunk_complete',offset=offset,**row,cumulative=dict(total))),flush=True)
    if total['accepted']<args.games*.8:raise ValueError('Pure-script twelve-tile success below 80%; do not start BC')
    done=dict(state='collected',contract=CONTRACT,goal=args.games,**total,archives=inventory)
    publish(output/'done.json',done);publish(output/'progress.json',done)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--sampler',type=Path,required=True)
    p.add_argument('--games',type=int,default=1000);p.add_argument('--seed',type=int,default=550100)
    p.add_argument('--chunk',type=int,default=8);p.add_argument('--workers',type=int,default=4)
    collect(p.parse_args())
