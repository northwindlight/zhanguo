"""Continuous exploratory PPO, independent of validation/promotion decisions.

Each world is durably saved before updating. Rejected candidates may continue
the experimental chain; no candidate is promoted by this training process.
"""
import argparse
import fcntl
import hashlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import random
import shutil
import time

import numpy as np
import torch
from host import GameHost

from .model import load_policy
from .online import ppo_update, rollout, validate_device, with_critic
from .overnight import storage_guard, write_json
from .provenance import provenance, require_compatible
from .scenarios import config
from .reward_shaping import objective
from .trajectories import load as load_trajectory, model_digest, save as save_trajectory, verify_log_probabilities


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_model(path, model, base, seen, *,invalid_action_penalty=0.,**extra):
    saved={k:v for k,v in base.items() if k not in ('model','optimizer','rng','metrics','config')}
    saved.update(**provenance(),model={k:v.detach().cpu() for k,v in model.state_dict().items()},
        config=model.config,fingerprint=model.fingerprint,seen_seeds=sorted(seen),
        selection_seeds=[8101,8102],experimental=True,accepted=False,
        training_objective=objective(invalid_action_penalty),
        reward=dict(name='paper_S_consumption_increment',scale=1000.,gamma=1.,gae_lambda=1.),**extra)
    temporary=path.with_suffix('.tmp');torch.save(saved,temporary);temporary.replace(path)


def world_manifest(number, count, turns, seed_base):
    return [dict(seed=seed_base+number*count+j,size=(20,24,28)[j%3],swap=bool(j%2),
                 turns=turns,mode='ppo') for j in range(count)]


def world_path(folder, case):
    return folder/f"trajectory-{case['seed']}.pkl.gz"


def complete_world(folder, case, behavior):
    path=world_path(folder,case)
    if not (path.exists() and path.with_suffix('.json').exists() and (folder/f"report-{case['seed']}.json").exists()):
        return False
    metadata=json.loads(path.with_suffix('.json').read_text());require_compatible(metadata)
    report=json.loads((folder/f"report-{case['seed']}.json").read_text());require_compatible(report)
    expected=dict(case,behavior_policy_sha256=behavior,complete=True)
    if any(metadata.get(k)!=v for k,v in expected.items()):raise ValueError('World belongs to a different behavior/configuration')
    if any(report.get(k)!=v for k,v in case.items()) or report['decisions']!=metadata['steps']:
        raise ValueError('World report/configuration mismatch')
    if sha(path)!=metadata['sha256']:raise ValueError('World checksum mismatch')
    return True


def initialize_world(case):
    # Validate only the untouched starting world, before any policy decision.
    GameHost.new(config(case['seed'],size=case['size'],swap=case['swap']))


def validated_cases(cases,folder,behavior,seen):
    result=[];rejected=[];chosen=set()
    for original in cases:
        for attempt in range(32):
            case=dict(original,seed=original['seed']+attempt*1_000_000)
            if case['seed'] in seen or case['seed'] in chosen:continue
            if not complete_world(folder,case,behavior):
                try:initialize_world(case)
                except ValueError as error:
                    if str(error)!='Start overlaps existing territory':raise
                    rejected.append(dict(seed=case['seed'],reason=str(error),replacement_attempt=attempt+1))
                    continue
            result.append(case);chosen.add(case['seed']);break
        else:raise RuntimeError(f"No initializable world for seed {original['seed']}")
    return result,rejected


def collect_world(behavior_path, folder, case, device_name):
    torch.set_num_threads(1);torch.set_num_interop_threads(1)
    random.seed(case['seed']);np.random.seed(case['seed']);torch.manual_seed(case['seed'])
    folder=Path(folder)
    # Child output is independent of the parent CLI connection.
    with (folder/f"world-{case['seed']}.log").open('a',buffering=1) as output:
        import contextlib
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            model,metadata=load_policy(behavior_path);require_compatible(metadata);model.to(device_name)
            behavior=model_digest(model)
            _,streams,report=rollout(model,**case)
            write_json(folder/f"report-{case['seed']}.json",report)
            # Preserve float32 arrays and shared references; never regenerate a completed world.
            save_trajectory(world_path(folder,case),streams,dict(case,behavior_policy_sha256=behavior,
                complete=True,collection_implementation=report['implementation']))
            print(json.dumps(dict(event='world_saved',seed=case['seed'],steps=sum(map(len,streams)))),flush=True)


def prune(root, keep=2):
    completed=sorted(p for p in root.glob('round-*') if (p/'candidate.json').exists())
    for folder in completed[:-keep]:
        for path in folder.glob('trajectory-*.pkl.gz'):
            path.unlink();path.with_suffix('.json').unlink(missing_ok=True)


