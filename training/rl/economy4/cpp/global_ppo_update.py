"""Global-event Monte Carlo GAE(lambda=1), short joint-action PPO, guarded KL."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

import numpy as np
import torch
from torch.nn import functional as F

from read_trajectory import episodes
from global_returns import global_targets,sequence_indices
from ppo_update import tensor_batch


def update(args):
    sys.path.insert(0,str(args.python_root));from rl.model import Policy;from rl.obs import collate
    torch.set_num_threads(2);torch.manual_seed(args.seed);torch.backends.mha.set_fastpath_enabled(False)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    device=torch.device(args.device);rng=np.random.default_rng(args.seed)
    checkpoint=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    model=Policy(**checkpoint['cfg']).to(device);model.load_state_dict(checkpoint['model']);model.train()
    actor=[p for n,p in model.named_parameters() if not n.startswith(('value.','exec_head.','mem_aux.'))]
    critic=list(model.value.parameters())
    actor_opt=torch.optim.AdamW(actor,lr=args.lr,weight_decay=0);critic_opt=torch.optim.AdamW(critic,lr=args.lr,weight_decay=0)
    for name,opt in (('actor_optimizer',actor_opt),('critic_optimizer',critic_opt)):
        if name in checkpoint:opt.load_state_dict(checkpoint[name])
        for group in opt.param_groups:group['lr']=args.lr
    data=list(episodes(args.trajectories,mmap_arrays=True))
    if not data:raise ValueError('No global PPO trajectories')
    targets,stats=global_targets(args.events,data,args.reward_scale)
    features=np.zeros((len(data),max(len(e[3]) for e in data),80),np.float32)
    rows=[];mapping=[];returns=[];segments=[];raw=[];starts=[]
    for episode,e in enumerate(data):
        samples,_,summary,hist,_=e;features[episode,:len(hist)]=np.stack(hist);offset=len(rows);starts.append(offset)
        for i,row in enumerate(samples):
            rows.append(row);mapping.append((episode,row['ti']));returns.append(targets[summary['seed'],summary['seat']][i])
            obs=row['obs'];tile=obs['tiles'].astype(np.float32);army=obs['armies'].astype(np.float32)
            raw.append(np.concatenate((obs['globals'],tile.mean(0),army.mean(0) if len(army) else np.zeros(20),obs['market'].reshape(-1))))
        segments.extend((offset+i,len(samples),i,episode) for i in range(len(samples)-1))
    features=torch.from_numpy(features).to(device);positions=torch.tensor(mapping,device=device)
    returns=torch.tensor(returns,dtype=torch.float32,device=device);old=copy.deepcopy(model).eval().requires_grad_(False)
    with torch.no_grad():old_memory=old.mem_seq(features)
    raw=torch.cat((torch.tensor(np.stack(raw),dtype=torch.float32,device=device),old_memory[positions[:,0],positions[:,1]].detach()),1)
    # Fit V on current frozen-policy data; keep whole maps out of the critic fit
    # for validation. This never changes policy logits or recurrent parameters.
    worlds=sorted({e[2]['seed'] for e in data});held={worlds[-1]} if len(worlds)>1 else set()
    train_ids=[i for i,(episode,_) in enumerate(mapping) if data[episode][2]['seed'] not in held]
    valid_ids=[i for i,(episode,_) in enumerate(mapping) if data[episode][2]['seed'] in held] or train_ids
    if not train_ids:train_ids=list(range(len(rows)))
    def value_metrics():
        with torch.no_grad():
            target=returns[valid_ids];prediction=model.value(raw[valid_ids]).squeeze(-1);error=target-prediction
            return dict(mse=float(error.square().mean()),explained_variance=float(1-error.var(unbiased=False)/target.var(unbiased=False).clamp_min(1e-8)))
    before=value_metrics();saved_value=copy.deepcopy(model.value.state_dict());saved_opt=copy.deepcopy(critic_opt.state_dict())
    for _ in range(args.critic_batches):
        ids=rng.choice(train_ids,min(256,len(train_ids)),replace=False)
        loss=F.mse_loss(model.value(raw[ids]).squeeze(-1),returns[ids]);critic_opt.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(critic,1.,error_if_nonfinite=True);critic_opt.step()
    after=value_metrics();critic_accepted=after['mse']<=before['mse']
    if not critic_accepted:model.value.load_state_dict(saved_value);critic_opt.load_state_dict(saved_opt)
    old.value.load_state_dict(model.value.state_dict())
    with torch.no_grad():baseline=old.value(raw).squeeze(-1);advantage=returns-baseline
    advantage=(advantage-advantage.mean())/(advantage.std(unbiased=False)+1e-6)
    actions=torch.tensor([r['a'] for r in rows],device=device);sampler_lp=torch.tensor([r['lp'] for r in rows],device=device)
    def forward(policy,ids,frozen=False):
        b=tensor_batch([rows[i]['obs'] for i in ids],device,collate);p=positions[ids]
        if frozen:b['mem']=old_memory[p[:,0],p[:,1]]
        else:
            unique,reverse=torch.unique(p[:,0],return_inverse=True);memory=policy.mem_seq(features[unique]);b['mem']=memory[reverse,p[:,1]]
        return policy(b)[0],b
    anchor=rng.choice(len(rows),min(32,len(rows)),replace=False).tolist()
    with torch.no_grad():anchor_old,b=forward(old,anchor,True);anchor_logp=F.log_softmax(anchor_old.float(),-1);anchor_prob=anchor_logp.exp();anchor_mask=b['cand_mask']
    def exact_kl():
        with torch.no_grad():
            logits,_=forward(model,anchor);current=F.log_softmax(logits.float(),-1)
            return float((anchor_prob*(anchor_logp-current).masked_fill(~anchor_mask,0)).sum(-1).mean())
    chosen=rng.permutation(len(segments))[:min(len(segments),args.max_batches*args.minibatch)]
    accepted=0;rejected=0;losses=[];lengths=[];sampler_gap=[];actor_norms=[];clip_fractions=[];final_kl=0.;backtracks=0;accepted_rates=[]
    for begin in range(0,len(chosen),args.minibatch):
        selected=chosen[begin:begin+args.minibatch];indices=[];spans=[];first=[]
        for j in selected:
            offset,n,start,episode=segments[j];part=sequence_indices(n,start,int(rng.integers(2,5)));ids=[starts[episode]+k for k in part]
            first.append(ids[0]);spans.append((len(indices),len(indices)+len(ids)));indices.extend(ids);lengths.append(len(ids))
        backup_model=copy.deepcopy(model.state_dict());backup_optimizer=copy.deepcopy(actor_opt.state_dict())
        logits,b=forward(model,indices)
        with torch.no_grad():old_logits,_=forward(old,indices,True);old_logp=F.log_softmax(old_logits.float(),-1).gather(1,actions[indices,None]).squeeze(1)
        logp=F.log_softmax(logits.float(),-1).gather(1,actions[indices,None]).squeeze(1)
        joint=torch.stack([(logp[a:z]-old_logp[a:z]).sum() for a,z in spans]);ratio=joint.clamp(-30,30).exp();adv=advantage[first]
        loss=-torch.minimum(ratio*adv,ratio.clamp(.8,1.2)*adv).mean()
        if not torch.isfinite(loss):raise ValueError('Nonfinite joint PPO objective')
        actor_opt.zero_grad(set_to_none=True);loss.backward();norm=torch.nn.utils.clip_grad_norm_(actor,1.,error_if_nonfinite=True);actor_opt.step()
        current_kl=exact_kl();rate=args.lr
        # Retry the same gradient from exactly the pre-step weights and Adam
        # state. Adam moments advance only for an accepted proposal.
        for attempt in range(6):
            if np.isfinite(current_kl) and current_kl<=args.target_kl:break
            model.load_state_dict(backup_model);actor_opt.load_state_dict(backup_optimizer)
            rate=args.lr*(.5**(attempt+1));backtracks+=1
            for group in actor_opt.param_groups:group['lr']=rate
            actor_opt.step();current_kl=exact_kl()
        for group in actor_opt.param_groups:group['lr']=args.lr
        if not np.isfinite(current_kl) or current_kl>args.target_kl:
            model.load_state_dict(backup_model);actor_opt.load_state_dict(backup_optimizer);rejected+=1;break
        accepted+=1;accepted_rates.append(rate);final_kl=current_kl;losses.append(float(loss.detach()));actor_norms.append(float(norm))
        sampler_gap.append(float((old_logp-sampler_lp[indices]).abs().mean()));clip_fractions.append(float(((ratio.detach()-1).abs()>.2).float().mean()))
    if not all(torch.isfinite(p).all().item() for p in model.parameters()):raise ValueError('Nonfinite global PPO checkpoint')
    temporary=args.output.with_suffix('.partial')
    if args.output.exists():raise FileExistsError(args.output)
    torch.save(dict(cfg=checkpoint['cfg'],model=model.state_dict(),actor_optimizer=actor_opt.state_dict(),critic_optimizer=critic_opt.state_dict(),
        upd=checkpoint.get('upd',0)+1,global_ppo=dict(clock='global_events',gamma=1.,gae_lambda=1.,sequence=[2,4],actor_observation_gdp='nominal_only')),temporary)
    temporary.replace(args.output)
    result=dict(episodes=len(data),decisions=len(rows),minibatches=accepted,rejected_minibatches=rejected,
        loss=float(np.mean(losses)) if losses else 0.,approx_kl=final_kl,exact_anchor_kl=final_kl,kl_anchor_states=len(anchor),
        sampler_dlogp=float(np.mean(sampler_gap)) if sampler_gap else 0.,clip_frac=float(np.mean(clip_fractions)) if clip_fractions else 0.,
        objective='nominal_gdp+territory_holding',reward_stats=stats,gae_lambda=1.,gamma=1.,clock='global_events',sequence_lengths=[2,4],
        critic_before=before,critic_after=after,critic_update_accepted=critic_accepted,actor_gradient_norm=float(np.mean(actor_norms)) if actor_norms else 0.,
        actor_backtracks=backtracks,actor_min_accepted_lr=min(accepted_rates) if accepted_rates else None,
        output=str(args.output))
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('checkpoint',type=Path);p.add_argument('trajectories',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--events',type=Path,required=True);p.add_argument('--python-root',type=Path,required=True);p.add_argument('--device',default='cuda:0')
    p.add_argument('--lr',type=float,default=1e-4);p.add_argument('--seed',type=int,default=0);p.add_argument('--minibatch',type=int,default=16)
    p.add_argument('--max-batches',type=int,default=128);p.add_argument('--target-kl',type=float,default=.02);p.add_argument('--critic-batches',type=int,default=64)
    p.add_argument('--reward-scale',type=float,default=3000.)
    args=p.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True);update(args)
