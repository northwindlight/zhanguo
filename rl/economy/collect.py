"""Generate synthetic teacher demonstrations, with full military and market feedback."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import time

from host import GameHost
from .adapter import ACTIONS,FINGERPRINT
from .client import RecordingTeacher
from .data import Writer
from .scenarios import config,source_hash,snapshot


def collect(seed,turns,path,*,nations=8,size=24):
    source=source_hash()
    cfg=config(seed,nations=nations,size=size)
    host=GameHost.new(cfg);writer=Writer();counts=Counter();failures=defaultdict(Counter)
    def sink(row):
        kind=ACTIONS[int(row['candidates'][row['target'],5:10].argmax())]
        counts[kind]+=1;writer.add(row)
    for actor,client in list(host.clients.items()):
        if client.version=='v13plus':
            host.clients[actor]=RecordingTeacher(state=client.state,sink=sink)
    def receipt(actor,action,args,result):
        if not result['ok']:failures[actor][action+':'+result['message']]+=1
    start=time.monotonic();initial=snapshot(host,failures)
    for turn in range(turns):
        host.cycle(on_result=receipt)
        if (turn+1)%10==0:
            print(json.dumps(dict(seed=seed,turn=turn+1,rows=len(writer.rows),seconds=round(time.monotonic()-start,1))),flush=True)
    if source_hash()!=source:raise RuntimeError('Source changed during collection; discard this world')
    metadata=dict(seed=seed,turns=turns,config=cfg,source=source,counts=dict(counts),
                  initial=initial,final=snapshot(host,failures),seconds=time.monotonic()-start)
    writer.save(path,metadata)
    return metadata


def main():
    p=argparse.ArgumentParser();p.add_argument('--seeds',type=int,nargs='+',required=True)
    p.add_argument('--turns',type=int,default=60);p.add_argument('--nations',type=int,default=8)
    p.add_argument('--sizes',type=int,nargs='+',default=[20,24,28]);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    for i,seed in enumerate(a.seeds):
        path=a.out/f'world-{seed}.npz'
        if path.exists():raise FileExistsError(path)
        metadata=collect(seed,a.turns,path,nations=a.nations,size=a.sizes[i%len(a.sizes)])
        path.with_suffix('.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(seed=seed,counts=metadata['counts'],seconds=metadata['seconds'])),flush=True)


if __name__=='__main__':main()
