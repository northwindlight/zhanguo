"""Retrieve each Kaggle PPO round, validate it, and enqueue its successor.

The GPU jobs are finite and export checksum-verified recovery bundles. Local
long-game validation is independent and never gates exploratory continuation.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.econ_ppo_watch import digest,install_backup,queue_evaluation,write

CLI='/home/northwind/.local/bin/kaggle'
KAGGLE_PYTHON='/home/northwind/.local/share/pipx/venvs/kaggle/bin/python'


def command(args,timeout):
    p=subprocess.run([CLI,*args],capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(f'Kaggle command failed ({p.returncode}): {args[:2]}')
    return p.stdout


def status(kernel,allow_missing=False):
    result=subprocess.run([CLI,'kernels','status',kernel],capture_output=True,text=True,timeout=45)
    text=result.stdout+result.stderr
    if result.returncode:
        if allow_missing and re.search(r'\b404\b|not found|does not exist',text,re.I):return None
        raise RuntimeError('Kaggle status request failed')
    match=re.search(r'KernelWorkerStatus\.([A-Z_]+)',text)
    if not match:raise ValueError('Unrecognized Kaggle status')
    return match.group(1)


def prepare_input(out,live):
    folder=out/'kaggle-input'
    recovery=folder/'recovery.tmp'
    completed=sorted(p for p in live.glob('round-*') if (p/'candidate.pt').exists() and (p/'candidate.json').exists())
    latest=completed[-1] if completed else None
    resume=None
    if latest is not None:
        record=json.loads((latest/'candidate.json').read_text())
        if digest(latest/'candidate.pt')!=record['sha256']:raise ValueError('Latest candidate checksum mismatch')
        resume=dict(last_completed_round=record['round'],next_round=record['round']+1,
                    last_candidate_sha256=record['sha256'])
    with tarfile.open(recovery,'w:gz',compresslevel=1) as archive:
        for path in sorted(live.rglob('*')):
            if path.is_file() and path.suffix in ('.pt','.json','.jsonl','.gz'):
                relative=path.relative_to(live)
                if latest is not None:
                    # A complete round resumes from its candidate, not its old
                    # on-policy rows. Preserve any later incomplete worlds.
                    if len(relative.parts)==1 and path.name not in ('run.json','anchor.pt','state.json'):continue
                    if len(relative.parts)>1 and relative.parts[0]<=latest.name:
                        if path.parent!=latest or path.name not in ('candidate.pt','candidate.json'):continue
                archive.add(path,arcname=str(Path('rl/runs/ppo-cycle')/path.relative_to(live)))
    recovery.replace(folder/'recovery.bin')
    manifest={p.name:dict(bytes=p.stat().st_size,sha256=digest(p)) for p in folder.glob('*.bin')}
    if resume is not None:manifest['resume']=resume
    (folder/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2))
    return folder


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--kernel',default='northwindlight/zhanguo-economy-ppo-continuous')
    p.add_argument('--dataset',default='northwindlight/zhanguo-economy-ppo-recovery-20261108')
    p.add_argument('--version',type=int,default=1)
    p.add_argument('--interval',type=int,default=120)
    args=p.parse_args();live=args.out/'live'
    state_path=args.out/'kaggle-watch-state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else dict(version=args.version,phase='running')
    failures=0
    while True:
        try:
            if shutil.disk_usage(args.out).free<1024**3:raise RuntimeError('Local reserve below 1 GiB')
            kernel=f"{args.kernel}/{state['version']}"
            current=status(kernel)
            state.update(remote_status=current,checked_at=time.time(),
                         phase='running' if current=='RUNNING' else 'queued' if current=='QUEUED' else 'collecting_results')
            write(state_path,state)
            print(json.dumps(dict(event='kaggle_status',version=state['version'],status=current)),flush=True)
            queue_evaluation(ROOT,args.out,live)
            if current in ('RUNNING','QUEUED'):
                failures=0;time.sleep(args.interval);continue
            if current not in ('COMPLETE','ERROR','CANCELLED'):
                raise ValueError('Unexpected terminal Kaggle status: '+current)
            download=args.out/'kaggle-output'/f"version-{state['version']:04d}"
            download.mkdir(parents=True,exist_ok=True)
            if not state.get('installed'):
                command(['kernels','output',kernel,'-p',str(download),'-o','-q',
                         '--file-pattern',r'(output-manifest\.json|ppo-round-backup\.zip|environment\.json|training\.log)$'],300)
                manifests=list(download.rglob('output-manifest.json'))
                if len(manifests)!=1:raise RuntimeError('Kaggle ended without a recovery manifest; inspect its logs')
                record=json.loads(manifests[0].read_text())
                install_backup(manifests[0].with_name('ppo-round-backup.zip'),live,record)
                for path in live.glob('round-*/trajectory-*'):
                    if str(path.relative_to(live)) not in record['files']:path.unlink()
                write(args.out/'kaggle-round-result.json',record)
                state.update(installed=True,round=record['round'],training_complete=record['training_complete'],
                             environment=record['environment'])
                write(state_path,state)
                # Retain two large downloaded bundles; all models, metrics,
                # environment records and training logs remain available.
                bundles=sorted((args.out/'kaggle-output').glob('version-*/ppo-round-backup.zip'))
                for path in bundles[:-2]:path.unlink()
            if not state['training_complete']:
                state.update(phase='training_failed');write(state_path,state)
                # Preserve failed-round evidence and completed worlds. Refuse
                # blind retries of a deterministic optimizer/contract failure.
                raise RuntimeError('Kaggle training failed; recovery artifacts saved')
            queue_evaluation(ROOT,args.out,live)
            accepted=any(json.loads(f.read_text()).get('accepted') is True
                         for f in (args.out/'evaluations').glob('*/summary.json'))
            if accepted or (args.out/'STOP').exists():
                state.update(phase='accepted' if accepted else 'stopped');write(state_path,state);return
            if not state.get('dataset_updated'):
                folder=prepare_input(args.out,live)
                upload=subprocess.run([KAGGLE_PYTHON,str(ROOT/'scripts/econ_kaggle_upload.py'),'--folder',str(folder),
                                       '--message',f"Resume after PPO round {state['round']}"],
                                      capture_output=True,text=True,timeout=300)
                if upload.returncode:raise RuntimeError('Recovery bundle upload failed')
                version=json.loads(next(line[16:] for line in upload.stdout.splitlines() if line.startswith('DATASET_VERSION ')))['version']
                state.update(dataset_updated=True,dataset_version=version,phase='dataset_processing')
                write(state_path,state)
            data_status=json.loads(command(['datasets','status',args.dataset,'--format','json'],45))
            if data_status['status']!='ready' or data_status['current_version_number']<state['dataset_version']:
                time.sleep(args.interval);continue
            if not state.get('dataset_verified'):
                verification=args.out/'dataset-verification'/str(state['dataset_version'])
                verification.mkdir(parents=True,exist_ok=True)
                command(['datasets','download',f"{args.dataset}/{state['dataset_version']}",
                         '-f','bundle-manifest.json','-p',str(verification),'-o','-q'],90)
                if (verification/'bundle-manifest.json').read_bytes()!=(args.out/'kaggle-input/bundle-manifest.json').read_bytes():
                    raise ValueError('Published dataset does not match the requested resume bundle')
                state.update(dataset_verified=True);write(state_path,state)
            # Reconcile a previously uncertain push before submitting again.
            if state.get('push_pending'):
                next_version=state['version']+1
                if status(f'{args.kernel}/{next_version}',allow_missing=True) in ('RUNNING','QUEUED','COMPLETE','ERROR','CANCELLED'):
                    state=dict(version=next_version,phase='running');write(state_path,state);continue
            kernel_folder=args.out/'kaggle-kernel'
            shutil.copyfile(ROOT/'scripts/econ_kaggle_job.py',kernel_folder/'train.py')
            metadata_path=kernel_folder/'kernel-metadata.json'
            metadata=json.loads(metadata_path.read_text())
            metadata['dataset_sources']=[f"{args.dataset}/{state['dataset_version']}"]
            metadata_path.write_text(json.dumps(metadata,indent=2))
            state.update(push_pending=True,phase='submitting');write(state_path,state)
            response=command(['kernels','push','-p',str(kernel_folder),'--accelerator','NvidiaTeslaT4','-t','28800'],120)
            match=re.search(r'Kernel version (\d+)',response)
            if not match:raise ValueError('No version in successful Kaggle push')
            state=dict(version=int(match.group(1)),phase='running');write(state_path,state)
            failures=0
        except Exception as error:
            failures+=1
            write(args.out/'kaggle-retry.json',dict(failures=failures,error=str(error),version=state['version']))
            print(f'Kaggle monitor retry {failures}: {type(error).__name__}',flush=True)
            # Status remains truthful: an API retry is not active GPU work.
        time.sleep(args.interval)


if __name__=='__main__':main()
