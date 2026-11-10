"""Recover ECS CPU PPO artifacts and queue the existing independent evaluation.

ECS is a fallback while free GPUs are unavailable. This controller never trains
locally, allocates a GPU or restarts failed optimizer jobs without inspection.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.econ_ppo_watch import digest,install_backup,queue_evaluation,remote_code,write


def snapshot(args,known,stop):
    remote=args.remote.rstrip('/')
    backup=remote+'/backup.zip'
    code=remote_code(known,stop,remote=remote,backup_path=backup)
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',args.host,
                           '/home/northwind/.venv/bin/python','-'],input=code,
                          capture_output=True,text=True,timeout=90)
    if result.returncode:raise RuntimeError('ECS snapshot failed')
    manifest=json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith('BACKUP ')))
    # A freshly allocated/mistargeted machine must never erase retained worlds.
    if not {'anchor.pt','run.json'}<=set(manifest['files']):
        raise RuntimeError('ECS training recovery state missing; preserve local artifacts')
    partial=args.out/'ecs-backup.partial.zip'
    copied=subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=10',
                           args.host+':'+backup,str(partial)],capture_output=True,text=True,timeout=180)
    if copied.returncode:raise RuntimeError('ECS backup download failed')
    install_backup(partial,args.out/'live',manifest);partial.unlink()
    for path in (args.out/'live').glob('round-*/trajectory-*'):
        if str(path.relative_to(args.out/'live')) not in manifest['files']:path.unlink()
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--host',default='northwind@ecs.northwind.site')
    p.add_argument('--remote',default='/home/northwind/zhanguo-ppo-cpu-20261110')
    p.add_argument('--interval',type=int,default=180)
    args=p.parse_args();live=args.out/'live';marker=args.out/'ecs-watch-state.json'
    while True:
        try:
            known={str(f.relative_to(live)):dict(sha256=digest(f),bytes=f.stat().st_size)
                   for f in live.rglob('*') if f.is_file() and f.suffix in ('.json','.jsonl','.pt','.gz')}
            accepted=any(json.loads(f.read_text()).get('accepted') is True
                         for f in (args.out/'evaluations').glob('*/summary.json'))
            stop=accepted or (args.out/'STOP').exists()
            manifest=snapshot(args,known,stop)
            phase='monitoring' if manifest['alive'] else 'accepted' if accepted else 'stopped' if stop else 'training_failed'
            write(marker,dict(phase=phase,**manifest))
            print(json.dumps(dict(phase=phase,alive=manifest['alive'],states=manifest['states'])),flush=True)
            queue_evaluation(ROOT,args.out,live)
            if not manifest['alive']:return
        except Exception as error:
            write(marker,dict(phase='retrying',error=str(error)))
            print('ECS backup retry: '+type(error).__name__,flush=True)
        time.sleep(args.interval)


if __name__=='__main__':main()
