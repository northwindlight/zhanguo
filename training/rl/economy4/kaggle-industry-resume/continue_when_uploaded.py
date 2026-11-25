"""Finish submission after the existing corpus upload, then start durable monitoring."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
STAGE=Path(__file__).resolve().parent
OUT=ROOT/'kaggle-industry-results'
os.environ['PATH']=str(Path.home()/'.local/bin')+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')
EVALUATION_ONLY='--evaluation-only' in sys.argv
DATASET='northwindlight/zhanguo-industry-bc-eval-handoff-20261123' if EVALUATION_ONLY else 'northwindlight/zhanguo-industry-bc-handoff-20261123'
TAIL='northwindlight/zhanguo-industry-bc-checkpoint-20261123'


def call(*args,timeout=120):
    return subprocess.run([str(a) for a in args],capture_output=True,text=True,timeout=timeout,cwd=ROOT)


def ready(ref):
    r=call('kaggle','datasets','status',ref,'--format','json',timeout=60)
    if r.returncode:return False
    try:return str(json.loads(r.stdout)['status']).lower()=='ready'
    except (ValueError,KeyError):return r.stdout.strip().lower()=='ready'


def main():
    while not ready(DATASET):
        time.sleep(30)
    print('Private corpus uploaded and ready',flush=True)
    candidates=sorted((ROOT/'industry-bc-results').glob('epoch-[0-9][0-9].pt'))
    metadata=STAGE/'kernel/kernel-metadata.json'
    m=json.loads(metadata.read_text())
    if EVALUATION_ONLY:
        m['dataset_sources']=[DATASET]
        metadata.write_text(json.dumps(m,indent=2))
    elif candidates and int(candidates[-1].stem.split('-')[1])>=4:
        r=call(sys.executable,STAGE/'prepare.py','--checkpoint-only',timeout=180)
        if r.returncode:raise RuntimeError('Cannot prepare the latest verified checkpoint: '+r.stdout+r.stderr)
        print(r.stdout.strip(),flush=True)
        if not ready(TAIL) and not (OUT/'checkpoint-uploaded.json').exists():
            r=call('kaggle','datasets','create','-p',STAGE/'checkpoint-dataset',timeout=900)
            (OUT/'checkpoint-upload.log').write_text(r.stdout+r.stderr)
            if r.returncode:raise RuntimeError('Supplemental checkpoint upload failed')
            (OUT/'checkpoint-uploaded.json').write_text(json.dumps(dict(dataset=TAIL,time=time.time())))
        while not ready(TAIL):time.sleep(20)
        if TAIL not in m['dataset_sources']:m['dataset_sources'].append(TAIL)
        metadata.write_text(json.dumps(m,indent=2))
        print('Latest completed BC checkpoint attached; completed epochs will be reused',flush=True)
    if not (OUT/'submitted.json').exists():
        r=call('kaggle','kernels','push','-p',STAGE/'kernel','--accelerator','NvidiaTeslaT4','-t','39600',timeout=180)
        (OUT/'submit.log').write_text(r.stdout+r.stderr)
        if r.returncode:raise RuntimeError('Cannot submit private Kaggle continuation')
        print(r.stdout.strip(),flush=True)
        (OUT/'submitted.json').write_text(json.dumps(dict(kernel=m['id'],time=time.time(),datasets=m['dataset_sources'])))
    active=call('systemctl','--user','is-active','zhanguo-kaggle-industry-watch.service')
    if active.returncode:
        r=call('systemd-run','--user','--unit=zhanguo-kaggle-industry-watch','--collect',
               '--property=Restart=on-failure','--property=RestartSec=30',
               '--property=StandardOutput=append:'+str(OUT/'watch.log'),
               '--property=StandardError=inherit',
               '/usr/bin/python3','-u',ROOT/'watch_kaggle_industry.py','--output',OUT,
               '--colab-config','/tmp/market4-launch-k7c1__mc.json',timeout=30)
        if r.returncode:raise RuntimeError('Cannot start persistent Kaggle watcher: '+r.stderr)
    print('Persistent Kaggle continuation and local download watcher started',flush=True)


if __name__=='__main__':main()
