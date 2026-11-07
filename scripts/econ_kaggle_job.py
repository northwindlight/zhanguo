"""Run one complete resumable PPO round on a Kaggle GPU, then export evidence.

The local controller retrieves this job's artifacts and starts the next round.
Keeping jobs finite makes recovery independent of a notebook connection.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    started=time.time()
    inputs=list(Path('/kaggle/input').rglob('bundle-manifest.json'))
    if len(inputs)!=1:raise ValueError('Expected one private recovery bundle')
    source=inputs[0].parent;manifest=json.loads(inputs[0].read_text())
    root=Path('/kaggle/working/zhanguo-ppo');root.mkdir(exist_ok=True)
    for name in ('runtime.bin','recovery.bin'):
        path=source/name
        if sha(path)!=manifest[name]['sha256'] or path.stat().st_size!=manifest[name]['bytes']:
            raise ValueError('Input bundle checksum mismatch: '+name)
        with tarfile.open(path) as archive:archive.extractall(root,filter='data')
    os.chdir(root)
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
    os.environ.update(env)
    import torch
    if not torch.cuda.is_available():raise RuntimeError('GPU job did not receive CUDA; refuse CPU training')
    torch.ones(8,device='cuda').sum().item()
    environment=dict(torch=str(torch.__version__),gpus=[torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
                     python=sys.version,started=started,input_manifest=manifest)
    Path('/kaggle/working/environment.json').write_text(json.dumps(environment,indent=2))
    print(json.dumps(dict(event='gpu_verified',**environment)),flush=True)
    subprocess.run([sys.executable,'-m','ruleai.v13.build'],check=True,env=env)
    run=root/'rl/runs/ppo-cycle'
    settings=json.loads((run/'run.json').read_text())['settings']
    completed=sorted(p for p in run.glob('round-*') if (p/'candidate.json').exists() and (p/'candidate.pt').exists())
    number=int(completed[-1].name.split('-')[-1])+1 if completed else 1
    args=[sys.executable,'-u','-m','rl.economy.ppo_cycle','--checkpoint',str(root/'initial.pt'),
          '--out',str(run),'--device','cuda','--workers','2','--max-rounds',str(number)]
    for name in ('worlds','turns','seed_base','epochs','effective_batch','micro_batch','lr'):
        args.extend(['--'+name.replace('_','-'),str(settings[name])])
    with Path('/kaggle/working/training.log').open('w',buffering=1) as log:
        process=subprocess.Popen(args,env=env,stdout=log,stderr=subprocess.STDOUT)
        while process.poll() is None:
            state_path=run/'state.json'
            state=json.loads(state_path.read_text()) if state_path.exists() else dict(phase='starting')
            progress={p.name:p.read_text()[-300:] for p in run.glob(f'round-{number:06d}/world-*.log')}
            print(json.dumps(dict(event='training_progress',state=state,worlds=progress)),flush=True)
            time.sleep(60)
        returncode=process.returncode
    # A failed round still exports completed worlds and its explicit failure.
    backup=Path('/kaggle/working/ppo-round-backup.zip');files={}
    with zipfile.ZipFile(backup,'w',compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(run.rglob('*')):
            if not path.is_file() or path.suffix not in ('.json','.jsonl','.pt','.gz'):continue
            if path.stat().st_size>128*1024**2:raise ValueError('Output entry too large')
            name=str(path.relative_to(run));data=path.read_bytes()
            files[name]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
            archive.writestr(name,data)
    record=dict(round=number,returncode=returncode,finished=time.time(),seconds=time.time()-started,
                training_complete=returncode==0 and (run/f'round-{number:06d}/candidate.json').exists(),
                bytes=backup.stat().st_size,sha256=sha(backup),files=files,changed=list(files),environment=environment)
    Path('/kaggle/working/output-manifest.json').write_text(json.dumps(record,indent=2))
    print(json.dumps({k:v for k,v in record.items() if k not in ('files','changed','environment')}),flush=True)


if __name__=='__main__':main()
