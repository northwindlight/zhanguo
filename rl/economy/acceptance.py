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
from .online import Deadline,check_deadline,rollout,bc_update,with_critic,ppo_update,validate_device
from .overnight import storage_guard,write_json,checkpoint
from .scenarios import source_hash
from .provenance import provenance,contracts,require_compatible
from .trajectories import model_digest,save as save_trajectory,load as load_trajectory,verify_log_probabilities


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
    policy_hash=model_digest(model)
    for i,case in enumerate(manifest):
        check_deadline(deadline);storage_guard(out.parent,budget=1024**3)
        key=f"{case['seed']}-{case['size']}-{case['turns']}-{int(case['swap'])}"
        teacher_path=cache/f'{key}.json'
        write_json(out/'state.json',dict(phase='teacher',case=case,index=i,total=len(manifest)))
        if teacher_path.exists():
            teacher=json.loads(teacher_path.read_text())
            if require_compatible(teacher)['teacher_contract']!=contracts()['teacher_contract']:raise ValueError('Teacher policy contract mismatch')
        else:
            teacher=run(**case,deadline=deadline);write_json(teacher_path,teacher)
        write_json(out/'state.json',dict(phase='student',case=case,index=i,total=len(manifest)))
        student_path=out/f'{key}-student-{policy_hash[:12]}.json'
        if student_path.exists():
            student=json.loads(student_path.read_text());require_compatible(student)
            if student.get('behavior_policy_sha256')!=policy_hash:raise ValueError('Student cache policy mismatch')
        else:
            student=run(**case,model=model,deadline=deadline)
            student['behavior_policy_sha256']=policy_hash;write_json(student_path,student)
        result=dict(**case,complete=True,evaluated_turns=case['turns'],**compare(teacher,student));results.append(result)
        write_json(out/'summary.json',summary(results))
        print(json.dumps(dict(event='case',**result),ensure_ascii=False),flush=True)
        # One completed long-world counterexample disproves the conjunction.
        # Do not spend the rest of the suite before collecting corrections.
        # Passing still requires every environment and fresh confirmation.
        if not result['near']:break
    return summary(results)


def regresses(new,old):
    if len(new['results'])<len(old['results']):return True
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


def aggregated_replay(original,root):
    """Keep prior on-policy corrections, rather than forgetting each round."""
    paths=sorted(root.glob('round-*/world-*.npz'))[-4:]
    if not paths:return original,set()
    dataset=Dataset(paths,fingerprint=PROJECT_FINGERPRINT)
    return original+[dataset[i] for i in range(len(dataset))],{m['seed'] for m in dataset.metadata}


