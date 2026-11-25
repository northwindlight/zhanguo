"""Two independent recurrent PPO learners, using disjoint seats of the same world.

Runs real production Policy and checkpoint on CPU. No distributed gradient sync.
"""
import copy
from itertools import combinations
import json
from pathlib import Path
import tempfile
import time

import torch
import torch.multiprocessing as mp

from verify_ddp import ROOT,HERE,Policy,RecurrentForward,fixture,loss


def owners(seed):
    """Cycle all six ways of assigning two of four seats to policy A."""
    seats=tuple(combinations(range(4),2))[seed%6]
    return tuple('A' if seat in seats else 'B' for seat in range(4))


def update(data,identity):
    torch.set_num_threads(1);torch.backends.mha.set_fastpath_enabled(False)
    countries=data['countries']
    active=[i for i,(country,_) in enumerate(data['positions'])
            if owners(countries[int(country)]['seed'])[countries[int(country)]['seat']]==identity]
    indices=torch.tensor(active)
    assert len(indices)==4
    local=dict(data)
    advantage=data['advantages'].clone()
    raw=advantage[indices]
    advantage[indices]=(raw-raw.mean())/(raw.std(unbiased=False)+1e-6)
    local['advantages']=advantage
    policy=Policy(**data['cfg']);policy.load_state_dict(data['model'])
    wrapper=RecurrentForward(policy)
    optimizer=torch.optim.AdamW(wrapper.parameters(),lr=5e-4,weight_decay=0)
    optimizer.load_state_dict(copy.deepcopy(data['optimizer']))
    for _ in range(3):
        optimizer.zero_grad(set_to_none=True)
        objective=loss(wrapper,local,indices)
        assert torch.isfinite(objective)
        objective.backward();torch.nn.utils.clip_grad_norm_(wrapper.parameters(),1.);optimizer.step()
    return dict(model=policy.state_dict(),optimizer=optimizer.state_dict(),
                indices=active,countries=[c for c in countries if owners(c['seed'])[c['seat']]==identity])


def worker(identity,source,destination):
    data=torch.load(source,weights_only=False)
    torch.save(update(data,identity),destination)


def main():
    started=time.monotonic()
    torch.set_num_threads(1);torch.backends.mha.set_fastpath_enabled(False)
    data=fixture(Path('/tmp/land-gdp-smoke/normal'),ROOT/'industry-bc-results/epoch-04.pt')
    assert len({c['seed'] for c in data['countries']})==1
    initial=copy.deepcopy(data['model'])
    expected=update(data,'A')
    assert all(torch.equal(v,data['model'][k]) for k,v in initial.items())
    poisoned=copy.deepcopy(data)
    for country,c in enumerate(poisoned['countries']):
        if owners(c['seed'])[c['seat']]=='B':
            poisoned['features'][country]+=1000
            selected=poisoned['positions'][:,0]==country
            poisoned['returns'][selected]+=1000
            poisoned['advantages'][selected]+=1000
    isolated=update(poisoned,'A')
    isolation_error=max((v-isolated['model'][k]).abs().max().item() for k,v in expected['model'].items())
    assert isolation_error==0,isolation_error
    with tempfile.TemporaryDirectory(prefix='zhanguo-independent-proof-') as temp:
        path=Path(temp);torch.save(data,path/'fixture.pt')
        processes=[]
        context=mp.get_context('spawn')
        for identity in ('A','B'):
            p=context.Process(target=worker,args=(identity,path/'fixture.pt',path/f'{identity}.pt'))
            p.start();processes.append(p)
        for process in processes:
            process.join(timeout=120)
            if process.is_alive():process.terminate();process.join();raise TimeoutError('Independent learner stalled')
            assert process.exitcode==0,process.exitcode
        learners={i:torch.load(path/f'{i}.pt',weights_only=True) for i in ('A','B')}
        a=learners['A'];b=learners['B']
        assert set(a['indices']).isdisjoint(b['indices'])
        assert set(a['indices'])|set(b['indices'])==set(range(8))
        concurrent_error=max((v-a['model'][k]).abs().max().item() for k,v in expected['model'].items())
        assert concurrent_error==0,concurrent_error
        divergence=max((v-b['model'][k]).abs().max().item() for k,v in a['model'].items())
        assert divergence>0
    rotation=[owners(seed) for seed in range(6)]
    assert len(set(rotation))==6
    assert all(r.count('A')==r.count('B')==2 for r in rotation)
    assert all(sum(r[seat]=='A' for r in rotation)==3 for seat in range(4))
    report=dict(backend='two independent CPU processes',model_cfg=data['cfg'],same_world_seed=data['countries'][0]['seed'],
                assignment=owners(data['countries'][0]['seed']),rotation=rotation,
                real_decisions=8,updates_per_learner=3,gradient_synchronization=False,
                own_history_only=True,independent_adam_states=True,
                other_learner_data_perturbation_parameter_error=isolation_error,
                concurrent_vs_serial_parameter_error=concurrent_error,policy_A_B_max_parameter_difference=divergence,
                A_countries=learners['A']['countries'],B_countries=learners['B']['countries'],
                seconds=time.monotonic()-started,limitations='CPU proof on 3-turn rollouts; dynamic C++ sampler and dual-T4 throughput remain unimplemented/unmeasured')
    (HERE/'independent-proof.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()
