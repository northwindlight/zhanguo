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
from .batching import LengthBucketedSampler
from .data import Dataset
from .model import Policy,collate,device_of,prepare
from .provenance import provenance,resolved_contracts,require_compatible


def curriculum_weights(batch,opening_weight=1.,mobilization_weight=1.):
    """Prioritize learning the complete zero-army investment chain, not a rule override."""
    target=batch['target'];indices=torch.arange(len(target),device=target.device)
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
    weights=torch.ones(len(target),dtype=torch.float32,device=target.device)
    weights*=torch.where(zero_army,opening_weight,1.)
    weights*=torch.where(mobilization,mobilization_weight,1.)
    return weights,zero_army,mobilization


def metrics(model,loader,optimizer=None,*,opening_weight=1.,mobilization_weight=1.):
    model.train(optimizer is not None);totals=Counter();by_kind={k:Counter() for k in ACTIONS}
    device=device_of(model)
    for batch in loader:
        with torch.set_grad_enabled(optimizer is not None):
            batch=prepare(batch,device)
            logits,quantities,executable=model(batch,return_exec=True);target=batch['target']
            indices=torch.arange(len(target),device=target.device);desired=batch['quantity']
            predicted_quantity=quantities[indices,target];trade=desired>0
            weights,opening,mobilization=curriculum_weights(batch,opening_weight,mobilization_weight)
            action_rows=F.cross_entropy(logits,target,reduction='none')
            quantity_rows=F.smooth_l1_loss(predicted_quantity,desired,reduction='none')
            action_loss=(action_rows*weights).sum()/weights.sum()
            quantity_loss=(quantity_rows[trade]*weights[trade]).sum()/weights[trade].sum().clamp_min(1)
            loss=action_loss+quantity_loss
            family=getattr(model,'last_family_logits',None)
            if family is not None:
                # 族头用教师自己的动作族监督：三分类，不会被上千个候选负样本淹没。
                teacher_family=batch['candidates'][indices,target,5:10].argmax(-1)
                family_loss=F.cross_entropy(family[indices,target],teacher_family)
                loss=loss+model.config.get('family_loss_weight',.5)*family_loss
                totals.update(family_loss=float(family_loss.detach())*len(target))
                predicted_family=family[indices,target].argmax(-1)
                totals.update(family_correct=int((predicted_family==teacher_family).sum()),
                              family_n=int(len(target)))
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
        family_loss=totals['family_loss']/totals['n'],
        family_accuracy=totals['family_correct']/max(1,totals['family_n']),
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
    p.add_argument('--device',default='cpu',help='cpu 或 cuda；有 CUDA 的机器上训练显著更快')
    p.add_argument('--max-candidates',type=int,default=0,
                   help='每行训练候选上限（教师选项必留 + 随机负样本）；默认 0 = 不截断，改变监督语义须显式开启')
    p.add_argument('--no-length-bucketing',dest='length_bucketing',action='store_false',
                   help='关闭按候选长度分桶组批（默认开启；只影响组批，不改监督语义）')
    p.add_argument('--bucket-width',type=int,default=32,
                   help='长度分桶的量化宽度：只有长度接近的行才同批')
    p.add_argument('--family-aux',action='store_true',
                   help='加动作族（finish/build/recruit）辅助头；默认关闭保持原模型')
    p.add_argument('--family-weight',type=float,default=1.,help='推理时族对数概率并入候选分数的权重')
    p.add_argument('--family-loss-weight',type=float,default=.5,help='族辅助损失的系数')
    p.add_argument('--allow-mixed-teachers',action='store_true',help='Explicitly combine compatible feature schemas from recorded teacher source revisions')
    a=p.parse_args()
    if a.resume and a.init:raise ValueError('Choose resume or initialization, not both')
    if min(a.opening_weight,a.mobilization_weight)<1:raise ValueError('Curriculum weights must be >= 1')
    if a.max_candidates<0:raise ValueError('--max-candidates must be >= 0')
    device=torch.device(a.device)
    if device.type=='cuda' and not torch.cuda.is_available():raise ValueError('--device cuda requested but CUDA is unavailable')
    torch.set_num_threads(a.threads);random.seed(a.seed);np.random.seed(a.seed);torch.manual_seed(a.seed)
    paths=sorted(a.data.glob('world-*.npz'));valid=set(a.valid_seeds)
    train_paths=[x for x in paths if int(x.stem.split('-')[-1]) not in valid]
    valid_paths=[x for x in paths if int(x.stem.split('-')[-1]) in valid]
    if {int(x.stem.split('-')[-1]) for x in valid_paths}!=valid:raise ValueError('Missing validation worlds')
    fingerprint=PROJECT_FINGERPRINT if a.projects else FINGERPRINT
    training=Dataset(train_paths,fingerprint=fingerprint,max_candidates=a.max_candidates,seed=a.seed)
    validation=Dataset(valid_paths,fingerprint=fingerprint,max_candidates=a.max_candidates,seed=a.seed)
    train_seeds={x['seed'] for x in training.metadata};valid_seeds={x['seed'] for x in validation.metadata}
    if train_seeds&valid_seeds:raise ValueError('World leakage across train/validation')
    sources={x['source'] for x in training.metadata+validation.metadata}
    teacher_contracts={resolved_contracts(x)['teacher_contract']for x in training.metadata+validation.metadata}
    if len(teacher_contracts)!=1 and not a.allow_mixed_teachers:raise ValueError('Mixed teacher contracts require --allow-mixed-teachers')
    digest=hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode());digest.update(path.read_bytes())
    dataset_hash=digest.hexdigest()
    model=Policy(action_mode='projects' if a.projects else 'atomic',family_aux=a.family_aux,
                 family_weight=a.family_weight,family_loss_weight=a.family_loss_weight)
    optimizer=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01)
    start=0;best=math.inf;ancestry=set()
    if a.init:
        initial=torch.load(a.init,map_location='cpu',weights_only=True)
        if initial['fingerprint']!=fingerprint:raise ValueError('Initialization feature mismatch')
        require_compatible(initial)
        model.load_state_dict(initial['model'])
        ancestry.update(initial.get('seen_seeds',initial['train_seeds']+initial['valid_seeds']))
    if a.resume:
        saved=torch.load(a.resume,map_location='cpu',weights_only=True)
        if saved['fingerprint']!=fingerprint or saved['dataset']!=dataset_hash:raise ValueError('Resume data/model mismatch')
        require_compatible(saved)
        for key in ('opening_weight','mobilization_weight'):
            if saved['args'].get(key,1.)!=getattr(a,key):raise ValueError('Use --init when changing the curriculum')
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        start=saved['epoch'];best=saved['best'];torch.set_rng_state(saved['rng'])
        ancestry.update(saved.get('seen_seeds',saved['train_seeds']+saved['valid_seeds']))
    model.to(device)
    # 优化器状态在建好参数后就跟着模型走；resume 之后再搬一次以防设备不同。
    for state in optimizer.state.values():
        for key,value in state.items():
            if torch.is_tensor(value):state[key]=value.to(device)
    a.out.mkdir(parents=True,exist_ok=True)
    metadata=dict(**provenance(),fingerprint=fingerprint,dataset=dataset_hash,teacher_sources=sorted(sources),teacher_contracts=sorted(teacher_contracts),
        config=model.config,train_seeds=sorted(train_seeds),valid_seeds=sorted(valid_seeds),
        seen_seeds=sorted(ancestry|train_seeds|valid_seeds),
        parameters=sum(p.numel() for p in model.parameters()),training_rows=len(training),validation_rows=len(validation),
        args={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()})
    if (a.out/'last.pt').exists() and not a.resume:raise FileExistsError('Use --resume or a new output directory')
    (a.out/'run.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(metadata),flush=True)
    def loader(dataset,*,shuffle):
        """按候选长度分桶：同一批只含长度相近的行，padding 接近零。

        分桶不改变行、标签与损失权重，只是组批方式；``--no-length-bucketing``
        退回随机组批（做对照时用）。
        """
        if not a.length_bucketing:
            return DataLoader(dataset,batch_size=a.batch,shuffle=shuffle,collate_fn=collate)
        sampler=LengthBucketedSampler(dataset.candidate_counts(),a.batch,seed=a.seed,width=a.bucket_width)
        return DataLoader(dataset,batch_sampler=sampler,collate_fn=collate)

    for epoch in range(start,a.epochs):
        begun=time.monotonic()
        training.set_epoch(epoch)
        objective=dict(opening_weight=a.opening_weight,mobilization_weight=a.mobilization_weight)
        train_loader=loader(training,shuffle=True)
        if a.length_bucketing:train_loader.batch_sampler.set_epoch(epoch)
        train=metrics(model,train_loader,optimizer,**objective)
        with torch.inference_mode():
            val=metrics(model,loader(validation,shuffle=False),**objective)
        result=dict(epoch=epoch+1,train=train,valid=val,seconds=time.monotonic()-begun)
        improved=val['loss']<best;best=min(best,val['loss'])
        checkpoint=dict(metadata,epoch=epoch+1,best=best,model=model.state_dict(),
            optimizer=optimizer.state_dict(),rng=torch.get_rng_state(),metrics=result)
        for name in ('last', 'best') if improved else ('last',):
            temp=a.out/f'{name}.tmp';torch.save(checkpoint,temp);temp.replace(a.out/f'{name}.pt')
        with (a.out/'metrics.jsonl').open('a') as stream:stream.write(json.dumps(result)+'\n')
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