def training_stage(near,confirmed_near,explore_ppo=False):
    return 'ppo' if explore_ppo or (near and confirmed_near) else 'dagger'


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--device',default='cpu');p.add_argument('--threads',type=int,default=1)
    p.add_argument('--until',required=True);p.add_argument('--rounds',type=int)
    p.add_argument('--smoke',action='store_true',help='Real short collection/update/validation; never promotes')
    p.add_argument('--explore-ppo',action='store_true');p.add_argument('--warmup-data',type=Path)
    p.add_argument('--continue-selection',action='store_true')
    p.add_argument('--ppo-execution-coef',type=float,default=.1)
    p.add_argument('--ppo-effective-batch',type=int,default=128);p.add_argument('--ppo-micro-batch',type=int,default=8)
    p.add_argument('--ppo-kl-mode',choices=('adaptive','legacy'),default='adaptive')
    a=p.parse_args()
    if a.threads<1:p.error('--threads must be positive')
    if a.rounds is None:a.rounds=1 if a.smoke else 20
    if a.rounds<0:p.error('--rounds must be nonnegative')
    a.device=validate_device(a.device);torch.set_num_threads(a.threads)
    end=datetime.fromisoformat(a.until)
    if end.tzinfo is None:raise ValueError('Explicit timezone required')
    deadline=end.timestamp();check_deadline(deadline)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(Deadline('Service stop requested')))
    a.out.mkdir(parents=True,exist_ok=True);cache=a.out/'teachers';cache.mkdir(exist_ok=True)
    import resource,fcntl
    _,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE,(min(hard,128*1024**2) if hard!=resource.RLIM_INFINITY else 128*1024**2,hard))
    signal.signal(signal.SIGXFSZ,lambda *_:(_ for _ in ()).throw(Deadline('Single file storage limit reached')))
    lock=(a.out/'run.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    resuming=(a.out/'current.pt').exists()
    previous=json.loads((a.out/'state.json').read_text()) if resuming and (a.out/'state.json').exists() else {}
    if previous:
        require_compatible(previous)
        if 'smoke' in previous and previous['smoke']!=a.smoke:raise ValueError('Smoke and formal runs must use separate directories')
        if a.warmup_data is None and previous.get('warmup_data'):a.warmup_data=Path(previous['warmup_data'])
    model,base=load_policy(a.out/'current.pt' if resuming else a.checkpoint);require_compatible(base)
    incumbent=base.get('online',{}).get('validation') if resuming else None
    ancestry=set(base.get('seen_seeds',base['train_seeds']+base['valid_seeds']))|set(previous.get('seen_seeds',[]))
    phase=previous.get('resume_phase',previous.get('phase'))
    retry_training=phase in ('collection','training')
    pending=phase=='candidate_ready' or (phase=='validation' and previous.get('pending_candidate',False))
    first=1+max([int(q.name.split('-')[-1])for q in a.out.glob('round-*')if q.is_dir()],default=-1)
    if retry_training:first=int(previous['round'])
    elif phase=='validation':first=int(previous['round'])
    elif pending:first=int(previous['round'])+1
    if pending:
        model,candidate=load_policy(a.out/'candidate.pt');require_compatible(candidate)
        ancestry.update(candidate.get('seen_seeds',[]))
    model.to(a.device);anchor,anchor_metadata=load_policy(a.checkpoint);require_compatible(anchor_metadata);anchor.to(a.device)
    manifest=cases(smoke=a.smoke);confirmation=cases(confirm=True,smoke=a.smoke)
    if a.smoke:manifest=manifest[:1];confirmation=confirmation[:1]
    if not resuming and not a.continue_selection and ancestry&{c['seed']for c in manifest}:raise ValueError('Selection seeds overlap model ancestry')
    while ancestry&{c['seed']for c in confirmation}:confirmation=[dict(c,seed=c['seed']+100)for c in confirmation]
    seen=ancestry|{c['seed']for c in manifest}
    data=Dataset([q for q in a.data.glob('world-*.npz')if int(q.stem.split('-')[-1])in base['train_seeds']],fingerprint=PROJECT_FINGERPRINT)
    replay=[data[i]for i in range(len(data))]
    latest={};last_decision=None
    ppo_options=dict(kl_mode=a.ppo_kl_mode,update_batch_size=a.ppo_effective_batch,batch_size=a.ppo_micro_batch,
        execution_coef=a.ppo_execution_coef,epochs=1 if a.smoke else 2)
    def save(name,m,**extra):
        storage_guard(a.out,budget=1024**3)
        m.to('cpu');checkpoint(a.out/name,m,base,seen,**extra);m.to(a.device)
    def state(**values):
        nonlocal latest
        latest=dict(provenance(),until=a.until,smoke=a.smoke,seen_seeds=sorted(seen),ppo_options=ppo_options,
            warmup_data=str(a.warmup_data) if a.warmup_data is not None else None,
            runtime=dict(device=str(a.device),threads=a.threads),**values)
        write_json(a.out/'state.json',latest)
    def event(kind,**values):
        with (a.out/'events.jsonl').open('a',buffering=1) as f:f.write(json.dumps(dict(time=time.time(),kind=kind,**values))+'\n')
    def finish(status,**values):
        state(phase=status,last_decision=last_decision,**values)
        write_json(a.out/'summary.json',dict(status=status,accepted=False,near=False if a.smoke else bool(incumbent and incumbent['near']),
            result=incumbent,last_decision=last_decision,**values))
    if not resuming:save('current.pt',model,stage='initial')
    number=first
    try:
        for number in range(first,a.rounds+1):
            folder=a.out/f'round-{number:03d}';folder.mkdir(exist_ok=True)
            confirmed_near=False
            if not (retry_training and number==first):
                state(phase='validation',round=number,pending_candidate=pending)
                result=suite(model,manifest,folder,cache,deadline)
                write_json(a.out/'summary.json',dict(status='validation',accepted=False,result=result))
                if incumbent is not None and regresses(result,incumbent):
                    model,_=load_policy(a.out/'current.pt');model.to(a.device);last_decision='candidate_rejected'
                    event('candidate_rejected',round=number,result=result)
                else:
                    incumbent=result;save('current.pt',model,stage='validated',round=number,validation=result)
                    last_decision='candidate_retained';event(last_decision,round=number,result=result)
                    if result['near'] and not a.smoke:
                        confirm_folder=folder/'confirmation';confirm_folder.mkdir(exist_ok=True)
                        confirmed=suite(model,confirmation,confirm_folder,cache,deadline)
                        seen.update(c['seed']for c in confirmation);confirmed_near=confirmed['near']
                        state(phase='confirmation',round=number,result=result,confirmation=confirmed,pending_candidate=False)
                        if confirmed['surpass']:
                            save('accepted.pt',model,stage='surpassed_teacher',confirmation=confirmed)
                            state(phase='accepted',round=number,result=result,confirmation=confirmed)
                            write_json(a.out/'summary.json',dict(status='accepted',accepted=True,result=result,confirmation=confirmed));return
                        confirmation=[dict(c,seed=c['seed']+100)for c in confirmation]
                pending=False
            if number==a.rounds:break
            check_deadline(deadline)
            mode=(previous.get('stage') if retry_training and number==first else
                training_stage(incumbent['near'],confirmed_near,a.explore_ppo))
            warmup=a.warmup_data is not None and number==0 and not resuming
            if warmup:mode='dagger_warmup'
            warmup=mode=='dagger_warmup'
            state(phase='collection',round=number,stage=mode,pending_candidate=False)
            behavior=folder/'behavior.pt'
            if behavior.exists():
                model,metadata=load_policy(behavior);require_compatible(metadata);model.to(a.device)
            else:
                if mode=='ppo':
                    torch.manual_seed(400000+number);model=with_critic(model).to(a.device)
                save(str(behavior.relative_to(a.out)),model,stage='behavior',round=number,mode=mode)
            behavior_hash=model_digest(model)
            rows=[];streams=[]
            if warmup:
                warm=Dataset(list(a.warmup_data.glob('world-*.npz')),fingerprint=PROJECT_FINGERPRINT)
                warm_seeds={m['seed']for m in warm.metadata}
                if warm_seeds&{c['seed']for c in manifest+confirmation}:raise ValueError('Warmup contains acceptance seeds')
                seen.update(warm_seeds);rows=[warm[i]for i in range(len(warm))]
            else:
                for j in range(1 if a.smoke else 2):
                    storage_guard(a.out,budget=1024**3)
                    seed=200000+number*2+j;seen.add(seed)
                    turns=8 if a.smoke else 150 if number<3 else 250
                    size=(20,24,28)[(number+j)%3];swap=bool(j)
                    expected=dict(seed=seed,turns=turns,size=size,swap=swap,mode=mode,behavior_policy_sha256=behavior_hash)
                    report_path=folder/f'train-{seed}.json';trajectory_path=folder/f'trajectory-{seed}.pkl.gz';data_path=folder/f'world-{seed}.npz'
                    if mode=='ppo' and trajectory_path.exists() and trajectory_path.with_suffix('.json').exists() and report_path.exists():
                        s=load_trajectory(trajectory_path,expected);streams.extend(s)
                        event('trajectory_reused',round=number,seed=seed)
                        continue
                    if mode!='ppo' and data_path.exists() and report_path.exists():
                        cached=Dataset([data_path],fingerprint=PROJECT_FINGERPRINT)
                        if any(cached.metadata[0].get(k)!=v for k,v in expected.items()):raise ValueError('DAgger behavior/config mismatch')
                        rows.extend(cached[i]for i in range(len(cached)));event('demonstrations_reused',round=number,seed=seed);continue
                    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
                    r,s,report=rollout(model,seed,turns,mode=mode,size=size,swap=swap,deadline=deadline)
                    write_json(report_path,report)
                    if mode=='ppo':
                        streams.extend(s);save_trajectory(trajectory_path,s,dict(**expected,complete=True))
                    else:
                        rows.extend(r);writer=Writer(PROJECT_FINGERPRINT)
                        for item in r:writer.add(item)
                        writer.save(data_path,expected)
            state(phase='training',round=number,stage=mode,pending_candidate=False)
            random.seed(600000+number);torch.manual_seed(600000+number)
            if mode=='ppo':
                error=verify_log_probabilities(model,streams)
                with (folder/'ppo-updates.jsonl').open('a',buffering=1) as log:
                    def observe(detail):log.write(json.dumps(dict(implementation=source_hash(),**detail))+'\n')
                    training=ppo_update(model,streams,anchor,deadline=deadline,diagnostic_callback=observe,
                        checkpoint_callback=lambda metrics:save('progress.pt',model,stage='ppo_partial',round=number,metrics=metrics),**ppo_options)
                training['behavior_logp_max_error']=error
            else:
                if warmup:training_rows=replay+rows
                else:
                    training_rows,replay_seeds=aggregated_replay(replay,a.out);seen.update(replay_seeds)
                if a.smoke and len(training_rows)>256:
                    # Exercise the real optimizer and full-candidate batches,
                    # while keeping a lifecycle smoke bounded. Formal runs use
                    # the entire replay without this diagnostic subsample.
                    training_rows=[training_rows[i]for i in np.linspace(0,len(training_rows)-1,256,dtype=int)]
                training=bc_update(model,training_rows,epochs=1 if a.smoke else 6,deadline=deadline)
            write_json(folder/'training.json',dict(mode=mode,metrics=training,parameters=ppo_options if mode=='ppo' else
                dict(epochs=1 if a.smoke else 6,training_rows=len(training_rows),smoke_row_limit=256 if a.smoke else None)))
            save('candidate.pt',model,stage=mode,round=number,parameters=ppo_options if mode=='ppo' else {})
            state(phase='candidate_ready',round=number,stage=mode,pending_candidate=True)
            pending=True;retry_training=False
            for pattern in ('round-*/world-*.npz','round-*/trajectory-*.pkl.gz'):
                for old in sorted(a.out.glob(pattern))[:-4]:
                    old.unlink()
                    if old.suffix=='.gz':old.with_suffix('.json').unlink(missing_ok=True)
        finish('smoke_complete' if a.smoke else 'not_accepted',reason='round_limit',round=number,pending_candidate=pending)
    except Deadline as error:
        finish('not_accepted',reason=str(error),round=number,resume_phase=latest.get('phase'),stage=latest.get('stage'),pending_candidate=latest.get('pending_candidate',False))
    except Exception as error:
        finish('failed',reason=f'{type(error).__name__}: {error}',round=number,resume_phase=latest.get('phase'),stage=latest.get('stage'),pending_candidate=latest.get('pending_candidate',False))
        raise


if __name__=='__main__':main()
