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

from .adapter import ACTIONS,FINGERPRINT
from .data import Dataset
from .model import Policy,collate
from .scenarios import source_hash


def metrics(model,loader,optimizer=None):
    model.train(optimizer is not None);totals=Counter();by_kind={k:Counter() for k in ACTIONS}
    for batch in loader:
        with torch.set_grad_enabled(optimizer is not None):
            logits,quantities=model(batch);target=batch['target']
            indices=torch.arange(len(target));desired=batch['quantity']
            predicted_quantity=quantities[indices,target];trade=desired>0
            action_loss=F.cross_entropy(logits,target)
            quantity_loss=(F.smooth_l1_loss(predicted_quantity[trade],desired[trade])
                           if trade.any() else predicted_quantity.sum()*0)
            loss=action_loss+quantity_loss
            if optimizer is not None:
                optimizer.zero_grad(set_to_none=True);loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.0);optimizer.step()
        prediction=logits.argmax(-1);right=prediction==target;n=len(target)
        totals.update(n=n,correct=int(right.sum()),loss=float(loss.detach())*n,
                      action_loss=float(action_loss.detach())*n,
                      trade=int(trade.sum()),quantity_log_error=float((predicted_quantity[trade]-desired[trade]).abs().sum().detach()))
        kinds=batch['candidates'][indices,target,5:10].argmax(-1)
        for kind,name in enumerate(ACTIONS):
            selected=kinds==kind;by_kind[name].update(n=int(selected.sum()),correct=int(right[selected].sum()))
    return dict(n=totals['n'],loss=totals['loss']/totals['n'],
        action_loss=totals['action_loss']/totals['n'],accuracy=totals['correct']/totals['n'],
        quantity_log_mae=totals['quantity_log_error']/max(1,totals['trade']),
        by_kind={k:dict(n=v['n'],accuracy=v['correct']/max(1,v['n'])) for k,v in by_kind.items()})


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True)
    p.add_argument('--valid-seeds',type=int,nargs='+',required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--epochs',type=int,default=20)
    p.add_argument('--batch',type=int,default=24);p.add_argument('--threads',type=int,default=2)
    p.add_argument('--seed',type=int,default=20261101);p.add_argument('--lr',type=float,default=3e-4)
    p.add_argument('--resume',type=Path);a=p.parse_args()
    torch.set_num_threads(a.threads);random.seed(a.seed);np.random.seed(a.seed);torch.manual_seed(a.seed)
    paths=sorted(a.data.glob('world-*.npz'));valid=set(a.valid_seeds)
    train_paths=[x for x in paths if int(x.stem.split('-')[-1]) not in valid]
    valid_paths=[x for x in paths if int(x.stem.split('-')[-1]) in valid]
    if {int(x.stem.split('-')[-1]) for x in valid_paths}!=valid:raise ValueError('Missing validation worlds')
    training=Dataset(train_paths);validation=Dataset(valid_paths)
    train_seeds={x['seed'] for x in training.metadata};valid_seeds={x['seed'] for x in validation.metadata}
    if train_seeds&valid_seeds:raise ValueError('World leakage across train/validation')
    sources={x['source'] for x in training.metadata+validation.metadata}
    if len(sources)!=1:raise ValueError('Mixed teacher source revisions; regenerate a coherent dataset')
    digest=hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode());digest.update(path.read_bytes())
    dataset_hash=digest.hexdigest()
    model=Policy();optimizer=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01)
    start=0;best=math.inf
    if a.resume:
        saved=torch.load(a.resume,map_location='cpu',weights_only=True)
        if saved['fingerprint']!=FINGERPRINT or saved['dataset']!=dataset_hash:raise ValueError('Resume data/model mismatch')
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        start=saved['epoch'];best=saved['best'];torch.set_rng_state(saved['rng'])
    a.out.mkdir(parents=True,exist_ok=True)
    metadata=dict(fingerprint=FINGERPRINT,dataset=dataset_hash,teacher_source=next(iter(sources)),
        source=source_hash(),config=model.config,train_seeds=sorted(train_seeds),valid_seeds=sorted(valid_seeds),
        parameters=sum(p.numel() for p in model.parameters()),training_rows=len(training),validation_rows=len(validation),
        args={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()})
    if (a.out/'last.pt').exists() and not a.resume:raise FileExistsError('Use --resume or a new output directory')
    (a.out/'run.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(metadata),flush=True)
    for epoch in range(start,a.epochs):
        begun=time.monotonic()
        train=metrics(model,DataLoader(training,batch_size=a.batch,shuffle=True,collate_fn=collate),optimizer)
        with torch.inference_mode():
            val=metrics(model,DataLoader(validation,batch_size=a.batch,collate_fn=collate))
        result=dict(epoch=epoch+1,train=train,valid=val,seconds=time.monotonic()-begun)
        improved=val['loss']<best;best=min(best,val['loss'])
        checkpoint=dict(metadata,epoch=epoch+1,best=best,model=model.state_dict(),
            optimizer=optimizer.state_dict(),rng=torch.get_rng_state(),metrics=result)
        for name in ('last', 'best') if improved else ('last',):
            temp=a.out/f'{name}.tmp';torch.save(checkpoint,temp);temp.replace(a.out/f'{name}.pt')
        with (a.out/'metrics.jsonl').open('a') as stream:stream.write(json.dumps(result)+'\n')
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
