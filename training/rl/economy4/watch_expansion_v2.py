"""Durable Colab launcher and checksummed backup for expansion BC v2."""

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote

import requests

from watch_fp32_day import colab, SESSION
from mixed_bc_run import digest, publish

HERE=Path(__file__).resolve().parent
REMOTE='content/zhanguo-expansion-v2/'
LAUNCHER='launch_expansion_v2.py'
CODE_STAGE='zhanguo-expansion-v2-code.zip'
RESTORE_STAGE='zhanguo-expansion-v2-restore.json'
REMOTE_ROOT='/content/zhanguo-expansion-v2'
os.environ['PATH']=str(Path.home()/'.local/bin')+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')


def fetch(session,config,relative,target):
    state=json.loads(config.read_text()).get(SESSION)
    if not state:return False
    try:
        response=session.get(state['url'].rstrip('/')+'/api/contents/'+quote(relative,safe='/'),
                             params={'authuser':'0','colab-runtime-proxy-token':state['token'],'content':'1'},
                             timeout=(12,45))
        if response.status_code!=200:return False
        data=response.json()
        content=data['content']
        payload=base64.b64decode(content) if data.get('format')=='base64' else str(content).encode()
        target.parent.mkdir(parents=True,exist_ok=True)
        staged=target.with_suffix(target.suffix+'.download')
        staged.write_bytes(payload)
        staged.replace(target)
        return True
    except (requests.RequestException,KeyError,ValueError):
        return False


def upload(config,path,name):
    try:
        result=colab(config,'upload','-s',SESSION,str(path),'content/'+name,timeout=100,direct=True)
    except subprocess.TimeoutExpired:
        result=None
    if result is None or result.returncode:
        result=colab(config,'upload','-s',SESSION,str(path),'content/'+name,timeout=180)
    if result.returncode:raise RuntimeError('Upload failed: '+path.name)


def parts_upload(config,path,prefix):
    pieces=[]
    with tempfile.TemporaryDirectory() as temp,path.open('rb') as source:
        index=0
        while data:=source.read(7*1024*1024):
            piece=Path(temp)/(prefix+f'.{index:03}')
            piece.write_bytes(data)
            upload(config,piece,piece.name)
            pieces.append(piece.name)
            index+=1
    return dict(sha256=digest(path),parts=pieces)


def launch(config):
    for direct in (True,False):
        try:
            result=colab(config,'exec','-s',SESSION,'-f',str(HERE/LAUNCHER),
                         '--timeout','25',timeout=55,direct=direct)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode==0:
            print(result.stdout.strip(),flush=True)
            return
    raise RuntimeError('Cannot launch expansion worker')


def restore(config,out):
    upload(config,out/'code.zip',CODE_STAGE)
    original=HERE/'mixed-bc-results'
    for source,name in (('best-batch-mean.pt','base.pt'),('best-batch-mean-native.pt','base-native.pt')):
        parts_upload(config,original/source,'v2-original-'+name)
    for path in sorted((original/'chunks').glob('chunk-*.zip')):
        upload(config,path,'v2-original-'+path.name)
        print('Restored original archive',path.name,flush=True)
    candidates=sorted(out.glob('epoch-[0-9][0-9].pt'))
    chosen=set()
    if candidates:chosen.add(candidates[-1])
    if (out/'best.json').exists():
        best=json.loads((out/'best.json').read_text())
        chosen.update(out/best[k] for k in ('checkpoint','native'))
    chosen.update((out/'chunks').glob('*.zip'))
    chosen.update(p for p in out.glob('*.json') if p.name not in ('inventory.json','done.json','progress.json','restore-upload.json'))
    mapping={}
    for index,path in enumerate(sorted(chosen)):
        if path.exists():mapping[path.relative_to(out).as_posix()]=parts_upload(config,path,f'v2-restore-{index:03}')
    manifest=out/'restore-upload.json'
    publish(manifest,mapping)
    upload(config,manifest,RESTORE_STAGE)
    launch(config)


