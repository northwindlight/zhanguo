"""Upload pure-script data, run ten fresh BC epochs, back up every two epochs."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import watch_land_gdp_ppo as shared
from prepare_script_bc import prepare
from mixed_bc_run import publish,digest

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'pure-script-bc';OUT=ROOT/'script-bc-kaggle-results'
KERNEL='northwindlight/zhanguo-pure-script-bc'
shared.OUT=OUT;shared.STAGE=STAGE;shared.KERNEL=KERNEL


def watch(stage):
    log=OUT/f'run-{stage:03}-live.log'
    with log.open('ab',buffering=0) as stream:
        follower=None
        try:
            while True:
                try:status=subprocess.run(['kaggle','kernels','status',KERNEL],capture_output=True,text=True,timeout=60)
                except (OSError,subprocess.TimeoutExpired):time.sleep(20);continue
                if status.returncode:time.sleep(20);continue
                if 'RUNNING' in status.stdout.upper() and (follower is None or follower.poll() is not None):
                    follower=subprocess.Popen(['kaggle','kernels','logs',KERNEL,'-f'],stdout=stream,stderr=subprocess.STDOUT)
                state=json.loads((STAGE/'state-data/resume-state.json').read_text())
                live=dict(state='bc',stage=stage,status=status.stdout.strip(),verified_epoch=state['epoch'],goal=10,time=time.time())
                production=False
                for line in log.read_text(errors='replace').splitlines():
                    try:row=json.loads(line)
                    except ValueError:continue
                    if row.get('stage')=='script_bc_production_begin':production=True
                    if production and row.get('stage') in ('bc_training','bc_epoch_complete'):live['progress']=row
                publish(OUT/'live.json',live)
                upper=status.stdout.upper()
                if 'COMPLETE' in upper:return
                if any(s in upper for s in ('ERROR','CANCELLED')):raise RuntimeError('Pure-script BC job failed')
                time.sleep(20)
        finally:
            if follower and follower.poll() is None:
                follower.terminate()
                try:follower.wait(timeout=10)
                except subprocess.TimeoutExpired:follower.kill();follower.wait()


def main():
    OUT.mkdir(exist_ok=True)
    while not (ROOT/'script-bc-results/done.json').exists():
        publish(OUT/'live.json',dict(state='waiting_for_script_corpus',time=time.time()));time.sleep(20)
    if not (STAGE/'prepared.json').exists():prepare()
    for folder,marker in (('code-data','code-uploaded.json'),('corpus-data','corpus-uploaded.json')):
        publish(OUT/'live.json',dict(state='uploading_'+folder,time=time.time()))
        shared.upload_data(STAGE/folder,OUT/marker)
    while True:
        state=json.loads((STAGE/'state-data/resume-state.json').read_text());epoch=state['epoch'];stage=epoch//2+1
        if epoch==10:return
        shared.upload_data(STAGE/'state-data',OUT/f'state-after-{epoch:02}.json',version=epoch!=0)
        marker=OUT/f'submitted-{stage:03}.json'
        if not marker.exists():
            kernel=STAGE/'kernel.py';text=kernel.read_text()
            text,n=re.subn(r'^EXPECTED_EPOCH = .* # SCRIPT_EXPECTED_EPOCH$',f'EXPECTED_EPOCH = {epoch} # SCRIPT_EXPECTED_EPOCH',text,flags=re.M)
            text,m=re.subn(r'^EXPECTED_SHA = .* # SCRIPT_EXPECTED_SHA$',f"EXPECTED_SHA = {state['sha256']!r} # SCRIPT_EXPECTED_SHA",text,flags=re.M)
            if n!=1 or m!=1:raise ValueError('Missing BC input identity guard')
            kernel.write_text(text)
            if shared.command(['kaggle','kernels','push','-p',STAGE,'--accelerator','NvidiaTeslaT4','-t','14400'],OUT/f'submit-{stage:03}.log'):
                raise RuntimeError('Cannot submit script BC')
            publish(marker,dict(epoch=epoch,sha256=state['sha256'],time=time.time()))
        watch(stage);destination=OUT/'segments'/f'{stage:03}';destination.mkdir(parents=True,exist_ok=True)
        if shared.command(['kaggle','kernels','output',KERNEL,'-p',destination,'-q','--file-pattern',r'^work/[^/]+\.(json|log|pt)$'],OUT/f'download-{stage:03}.log',timeout=1800):
            raise RuntimeError('Cannot download pure-script BC')
        work=destination/'work';new=json.loads((work/'state.json').read_text());segment=json.loads((work/'segment.json').read_text())
        if new['epoch']!=min(10,epoch+2) or segment['sha256']!=new['sha256'] or digest(work/new['latest'])!=new['sha256']:
            raise ValueError('Wrong or corrupt BC segment')
        publish(destination/'verified.json',dict(epoch=new['epoch'],sha256=new['sha256'],time=time.time()))
        source=STAGE/'state-data';target=source/'resume.pt';temporary=target.with_suffix('.partial')
        shutil.copyfile(work/new['latest'],temporary);temporary.replace(target);publish(source/'resume-state.json',dict(new,latest=target.name))
        if new['epoch']==10:
            if digest(work/'epoch-10-native.pt')!=segment['native_sha256']:raise ValueError('Bad final BC native model')
            publish(OUT/'verified.json',dict(epoch=10,sha256=new['sha256'],work=str(work),initialization='random',targets='pure_script_only',time=time.time()))
            publish(OUT/'next-stage-ready.json',dict(work=str(work),checkpoint=new['latest'],sha256=new['sha256'],
                reward='nominal_gdp+territory_holding',actor_observation_gdp='nominal_only',time=time.time()))
            publish(OUT/'live.json',dict(state='bc_completed',verified_epoch=10,next_stage='global_ppo',time=time.time()));return


if __name__=='__main__':
    try:main()
    except Exception as error:
        OUT.mkdir(exist_ok=True);publish(OUT/'failure.json',dict(error=type(error).__name__,message=str(error),time=time.time()))
        print('Script BC scheduler stopped:',type(error).__name__,str(error),flush=True)
