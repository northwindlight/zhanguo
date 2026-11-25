"""Migrate at the verified boundary, then automatically finish finite A/B PPO."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from zipfile import ZipFile,ZIP_DEFLATED

import watch_land_gdp_ppo as shared
from prepare_ab import prepare,source_digest

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'ab-dynamic-ppo';OUT=ROOT/'ab-dynamic-ppo-results'
KERNEL='northwindlight/zhanguo-dynamic-ab-ppo'
shared.STAGE=STAGE;shared.OUT=OUT;shared.KERNEL=KERNEL
publish=shared.publish;sha=shared.sha


def pull(stage):
    destination=OUT/'segments'/f'{stage:03}';destination.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        if shared.command(['kaggle','kernels','output',KERNEL,'-p',destination,'-q','--file-pattern',r'^work/[^/]+\.(json|log|pt)$'],
                          OUT/f'download-{stage:03}.log',timeout=1200)==0:break
        if attempt==2:raise RuntimeError('Cannot download A/B results')
        time.sleep(20)
    work=destination/'work';state=json.loads((work/'state.json').read_text());segment=json.loads((work/'segment.json').read_text())
    if segment['models']!=state['models'] or segment['games']!=state['games']:raise ValueError('Incomplete checkpoint pair transaction')
    for identity,row in state['models'].items():
        if sha(work/row['latest'])!=row['sha256']:raise ValueError('A/B model checksum mismatch')
        native=segment['natives'][identity]
        if sha(work/native['path'])!=native['sha256']:raise ValueError('A/B native checksum mismatch')
    publish(destination/'verified.json',dict(games=state['games'],models=state['models'],time=time.time()))
    return work,state,segment


def submit(stage,state):
    marker=OUT/f'submitted-{stage:03}.json'
    if marker.exists():return
    intent=OUT/f'submit-intent-{stage:03}.json'
    status=subprocess.run(['kaggle','kernels','status',KERNEL],capture_output=True,text=True,timeout=60)
    active=intent.exists() and status.returncode==0 and any(s in status.stdout.upper() for s in ('RUNNING','QUEUED'))
    if not active:
        script=STAGE/'kernel.py';text=script.read_text();pair={i:r['sha256'] for i,r in state['models'].items()}
        text,n=re.subn(r'^EXPECTED_GAMES = .* # AB_EXPECTED_GAMES$',f"EXPECTED_GAMES = {state['games']} # AB_EXPECTED_GAMES",text,flags=re.M)
        text,m=re.subn(r'^EXPECTED_PAIR = .* # AB_EXPECTED_PAIR$',f'EXPECTED_PAIR = {pair!r} # AB_EXPECTED_PAIR',text,flags=re.M)
        if n!=1 or m!=1:raise ValueError('Missing A/B input identity guard')
        script.write_text(text);publish(intent,dict(games=state['games'],models=state['models'],time=time.time()))
        timeout=state['contract'].get('session_timeout_seconds',39600)
        if shared.command(['kaggle','kernels','push','-p',STAGE,'--accelerator','NvidiaTeslaT4','-t',timeout],OUT/f'submit-{stage:03}.log'):
            raise RuntimeError('Cannot submit finite A/B segment')
    publish(marker,dict(games_before=state['games'],models=state['models'],time=time.time()))
    print('Queued dynamic A/B segment',stage,'after',state['games'],'verified worlds',flush=True)


def finish(work,state):
    done=json.loads((work/'done.json').read_text());goal=state['contract']['games']
    if done['games']!=goal or done['models']!=state['models']:raise ValueError('A/B final goal or model mismatch')
    publish(OUT/'done.json',done);shared.record_health()
    package=OUT/f'zhanguo-dynamic-ab-{goal}-delivery.zip'
    with ZipFile(package.with_suffix('.partial'),'w',ZIP_DEFLATED,compresslevel=2) as archive:
        for path in work.iterdir():
            if path.is_file():archive.write(path,'results/'+path.name)
        for path in OUT.glob('segments/*/work/*'):
            if path.is_file() and path.suffix in ('.log','.json'):archive.write(path,'history/'+str(path.relative_to(OUT)))
        for name in ('README.md','run-config.json','migration.json','kernel.py'):
            if (STAGE/name).exists():archive.write(STAGE/name,'continuation/'+name)
        for name in ('ab_ppo_run.py','watch_ab_ppo.py','prepare_ab.py'):archive.write(ROOT/name,'continuation/'+name)
        for name in ('global_ppo_run.py','watch_global_ppo.py','prepare_global_ppo.py'):
            if (ROOT/name).exists():archive.write(ROOT/name,'continuation/'+name)
        bundle=STAGE/'code-data/ab-code.bundle'
        if bundle.exists():archive.write(bundle,'continuation/ab-code.bundle')
    package.with_suffix('.partial').replace(package)
    publish(OUT/'verified.json',dict(verified=True,games=goal,models=state['models'],package=package.name,time=time.time()))
    publish(OUT/'live.json',dict(state='completed',games=goal,time=time.time()))
    print('Dynamic A/B training, evaluation and local verification completed:',goal,flush=True)


def main():
    OUT.mkdir(exist_ok=True)
    if (ROOT/'training-user-stop.json').exists():
        print('A/B migration remains stopped at the user request',flush=True);return
    if (OUT/'verified.json').exists():return
    restart=STAGE/'restart-ready.json'
    boundary=restart if restart.exists() else ROOT/'land-gdp-ppo-results/switch-ready.json'
    while not boundary.exists() or not (STAGE/'validated-code.json').exists():
        publish(OUT/'live.json',dict(stage='waiting_for_verified_boundary_and_code',parent_boundary_ready=boundary.exists(),
                                    implementation_validated=(STAGE/'validated-code.json').exists(),time=time.time()))
        time.sleep(30)
    validated=json.loads((STAGE/'validated-code.json').read_text())
    if source_digest()!=validated['source_sha256']:raise ValueError('A/B implementation changed after validation')
    if not (STAGE/'migration.json').exists():prepare(boundary)
    migration=json.loads((STAGE/'migration.json').read_text())
    if sha(STAGE/'code-data/ab-code.bundle')!=migration['code_sha256']:raise ValueError('A/B code bundle changed after migration')
    shared.upload_data(STAGE/'code-data',OUT/'code-uploaded.json')
    while True:
        state=json.loads((STAGE/'state-data/resume-state.json').read_text());games=state['games']
        marker=OUT/f'state-after-{games:04}.json'
        shared.upload_data(STAGE/'state-data',marker,version=games!=migration['parent_games'])
        stage=games//state['contract']['segment_games']+1
        submit(stage,state);shared.watch_run(stage)
        work,new,segment=pull(stage)
        if new['games']!=min(state['contract']['games'],stage*state['contract']['segment_games']) or new['contract']!=state['contract']:
            raise ValueError('Wrong A/B segment count/contract')
        if segment['finished']:finish(work,new);return
        source=STAGE/'state-data';models={}
        for identity,row in new['models'].items():
            target=source/f'{identity}-resume.pt';temp=target.with_suffix('.partial')
            shutil.copyfile(work/row['latest'],temp);temp.replace(target)
            models[identity]=dict(latest=target.name,sha256=row['sha256'])
        publish(source/'resume-state.json',dict(new,models=models))


if __name__=='__main__':
    try:main()
    except Exception as error:
        OUT.mkdir(exist_ok=True);publish(OUT/'failure.json',dict(error=type(error).__name__,message=str(error),time=time.time()))
        print('A/B scheduler stopped:',type(error).__name__,str(error),flush=True)