def backup(session,config,out):
    fetch(session,config,REMOTE+'driver.log',out/'driver.log')
    if not fetch(session,config,REMOTE+'progress.json',out/'progress.json'):
        return None
    progress=json.loads((out/'progress.json').read_text())
    names=['budget.json','best.json','baseline.json','baseline.log','final-base.json','final-model.json',
           'final-base.log','final-model.log','build.log']
    for epoch in range(1,int(progress.get('epoch',1))+1):
        names.extend([f'epoch-{epoch:02}.json',f'epoch-{epoch:02}-train.log'])
        if epoch%2==0:names.extend([f'epoch-{epoch:02}-eval.json',f'epoch-{epoch:02}-eval.log'])
    for name in names:
        # Completed reports and evaluation logs are immutable. Training logs may still grow.
        local=out/name
        if local.exists() and not name.endswith('-train.log') and name not in ('best.json',):
            # A rollout log can be visible while sampling is still running. Only
            # its final sampler footer establishes that this local copy is complete.
            if not name.endswith('.log') or '\ncompleted=' in local.read_text():
                continue
        fetch(session,config,REMOTE+name,out/name)
    if fetch(session,config,REMOTE+'inventory.json',out/'inventory.json'):
        inventory=json.loads((out/'inventory.json').read_text())
        for relative,item in inventory.items():
            if not relative.startswith(('epoch-','chunks/')) or '..' in Path(relative).parts:
                raise ValueError('Invalid backup relative path')
            target=out/relative
            if target.exists() and digest(target)==item['sha256']:continue
            pieces=[]
            for part in item['parts']:
                piece=out/part['name']
                if not piece.exists() or digest(piece)!=part['sha256']:
                    if not fetch(session,config,REMOTE+part['name'],piece):return progress
                    if digest(piece)!=part['sha256']:raise ValueError('Backup piece hash mismatch')
                pieces.append(piece)
            target.parent.mkdir(parents=True,exist_ok=True)
            staged=target.with_suffix(target.suffix+'.partial')
            with staged.open('wb') as dest:
                for piece in pieces:
                    with piece.open('rb') as source:shutil.copyfileobj(source,dest)
            if digest(staged)!=item['sha256']:raise ValueError('Backup artifact hash mismatch')
            staged.replace(target)
            print('Verified backup',relative,flush=True)
    if progress['state']=='completed' and fetch(session,config,REMOTE+'done.json',out/'done.download'):
        done=json.loads((out/'done.download').read_text())
        for key,sha in (('checkpoint','sha256'),('native','native_sha256')):
            target=out/done[key]
            if not target.exists() or digest(target)!=done[sha]:return progress
        if not all((out/'chunks'/name).exists() for name in done['recovery_chunks']):return progress
        (out/'done.download').replace(out/'done.json')
        return dict(progress,verified=True)
    return progress


def release(config,out):
    if (out/'released').exists():return
    for _ in range(3):
        result=colab(config,'stop','-s',SESSION,timeout=60)
        if result.returncode==0:
            (out/'released').touch()
            print('Dedicated L4 released',flush=True)
            return
        active=colab(config,'sessions',timeout=45)
        if active.returncode==0 and f'[{SESSION}]' not in active.stdout:
            (out/'released').touch()
            return
        time.sleep(20)
    raise RuntimeError('L4 release will be retried by the service')


def watch(config,out):
    out.mkdir(parents=True,exist_ok=True)
    if (out/'done.json').exists():
        release(config,out)
        return
    active=colab(config,'sessions',timeout=45)
    if active.returncode:
        raise RuntimeError('Cannot inspect active Colab sessions')
    if f'[{SESSION}]' not in active.stdout:
        created=colab(config,'new','-s',SESSION,'--gpu','L4',timeout=300)
        if created.returncode:
            print(created.stdout[-1200:],created.stderr[-1200:],flush=True)
            raise RuntimeError('Cannot allocate replacement L4')
        (out/'recovery-pending').touch()
    if (out/'recovery-pending').exists():
        print('Restoring verified original dataset and weights',flush=True)
        restore(config,out)
        (out/'recovery-pending').unlink(missing_ok=True)
    else:
        upload(config,out/'code.zip',CODE_STAGE)
        launch(config)
    failures=0
    previous=None
    last_change=time.monotonic()
    with requests.Session() as session:
        session.trust_env=False
        while True:
            try:
                progress=backup(session,config,out)
                if progress and progress!=previous:
                    print(json.dumps(progress),flush=True)
                    previous=progress
                    last_change=time.monotonic()
                if progress and progress.get('verified'):
                    print('Training and final evaluation complete; local artifacts verified',flush=True)
                    release(config,out)
                    return
                failures=0 if progress else failures+1
                if failures>=2:
                    active=colab(config,'sessions',timeout=45)
                    if active.returncode==0 and f'[{SESSION}]' not in active.stdout:
                        result=colab(config,'new','-s',SESSION,'--gpu','L4',timeout=300)
                        if result.returncode:raise RuntimeError('Cannot replace expired L4 runtime')
                        (out/'recovery-pending').touch()
                    elif active.returncode==0 and not (out/'recovery-pending').exists():
                        probe=out/'runtime-probe.py'
                        probe.write_text("from pathlib import Path\nprint('v2-root-present',Path("+repr(REMOTE_ROOT+"/code")+").exists(),flush=True)\n")
                        checked=colab(config,'exec','-s',SESSION,'-f',str(probe),'--timeout','20',timeout=45)
                        if checked.returncode==0 and 'v2-root-present False' in checked.stdout:
                            (out/'recovery-pending').touch()
                    if active.returncode==0 and (out/'recovery-pending').exists():
                        restore(config,out)
                        (out/'recovery-pending').unlink(missing_ok=True)
                        failures=0
                        last_change=time.monotonic()
                if progress and time.monotonic()-last_change>1800:
                    launch(config)
                    last_change=time.monotonic()
            except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
                print('Watcher retry',type(error).__name__,str(error),flush=True)
            time.sleep(45)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('config',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'.watch-lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        watch(args.config,args.output)
