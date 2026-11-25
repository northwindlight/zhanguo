"""Compare single-process recurrent PPO to two Gloo ranks using real rollout states.

This is a CPU correctness probe, not a CUDA speed claim or a production updater.
Each rank retains full history of its selected countries; no hidden-state detach.
"""
import argparse
import copy
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from torch.nn import functional as F
from torch.nn.parallel import DistributedDataParallel as DDP

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path[:0]=[str(HERE/'reference/python'),str(ROOT/'cpp')]
from rl.model import Policy
from rl.obs import collate
from read_trajectory import episodes


class RecurrentForward(torch.nn.Module):
    """Keep both GRU and decision computation within the DDP forward graph."""
    def __init__(self,policy):
        super().__init__();self.policy=policy

    def forward(self,observations,history,positions):
        memory=self.policy.mem_seq(history)
        logits,_,value=self.policy(dict(observations,mem=memory[positions[:,0],positions[:,1]]))
        return logits,value


def select(data,indices):
    rows=[data['decisions'][i] for i in indices]
    b={k:torch.from_numpy(v) for k,v in collate([r['obs'] for r in rows]).items()}
    b['cand_ptr']=b['cand_ptr'].long()
    position=data['positions'][indices]
    countries,local=torch.unique(position[:,0],sorted=True,return_inverse=True)
    # Drop unused countries and future turns while retaining every causal prefix.
    history=data['features'][countries,:int(position[:,1].max())+1]
    local_position=torch.stack((local,position[:,1]),dim=1)
    return b,history,local_position


def loss(wrapper,data,indices):
    b,history,positions=select(data,indices)
    logits,values=wrapper(b,history,positions)
    actions=data['actions'][indices]
    lp=F.log_softmax(logits.float(),-1).gather(1,actions[:,None]).squeeze(1)
    ratio=(lp-data['old_logp'][indices]).exp()
    advantage=data['advantages'][indices]
    actor=-torch.minimum(ratio*advantage,ratio.clamp(.8,1.2)*advantage).mean()
    critic=.5*F.mse_loss(values.float(),data['returns'][indices])
    return actor+critic


def gradients(module):
    return {k:p.grad.detach().clone() for k,p in module.named_parameters() if p.grad is not None}


def worker(rank,rendezvous,fixture,output):
    torch.set_num_threads(1);torch.backends.mha.set_fastpath_enabled(False)
    dist.init_process_group('gloo',rank=rank,world_size=2,init_method='file://'+rendezvous,timeout=timedelta(seconds=90))
    try:
        data=torch.load(fixture,weights_only=False)
        policy=Policy(**data['cfg']);policy.load_state_dict(data['model'])
        wrapper=RecurrentForward(policy)
        # Execution and auxiliary heads are deliberately unused in this PPO objective.
        ddp=DDP(wrapper,find_unused_parameters=True)
        optimizer=torch.optim.AdamW(wrapper.parameters(),lr=5e-4,weight_decay=0)
        optimizer.load_state_dict(data['optimizer'])
        results=[]
        for selection,split in data['steps']:
            indices=torch.tensor(selection[:split] if rank==0 else selection[split:])
            optimizer.zero_grad(set_to_none=True)
            # DDP averages ranks. Weight each local mean to recover the global sample mean.
            objective=loss(ddp,data,indices)*(2*len(indices)/len(selection))
            objective.backward()
            grads=gradients(wrapper)
            torch.nn.utils.clip_grad_norm_(wrapper.parameters(),1.)
            optimizer.step()
            results.append(dict(grads=grads,model=copy.deepcopy(policy.state_dict())))
        torch.save(results,Path(output)/f'rank-{rank}.pt')
    finally:dist.destroy_process_group()


def fixture(rollouts,checkpoint):
    completed=list(episodes(rollouts,mmap_arrays=True))
    # Select two decisions from each of four independent country histories.
    decisions=[];positions=[];features=[];countries=[]
    for country,(rows,_,national,summary,_) in enumerate(completed[:4]):
        countries.append(dict(seed=national['seed'],seat=national['seat']))
        features.append(np.stack(summary))
        for row in (rows[0],rows[-1]):
            decisions.append(row);positions.append((country,row['ti']))
    assert len(decisions)==8
    ck=torch.load(checkpoint,map_location='cpu',weights_only=True)
    policy=Policy(**ck['cfg']);policy.load_state_dict(ck['model'])
    wrapped=RecurrentForward(policy)
    data=dict(decisions=decisions,positions=torch.tensor(positions),features=torch.from_numpy(np.stack(features)),countries=countries,
              actions=torch.tensor([r['a'] for r in decisions]),cfg=ck['cfg'])
    with torch.no_grad():
        b,h,pos=select(data,torch.arange(8));logits,values=wrapped(b,h,pos)
        data['old_logp']=F.log_softmax(logits.float(),-1).gather(1,data['actions'][:,None]).squeeze(1)
    advantage=torch.tensor([-1.,.5,1.,-.5,.2,-.2,1.5,-1.5])
    data['advantages']=(advantage-advantage.mean())/(advantage.std(unbiased=False)+1e-6)
    data['returns']=values+torch.linspace(-.2,.2,8)
    optimizer=torch.optim.AdamW(wrapped.parameters(),lr=5e-4,weight_decay=0)
    # Warm-start Adam moments, as required when resuming the real PPO checkpoint.
    optimizer.zero_grad(set_to_none=True);loss(wrapped,data,torch.arange(8)).backward()
    torch.nn.utils.clip_grad_norm_(wrapped.parameters(),1.);optimizer.step()
    data.update(model=copy.deepcopy(policy.state_dict()),optimizer=copy.deepcopy(optimizer.state_dict()),
                steps=[(list(range(8)),4),(list(reversed(range(8))),3),([1,4,7],2)])
    return data


