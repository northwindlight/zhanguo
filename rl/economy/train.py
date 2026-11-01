"""CPU BC training; select checkpoints on held-out worlds, no PPO or reward hacks."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch.nn import functional as F
from torch.utils.data import DataLoader

from .adapter import ACTIONS,BUILDINGS,FINGERPRINT,PROJECT_FINGERPRINT
from .data import Dataset
from .model import Policy,collate
from .scenarios import source_hash


def curriculum_weights(batch,opening_weight=1.,mobilization_weight=1.):
    """Prioritize learning the complete zero-army investment chain, not a rule override."""
    target=batch['target'];indices=torch.arange(len(target))
    chosen=batch['candidates'][indices,target]
    zero_army=batch['context'][:,0,41]==0
    barracks=(chosen[:,6]>0)&(chosen[:,17+BUILDINGS.index('native:barracks')]>0)
    power=(chosen[:,6]>0)&((chosen[:,17+BUILDINGS.index('native:wood-power-plant')]>0)|
                          (chosen[:,17+BUILDINGS.index('native:oil-power-plant')]>0))
    recruit=chosen[:,7]>0
    facility=((batch['candidates'][:,:,7]>0)&(batch['candidates'][:,:,40]>0)).any(-1)
    # Projects mode has no buy action; the mobilization-material emphasis is
    # the supply/equipment factory build while the opening army is still zero.
    material=(chosen[:,6]>0)&((chosen[:,17+BUILDINGS.index('native:supply-factory')]>0)|
                              (chosen[:,17+BUILDINGS.index('native:equipment-factory')]>0))&facility&zero_army
    mobilization=barracks|recruit|material|(power&zero_army)
    weights=torch.ones(len(target),dtype=torch.float32)
    weights*=torch.where(zero_army,opening_weight,1.)
    weights*=torch.where(mobilization,mobilization_weight,1.)
    return weights,zero_army,mobilization


def metrics(model,loader,optimizer=None,*,opening_weight=1.,mobilization_weight=1.):
    model.train(optimizer is not None);totals=Counter();by_kind={k:Counter() for k in ACTIONS}
    for batch in loader:
        with torch.set_grad_enabled(optimizer is not None):
            logits,quantities,executable=model(batch,return_exec=True);target=batch['target']
            indices=torch.arange(len(target));desired=batch['quantity']
            predicted_quantity=quantities[indices,target];trade=desired>0
            weights,opening,mobilization=curriculum_weights(batch,opening_weight,mobilization_weight)
            action_rows=F.cross_entropy(logits,target,reduction='none')
            quantity_rows=F.smooth_l1_loss(predicted_quantity,desired,reduction='none')
            action_loss=(action_rows*weights).sum()/weights.sum()
            quantity_loss=(quantity_rows[trade]*weights[trade]).sum()/weights[trade].sum().clamp_min(1)
            loss=action_loss+quantity_loss
            if executable is not None:
                exec_loss=F.binary_cross_entropy_with_logits(executable[batch['candidate_mask']],
                    batch['legal'][batch['candidate_mask']].float())
                loss=loss+.2*exec_loss
                totals.update(exec_loss=float(exec_loss.detach())*len(target))
            if optimizer is not None:
                optimizer.zero_grad(set_to_none=True);loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.0);optimizer.step()
        prediction=logits.argmax(-1);right=prediction==target;n=len(target)
        totals.update(n=n,correct=int(right.sum()),
                      weighted_action=float((action_rows*weights).sum().detach()),weight=float(weights.sum()),
                      weighted_quantity=float((quantity_rows[trade]*weights[trade]).sum().detach()),trade_weight=float(weights[trade].sum()),
                      action_loss=float(action_rows.sum().detach()),
                      opening=int(opening.sum()),opening_correct=int(right[opening].sum()),
                      mobilization=int(mobilization.sum()),mobilization_correct=int(right[mobilization].sum()),
                      trade=int(trade.sum()),quantity_log_error=float((predicted_quantity[trade]-desired[trade]).abs().sum().detach()))
        kinds=batch['candidates'][indices,target,5:10].argmax(-1)
        for kind,name in enumerate(ACTIONS):
            selected=kinds==kind;by_kind[name].update(n=int(selected.sum()),correct=int(right[selected].sum()))
    return dict(n=totals['n'],loss=totals['weighted_action']/totals['weight']+
        totals['weighted_quantity']/max(1,totals['trade_weight']),
        action_loss=totals['action_loss']/totals['n'],accuracy=totals['correct']/totals['n'],
        opening_accuracy=totals['opening_correct']/max(1,totals['opening']),
        mobilization_accuracy=totals['mobilization_correct']/max(1,totals['mobilization']),
        quantity_log_mae=totals['quantity_log_error']/max(1,totals['trade']),
        exec_loss=totals['exec_loss']/totals['n'],
        by_kind={k:dict(n=v['n'],accuracy=v['correct']/max(1,v['n'])) for k,v in by_kind.items()})


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True)
    p.add_argument('--valid-seeds',type=int,nargs='+',required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--epochs',type=int,default=20)
    p.add_argument('--batch',type=int,default=24);p.add_argument('--threads',type=int,default=2)
    p.add_argument('--seed',type=int,default=20261101);p.add_argument('--lr',type=float,default=3e-4)
    p.add_argument('--resume',type=Path);p.add_argument('--init',type=Path)
    p.add_argument('--opening-weight',type=float,default=4.)
    p.add_argument('--mobilization-weight',type=float,default=3.)
    p.add_argument('--projects',action='store_true')
    p.add_argument('--allow-mixed-teachers',action='store_true',help='Explicitly combine compatible feature schemas from recorded teacher source revisions')
    a=p.parse_args()
    if a.resume and a.init:raise ValueError('Choose resume or initialization, not both')
    if min(a.opening_weight,a.mobilization_weight)<1:raise ValueError('Curriculum weights must be >= 1')
    torch.set_num_threads(a.threads);random.seed(a.seed);np.random.seed(a.seed);torch.manual_seed(a.seed)
    paths=sorted(a.data.glob('world-*.npz'));valid=set(a.valid_seeds)
    train_paths=[x for x in paths if int(x.stem.split('-')[-1]) not in valid]
    valid_paths=[x for x in paths if int(x.stem.split('-')[-1]) in valid]
    if {int(x.stem.split('-')[-1]) for x in valid_paths}!=valid:raise ValueError('Missing validation worlds')
    fingerprint=PROJECT_FINGERPRINT if a.projects else FINGERPRINT
    training=Dataset(train_paths,fingerprint=fingerprint);validation=Dataset(valid_paths,fingerprint=fingerprint)
    train_seeds={x['seed'] for x in training.metadata};valid_seeds={x['seed'] for x in validation.metadata}
    if train_seeds&valid_seeds:raise ValueError('World leakage across train/validation')
    sources={x['source'] for x in training.metadata+validation.metadata}
    if len(sources)!=1 and not a.allow_mixed_teachers:raise ValueError('Mixed teacher source revisions require --allow-mixed-teachers')
    digest=hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode());digest.update(path.read_bytes())
    dataset_hash=digest.hexdigest()
    model=Policy(action_mode='projects' if a.projects else 'atomic');optimizer=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01)
    start=0;best=math.inf;ancestry=set()
    if a.init:
        initial=torch.load(a.init,map_location='cpu',weights_only=True)
        if initial['fingerprint']!=fingerprint:raise ValueError('Initialization feature mismatch')
        model.load_state_dict(initial['model'])
        ancestry.update(initial.get('seen_seeds',initial['train_seeds']+initial['valid_seeds']))
    if a.resume:
        saved=torch.load(a.resume,map_location='cpu',weights_only=True)
        if saved['fingerprint']!=fingerprint or saved['dataset']!=dataset_hash:raise ValueError('Resume data/model mismatch')
        for key in ('opening_weight','mobilization_weight'):
            if saved['args'].get(key,1.)!=getattr(a,key):raise ValueError('Use --init when changing the curriculum')
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        start=saved['epoch'];best=saved['best'];torch.set_rng_state(saved['rng'])
        ancestry.update(saved.get('seen_seeds',saved['train_seeds']+saved['valid_seeds']))
    a.out.mkdir(parents=True,exist_ok=True)
    metadata=dict(fingerprint=fingerprint,dataset=dataset_hash,teacher_sources=sorted(sources),
        source=source_hash(),config=model.config,train_seeds=sorted(train_seeds),valid_seeds=sorted(valid_seeds),
        seen_seeds=sorted(ancestry|train_seeds|valid_seeds),
        parameters=sum(p.numel() for p in model.parameters()),training_rows=len(training),validation_rows=len(validation),
        args={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()})
    if (a.out/'last.pt').exists() and not a.resume:raise FileExistsError('Use --resume or a new output directory')
    (a.out/'run.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(metadata),flush=True)
    for epoch in range(start,a.epochs):
        begun=time.monotonic()
        objective=dict(opening_weight=a.opening_weight,mobilization_weight=a.mobilization_weight)
        train=metrics(model,DataLoader(training,batch_size=a.batch,shuffle=True,collate_fn=collate),optimizer,**objective)
        with torch.inference_mode():
            val=metrics(model,DataLoader(validation,batch_size=a.batch,collate_fn=collate),**objective)
        result=dict(epoch=epoch+1,train=train,valid=val,seconds=time.monotonic()-begun)
        improved=val['loss']<best;best=min(best,val['loss'])
        checkpoint=dict(metadata,epoch=epoch+1,best=best,model=model.state_dict(),
            optimizer=optimizer.state_dict(),rng=torch.get_rng_state(),metrics=result)
        for name in ('last', 'best') if improved else ('last',):
            temp=a.out/f'{name}.tmp';torch.save(checkpoint,temp);temp.replace(a.out/f'{name}.pt')
        with (a.out/'metrics.jsonl').open('a') as stream:stream.write(json.dumps(result)+'\n')
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
