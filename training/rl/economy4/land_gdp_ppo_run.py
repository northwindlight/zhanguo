"""Finite four-seat FP32 PPO, real nominal GDP plus held-territory training GDP."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'cpp')]
from rewarding import reward_turns
from mixed_bc_run import publish,digest,reported_games


def run(label,args,log,env):
    print(json.dumps(dict(stage=label,state='starting',time=time.time())),flush=True)
    start=time.monotonic()
    with log.open('w') as f:
        subprocess.run([str(x) for x in args],stdout=f,stderr=subprocess.STDOUT,env=env,check=True)
    print(json.dumps(dict(stage=label,state='completed',seconds=time.monotonic()-start)),flush=True)


def ownership(logs,count,seed,turns):
    rows={};ends=[]
    for log in logs:
        text=log.read_text();ends.extend(reported_games(text.splitlines()))
        if '\ncompleted=' not in text:raise ValueError('Sampler did not complete')
        for line in text.splitlines():
            if not line.startswith('market '):continue
            f=dict(re.findall(r'(\w+)=(\S+)',line));key=f"{f['seed']}:{f['seat']}"
            r=rows.setdefault(key,dict(initial_land=int(f['initial_land']),land=[]))
            if int(f['turn'])!=len(r['land'])+1 or r['initial_land']!=int(f['initial_land']):
                raise ValueError('Missing or out-of-order ownership settlement')
            r['land'].append(int(f['land']))
    expected={f'{s}:{seat}' for s in range(seed,seed+count) for seat in range(4)}
    if rows.keys()!=expected or len(ends)!=count*4 or any(len(r['land'])!=turns for r in rows.values()):
        raise ValueError('Incomplete four-seat collection or reward ownership history')
    if len({(r['seed'],r['seat']) for r in ends})!=len(ends):raise ValueError('Duplicate sampled country')
    n=len(ends)
    stats=dict(games=count,seats=n,decisions=sum(int(r['decisions']) for r in ends),
        mean_fixed_gdp=sum(float(r['reward']) for r in ends)/(n*turns),
        mean_nominal_gdp=sum(float(r['current_gdp']) for r in ends)/(n*turns),
        mean_land=sum(int(r['land']) for r in ends)/n,
        land_distribution={str(k):sum(int(r['land'])==k for r in ends) for k in sorted({int(r['land']) for r in ends})},
        expanded_seats=sum(int(r['land'])>int(r['initial_land']) for r in ends),
        recruited_seats=sum(int(r['recruits'])>0 for r in ends),attacked_seats=sum(int(r['attacks'])>0 for r in ends),
        recruits=sum(int(r['recruits']) for r in ends),attacks=sum(int(r['attacks']) for r in ends),
        disbands=sum(int(r['disbands']) for r in ends),starved_units=sum(int(r['starved_units']) for r in ends),
        supply_short_turns=sum(int(r['supply_short_turns']) for r in ends),
        blackout_rate=sum(int(r['blackout']) for r in ends)/(n*turns))
    bonus=sum(sum(reward_turns([0.0]*turns,r['land'],r['initial_land'])[1]) for r in rows.values())/(n*turns)
    stats.update(mean_territory_gdp=bonus,mean_training_gdp=stats['mean_nominal_gdp']+bonus)
    return rows,stats


def sample(sampler,model,work,tracks,count,seed,config,env,label):
    import torch
    devices=min(torch.cuda.device_count(),count)
    parts=[];offset=0
    for gpu in range(devices):
        number=count//devices+(gpu<count%devices)
        output=work/f'{label}-gpu{gpu}.log'
        e=env.copy();e.update(CUDA_VISIBLE_DEVICES=str(gpu),OMP_NUM_THREADS=str(max(1,4//devices)))
        args=[sampler,'sample',model,'cuda',number,number,24,config['turns'],seed+offset,
              max(1,4//devices),'fp32']
        if tracks is not None:args.append(tracks)
        args.append('--market4-selfplay')
        parts.append((f'{label}-gpu{gpu}',args,output,e));offset+=number
    with ThreadPoolExecutor(max_workers=devices) as executor:
        futures=[executor.submit(run,*p) for p in parts]
        for f in futures:f.result()
    logs=[p[2] for p in parts]
    rows,stats=ownership(logs,count,seed,config['turns'])
    return rows,stats


def campaign(work,baseline,config,stage_games):
    import torch
    env=os.environ.copy();env.update(NVIDIA_TF32_OVERRIDE='0',ZHANGUO_REWARD_LAND_LOG='1',
                                     OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='1')
    for key in ('ZHANGUO_TEACHER_INDUSTRY','ZHANGUO_TEACHER_PRODUCTION_ONLY','ZHANGUO_SAMPLE_OPPS'):
        env.pop(key,None)
    state=json.loads((work/'state.json').read_text())
    if state['contract']!=config:raise ValueError('PPO reward or training contract changed during resume')
    current=work/state['latest']
    if digest(current)!=state['sha256']:raise ValueError('Resume checkpoint checksum mismatch')
    if state['games']>config['games'] or state['games']<0:raise ValueError('Invalid completed game count')
    sampler=work/'build/zhanguo_torch'
    run('build',[sys.executable,ROOT/'cpp/build_colab.py','--build',work/'build','--jobs',4],work/'build.log',env)
    target=min(config['games'],(state['games']//stage_games+1)*stage_games)
    native=work/'current-native.pt'
    if native.exists():native.unlink()
    while state['games']<target:
        if not native.exists():
            run('export',[sys.executable,ROOT/'cpp/export_policy.py',current,native,'--python-root',ROOT/'python'],work/'export.log',env)
        disk_limit=max(1,int((shutil.disk_usage('/tmp').free/2**30-3)//2.5))
        count=min(config['batch_games'],target-state['games'],disk_limit)
        seed=config['seed']+state['games'];batch=state['batches']+1
        publish(work/'progress.json',dict(state='sampling',games=state['games'],goal=config['games'],batch=batch,stage_target=target))
        # Raw 576-cell trajectories are large; keep a small batch in unsaved scratch space.
        with tempfile.TemporaryDirectory(prefix='land-gdp-roll-') as temp:
            tracks=Path(temp)
            held,stats=sample(sampler,native,work,tracks,count,seed,config,env,f'sample-{batch:04}')
            sidecar=tracks/'turn-land.json';publish(sidecar,held)
            candidate=work/f'ck-{batch:04}.pt'
            publish(work/'progress.json',dict(state='updating',games=state['games'],sampled_games=count,goal=config['games'],batch=batch))
            run('ppo-update',[sys.executable,ROOT/'cpp/ppo_update.py',current,tracks,candidate,
                '--python-root',ROOT/'python','--device','cuda','--lr',config['lr'],
                '--seed',seed,'--minibatch',config['minibatch'],'--subsample',.25,
                '--max-batches',max(1,config['max_minibatches']*count//config['batch_games']),
                '--clip',.2,'--gamma',config['gamma'],'--gae-lambda',config['gae_lambda'],'--target-kl','inf',
                '--reward-scale',config['reward_scale'],'--gdp-reward','nominal','--turn-land',sidecar,'--land-gdp-mode','holding',
                '--invalid-penalty',0,'--aux-weight',0,'--exec-weight',0,'--kl-ref',0],work/f'update-{batch:04}.log',env)
            update=json.loads((work/f'update-{batch:04}.log').read_text().splitlines()[-1])
            if update['episodes']!=count*4 or update['decisions']!=stats['decisions'] or update['objective']!='nominal_gdp+territory_holding':
                raise ValueError('PPO update does not match the sampled countries and reward contract')
            ck=torch.load(candidate,map_location='cpu',weights_only=True)
            if not all(torch.isfinite(v).all().item() for v in ck['model'].values()):raise ValueError('Nonfinite updated weights')
            del ck
            record=dict(batch=batch,seed=seed,**stats,update=update,checkpoint=candidate.name)
            publish(work/f'batch-{batch:04}.json',record)
            old=current
            state=dict(state,games=state['games']+count,batches=batch,latest=candidate.name,sha256=digest(candidate))
            publish(work/'state.json',state);current=candidate
            if old!=current:old.unlink(missing_ok=True)
            native.unlink(missing_ok=True)
            print(json.dumps(dict(record,stage='batch_complete',batch_games=count,games=state['games'],goal=config['games'])),flush=True)
    run('export-final',[sys.executable,ROOT/'cpp/export_policy.py',current,native,'--python-root',ROOT/'python'],work/'export.log',env)
    if state['games']==config['games']:
        publish(work/'progress.json',dict(state='final_evaluation',games=state['games'],goal=config['games']))
        # Each call already distributes distinct maps over available GPUs; use sequential policy comparisons.
        _,before=sample(sampler,baseline,work,None,config['eval_games'],config['eval_seed'],config,env,'eval-before')
        _,after=sample(sampler,native,work,None,config['eval_games'],config['eval_seed'],config,env,'eval-after')
        publish(work/'evaluation.json',dict(before=before,after=after,maps=config['eval_games'],seed=config['eval_seed']))
        publish(work/'done.json',dict(state='completed',games=state['games'],checkpoint=current.name,sha256=state['sha256'],
            native=native.name,native_sha256=digest(native),contract=config,evaluation=dict(before=before,after=after)))
    publish(work/'segment.json',dict(state='completed',games=state['games'],latest=state['latest'],sha256=state['sha256'],
        native=native.name,native_sha256=digest(native),finished=state['games']==config['games']))
    publish(work/'progress.json',dict(state='completed' if state['games']==config['games'] else 'segment_completed',games=state['games'],goal=config['games']))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--work',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--stage-games',type=int,default=200)
    a=p.parse_args();a.work.mkdir(exist_ok=True,parents=True)
    campaign(a.work,a.baseline,json.loads(a.config.read_text()),a.stage_games)
