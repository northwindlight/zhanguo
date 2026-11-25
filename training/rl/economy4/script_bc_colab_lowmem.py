"""Disk-backed observations; identical targets, sampling, model, and BC loss."""
import argparse
import json
from pathlib import Path
import pickle
import sys
from types import SimpleNamespace
from zipfile import ZipFile

sys.path.insert(0,'/content/zhanguo-pure-script-bc/code')
import script_bc_train as trainer

SPOOL=Path('/content/zhanguo-pure-script-bc/observations.bin')
INDEX=SPOOL.with_suffix('.index.pkl')
READER=None

class Observation:
    def __init__(self,offset,length):self.offset=offset;self.length=length
    def read(self):
        global READER
        if READER is None:READER=SPOOL.open('rb')
        READER.seek(self.offset)
        return pickle.loads(READER.read(self.length))

def load(data):
    if INDEX.exists():
        with INDEX.open('rb') as source:return pickle.load(source)
    episodes=[];seen=set()
    with SPOOL.open('wb') as output:
        for path in sorted((data/'chunks').glob('chunk-*.zip')):
            with ZipFile(path) as archive:
                for name in archive.namelist():
                    if not name.endswith('.pkl'):continue
                    episode=pickle.loads(archive.read(name));seed=episode['seed']
                    if seed in seen:raise ValueError('Duplicate script map')
                    seen.add(seed)
                    if episode.get('anchor') or episode['contract']['source']!='pure_script':raise ValueError('Non-script target')
                    if not episode['accepted'] or not episode['teacher']:continue
                    rows=[]
                    for obs,action,turn,group in episode['teacher']:
                        payload=pickle.dumps(obs,protocol=5);offset=output.tell();output.write(payload)
                        rows.append((Observation(offset,len(payload)),action,turn,group))
                    episodes.append(dict(seed=seed,features=episode['features'],teacher=rows))
            print(json.dumps(dict(stage='bc_disk_index',maps=len(episodes))),flush=True)
    temp=INDEX.with_suffix('.partial')
    with temp.open('wb') as output:pickle.dump(episodes,output,protocol=5)
    temp.replace(INDEX)
    return episodes

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--python-root',type=Path,required=True);p.add_argument('--epochs',type=int,default=10)
    p.add_argument('--device',default='cuda:0');p.add_argument('--batch',type=int,default=24);p.add_argument('--lr',type=float,default=3e-4)
    p.add_argument('--seed',type=int,default=20261124);p.add_argument('--limit-steps',type=int,default=0)
    p.add_argument('--validation-limit',type=int,default=48);p.add_argument('--stop-after-epoch',type=int,default=0)
    args=p.parse_args();args.small=False
    sys.path.insert(0,str(args.python_root))
    import rl.obs
    original=rl.obs.collate
    rl.obs.collate=lambda observations:original([obs.read() if isinstance(obs,Observation) else obs for obs in observations])
    trainer.load=load
    trainer.train(args)

if __name__=='__main__':main()