def main():
    p=argparse.ArgumentParser();p.add_argument('--rollouts',type=Path,default=Path('/tmp/land-gdp-smoke/normal'))
    p.add_argument('--checkpoint',type=Path,default=Path('/tmp/land-gdp-smoke/base.pt'))
    p.add_argument('--report',type=Path,default=HERE/'ddp-proof.json')
    args=p.parse_args();torch.set_num_threads(1);torch.backends.mha.set_fastpath_enabled(False)
    data=fixture(args.rollouts,args.checkpoint)
    policy=Policy(**data['cfg']);policy.load_state_dict(data['model']);wrapped=RecurrentForward(policy)
    optimizer=torch.optim.AdamW(wrapped.parameters(),lr=5e-4,weight_decay=0);optimizer.load_state_dict(copy.deepcopy(data['optimizer']))
    expected=[]
    for selection,_ in data['steps']:
        optimizer.zero_grad(set_to_none=True);loss(wrapped,data,torch.tensor(selection)).backward()
        grads=gradients(wrapped);torch.nn.utils.clip_grad_norm_(wrapped.parameters(),1.);optimizer.step()
        expected.append(dict(grads=grads,model=copy.deepcopy(policy.state_dict())))
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='zhanguo-ddp-proof-') as temporary:
        path=Path(temporary);torch.save(data,path/'fixture.pt')
        mp.spawn(worker,args=(str(path/'rendezvous'),path/'fixture.pt',path),nprocs=2,join=True)
        ranks=[torch.load(path/f'rank-{i}.pt',weights_only=True) for i in range(2)]
        rows=[]
        for step,target in enumerate(expected):
            grad_error=max((target['grads'][k]-ranks[0][step]['grads'][k]).abs().max().item() for k in target['grads'])
            difference_sq=sum((v.double()-ranks[0][step]['grads'][k].double()).square().sum().item() for k,v in target['grads'].items())
            reference_sq=sum(v.double().square().sum().item() for v in target['grads'].values())
            gradient_relative_l2=(difference_sq/max(reference_sq,1e-30))**.5
            errors={k:(v-ranks[0][step]['model'][k]).abs().max().item() for k,v in target['model'].items()}
            parameter_error=max(errors.values());worst=max(errors,key=errors.get)
            rank_error=max((v-ranks[1][step]['model'][k]).abs().max().item() for k,v in ranks[0][step]['model'].items())
            assert 'policy.gru.weight_ih_l0' in target['grads']
            assert rank_error==0,rank_error
            # The scalar common logit bias has mathematically zero policy gradient;
            # FP32 reduction noise can move its tiny Adam moments without changing pi.
            other_parameter_error=max(v for k,v in errors.items() if k!='logit.bias')
            reference=Policy(**data['cfg']);reference.load_state_dict(target['model'])
            candidate=Policy(**data['cfg']);candidate.load_state_dict(ranks[0][step]['model'])
            b,h,pos=select(data,torch.arange(8))
            with torch.no_grad():
                expected_logits,expected_values=RecurrentForward(reference)(b,h,pos)
                actual_logits,actual_values=RecurrentForward(candidate)(b,h,pos)
                probability_error=(expected_logits.softmax(-1)-actual_logits.softmax(-1)).abs().max().item()
                value_error=(expected_values-actual_values).abs().max().item()
            rows.append(dict(global_samples=len(data['steps'][step][0]),rank0_samples=data['steps'][step][1],
                             gradient_max_abs_error=grad_error,parameter_max_abs_error=parameter_error,
                             gradient_relative_l2_error=gradient_relative_l2,
                             largest_parameter_difference=worst,other_parameter_max_abs_error=other_parameter_error,
                             policy_probability_max_abs_error=probability_error,value_max_abs_error=value_error,
                             between_rank_parameter_error=rank_error))
    report=dict(backend='cpu/gloo',torch=torch.__version__,model_cfg=data['cfg'],
                real_rollout_decisions=len(data['decisions']),full_country_history=True,
                history_turns=int(data['features'].shape[1]),
                resumed_adam=True,steps=rows,seconds=time.monotonic()-started,
                limitation='Correctness only; FP32 split-batch parameters can differ in softmax-invariant biases; two-T4 throughput has not been measured')
    args.report.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    assert all(r['gradient_relative_l2_error']<1e-4 for r in rows),'Relative gradient mismatch; inspect report'
    assert all(r['policy_probability_max_abs_error']<1e-5 for r in rows),'Policy mismatch; inspect report'
    assert all(r['value_max_abs_error']<1e-5 for r in rows),'Critic mismatch; inspect report'


if __name__=='__main__':main()
