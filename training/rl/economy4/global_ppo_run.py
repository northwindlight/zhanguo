"""Independent A/B PPO using global event returns and joint action fragments."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

import ab_ppo_run as base
from mixed_bc_run import publish,digest

ROOT=Path(__file__).resolve().parent
original_sample=base.sample


def sample(*args,**kwargs):
    result=original_sample(*args,**kwargs)
    # Preserve replayable global actions after the bulky observation tracks are
    # consumed. These logs contain rewards, never additional actor input fields.
    work=args[3];tracks=args[4];label=args[9]
    if tracks is not None:
        events={p.stem:[json.loads(line) for line in p.read_text().splitlines()] for p in (tracks/'events').glob('*.jsonl')}
        if len(events)!=args[5]:raise ValueError('Missing shared-world action timeline')
        publish(work/('events-'+label+'.json'),events)
    return result


def update_pair(current,candidates,devices,tracks,work,stats,config,env,label,seed,limit=None):
    if config['gamma']!=1 or config['gae_lambda']!=1:raise ValueError('Global event reference currently requires gamma=lambda=1')
    if limit is None:
        best=work/'best-sampled-pair.json';old=json.loads(best.read_text()) if best.exists() else None
        if old is None or stats['mean_nominal_gdp']>old['mean_nominal_gdp']:
            models={}
            for identity,path in current.items():
                target=work/f'best-sampled-{identity}.pt';shutil.copyfile(path,target);models[identity]=dict(path=target.name,sha256=digest(target))
            publish(best,dict(mean_nominal_gdp=stats['mean_nominal_gdp'],models=models,
                note='Best observed sampling batch in this segment; not a claim of stable superiority.'))
    parts=[]
    for index,identity in enumerate(('A','B')):
        log=work/f'{label}-{identity}.log'
        args=[sys.executable,ROOT/'cpp/global_ppo_update.py',current[identity],tracks/identity,candidates[identity],
            '--events',tracks/'events','--python-root',base.PYTHON,'--device',devices[index],'--lr',config['lr'],
            '--seed',seed+index,'--minibatch',config['minibatch'],'--max-batches',limit or config['max_minibatches'],
            '--target-kl',config['target_kl'],'--critic-batches',config['critic_batches'],'--reward-scale',config['reward_scale']]
        parts.append((f'{label}-{identity}',args,log,dict(env,OMP_NUM_THREADS='2')))
    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(base.run,*part) for part in parts];durations=[f.result() for f in futures]
    result={}
    for identity,part,seconds in zip(('A','B'),parts,durations):
        row=json.loads(part[2].read_text().splitlines()[-1]);expected=stats['by_policy'][identity]
        if row['episodes']!=expected['seats'] or row['decisions']!=expected['decisions'] or row['clock']!='global_events':
            raise ValueError('Global learner policy/clock mismatch')
        base.verify_checkpoint(candidates[identity]);result[identity]=dict(row,seconds=seconds,device=devices[('A','B').index(identity)])
    return result,time.monotonic()-started


def main(args):
    base.sample=sample;base.update_pair=update_pair
    config=json.loads(args.config.read_text())
    base.campaign(args.work,config,('cpu','cpu') if args.cpu else ('cuda:0','cuda:1'),args.sampler,not args.no_preflight,args.baseline)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--work',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--sampler',type=Path);p.add_argument('--cpu',action='store_true');p.add_argument('--no-preflight',action='store_true')
    main(p.parse_args())
