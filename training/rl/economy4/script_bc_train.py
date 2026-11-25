"""Random-initialized recurrent policy, supervised exclusively by pure scripts."""
import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import pickle
import sys
import time
from zipfile import ZipFile

import numpy as np
import torch
from torch.nn import functional as F

ROOT=Path(__file__).resolve().parent
from mixed_bc_run import publish,digest

CFG=dict(d=192,layers=4,heads=8,value_grad_scale=.1,exec_prior_weight=0,mem_h=128,arch='xattn',cand_layers=2,cand_self=True)
WEIGHTS=dict(end=.04,extraction=.12,power=.10,supply_factory=.10,equipment_factory=.08,
             supply_inputs=.10,fuel=.10,recruit=.12,attack=.12,materials=.05,finance=.05,setup=.04,demobilize=.04)


class Dataset:
    def __init__(self,episodes,device):
        self.device=device;self.rows=[];self.groups=defaultdict(list)
        features=np.zeros((len(episodes),max(len(e['features']) for e in episodes),80),np.float32)
        for index,e in enumerate(episodes):
            features[index,:len(e['features'])]=e['features']
            for obs,action,turn,group in e['teacher']:
                self.groups[turn>=30,group].append(len(self.rows));self.rows.append((obs,action,index,turn))
        self.features=torch.from_numpy(features).to(device)

    def draw(self,rng,count):
        keys=list(self.groups);weights=np.array([(.45 if phase else .55)*WEIGHTS[group]/
            sum(WEIGHTS[g] for p,g in keys if p==phase) for phase,group in keys]);weights/=weights.sum()
        return [rng.choice(self.groups[keys[i]]) for i in rng.choice(len(keys),count,p=weights)]

    def batch(self,ids,policy):
        from rl.obs import collate
        rows=[self.rows[int(i)] for i in ids];obs,actions,episodes,turns=zip(*rows)
        data={k:torch.from_numpy(v).to(self.device) for k,v in collate(obs).items()};data['cand_ptr']=data['cand_ptr'].long()
        unique,reverse=np.unique(episodes,return_inverse=True);memory=policy.mem_seq(self.features[unique])
        data['mem']=memory[torch.as_tensor(reverse,device=self.device),torch.as_tensor(turns,device=self.device)]
        return data,torch.tensor(actions,device=self.device)


def load(data):
    episodes=[];seen=set()
    for path in sorted((data/'chunks').glob('chunk-*.zip')):
        with ZipFile(path) as z:
            for name in z.namelist():
                if not name.endswith('.pkl'):continue
                e=pickle.loads(z.read(name))
                if e['seed'] in seen:raise ValueError('Duplicate script map')
                seen.add(e['seed'])
                if e.get('anchor') or e['contract']['source']!='pure_script':raise ValueError('Non-script target in BC corpus')
                if e['accepted'] and e['teacher']:episodes.append(e)
    return episodes


@torch.no_grad()
def evaluate(policy,forward,data,batch,limit):
    policy.eval();output={}
    for key,ids in data.groups.items():
        chosen=np.array(ids)[np.linspace(0,len(ids)-1,min(limit,len(ids)),dtype=int)];n=0;ce=correct=kind_correct=valid=0.
        for start in range(0,len(chosen),batch):
            b,a=data.batch(chosen[start:start+batch],policy);logits=forward(b)[0];best=logits.argmax(-1);n+=len(a)
            ce+=F.cross_entropy(logits,a,reduction='sum').item();correct+=(best==a).sum().item()
            kind_correct+=(b['cand_type'].gather(1,best[:,None])==b['cand_type'].gather(1,a[:,None])).sum().item()
            valid+=b['cand_exec'].gather(1,best[:,None]).sum().item()
        output[('military' if key[0] else 'economy')+'/'+key[1]]=dict(n=n,ce=ce/n,exact=correct/n,type_accuracy=kind_correct/n,executable=valid/n)
    return output