def configure(model,args):
    """Apply experimental switches to a loaded checkpoint.

    They live in ``model.config`` so they are saved with every checkpoint and will
    round-trip through ``load_policy``; old checkpoints simply get the historical
    defaults (soft prior 0.5, no legality mask).
    """
    model.config['exec_prior_weight']=float(args.exec_prior_weight)
    model.config['legal_mask']=bool(args.legal_mask)
    return model


def run(args):
    device=validate_device(args.device);torch.set_num_threads(args.threads)
    args.out.mkdir(parents=True,exist_ok=True)
    lock=(args.out/'run.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    initial,base=load_policy(args.checkpoint);require_compatible(base);configure(initial,args)
    settings=dict(initial_sha256=sha(args.checkpoint),worlds=args.worlds,turns=args.turns,seed_base=args.seed_base,
                  epochs=args.epochs,effective_batch=args.effective_batch,micro_batch=args.micro_batch,lr=args.lr,
                  legal_mask=bool(args.legal_mask),exec_prior_weight=float(args.exec_prior_weight),
                  advantage_mode=args.advantage_mode,gamma=float(args.gamma))
    penalty=objective(args.invalid_action_penalty)['invalid_action_penalty']
    if penalty:settings['invalid_action_penalty']=penalty
    config_path=args.out/'run.json'
    if config_path.exists():
        if json.loads(config_path.read_text())['settings']!=settings:raise ValueError('Resume configuration changed; use a new experiment directory')
    else:write_json(config_path,dict(settings=settings,**provenance(),promotion='external validation only'))
    if not (args.out/'anchor.pt').exists():shutil.copyfile(args.checkpoint,args.out/'anchor.pt')
    seen=set(base.get('seen_seeds',base.get('train_seeds',[])+base.get('valid_seeds',[])))
    source=args.checkpoint
    # Recover the last complete update, not an incomplete optimizer step.
    completed=sorted(p for p in args.out.glob('round-*') if (p/'candidate.pt').exists() and (p/'candidate.json').exists())
    number=1
    if completed:
        last=completed[-1];record=json.loads((last/'candidate.json').read_text())
        if sha(last/'candidate.pt')!=record['sha256']:raise ValueError('Candidate checksum mismatch')
        source=last/'candidate.pt';number=int(last.name.split('-')[-1])+1
        _,latest=load_policy(source);seen.update(latest.get('seen_seeds',[]))
        require_compatible(latest)
    context=mp.get_context('spawn')
    (args.out/'failure.json').unlink(missing_ok=True)
    while args.max_rounds is None or number<=args.max_rounds:
        if (args.out/'STOP').exists():break
        prune(args.out);storage_guard(args.out,budget=args.storage_mib*1024**2)
        folder=args.out/f'round-{number:06d}';folder.mkdir(exist_ok=True)
        cases=world_manifest(number,args.worlds,args.turns,args.seed_base)
        if {c['seed'] for c in cases}&seen:raise ValueError('Training seed reused')
        behavior_path=folder/'behavior.pt'
        if behavior_path.exists():model,metadata=load_policy(behavior_path);require_compatible(metadata);configure(model,args)
        else:
            torch.manual_seed(410000+number)
            model,metadata=load_policy(source);configure(model,args);model=with_critic(model)
            save_model(behavior_path,model,metadata,seen,round=number,stage='behavior',invalid_action_penalty=penalty)
        behavior=model_digest(model)
        cases,rejected=validated_cases(cases,folder,behavior,seen)
        write_json(folder/'manifest.json',dict(cases=cases,behavior_policy_sha256=behavior,settings=settings,
                                             rejected_initializations=rejected))
        if rejected:print(json.dumps(dict(event='invalid_starting_worlds',rejected=rejected,cases=cases)),flush=True)
        running={};attempts={c['seed']:0 for c in cases}
        while True:
            done=[c for c in cases if complete_world(folder,c,behavior)]
            for seed,process in list(running.items()):
                if not process.is_alive():
                    process.join()
                    if process.exitcode and not any(c['seed']==seed for c in done):
                        if attempts[seed]>=3:raise RuntimeError(f'World worker repeatedly failed: {seed}')
                    del running[seed]
            if len(done)==len(cases):break
            done_seeds={c['seed'] for c in done}
            for case in cases:
                if len(running)>=args.workers:break
                if case['seed'] in done_seeds or case['seed'] in running:continue
                storage_guard(args.out,budget=args.storage_mib*1024**2)
                process=context.Process(target=collect_world,args=(str(behavior_path),str(folder),case,str(device)))
                process.start();running[case['seed']]=process;attempts[case['seed']]+=1
            write_json(args.out/'state.json',dict(phase='collection',round=number,completed_worlds=len(done),worlds=len(cases),
                       worker_pids={str(seed):p.pid for seed,p in running.items()},at=time.time(),accepted=False))
            time.sleep(5)
        for process in running.values():process.join()
        streams=[]
        for case in cases:
            streams.extend(load_trajectory(world_path(folder,case),dict(case,behavior_policy_sha256=behavior,complete=True)))
        seen.update(c['seed'] for c in cases)
        model.to(device);anchor,_=load_policy(args.out/'anchor.pt');configure(anchor,args);anchor.to(device)
        error=verify_log_probabilities(model,streams)
        random.seed(610000+number);torch.manual_seed(610000+number)
        write_json(args.out/'state.json',dict(phase='training',round=number,worlds=len(cases),
                   steps=sum(map(len,streams)),behavior_logp_max_error=error,at=time.time(),accepted=False))
        with (folder/'updates.jsonl').open('a',buffering=1) as output:
            def observe(record):
                output.write(json.dumps(record)+'\n')
                if record['updates']%20==0:
                    write_json(args.out/'state.json',dict(phase='training',round=number,at=time.time(),accepted=False,**record))
            def progress(record):
                save_model(args.out/'progress.pt',model,metadata,seen,round=number,stage='partial',progress=record,
                           invalid_action_penalty=penalty)
            training=ppo_update(model,streams,anchor,epochs=args.epochs,lr=args.lr,batch_size=args.micro_batch,
                       update_batch_size=args.effective_batch,kl_mode='adaptive',diagnostic_callback=observe,
                       checkpoint_callback=progress,invalid_action_penalty=penalty,
                       advantage_mode=args.advantage_mode,gamma=float(args.gamma))
        if training['coverage']!=1. or training['epochs_completed']!=args.epochs:
            raise RuntimeError('Incomplete PPO update cannot advance the experiment')
        training['behavior_logp_max_error']=error
        save_model(folder/'candidate.pt',model,metadata,seen,round=number,stage='exploratory_ppo',ppo_round=training,
                   invalid_action_penalty=penalty)
        write_json(folder/'candidate.json',dict(round=number,sha256=sha(folder/'candidate.pt'),
                   policy_sha256=model_digest(model),metrics=training,accepted=False))
        shutil.copyfile(folder/'candidate.pt',args.out/'train-current.tmp')
        (args.out/'train-current.tmp').replace(args.out/'train-current.pt')
        write_json(args.out/'state.json',dict(phase='round_complete',round=number,at=time.time(),accepted=False,metrics=training))
        print(json.dumps(dict(event='ppo_round_complete',round=number,metrics=training)),flush=True)
        source=folder/'candidate.pt';number+=1
        del model,anchor,streams
        if device.type=='cuda':torch.cuda.empty_cache()
    write_json(args.out/'state.json',dict(phase='stopped',round=number,at=time.time(),accepted=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--device',default='cuda');p.add_argument('--threads',type=int,default=1)
    p.add_argument('--worlds',type=int,default=8);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--turns',type=int,default=150);p.add_argument('--seed-base',type=int,default=800000)
    p.add_argument('--epochs',type=int,default=1);p.add_argument('--effective-batch',type=int,default=128)
    p.add_argument('--micro-batch',type=int,default=8);p.add_argument('--lr',type=float,default=3e-5)
    p.add_argument('--invalid-action-penalty',type=float,default=0.,
                   help='Update-time penalty per failed project in delta-S/1000 units; disabled by default')
    p.add_argument('--legal-mask',action='store_true',
                   help='Extra executability mask: restrict sampled/greedy actions to financeable projects')
    p.add_argument('--exec-prior-weight',type=float,default=.5,
                   help='Weight of the learned executability soft prior (0.5 = historical behaviour)')
    p.add_argument('--advantage-mode',choices=('mc','td'),default='mc',
                   help='mc = historical constant-per-period advantage; td = per-decision r+V(s_next)-V(s)')
    p.add_argument('--gamma',type=float,default=1.,help='Discount used by td advantage')
    p.add_argument('--storage-mib',type=int,default=1024);p.add_argument('--max-rounds',type=int)
    a=p.parse_args()
    if min(a.threads,a.worlds,a.workers,a.turns,a.epochs,a.effective_batch,a.micro_batch)<1:p.error('positive sizes required')
    if a.exec_prior_weight<0:p.error('exec prior weight must be nonnegative')
    if not 0.<a.gamma<=1.:p.error('gamma must be in (0,1]')
    try:run(a)
    except BaseException as error:
        a.out.mkdir(parents=True,exist_ok=True)
        write_json(a.out/'failure.json',dict(error=repr(error),at=time.time(),accepted=False))
        raise


if __name__=='__main__':main()
