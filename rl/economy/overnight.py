"""Bounded autonomous BC/DAgger/PPO training with measured promotion and rollback.

Fixed gate worlds are validation, never claimed as final holdouts. Only an
accepted checkpoint becomes current. Each iteration journals its choice,
training/evaluation metrics and exact seeds. No service/game outside this run
is touched; generated replay and candidate checkpoints have bounded retention.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import random
import shutil
import signal
import time

import numpy as np
import torch

from .adapter import PROJECT_FINGERPRINT
from .data import Dataset,Writer
from .evaluate import run as evaluate
from .model import load_policy
from .online import Deadline,check_deadline,rollout,bc_update,with_critic,ppo_update,S_SCALE
from .scenarios import source_hash


GATE_SEEDS=(7101,7102)


def curriculum_turns(round_number,*,smoke=False):
    if smoke:return 8
    return 150 if round_number<=3 else 200 if round_number<=7 else 250


def storage_guard(root,*,budget=1024**3,headroom=128*1024**2,reserve=1024**3):
    used=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    free=shutil.disk_usage(root).free
    if used+headroom>budget:raise Deadline('Run storage budget reached; preserving models and evaluations')
    if free<reserve+headroom:raise Deadline('Disk reserve reached; preserving checkpoints')
    return dict(run_bytes=used,free_bytes=free,budget_bytes=budget,write_headroom_bytes=headroom)


def write_json(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def gate_summary(results):
    total=Counter();rows=[]
    for r in results:
        for actor,c in r['history'][-1]['countries'].items():
            if c['version'] not in ('v13plus','economy-project-bc'):continue
            # In a learner case its opponents may still be v13; the focal
            # version above is unique, so no other country's private score leaks.
            m=r['milestones'].get(actor,{})
            early=m.get('first_recruit',math.inf)<=8 and m.get('first_expansion',math.inf)<=12
            row=dict(actor=actor,seed=r['seed'],swap=r['swap'],early=early,
                first_recruit=m.get('first_recruit'),first_expansion=m.get('first_expansion'),
                # Paper S: cumulative actual consumption (recruit+military+admin).
                # tail_consumption is the mean per-turn S increment in the last 50.
                consumption=r['consumption'].get(actor,0),
                tail_consumption=r['tail_consumption'].get(actor,0),
                assets=c['report'].get('net_assets',{}).get('total',0) or 0,
                late_economy=r.get('late_economy',{}).get(actor,{}),
                land=c['land'],army=c['army'],failures=c['project_failures']+sum(c['failures'].values()),
                truncated=c['truncated'],shortfall=r['supply_shortfalls'].get(actor,0),
                decisions=sum(c['economic_actions'].values())+r['turns'])
            rows.append(row)
            total.update({k:v for k,v in row.items() if k in ('consumption','tail_consumption','failures','truncated','shortfall','decisions')})
            total['early']+=int(early);total['n']+=1
    return dict(total,early_rate=total['early']/max(1,total['n']),failure_rate=total['failures']/max(1,total['decisions']),countries=rows)


def _seat_key(row):return (row['actor'],row['seed'],row['swap'])
def _seat_pairs(capability,reference):
    """Per-seat S comparison; sorted by seat for deterministic reporting."""
    ref={_seat_key(r):r for r in reference['countries']}
    rows=[]
    for r in capability['countries']:
        other=ref.get(_seat_key(r))
        if other is None:return None
        rows.append((r['consumption'],other['consumption'],r['tail_consumption'],other['tail_consumption']))
    return rows
def _ratio(candidate,reference):
    return candidate/reference if reference>0 else (1.0 if candidate>=reference else float('-inf'))


def choose_stage(capability,teacher,agreement):
    if capability['early_rate']<1:return 'bc'
    reliable=(capability['failure_rate']<=.002 and capability['truncated']==0 and capability['shortfall']==0)
    pairs=_seat_pairs(capability,teacher)
    ratios=sorted(_ratio(c,t) for c,t,_,_ in pairs) if pairs else []
    tail_ratios=sorted(_ratio(c,t) for _,_,c,t in pairs) if pairs else []
    median=lambda values:values[len(values)//2] if values else float('-inf')
    ready=(reliable and agreement>=.9 and pairs is not None and
           median(ratios)>=.95 and _ratio(capability['consumption'],teacher['consumption'])>=.95 and
           median(tail_ratios)>=.9)
    return 'ppo' if ready else 'dagger'


def accept(candidate,incumbent):
    reasons=[]
    if candidate['early_rate']<incumbent['early_rate']:reasons.append('opening_regression')
    if candidate['failure_rate']>max(.002,incumbent['failure_rate']):reasons.append('invalid_actions')
    if candidate['truncated']>incumbent['truncated']:reasons.append('action_cap')
    if candidate['shortfall']>incumbent['shortfall']:reasons.append('supply_regression')
    pairs=_seat_pairs(candidate,incumbent)
    if pairs is None:reasons.append('missing_seat')
    else:
        for c,t,tc,tt in pairs:
            if c<.95*t:reasons.append('seat_consumption_regression')
            if tc<.95*tt:reasons.append('seat_tail_regression')
    if candidate['consumption']<incumbent['consumption']:reasons.append('consumption_regression')
    return not reasons,reasons


def checkpoint(path,model,base,seen,**extra):
    saved={k:v for k,v in base.items() if k not in ('model','optimizer','rng','metrics','config')}
    saved.update(model=model.state_dict(),config=model.config,fingerprint=PROJECT_FINGERPRINT,
        source=source_hash(),seen_seeds=sorted(seen),online=extra,
        reward=dict(name='paper_S_consumption_increment',scale=S_SCALE,gamma=1.,gae_lambda=1.))
    tmp=path.with_suffix('.tmp');torch.save(saved,tmp);tmp.replace(path)


def gates(model,turns,out,deadline,*,prefix,guard=lambda:None):
    rows=[]
    for i,seed in enumerate(GATE_SEEDS):
        path=out/f'{prefix}-{turns}-{seed}.json'
        if path.exists():r=json.loads(path.read_text())
        else:
            guard()
            r=evaluate(seed,turns,size=24 if i==0 else 28,swap=bool(i),model=model,deadline=deadline)
            write_json(path,r)
        rows.append(r)
    return gate_summary(rows)


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--teacher-data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--until',required=True,help='ISO datetime with explicit UTC offset')
    p.add_argument('--threads',type=int,default=2);p.add_argument('--max-rounds',type=int,default=100)
    p.add_argument('--device',default='cpu',help='训练（BC/PPO 更新）所用设备；对局 rollout 始终在 CPU')
    p.add_argument('--max-candidates',type=int,default=384,
                   help='BC 每行候选上限（教师选项必留 + 随机负样本）；0 = 不截断')
    p.add_argument('--max-run-mb',type=int,default=1024)
    p.add_argument('--smoke',action='store_true',help='Short execution check; never promotes a model')
    a=p.parse_args();end=datetime.fromisoformat(a.until)
    if end.tzinfo is None:raise ValueError('An explicit timezone is required')
    deadline=end.timestamp()
    if time.time()>=deadline:return
    a.out.mkdir(parents=True,exist_ok=True)
    if a.max_run_mb<256:raise ValueError('Run budget must leave at least 256 MiB')
    guard=lambda:storage_guard(a.out,budget=a.max_run_mb*1024**2)
    import resource
    _,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    file_limit=128*1024**2 if hard==resource.RLIM_INFINITY else min(hard,128*1024**2)
    resource.setrlimit(resource.RLIMIT_FSIZE,(file_limit,hard))
    signal.signal(signal.SIGXFSZ,lambda *_:(_ for _ in ()).throw(Deadline('128 MiB single-file limit reached')))
    # Prevent two systemd/manual invocations from training into the same run.
    import fcntl
    lock=(a.out/'run.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(Deadline('Service stop requested')))
    signal.signal(signal.SIGINT,lambda *_:(_ for _ in ()).throw(Deadline('Interrupted')))
    torch.set_num_threads(a.threads)
    device=torch.device(a.device)
    if device.type=='cuda' and not torch.cuda.is_available():raise ValueError('--device cuda requested but CUDA is unavailable')
    model,base=load_policy(a.out/'current.pt' if (a.out/'current.pt').exists() else a.checkpoint)
    if model.action_mode!='projects':raise ValueError('Overnight job requires project BC')
    if base.get('source')!=source_hash():raise ValueError('Checkpoint source does not match the current feature contract; retrain')
    anchor,_=load_policy(a.checkpoint);anchor.eval()
    seen=set(base.get('seen_seeds',base['train_seeds']+base['valid_seeds']))|set(GATE_SEEDS)
    teacher_paths=[x for x in a.teacher_data.glob('world-*.npz') if int(x.stem.split('-')[-1]) in base['train_seeds']]
    initial=Dataset(teacher_paths,fingerprint=PROJECT_FINGERPRINT,max_candidates=a.max_candidates)
    if any(m['source']!=source_hash() for m in initial.metadata):
        raise ValueError('Teacher data source does not match the current feature contract; recollect')
    # Fixed initial BC demonstrations protect the opening in later DAgger updates.
    original_rows=[initial[i] for i in range(len(initial))]
    old_state=json.loads((a.out/'state.json').read_text()) if (a.out/'state.json').exists() else {}
    if old_state and old_state.get('source')!=source_hash():raise ValueError('Resume requires the same source; use a new run directory')
    agreement=old_state.get('agreement',0.);stage='dagger';incumbent=None;errors=0
    completed=max([0]+[int(x.name.split('-')[-1]) for x in a.out.glob('round-*') if x.is_dir()])
    def event(kind,**values):
        entry=dict(time=datetime.now().astimezone().isoformat(),kind=kind,**values)
        with (a.out/'events.jsonl').open('a') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
        print(json.dumps(entry,ensure_ascii=False),flush=True)
    def state(phase,**values):write_json(a.out/'state.json',dict(phase=phase,until=a.until,round=completed,agreement=agreement,source=source_hash(),**values))
    checkpoint(a.out/'current.pt',model,base,seen,stage='initial',parent=str(a.checkpoint))
    if not (a.out/'best.pt').exists():shutil.copy2(a.out/'current.pt',a.out/'best.pt')
    event('start',until=a.until,checkpoint=str(a.checkpoint),smoke=a.smoke,storage=guard())
    try:
        for number in range(completed+1,a.max_rounds+1):
            check_deadline(deadline)
            guard()
            folder=a.out/f'round-{number:04d}';folder.mkdir();completed=number
            eval_turns=curriculum_turns(number,smoke=a.smoke)
            state('capability',stage=stage,turns=eval_turns)
            torch.set_num_threads(1)
            teacher=gates(None,eval_turns,a.out,deadline,prefix='teacher',guard=guard)
            # Reuse the incumbent only at the same horizon; after promotion its
            # candidate gate is already the current gate for the next round.
            if incumbent is None or incumbent.get('turns')!=eval_turns:
                incumbent=gates(model,eval_turns,folder,deadline,prefix='incumbent',guard=guard);incumbent['turns']=eval_turns
            stage=choose_stage(incumbent,teacher,agreement)
            if a.smoke:stage='dagger'
            event('stage',round=number,stage=stage,agreement=agreement,capability=incumbent,teacher=teacher)
            random.seed(number);np.random.seed(number);torch.manual_seed(number)
            candidate=with_critic(model) if stage=='ppo' else load_policy(a.out/'current.pt')[0]
            if candidate is model:
                # An existing actor-critic must not mutate the accepted model.
                candidate=load_policy(a.out/'current.pt')[0]
            collection=[];trajectories=[];reports=[]
            try:
                state('collection',stage=stage)
                for j in range(1 if a.smoke else 2):
                    guard()
                    seed=100000+number*2+j;seen.add(seed)
                    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
                    rows,streams,report=rollout(candidate,seed,6 if a.smoke else eval_turns,mode=stage,
                        size=(20,24,28)[seed%3],swap=bool(j),deadline=deadline)
                    collection.extend(rows);trajectories.extend(streams);reports.append(report)
                    # PPO observations retain float32 in memory; only the BC
                    # replay copy is compressed to float16 on disk.
                    w=Writer(PROJECT_FINGERPRINT)
                    for row in rows:w.add(row)
                    guard()
                    w.save(folder/f'world-{seed}.npz',dict(seed=seed,source=source_hash(),mode=stage,turns=report['turns']))
                    write_json(folder/f'world-{seed}.json',report)
                agreement=sum(r['family_agreement']*r['decisions'] for r in reports)/max(1,sum(r['decisions']for r in reports))
                state('training',stage=stage);torch.set_num_threads(a.threads)
                # 只有更新这一步上 GPU：对局 rollout 与能力门是引擎模拟，纯 CPU。
                candidate.to(device)
                try:
                    if stage=='ppo':training=ppo_update(candidate,trajectories,anchor,epochs=1 if a.smoke else 2,deadline=deadline)
                    else:
                        paths=sorted(a.out.glob('round-*/world-*.npz'))[-4:]
                        replay=Dataset(paths,fingerprint=PROJECT_FINGERPRINT,max_candidates=a.max_candidates)
                        seen.update(m['seed'] for m in replay.metadata)
                        training=bc_update(candidate,original_rows+[replay[i]for i in range(len(replay))],epochs=1 if a.smoke else 3,deadline=deadline)
                finally:
                    candidate.to('cpu')
                guard();checkpoint(a.out/'candidate.pt',candidate,base,seen,stage=stage,round=number,training=training)
                write_json(folder/'training.json',dict(stage=stage,training=training,agreement=agreement))
                state('validation',stage=stage);torch.set_num_threads(1)
                score=gates(candidate,eval_turns,folder,deadline,prefix='candidate',guard=guard);score['turns']=eval_turns
                accepted,reasons=accept(score,incumbent)
                if a.smoke:accepted=False;reasons=['smoke_only']
                event('decision',round=number,stage=stage,accepted=accepted,reasons=reasons,score=score,agreement=agreement)
                if accepted:
                    model=candidate;incumbent=score
                    checkpoint(a.out/'current.pt',model,base,seen,stage=stage,round=number,gate=score)
                    shutil.copy2(a.out/'current.pt',a.out/'accepted.pt')
                    shutil.copy2(a.out/'current.pt',a.out/'best.pt')
                errors=0;state('round_complete',stage=stage,accepted=accepted,capability=incumbent)
                # Only this run's replay shards are pruned; logs/metrics remain.
                for stale in sorted(a.out.glob('round-*/world-*.npz'))[:-4]:stale.unlink()
            except Deadline:raise
            except Exception as error:
                errors+=1;event('round_failed',round=number,error=repr(error));state('recovering',error=repr(error))
                if errors>=3:raise
                model,_=load_policy(a.out/'current.pt');agreement=0.
            if a.smoke:break
        state('complete',reason='round_limit');event('complete',reason='round_limit')
    except Deadline as stop:
        state('complete',reason=str(stop));event('complete',reason=str(stop))
    except BaseException as error:
        state('failed',error=repr(error));event('failed',error=repr(error));raise
    finally:
        write_json(a.out/'summary.json',dict(until=a.until,round=completed,stage=stage,
            checkpoint=str(a.out/'best.pt'),capability=incumbent,agreement=agreement,
            state=json.loads((a.out/'state.json').read_text())))


if __name__=='__main__':main()
