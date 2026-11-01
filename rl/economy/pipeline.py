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
    p.add_argument('--turns',type=int,default=40);p.add_argument('--epochs',type=int,default=12)
    p.add_argument('--workers',type=int,default=2);a=p.parse_args()
    if a.workers<1 or len(set(a.seeds))!=len(a.seeds):raise ValueError('Invalid workers or duplicate seeds')
    if not set(a.valid_seeds)<set(a.seeds):raise ValueError('Both train and validation worlds are required')
    if a.out.exists():raise FileExistsError('Use a fresh run directory')
    a.out.mkdir(parents=True)
    def phase(name,**values):
        (a.out/'phase.json').write_text(json.dumps(dict(phase=name,time=time.time(),**values),indent=2)+'\n')
        print(name,values,flush=True)
    phase('collection')
    children=[];logs=[]
    try:
        for worker in range(min(a.workers,len(a.seeds))):
            seeds=a.seeds[worker::a.workers];log=(a.out/f'collect-{worker}.log').open('w');logs.append(log)
            children.append(subprocess.Popen([sys.executable,'-m','rl.economy.collect',
                '--seeds',*map(str,seeds),'--turns',str(a.turns),'--out',str(a.out/'data')],stdout=log,stderr=subprocess.STDOUT))
        codes=[child.wait() for child in children]
        if any(codes):raise RuntimeError(f'Collectors failed: {codes}; see collect logs')
        phase('training')
        with (a.out/'train.log').open('w') as log:
            subprocess.run([sys.executable,'-m','rl.economy.train','--data',str(a.out/'data'),
                '--valid-seeds',*map(str,a.valid_seeds),'--epochs',str(a.epochs),
                '--out',str(a.out/'model')],stdout=log,stderr=subprocess.STDOUT,check=True)
        phase('complete',checkpoint=str(a.out/'model'/'best.pt'))
    except BaseException as error:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:child.wait()
        phase('failed',reason=str(error));raise
    finally:
        for log in logs:log.close()


if __name__=='__main__':main()
