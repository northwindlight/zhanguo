"""User-specified industrial BC: old economy expert, scripted military, stop at 12 tiles."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

import numpy as np

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'cpp')]
from mixed_bc_run import BASE_SHA,publish,digest,run,reported_games
from expansion_v2_run import pack,snapshot,export


def evaluate(sampler,weights,prefix,maps,seed,env):
    path=prefix.with_suffix('.json')
    if path.exists():return json.loads(path.read_text())
    log=prefix.with_suffix('.log')
    evaluation_env=env.copy()
    for key in ('ZHANGUO_TEACHER_INDUSTRY','ZHANGUO_TEACHER_ALLOW_ORE_IMPORT'):
        evaluation_env.pop(key,None)
    threads=int(env.get('ZHANGUO_ENV_THREADS','8'))
    run(prefix.name,[sampler,'sample',weights,'cuda',maps,maps,24,100,seed,threads,'fp32','--market4-selfplay'],log,evaluation_env)
    rows=reported_games(log.read_text().splitlines())
    if len(rows)!=maps*4 or len({(r['seed'],r['seat']) for r in rows})!=maps*4:
        raise ValueError('Incomplete independent 100-turn evaluation')
    terminal=[];late=[]
    for line in log.read_text().splitlines():
        if not line.startswith('market '):continue
        fields=dict(item.split('=',1) for item in line.split() if '=' in item)
        if int(fields['turn'])>=91:late.append(float(fields['current']))
        if int(fields['turn'])==100:terminal.append(float(fields['current']))
    n=len(rows)
    if len(late)!=10*n or len(terminal)!=n:raise ValueError('Missing terminal nominal GDP')
    result=dict(maps=maps,seats=n,turns=100,land=dict(Counter(int(r['land']) for r in rows)),
        mean_land=sum(int(r['land']) for r in rows)/n,
        retained_two_rate=sum(int(r['land'])>=7 for r in rows)/n,
        twelve_tile_rate=sum(int(r['land'])>=12 for r in rows)/n,
        gdp=sum(float(r['reward']) for r in rows)/(100*n),
        nominal_gdp=sum(float(r['current_gdp']) for r in rows)/(100*n),
        terminal_nominal_gdp=float(np.mean(terminal)),last10_nominal_gdp=float(np.mean(late)),
        recruit_rate=sum(int(r['recruits'])>0 for r in rows)/n,
        attack_rate=sum(int(r['attacks'])>0 for r in rows)/n,
        starved_units=sum(int(r['starved_units']) for r in rows),
        starved_rate=sum(int(r['starved_units'])>0 for r in rows)/n,
        supply_short_turns=sum(int(r['supply_short_turns']) for r in rows))
    publish(path,result)
    return result


def campaign(args):
    work=args.output.resolve();old=args.existing.resolve()
    work.mkdir(parents=True,exist_ok=True);(work/'chunks').mkdir(exist_ok=True)
    base,native=old/'base.pt',old/'base-native.pt'
    if digest(base)!=BASE_SHA:raise ValueError('Must restart from original ~40 GDP economic checkpoint')
    env=os.environ.copy();env['NVIDIA_TF32_OVERRIDE']='0';env['ZHANGUO_TEACHER_INDUSTRY']='1'
    env['ZHANGUO_TEACHER_ALLOW_ORE_IMPORT']='1'
    for k in ('TORCH_ALLOW_TF32_CUBLAS_OVERRIDE','ZHANGUO_SAMPLE_PROFILE','ZHANGUO_SAMPLE_BUCKET_MIN',
              'ZHANGUO_TEACHER_PRODUCTION_ONLY'):env.pop(k,None)
    sampler=work/'build/zhanguo_torch'
    source_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'cpp').iterdir())
                                       if p.suffix in ('.cpp','.hpp') or p.name=='CMakeLists.txt')).hexdigest()
    stamp=work/'sampler-source.sha256'
    if not sampler.exists() or not stamp.exists() or stamp.read_text().strip()!=source_hash:
        run('sampler build',[sys.executable,ROOT/'cpp/build_colab.py','--build',work/'build','--jobs',4],work/'build.log',env)
        stamp.write_text(source_hash+'\n')
    cumulative=Counter();files=[];offset=0;seed=293000
    while offset<args.games:
        count=min(8 if offset==0 else 64,args.games-offset)
        archive=work/'chunks'/f'chunk-{offset:04}.zip'
        publish(work/'progress.json',dict(state='collecting',epoch=0,games=offset,goal=args.games,chunks=files,**{
            k:cumulative[k] for k in ('clean','expanded','teacher','anchor','teacher_short_turns','teacher_deaths','supply_purchases')}))
        if archive.exists():
            with ZipFile(archive) as z:summary=json.loads(z.read('summary.json'))
            if summary['games']!=count or summary['seed']!=seed:raise ValueError('Recovery curriculum does not match schedule')
        else:
            with tempfile.TemporaryDirectory(dir=work,prefix='collect-') as temp:
                directory=Path(temp);log=directory/'sample.log'
                threads=int(env.get('ZHANGUO_ENV_THREADS','8'))
                run(f'industry games {offset+1}-{offset+count}',[sampler,'sample',native,'cuda',count,count,24,100,seed,threads,
                    'fp32',directory,'--market4-selfplay','--mixed-bc',30,100],log,env)
                summary=pack(directory,log,archive,seed,count,production_only=True,industry=True)
        for key in ('games','expanded','clean','teacher','anchor','starved_units','teacher_short_turns','teacher_deaths','supply_purchases'):
            cumulative[key]+=summary.get(key,0)
        files.append(archive.name);offset+=count;seed=summary['last_seed']+1
        snapshot(work)
        print(json.dumps(dict(stage='collected',offset=offset,**summary)),flush=True)
        if offset==8 and summary['clean']<4:
            publish(work/'needs-review.json',dict(reason='Industrial tutor pilot clean success below half',summary=summary))
            publish(work/'progress.json',dict(state='needs_review',epoch=0,games=offset,goal=args.games,chunks=files))
            return
    if cumulative['supply_purchases'] or cumulative['clean']<args.games*.5:
        raise ValueError('Industrial demonstrations failed corpus quality checks')
    baseline=evaluate(sampler,native,work/'baseline',12,296000,env)
    current=base;best=None
    if (work/'best.json').exists():best=json.loads((work/'best.json').read_text())
    saved=[int(p.stem.split('-')[1]) for p in work.glob('epoch-[0-9][0-9].pt')]
    for epoch in range(max(saved,default=1),args.epochs+1):
        checkpoint=work/f'epoch-{epoch:02}.pt';exported=work/f'epoch-{epoch:02}-native.pt'
        publish(work/'progress.json',dict(state='training',epoch=epoch,max_epochs=args.epochs,games=offset,goal=args.games,chunks=files))
        if not checkpoint.exists():
            run(f'industry BC epoch {epoch}',[sys.executable,ROOT/'expansion_bc_v2.py','--base',base,'--initial',current,
                '--output',checkpoint,'--data',work/'chunks','--anchor-data',old/'chunks','--production-only',
                '--python-root',args.python_root,'--epoch',epoch],work/f'epoch-{epoch:02}-train.log',env)
        current=checkpoint;snapshot(work)
        if epoch%2:continue
        export(checkpoint,exported,args.python_root,env);snapshot(work)
        publish(work/'progress.json',dict(state='evaluating',epoch=epoch,games=offset,goal=args.games,chunks=files))
        result=evaluate(sampler,exported,work/f'epoch-{epoch:02}-eval',12,296000,env)
        score=result['mean_land']-5*result['starved_rate']+.1*result['last10_nominal_gdp']
        if best is None or score>best['score']:
            best=dict(epoch=epoch,checkpoint=checkpoint.name,native=exported.name,result=result,score=score)
            publish(work/'best.json',best)
        print(json.dumps(dict(stage='unaided',epoch=epoch,**result)),flush=True)
    if best is None:raise RuntimeError('No independent evaluation completed')
    publish(work/'progress.json',dict(state='final_evaluation',epoch=best['epoch'],games=offset,goal=args.games,chunks=files))
    final_base=evaluate(sampler,native,work/'final-base',24,297000,env)
    final=evaluate(sampler,work/best['native'],work/'final-model',24,297000,env)
    accepted=final['mean_land']>=10 and final['starved_rate']<=.05 and final['last10_nominal_gdp']>=.8*final_base['last10_nominal_gdp']
    snapshot(work)
    done=dict(state='completed',accepted=accepted,checkpoint=best['checkpoint'],native=best['native'],
        sha256=digest(work/best['checkpoint']),native_sha256=digest(work/best['native']),baseline=final_base,evaluation=final,
        curriculum=dict(first=30,target_total_land=12,allow_ore_import=True,no_supply_purchases=True,hybrid=True),
        collected=dict(cumulative),chunks=files,recovery_chunks=files,selected=best)
    publish(work/'done.json',done)
    publish(work/'progress.json',dict(state='completed',accepted=accepted,epoch=best['epoch'],games=offset,goal=args.games,chunks=files))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True);p.add_argument('--existing',type=Path,required=True)
    p.add_argument('--python-root',type=Path,required=True);p.add_argument('--games',type=int,default=1000)
    p.add_argument('--epochs',type=int,default=4)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not (args.output/'done.json').exists():campaign(args)
