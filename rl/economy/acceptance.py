"""Teacher-relative long-game acceptance across environments, followed by learning.

Environment cases are model-selection validation. After they all qualify, a
separate fresh-seed confirmation suite is required. No aggregate hides a failed
case, and completed epochs are never treated as acceptance.
"""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import signal
import statistics
import time

import numpy as np
import torch

from .adapter import PROJECT_FINGERPRINT
from .data import Dataset,Writer
from .evaluate import run
from .model import load_policy
from .online import Deadline,check_deadline,rollout,bc_update,with_critic,ppo_update
from .overnight import storage_guard,write_json,checkpoint
from .scenarios import source_hash


def cases(confirm=False,smoke=False):
    seeds=(9901,9902,9903) if confirm else (8101,8102,8103)
    return [dict(seed=seed,size=size,turns=8 if smoke else turns,swap=swap)
        for seed,size,turns in zip(seeds,(20,24,28),(150,200,250)) for swap in (False,True)]


def ratio(value,reference):
    return value/reference if reference>0 else (1. if value>=reference else 0.)


def compare(teacher,student):
    expected_ids={a for a,c in teacher['history'][-1]['countries'].items() if c['version']=='v13plus'}
    actual_ids={a for a,c in student['history'][-1]['countries'].items() if c['version']=='economy-project-bc'}
    if actual_ids!=expected_ids:raise ValueError('Missing or extra focal seat')
    seats=[]
    for actor,c in student['history'][-1]['countries'].items():
        if c['version']!='economy-project-bc':continue
        expected=teacher['history'][-1]['countries'].get(actor)
        if not expected or expected['version']!='v13plus':raise ValueError('Focal seat mismatch')
        m=student['milestones'].get(actor,{})
        seats.append(dict(actor=actor,profile=c['profile'],
            student_S=student['consumption'][actor],teacher_S=teacher['consumption'][actor],
            S_ratio=ratio(student['consumption'][actor],teacher['consumption'][actor]),
            tail_ratio=ratio(student['tail_consumption'][actor],teacher['tail_consumption'][actor]),
            first_recruit=m.get('first_recruit'),first_expansion=m.get('first_expansion'),
            early=m.get('first_recruit',math.inf)<=8 and m.get('first_expansion',math.inf)<=12,
            failures=c['project_failures']+sum(c['failures'].values()),
            truncated=c['truncated'],shortfalls=student['supply_shortfalls'].get(actor,0)))
    if not seats:raise ValueError('No focal countries')
    total=ratio(sum(c['student_S']for c in seats),sum(c['teacher_S']for c in seats))
    worst=min(c['S_ratio']for c in seats);tail=min(c['tail_ratio']for c in seats)
    stable=all(c['early'] and c['failures']==0 and c['truncated']==0 and c['shortfalls']==0 for c in seats)
    # Every seat must approach its teacher; a strong country cannot compensate
    # for a weak country. Zero teacher consumption has explicit neutral semantics.
    near=student['turns']>=150 and all(c['early'] for c in seats) and worst>=.95 and tail>=.95
    surpass=near and worst>=1. and total>=1.
    return dict(total_ratio=total,worst_ratio=worst,median_ratio=statistics.median(c['S_ratio']for c in seats),
                worst_tail_ratio=tail,stable=stable,near=near,surpass=surpass,seats=seats)


def summary(results):
    return dict(cases=len(results),expected_cases=6,near=len(results)==6 and all(r['near']for r in results),
        surpass=len(results)==6 and all(r['surpass']for r in results),
        worst_ratio=min((r['worst_ratio']for r in results),default=0.),
        results=results)


def suite(model,manifest,out,cache,deadline):
    results=[]
    for i,case in enumerate(manifest):
        check_deadline(deadline);storage_guard(out.parent,budget=1024**3)
        key=f"{case['seed']}-{case['size']}-{case['turns']}-{int(case['swap'])}"
        teacher_path=cache/f'{key}.json'
        write_json(out/'state.json',dict(phase='teacher',case=case,index=i,total=len(manifest)))
        if teacher_path.exists():
            teacher=json.loads(teacher_path.read_text())
            if teacher['source']!=source_hash():raise ValueError('Teacher cache source mismatch')
        else:
            teacher=run(**case,deadline=deadline);write_json(teacher_path,teacher)
        write_json(out/'state.json',dict(phase='student',case=case,index=i,total=len(manifest)))
        student=run(**case,model=model,deadline=deadline)
        write_json(out/f'{key}-student.json',student)
        result=dict(**case,complete=True,evaluated_turns=case['turns'],**compare(teacher,student));results.append(result)
        write_json(out/'summary.json',summary(results))
        print(json.dumps(dict(event='case',**result),ensure_ascii=False),flush=True)
    return summary(results)


