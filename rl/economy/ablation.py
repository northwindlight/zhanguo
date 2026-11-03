"""Controlled PPO sensitivity on identical, complete, locally generated rollouts.

These are diagnostic candidates. No arm is promoted, and frozen-state drift is
never used as a substitute for full long-game evaluation.
"""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import pickle
import random

import numpy as np
import torch

from .adapter import PROJECT_FINGERPRINT
from .data import Dataset
from .model import load_policy,collate,prepare,device_of
from .online import ppo_update,monte_carlo
from .overnight import checkpoint,storage_guard,write_json
from .provenance import require_compatible,provenance


ARMS={
    'default':{},
    'lr_1e5':dict(lr=1e-5),
    'no_value':dict(value_coef=0.),
    'value_encoder_detached':dict(detach_value_encoder=True),
    'no_execution':dict(execution_coef=0.),
    'execution_03':dict(execution_coef=.3),
    'execution_1':dict(execution_coef=1.),
    'no_anchor':dict(anchor_coef=0.),
    'anchor_all_periods':dict(anchor_periods=10000),
    'no_entropy':dict(entropy_coef=0.),
    'clip_01':dict(clip_ratio=.1),
    'kl_001':dict(target_kl=.01),
    'one_epoch':dict(epochs=1),
    'no_weight_decay':dict(weight_decay=0.),
    'grad_clip_01':dict(max_grad_norm=.1),
    'batch_32':dict(batch_size=32),
    # Separate component isolation; unlike the arms above these change several settings.
    'policy_only':dict(value_coef=0.,execution_coef=0.,anchor_coef=0.,entropy_coef=0.,weight_decay=0.),
    'value_only':dict(policy_coef=0.,execution_coef=0.,anchor_coef=0.,entropy_coef=0.,weight_decay=0.),
    'execution_only':dict(policy_coef=0.,value_coef=0.,anchor_coef=0.,entropy_coef=0.,weight_decay=0.),
    'anchor_only':dict(policy_coef=0.,value_coef=0.,execution_coef=0.,entropy_coef=0.,weight_decay=0.),
    'entropy_only':dict(policy_coef=0.,value_coef=0.,execution_coef=0.,anchor_coef=0.,weight_decay=0.),
}


def load_streams(root):
    streams=[];manifests=[]
    for path in sorted(root.glob('world-*.pkl.gz')):
        manifest=json.loads(path.with_name(path.name.replace('.pkl.gz','-manifest.json')).read_text())
        require_compatible(manifest)
        if not manifest['complete'] or manifest['turns']<150:raise ValueError('Require complete >=150 turn worlds')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('Trajectory checksum mismatch')
        # Only this experiment's own files, checksum-verified before unpickling.
        with gzip.open(path,'rb') as f:world=pickle.load(f)
        if sum(map(len,world))!=manifest['steps']:raise ValueError('Trajectory length mismatch')
        streams.extend(world);manifests.append(manifest)
    if len(manifests)<2:raise ValueError('Require two complete frozen worlds')
    return streams,manifests


def forward_rows(model,rows,*,return_values=False):
    outputs=[];values=[];model.eval()
    with torch.inference_mode():
        for start in range(0,len(rows),4):
            chunk=rows[start:start+4]
            logits,critic,_=model(prepare(collate(chunk),device_of(model)),return_value=True)
            outputs.extend(logits[j,:len(row['candidates'])].double().cpu() for j,row in enumerate(chunk))
            values.extend(float(v)for v in critic)
    return (outputs,values) if return_values else outputs


