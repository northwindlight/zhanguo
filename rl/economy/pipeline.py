"""Bounded ECS collection + BC job; evaluation runs separately on the local host."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--seeds',type=int,nargs='+',default=[101,102,103,104,105,106,201,202])
    p.add_argument('--valid-seeds',type=int,nargs='+',default=[201,202])
    p.add_argument('--turns',type=int,default=150);p.add_argument('--epochs',type=int,default=12)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--projects',action='store_true')
    p.add_argument('--student-checkpoint',type=Path)
    p.add_argument('--teacher-probability',type=float,default=.5)
    p.add_argument('--reuse-data',type=Path,help='Reuse complete matching worlds after a collector failure; original source hashes remain recorded')
    a=p.parse_args()
    if a.workers<1 or len(set(a.seeds))!=len(a.seeds):raise ValueError('Invalid workers or duplicate seeds')
    if not set(a.valid_seeds)<set(a.seeds):raise ValueError('Both train and validation worlds are required')
    if a.out.exists():raise FileExistsError('Use a fresh run directory')
    a.out.mkdir(parents=True)
    data=a.reuse_data or a.out/'data'
    def phase(name,**values):
        (a.out/'phase.json').write_text(json.dumps(dict(phase=name,time=time.time(),**values),indent=2)+'\n')
        print(name,values,flush=True)
    phase('collection')
    children=[];logs=[]
    try:
        # Distribute missing worlds first when resuming, so existing shards do
        # not leave all remaining generation on one worker.
        seeds_order=sorted(a.seeds,key=lambda seed:(data/f'world-{seed}.npz').exists())
        for worker in range(min(a.workers,len(a.seeds))):
            seeds=seeds_order[worker::a.workers];log=(a.out/f'collect-{worker}.log').open('w');logs.append(log)
            mixture=(['--student-checkpoint',str(a.student_checkpoint),
                      '--teacher-probability',str(a.teacher_probability),
                      '--teacher-only-seeds',*map(str,a.valid_seeds)] if a.student_checkpoint else [])
            children.append(subprocess.Popen([sys.executable,'-m','rl.economy.collect',
                '--seeds',*map(str,seeds),'--turns',str(a.turns),'--out',str(data),*mixture,
                *(['--skip-existing'] if a.reuse_data else []),*(['--projects'] if a.projects else [])],stdout=log,stderr=subprocess.STDOUT))
        codes=[child.wait() for child in children]
        if any(codes):raise RuntimeError(f'Collectors failed: {codes}; see collect logs')
        phase('training')
        with (a.out/'train.log').open('w') as log:
            subprocess.run([sys.executable,'-m','rl.economy.train','--data',str(data),
                '--valid-seeds',*map(str,a.valid_seeds),'--epochs',str(a.epochs),
                '--out',str(a.out/'model'),*(['--init',str(a.student_checkpoint)] if a.student_checkpoint else []),
                *(['--allow-mixed-teachers'] if a.reuse_data else []),*(['--projects'] if a.projects else [])],
                stdout=log,stderr=subprocess.STDOUT,check=True)
        phase('complete',checkpoint=str(a.out/'model'/'best.pt'))
    except BaseException as error:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:child.wait()
        phase('failed',reason=str(error));raise
    finally:
        for log in logs:log.close()


if __name__=='__main__':main()