def train(args):
    sys.path.insert(0,str(args.python_root));from rl.model import Policy
    torch.set_num_threads(2);torch.manual_seed(args.seed);np.random.seed(args.seed)
    torch.backends.mha.set_fastpath_enabled(False);torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    device=torch.device(args.device);work=args.output.resolve();work.mkdir(parents=True,exist_ok=True)
    cfg=dict(CFG)
    if args.small:cfg.update(d=32,layers=1,heads=4,mem_h=16,cand_layers=1)
    model=Policy(**cfg).to(device)
    for part in (model.value,model.exec_head,model.mem_aux):part.requires_grad_(False)
    actor=[p for p in model.parameters() if p.requires_grad];optimizer=torch.optim.AdamW(actor,lr=args.lr,weight_decay=0)
    state_path=work/'state.json';initial=work/'random-initial.pt'
    if state_path.exists():
        state=json.loads(state_path.read_text());ck=torch.load(work/state['latest'],map_location='cpu',weights_only=True)
        if ck['cfg']!=cfg or state['seed']!=args.seed:raise ValueError('Random-init campaign identity changed')
        model.load_state_dict(ck['model']);optimizer.load_state_dict(ck['bc_optimizer']);start=state['epoch']+1
    else:
        if initial.exists():raise FileExistsError('Uncommitted random initial checkpoint')
        torch.save(dict(cfg=cfg,model=model.state_dict(),upd=0,initialization='random',seed=args.seed),initial)
        state=dict(epoch=0,latest=initial.name,sha256=digest(initial),seed=args.seed,initialization='random',epochs=args.epochs)
        publish(state_path,state);start=1
    episodes=load(args.data);training=[e for e in episodes if e['seed']%5!=0];validation=[e for e in episodes if e['seed']%5==0]
    if not training or not validation:raise ValueError('Missing independent script map partitions')
    train_data,valid_data=Dataset(training,device),Dataset(validation,device)
    forward=torch.nn.DataParallel(model,device_ids=[0,1]) if device.type=='cuda' and torch.cuda.device_count()==2 else model
    publish(work/'training-contract.json',dict(initialization='random',cfg=cfg,epochs=args.epochs,lr=args.lr,
        targets='pure_script_only',economic_anchors_from_old_model=False,value_training=False,
        train_maps=len(training),validation_maps=len(validation),train_rows=len(train_data.rows),validation_rows=len(valid_data.rows),
        devices=torch.cuda.device_count() if device.type=='cuda' else 0))
    target=min(args.epochs,args.stop_after_epoch or args.epochs)
    for epoch in range(start,target+1):
        rng=np.random.default_rng(args.seed+epoch);steps=args.limit_steps or math.ceil(len(train_data.rows)/args.batch)
        model.train();total=0.;begun=time.monotonic()
        for step in range(steps):
            b,a=train_data.batch(train_data.draw(rng,args.batch),model);logits=forward(b)[0]
            loss=F.cross_entropy(logits,a)
            if not torch.isfinite(loss):raise ValueError('Nonfinite script BC loss')
            optimizer.zero_grad(set_to_none=True);loss.backward();torch.nn.utils.clip_grad_norm_(actor,1.);optimizer.step();total+=loss.item()
            if (step+1)%100==0:print(json.dumps(dict(stage='bc_training',epoch=epoch,step=step+1,steps=steps,loss=total/(step+1))),flush=True)
        metrics=evaluate(model,forward,valid_data,args.batch,args.validation_limit)
        if not all(torch.isfinite(p).all().item() for p in model.parameters()):raise ValueError('Nonfinite BC checkpoint')
        path=work/f'epoch-{epoch:02}.pt';temporary=path.with_suffix('.partial')
        torch.save(dict(cfg=cfg,model=model.state_dict(),upd=0,bc_optimizer=optimizer.state_dict(),
            initialization='random',seed=args.seed,script_bc_epoch=epoch),temporary);temporary.replace(path)
        state=dict(state,epoch=epoch,latest=path.name,sha256=digest(path));publish(state_path,state)
        result=dict(epoch=epoch,loss=total/steps,seconds=time.monotonic()-begun,validation=metrics,sha256=state['sha256'])
        publish(work/f'epoch-{epoch:02}.json',result);print(json.dumps(dict(stage='bc_epoch_complete',**result)),flush=True)
    if state['epoch']==args.epochs:publish(work/'done.json',dict(state,stage='bc_complete',value_calibration_required=True))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--python-root',type=Path,required=True);p.add_argument('--epochs',type=int,default=10)
    p.add_argument('--device',default='cuda:0');p.add_argument('--batch',type=int,default=24);p.add_argument('--lr',type=float,default=3e-4)
    p.add_argument('--seed',type=int,default=20261124);p.add_argument('--limit-steps',type=int,default=0)
    p.add_argument('--validation-limit',type=int,default=48);p.add_argument('--small',action='store_true')
    p.add_argument('--stop-after-epoch',type=int,default=0)
    train(p.parse_args())