def drift(rows,before,after):
    result=[]
    for row,old,new in zip(rows,before,after):
        p=old.softmax(-1);q=new.softmax(-1);a=int(old.argmax());b=int(new.argmax())
        family=lambda index:tuple(int(row['candidates'][index,lo:hi].argmax())for lo,hi in ((5,10),(17,36)))
        legal=torch.as_tensor(row['legal'],dtype=torch.bool)
        result.append(dict(kl=float((p*(old.log_softmax(-1)-new.log_softmax(-1))).sum()),
            argmax_changed=int(a!=b),family_changed=int(family(a)!=family(b)),
            legal_mass=float(q[legal].sum()),argmax_legal=int(legal[b]),
            target_accuracy=int(b==row['target']),entropy=float(-(q*q.clamp_min(1e-300).log()).sum())))
    return dict(**{k:float(np.mean([r[k]for r in result]))for k in result[0]},
        p95_kl=float(np.quantile([r['kl']for r in result],.95)),max_kl=max(r['kl']for r in result))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--base',type=Path,required=True);parser.add_argument('--anchor',type=Path,required=True)
    parser.add_argument('--validation-data',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--device',default='cpu');parser.add_argument('--threads',type=int,default=1)
    parser.add_argument('--max-updates',type=int,default=64);parser.add_argument('--arms',nargs='+',choices=list(ARMS))
    parser.add_argument('--kl-mode',choices=('adaptive','legacy'),default='legacy',help='Legacy reproduces the archived parameter study')
    parser.add_argument('--update-batch-size',type=int,default=128);parser.add_argument('--kl-coef',type=float,default=.1)
    parser.add_argument('--epochs',type=int,help='Explicit common epoch count for a new controlled experiment')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True);torch.set_num_threads(args.threads)
    streams,manifests=load_streams(args.data);steps=[step for stream in streams for step in stream]
    model,base=load_policy(args.base);require_compatible(base);model.to(args.device)
    anchor,anchor_meta=load_policy(args.anchor);require_compatible(anchor_meta);anchor.to(args.device)
    if model.critic is None:raise ValueError('Frozen base must include the same initial critic')
    train_ids=np.linspace(0,len(steps)-1,min(128,len(steps)),dtype=int)
    train_rows=[steps[i]['row']for i in train_ids]
    validation=Dataset([args.validation_data/'world-221.npz',args.validation_data/'world-222.npz'],fingerprint=PROJECT_FINGERPRINT)
    valid_rows=[]
    for shard in range(2):
        ids=[i for i,(s,_)in enumerate(validation.index)if s==shard]
        valid_rows.extend(validation[ids[i]]for i in np.linspace(0,len(ids)-1,64,dtype=int))
    probe_rows=dict(train=train_rows,validation=valid_rows);decision=None
    if (args.data/'decision-probe.pkl.gz').exists():
        with gzip.open(args.data/'decision-probe.pkl.gz','rb') as f:decision=pickle.load(f)
        probe_rows['decision']=[decision['row']]
    references={name:forward_rows(model,rows)for name,rows in probe_rows.items()}
    errors=[abs(float(v.log_softmax(-1)[steps[i]['action']])-steps[i]['logp'])for i,v in zip(train_ids,references['train'])]
    if max(errors)>1e-4:raise ValueError(f'Old log probabilities no longer match base: {max(errors)}')
    monte_carlo(streams)
    returns=torch.tensor([s['return']for s in steps]);old_values=torch.tensor([s['value']for s in steps])
    advantages=returns-old_values
    advantages=(advantages-advantages.mean())/advantages.std(unbiased=False).clamp_min(1e-6)
    manifest=dict(**provenance(),worlds=manifests,steps=len(steps),initial_logp_max_error=max(errors),
        base_sha256=hashlib.sha256(args.base.read_bytes()).hexdigest(),anchor_sha256=hashlib.sha256(args.anchor.read_bytes()).hexdigest(),
        random_seed=31002,max_updates=args.max_updates,arms={k:ARMS[k]for k in (args.arms or ARMS)},
        kl_mode=args.kl_mode,effective_batch_size=args.update_batch_size,kl_coef=args.kl_coef,epochs=args.epochs,
        probe_rows={k:len(v)for k,v in probe_rows.items()},
        control={key:drift(rows,references[key],references[key])for key,rows in probe_rows.items()},
        caveat='Frozen-state sensitivity, not long-game acceptance. Component-only arms change several coefficients.')
    write_json(args.out/'manifest.json',manifest)
    seen=set(base.get('seen_seeds',[]))|{m['seed']for m in manifests};results={}
    for name in (args.arms or ARMS):
        storage_guard(args.out)
        m=copy.deepcopy(model);random.seed(31002);np.random.seed(31002);torch.manual_seed(31002)
        print('ARM_START',name,ARMS[name],flush=True)
        with (args.out/f'{name}-updates.jsonl').open('w',buffering=1) as log:
            def observe(detail):
                log.write(json.dumps(detail,ensure_ascii=False)+'\n')
                print('UPDATE',name,detail['updates'],round(detail['kl'],6),'stop',detail['kl_stop'],flush=True)
            arm_options=dict(ARMS[name])
            if args.epochs is not None:arm_options['epochs']=args.epochs
            metrics=ppo_update(m,streams,anchor,max_updates=args.max_updates,diagnostic_callback=observe,
                kl_mode=args.kl_mode,update_batch_size=args.update_batch_size,
                kl_coef=0. if name.endswith('_only') else args.kl_coef,**arm_options)
        m.to('cpu');checkpoint(args.out/f'{name}.pt',m,base,seen,stage='ablation',arm=name,parameters=ARMS[name],metrics=metrics)
        # Evaluate the exact serialized weights with cold inference caches.
        m,_=load_policy(args.out/f'{name}.pt');m.to(args.device)
        train_logits,new_values=forward_rows(m,train_rows,return_values=True)
        predictions={key:forward_rows(m,rows)for key,rows in probe_rows.items() if key!='train'}
        predictions['train']=train_logits
        probes={key:drift(rows,references[key],predictions[key])for key,rows in probe_rows.items()}
        results[name]=dict(parameters=ARMS[name],metrics=metrics,probes=probes)
        ratios=torch.tensor([float(logits.log_softmax(-1)[steps[i]['action']])-steps[i]['logp']
            for i,logits in zip(train_ids,train_logits)]).exp()
        adv=advantages[train_ids];target=returns[train_ids];prediction=torch.tensor(new_values)
        variance=target.var(unbiased=False)
        results[name]['frozen_objective']=dict(
            surrogate_gain=float((torch.minimum(ratios*adv,ratios.clamp(.8,1.2)*adv)-adv).mean()),
            value_mse=float((target-prediction).square().mean()),
            initial_value_mse=float((target-old_values[train_ids]).square().mean()),
            value_explained_variance=float(1-(target-prediction).var(unbiased=False)/variance) if float(variance)>0 else None)
        if decision is not None:
            probabilities=predictions['decision'][0].softmax(-1)
            results[name]['decision']=dict(period=decision['period'],actor=decision['actor'],
                chosen=decision['actions'][int(probabilities.argmax())],
                old_action_probability=float(probabilities[decision['old_index']]),
                previous_ppo_choice_probability=float(probabilities[decision['previous_ppo_index']]))
        write_json(args.out/f'{name}.json',results[name]);write_json(args.out/'summary.json',results)
        print('ARM_DONE',name,json.dumps(results[name]),flush=True)


if __name__=='__main__':main()
