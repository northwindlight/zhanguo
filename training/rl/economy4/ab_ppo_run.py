"""Shared-world dynamic A/B self-play, two independent FP32 PPO learners."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent
PYTHON=Path(os.environ.get('ZHANGUO_PYTHON_ROOT',str(ROOT/'python')))
sys.path[:0]=[str(ROOT),str(ROOT/'cpp')]
from mixed_bc_run import publish,digest,reported_games
from land_gdp_ppo_run import ownership
from rewarding import reward_turns


def owner(seed,seat):
    pairs=((0,1),(0,2),(0,3),(1,2),(1,3),(2,3))
    return 'A' if seat in pairs[seed%6] else 'B'


def run(label,args,log,env):
    started=time.monotonic();print(json.dumps(dict(stage=label,state='starting',time=time.time())),flush=True)
    with log.open('w') as stream:subprocess.run([str(x) for x in args],stdout=stream,stderr=subprocess.STDOUT,env=env,check=True)
    seconds=time.monotonic()-started
    print(json.dumps(dict(stage=label,state='completed',seconds=seconds)),flush=True)
    return seconds


def verify_checkpoint(path):
    import torch
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if not all(torch.isfinite(v).all().item() for v in ck['model'].values()):raise ValueError('Nonfinite '+str(path))
    for row in ck.get('optimizer',{}).get('state',{}).values():
        if any(isinstance(v,torch.Tensor) and not torch.isfinite(v).all().item() for v in row.values()):
            raise ValueError('Nonfinite optimizer '+str(path))
    return ck['cfg']


def sample(sampler,natives,devices,work,tracks,count,seed,config,env,label,turns=None):
    horizon=turns or config['turns'];log=work/(label+'.log')
    seconds=run(label,[sampler,natives['A'],natives['B'],*devices,count,count,config['map_size'],horizon,seed,4,
                       tracks if tracks is not None else '-'],log,env)
    held,stats=ownership([log],count,seed,horizon)
    rows=reported_games(log.read_text().splitlines());per_policy={}
    for identity in ('A','B'):
        selected=[r for r in rows if owner(int(r['seed']),int(r['seat']))==identity]
        if len(selected)!=count*2 or any(r['policy']!=identity for r in selected):raise ValueError('Wrong policy/seat routing')
        seat_keys={f"{r['seed']}:{r['seat']}" for r in selected}
        if tracks is not None:
            files=list((tracks/identity).glob('*.zgrl'))
            actual={f.stem.replace('-',':') for f in files}
            if actual!=seat_keys:raise ValueError('Missing/foreign country trajectory for '+identity)
            publish(tracks/identity/'turn-land.json',{k:held[k] for k in seat_keys})
        n=len(selected)
        per_policy[identity]=dict(seats=n,decisions=sum(int(r['decisions']) for r in selected),
            mean_nominal_gdp=sum(float(r['current_gdp']) for r in selected)/(n*horizon),
            mean_fixed_gdp=sum(float(r['reward']) for r in selected)/(n*horizon),
            mean_land=sum(int(r['land']) for r in selected)/n,
            recruited_seats=sum(int(r['recruits'])>0 for r in selected),attacked_seats=sum(int(r['attacks'])>0 for r in selected),
            expanded_seats=sum(int(r['land'])>int(r['initial_land']) for r in selected),
            mean_territory_gdp=sum(sum(reward_turns([0.]*horizon,held[k]['land'],held[k]['initial_land'])[1]) for k in seat_keys)/(n*horizon))
    stats.update(by_policy=per_policy,sample_seconds=seconds,sample_decisions_per_second=stats['decisions']/seconds)
    return stats


def update_pair(current,candidates,devices,tracks,work,stats,config,env,label,seed,limit=None):
    parts=[]
    for index,identity in enumerate(('A','B')):
        log=work/f'{label}-{identity}.log';e=dict(env,OMP_NUM_THREADS='2')
        args=[sys.executable,ROOT/'cpp/ppo_update.py',current[identity],tracks/identity,candidates[identity],
            '--python-root',PYTHON,'--device',devices[index],'--lr',config['lr'],'--seed',seed+index,
            '--minibatch',config['minibatch'],'--subsample',.25,'--max-batches',limit or config['max_minibatches'],
            '--clip',.2,'--gamma',config['gamma'],'--gae-lambda',config['gae_lambda'],'--target-kl','inf',
            '--reward-scale',config['reward_scale'],'--gdp-reward','nominal','--turn-land',tracks/identity/'turn-land.json',
            '--land-gdp-mode','holding','--invalid-penalty',0,'--aux-weight',0,'--exec-weight',0,'--kl-ref',0]
        parts.append((f'{label}-{identity}',args,log,e))
    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,*part) for part in parts]
        seconds=[future.result() for future in futures]
    result={}
    for identity,part,duration in zip(('A','B'),parts,seconds):
        row=json.loads(part[2].read_text().splitlines()[-1]);expected=stats['by_policy'][identity]
        if row['episodes']!=expected['seats'] or row['decisions']!=expected['decisions'] or row['objective']!='nominal_gdp+territory_holding':
            raise ValueError('Independent PPO update crossed policy boundaries')
        verify_checkpoint(candidates[identity]);result[identity]=dict(row,seconds=duration,device=devices[('A','B').index(identity)])
    return result,time.monotonic()-started


def export_pair(current,natives,work,env):
    for identity in ('A','B'):
        natives[identity].unlink(missing_ok=True)
        run('export-'+identity,[sys.executable,ROOT/'cpp/export_policy.py',current[identity],natives[identity],'--python-root',PYTHON],work/f'export-{identity}.log',env)


def campaign(work,config,devices=('cuda:0','cuda:1'),sampler=None,preflight=True,baseline=None):
    env=dict(os.environ,NVIDIA_TF32_OVERRIDE='0',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='4')
    for key in list(env):
        if key.startswith('ZHANGUO_TEACHER_'):env.pop(key)
    state=json.loads((work/'state.json').read_text())
    if state['contract']!=config:raise ValueError('A/B campaign contract changed')
    current={identity:work/state['models'][identity]['latest'] for identity in ('A','B')}
    for identity,path in current.items():
        if digest(path)!=state['models'][identity]['sha256']:raise ValueError('Wrong A/B input checkpoint')
    if sampler is None:
        run('build',[sys.executable,ROOT/'cpp/build_colab.py','--build',work/'build','--jobs',4,'--target','zhanguo_dual'],work/'build.log',env)
        sampler=work/'build/zhanguo_dual'
    natives={i:work/f'{i}-native.pt' for i in ('A','B')};export_pair(current,natives,work,env)
    if preflight:
        with tempfile.TemporaryDirectory(prefix='ab-preflight-') as temporary:
            tracks=Path(temporary)
            stats=sample(sampler,natives,devices,work,tracks,2,config['preflight_seed'],config,env,'preflight-sample',turns=3)
            candidates={i:tracks/f'{i}-test.pt' for i in ('A','B')}
            update,elapsed=update_pair(current,candidates,devices,tracks,work,stats,config,env,'preflight-update',config['preflight_seed'],limit=2)
            publish(work/'preflight.json',dict(passed=True,devices=devices,sample=stats,updates=update,update_wall_seconds=elapsed,
                production_weights_unchanged={i:digest(current[i])==state['models'][i]['sha256'] for i in ('A','B')}))
    target=min(config['games'],(state['games']//config['segment_games']+1)*config['segment_games'])
    while state['games']<target:
        count=min(config['batch_games'],target-state['games']);batch=state['batches']+1;seed=config['seed']+state['games']
        publish(work/'progress.json',dict(stage='sampling',games=state['games'],goal=config['games'],target=target,batch=batch))
        with tempfile.TemporaryDirectory(prefix='ab-rollout-') as temporary:
            tracks=Path(temporary)
            stats=sample(sampler,natives,devices,work,tracks,count,seed,config,env,f'sample-{batch:04}')
            candidates={i:work/f'ck-{batch:04}-{i}.pt' for i in ('A','B')}
            update,elapsed=update_pair(current,candidates,devices,tracks,work,stats,config,env,f'update-{batch:04}',seed)
            old=current
            # Commit the pair only after both complete, validate and retain optimizer state.
            models={i:dict(latest=candidates[i].name,sha256=digest(candidates[i])) for i in ('A','B')}
            state=dict(state,games=state['games']+count,batches=batch,models=models)
            record=dict(stats,batch=batch,seed=seed,batch_games=count,games=state['games'],goal=config['games'],updates=update,update_wall_seconds=elapsed)
            publish(work/f'batch-{batch:04}.json',record);publish(work/'state.json',state);current=candidates
            for identity in ('A','B'):old[identity].unlink(missing_ok=True)
            print(json.dumps(dict(record,stage='batch_complete')),flush=True)
        export_pair(current,natives,work,env)
    finished=state['games']==config['games']
    if finished:
        if baseline is None:raise ValueError('Missing independent evaluation baseline')
        before=sample(sampler,dict(A=baseline,B=baseline),devices,work,None,config['eval_games'],config['eval_seed'],config,env,'eval-before')
        after=sample(sampler,natives,devices,work,None,config['eval_games'],config['eval_seed'],config,env,'eval-after')
        publish(work/'evaluation.json',dict(before=before,after=after,maps=config['eval_games'],seed=config['eval_seed']))
    segment=dict(games=state['games'],models=state['models'],finished=finished,
                 natives={i:dict(path=natives[i].name,sha256=digest(natives[i])) for i in ('A','B')},contract=config)
    publish(work/'segment.json',segment)
    if finished:publish(work/'done.json',dict(segment,evaluation=json.loads((work/'evaluation.json').read_text())))
    publish(work/'progress.json',dict(stage='completed' if finished else 'segment_completed',games=state['games'],goal=config['games']))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--work',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--baseline',type=Path);p.add_argument('--sampler',type=Path);p.add_argument('--cpu',action='store_true');p.add_argument('--no-preflight',action='store_true')
    a=p.parse_args();campaign(a.work,json.loads(a.config.read_text()),('cpu','cpu') if a.cpu else ('cuda:0','cuda:1'),a.sampler,not a.no_preflight,a.baseline)
