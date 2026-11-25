"""Persistently watch the private Kaggle continuation and verify local delivery."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import time
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parent
KERNEL='northwindlight/zhanguo-industry-bc-resume'
os.environ['PATH']=str(Path.home()/'.local/bin')+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')


def run(*args,timeout=120):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def logs(text):
    try:
        records=json.loads(text)
        return ''.join(r.get('data','') for r in records)
    except (ValueError,AttributeError,TypeError):return text


def handoff(out,config):
    if not config or (out/'colab-released.json').exists():return
    # Only run after the new host has verified checkpoint, optimizer and actual CUDA execution.
    r=run('systemctl','--user','stop','zhanguo-industry-bc-watch.service')
    if r.returncode and run('systemctl','--user','is-active','zhanguo-industry-bc-watch.service').returncode==0:
        raise RuntimeError('Cannot stop superseded Colab watcher')
    from watch_fp32_day import colab, SESSION
    r=colab(config,'stop','-s',SESSION,timeout=60)
    if r.returncode:
        sessions=colab(config,'sessions',timeout=45)
        if sessions.returncode or f'[{SESSION}]' in sessions.stdout:
            raise RuntimeError('Colab release failed; will retry')
    (out/'colab-released.json').write_text(json.dumps(dict(released=True,time=time.time())))
    print('Kaggle CUDA verified; superseded Colab watcher stopped and L4 released',flush=True)


def collect(out):
    dest=out/'artifacts'
    dest.mkdir(exist_ok=True)
    r=run('kaggle','kernels','output',KERNEL,'-p',str(dest),'-q',
          '--file-pattern',r'(^|/)work/[^/]+\.(json|log|pt|sha256)$',timeout=600)
    if r.returncode:raise RuntimeError('Kaggle output download failed: '+r.stderr[-600:])
    candidates=list(dest.rglob('done.json'))
    if len(candidates)!=1:raise RuntimeError('No unambiguous completed training report in outputs')
    done=json.loads(candidates[0].read_text());work=candidates[0].parent
    for name,digest in [('checkpoint','sha256'),('native','native_sha256')]:
        if sha(work/done[name])!=done[digest]:raise ValueError('Final checkpoint checksum mismatch')
    for name,digest in done.get('source_chunks_sha256',{}).items():
        if Path(name).name!=name or sha(ROOT/'industry-bc-results/chunks'/name)!=digest:
            raise ValueError('Original local demonstration archive checksum mismatch')
    (out/'done.json').write_text(json.dumps(done,indent=2))
    package=out/'zhanguo-industry-bc-kaggle-delivery.zip'
    with ZipFile(package.with_suffix('.partial'),'w',ZIP_DEFLATED,compresslevel=2) as z:
        for p in sorted(work.iterdir()):
            if p.suffix in ('.json','.log') or p.name in (done['checkpoint'],done['native']):
                z.write(p,'results/'+p.name)
        source_manifest='evaluation-handoff.json' if done.get('kaggle_evaluation_only') else 'handoff.json'
        for name in ['kernel/resume.py','kernel/kernel-metadata.json','prepare.py',source_manifest,'code.zip']:
            z.write(ROOT/'kaggle-industry-resume'/name,'continuation/'+name)
    package.with_suffix('.partial').replace(package)
    (out/'verified.json').write_text(json.dumps(dict(verified=True,accepted=done['accepted'],
        checkpoint=done['checkpoint'],sha256=done['sha256'],package=package.name,time=time.time()),indent=2))
    print('Training outputs downloaded and verified; accepted='+str(done['accepted']),flush=True)


def watch(out,config):
    live_file=(out/'runtime-live.log').open('ab',buffering=0)
    follower=None
    while not (out/'verified.json').exists():
        try:
            if follower is None or follower.poll() is not None:
                follower=subprocess.Popen(['kaggle','kernels','logs',KERNEL,'-f'],
                    stdout=live_file,stderr=subprocess.STDOUT)
            status=run('kaggle','kernels','status',KERNEL,timeout=60)
            if status.returncode:raise RuntimeError('Cannot read Kaggle run status')
            state=status.stdout.strip()
            (out/'status.json').write_text(json.dumps(dict(status=state,time=time.time())))
            live=run('kaggle','kernels','logs',KERNEL,timeout=60)
            if live.returncode==0 and live.stdout.strip():
                plain=logs(live.stdout)
                (out/'runtime.log').write_text(plain)
            streamed=(out/'runtime-live.log').read_text(errors='replace')
            if '"stage": "kaggle_ready"' in streamed:
                handoff(out,config)
            if 'COMPLETE' in state.upper():
                collect(out)
                if follower.poll() is None:follower.terminate()
                live_file.close()
                return
            if any(s in state.upper() for s in ['ERROR','CANCELLED','CANCELED']):
                (out/'failed.json').write_text(json.dumps(dict(status=state,time=time.time())))
                print('Kaggle run failed; retaining logs and original checkpoint',flush=True)
                if follower.poll() is None:follower.terminate()
                live_file.close()
                return
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as e:
            print('Watcher retry:',type(e).__name__,str(e),flush=True)
        time.sleep(45)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=ROOT/'kaggle-industry-results')
    p.add_argument('--colab-config',type=Path)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    watch(args.output,args.colab_config)