def regresses(new,old):
    if len(new['results'])!=len(old['results']):return True
    for n,o in zip(new['results'],old['results']):
        if any(n[k]!=o[k] for k in ('seed','size','turns','swap')):raise ValueError('Environment mismatch')
        if o.get('complete',True) and not n.get('complete',True):return True
        if not o.get('complete',True):continue
        for a,b in zip(n['seats'],o['seats']):
            if a['actor']!=b['actor']:raise ValueError('Seat mismatch')
            if b['early'] and not a['early']:return True
            if a['S_ratio']<.95*b['S_ratio'] or a['tail_ratio']<.95*b['tail_ratio']:return True
        if n['total_ratio']<o['total_ratio']:return True
    return False


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--device',default='cpu');p.add_argument('--until',required=True)
    p.add_argument('--rounds',type=int,default=20);p.add_argument('--smoke',action='store_true')
    a=p.parse_args();deadline=datetime.fromisoformat(a.until)
    if deadline.tzinfo is None:raise ValueError('Explicit timezone required')
    deadline=deadline.timestamp();check_deadline(deadline)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(Deadline('Service stop requested')))
    a.out.mkdir(parents=True,exist_ok=True);cache=a.out/'teachers';cache.mkdir(exist_ok=True)
    import resource
    _,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE,(min(hard,128*1024**2) if hard!=resource.RLIM_INFINITY else 128*1024**2,hard))
    signal.signal(signal.SIGXFSZ,lambda *_:(_ for _ in ()).throw(Deadline('Single file storage limit reached')))
    import fcntl
    lock=(a.out/'run.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    resuming=(a.out/'current.pt').exists()
    if resuming and (a.out/'state.json').exists():
        previous=json.loads((a.out/'state.json').read_text())
        if previous.get('source')!=source_hash():raise ValueError('Resume requires unchanged source')
    model,base=load_policy(a.out/'current.pt' if resuming else a.checkpoint)
    model.to(a.device);anchor,_=load_policy(a.checkpoint);anchor.to(a.device)
    manifest=cases(smoke=a.smoke);confirmation=cases(confirm=True,smoke=a.smoke)
    ancestry=set(base.get('seen_seeds',base['train_seeds']+base['valid_seeds']))
    if not resuming and ancestry&{c['seed']for c in manifest+confirmation}:raise ValueError('Acceptance seeds overlap model ancestry')
    while ancestry&{c['seed']for c in confirmation}:
        confirmation=[dict(c,seed=c['seed']+100) for c in confirmation]
    seen=ancestry|{c['seed']for c in manifest}
    data=Dataset([q for q in a.data.glob('world-*.npz') if int(q.stem.split('-')[-1]) in base['train_seeds']],fingerprint=PROJECT_FINGERPRINT)
    replay=[data[i]for i in range(len(data))];torch.set_num_threads(2)
    def save(name,m,**extra):
        storage_guard(a.out,budget=1024**3)
        m.to('cpu');checkpoint(a.out/name,m,base,seen,**extra);m.to(a.device)
    def state(**kwargs):write_json(a.out/'state.json',dict(until=a.until,source=source_hash(),**kwargs))
    save('current.pt',model,stage='initial')
    incumbent=None
    try:
        first=1+max([int(p.name.split('-')[-1]) for p in a.out.glob('round-*') if p.is_dir()],default=-1)
        for number in range(first,a.rounds+1):
            confirmed_near=False
            folder=a.out/f'round-{number:03d}';folder.mkdir(exist_ok=True)
            state(phase='validation',round=number)
            result=suite(model,manifest,folder,cache,deadline)
            if incumbent is not None and regresses(result,incumbent):
                model,_=load_policy(a.out/'current.pt');model.to(a.device)
                state(phase='candidate_rejected',round=number,result=result)
            else:
                incumbent=result;save('current.pt',model,stage='validated',round=number,validation=result)
                if result['near']:
                    confirm_folder=folder/'confirmation';confirm_folder.mkdir(exist_ok=True)
                    confirmed=suite(model,confirmation,confirm_folder,cache,deadline)
                    seen.update(c['seed'] for c in confirmation)
                    confirmed_near=confirmed['near']
                    state(phase='confirmation',round=number,result=result,confirmation=confirmed)
                    if confirmed['surpass']:
                        save('accepted.pt',model,stage='surpassed_teacher',confirmation=confirmed)
                        state(phase='accepted',round=number,result=result,confirmation=confirmed);return
                    # A consulted confirmation is no longer an untouched test.
                    # Continue training, but require a fresh seed family next time.
                    confirmation=[dict(c,seed=c['seed']+100) for c in confirmation]
            if number==a.rounds or a.smoke:break
            check_deadline(deadline);mode='ppo' if incumbent['near'] and confirmed_near else 'dagger'
            if mode=='ppo':model=with_critic(model).to(a.device)
            state(phase='collection',round=number,stage=mode)
            rows=[];streams=[]
            for j in range(2):
                storage_guard(a.out,budget=1024**3)
                seed=200000+number*2+j;seen.add(seed)
                random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
                r,s,report=rollout(model,seed,150 if number<3 else 250,mode=mode,
                    size=(20,24,28)[(number+j)%3],swap=bool(j),deadline=deadline)
                rows.extend(r);streams.extend(s);write_json(folder/f'train-{seed}.json',report)
                w=Writer(PROJECT_FINGERPRINT)
                for item in r:w.add(item)
                storage_guard(a.out,budget=1024**3)
                w.save(folder/f'world-{seed}.npz',dict(seed=seed,source=source_hash(),turns=report['turns']))
            state(phase='training',round=number,stage=mode)
            if mode=='ppo':training=ppo_update(model,streams,anchor,deadline=deadline)
            else:training=bc_update(model,replay+rows,epochs=6,deadline=deadline)
            write_json(folder/'training.json',dict(mode=mode,metrics=training));save('candidate.pt',model,stage=mode,round=number)
            for old in sorted(a.out.glob('round-*/world-*.npz'))[:-4]:old.unlink()
        state(phase='not_accepted',reason='round_limit',result=incumbent)
    except Deadline as end:state(phase='not_accepted',reason=str(end),result=incumbent)


if __name__=='__main__':main()
