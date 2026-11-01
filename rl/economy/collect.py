"""Generate synthetic teacher demonstrations, with full military and market feedback."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import time

from host import GameHost
from .adapter import ACTIONS,FINGERPRINT,PROJECT_FINGERPRINT
from .client import RecordingTeacher,CorrectingTeacher
from .data import Writer
from .scenarios import config,source_hash,snapshot


def collect(seed,turns,path,*,nations=8,size=24,model=None,teacher_probability=.5,student_hash=None,projects=False):
    source=source_hash()
    cfg=config(seed,nations=nations,size=size)
    host=GameHost.new(cfg);writer=Writer(PROJECT_FINGERPRINT if projects else FINGERPRINT);counts=Counter();failures=defaultdict(Counter)
    if projects:
        from .projects import RecordingProjects,CorrectingProjects
        teacher,corrector=RecordingProjects,CorrectingProjects
    else:teacher,corrector=RecordingTeacher,CorrectingTeacher
    if model is not None and (model.action_mode=='projects')!=projects:raise ValueError('Student action schema mismatch')
    def sink(row):
        kind=ACTIONS[int(row['candidates'][row['target'],5:10].argmax())]
        counts[kind]+=1;writer.add(row)
    for actor,client in list(host.clients.items()):
        if client.version=='v13plus':
            host.clients[actor]=(teacher(state=client.state,sink=sink) if model is None else
                corrector(state=client.state,sink=sink,model=model,
                    teacher_probability=teacher_probability,seed=f'{seed}:{actor}'))
    def receipt(actor,action,args,result):
        if not result['ok']:failures[actor][action+':'+result['message']]+=1
    start=time.monotonic();initial=snapshot(host,failures)
    for turn in range(turns):
        host.cycle(on_result=receipt)
        if (turn+1)%10==0:
            print(json.dumps(dict(seed=seed,turn=turn+1,rows=len(writer.rows),seconds=round(time.monotonic()-start,1))),flush=True)
    if source_hash()!=source:raise RuntimeError('Source changed during collection; discard this world')
    metadata=dict(seed=seed,turns=turns,config=cfg,source=source,counts=dict(counts),
                  initial=initial,final=snapshot(host,failures),seconds=time.monotonic()-start,
                  student_checkpoint_sha256=student_hash if model is not None else None,
                  teacher_probability=teacher_probability if model is not None else 1.,
                  mixture_turns={a:dict(teacher=c.teacher_turns,student=c.student_turns)
                    for a,c in host.clients.items() if isinstance(c,corrector)})
    writer.save(path,metadata)
    return metadata


def main():
    p=argparse.ArgumentParser();p.add_argument('--seeds',type=int,nargs='+',required=True)
    p.add_argument('--turns',type=int,default=60);p.add_argument('--nations',type=int,default=8)
    p.add_argument('--sizes',type=int,nargs='+',default=[20,24,28]);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--student-checkpoint',type=Path);p.add_argument('--teacher-probability',type=float,default=.5)
    p.add_argument('--teacher-only-seeds',type=int,nargs='*',default=[])
    p.add_argument('--skip-existing',action='store_true',help='Reuse complete compatible worlds; preserve their original source metadata')
    p.add_argument('--projects',action='store_true')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    if not 0<a.teacher_probability<=1:raise ValueError('Teacher probability must be in (0, 1]')
    model=student_hash=None
    if a.student_checkpoint:
        import torch
        from .model import load_policy
        torch.set_num_threads(1)
        model,_=load_policy(a.student_checkpoint)
        student_hash=hashlib.sha256(a.student_checkpoint.read_bytes()).hexdigest()
    for i,seed in enumerate(a.seeds):
        path=a.out/f'world-{seed}.npz'
        size=a.sizes[seed%len(a.sizes)]
        active=model is not None and seed not in a.teacher_only_seeds
        if path.exists():
            if not a.skip_existing:raise FileExistsError(path)
            import numpy as np
            with np.load(path,allow_pickle=False) as z:old=json.loads(str(z['metadata']))
            expected=dict(fingerprint=PROJECT_FINGERPRINT if a.projects else FINGERPRINT,seed=seed,turns=a.turns,
                config=config(seed,nations=a.nations,size=size),
                teacher_probability=a.teacher_probability if active else 1.,
                student_checkpoint_sha256=student_hash if active else None)
            if any(old.get(k)!=v for k,v in expected.items()):raise ValueError(f'Existing world configuration mismatch: {path}')
            print(json.dumps(dict(reused=str(path),source=old['source'])),flush=True)
            continue
        metadata=collect(seed,a.turns,path,nations=a.nations,size=size,
            model=None if seed in a.teacher_only_seeds else model,
            teacher_probability=a.teacher_probability,student_hash=student_hash,projects=a.projects)
        path.with_suffix('.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(seed=seed,counts=metadata['counts'],seconds=metadata['seconds'])),flush=True)


if __name__=='__main__':main()
