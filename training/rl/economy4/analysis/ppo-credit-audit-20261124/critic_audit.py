"""Check critic inheritance, gradient isolation and production update diagnostics."""
import json
from pathlib import Path
import sys
import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'analysis/dynamic-turn-dual-ppo-20261124/reference/python'))
from rl.model import Policy
torch.set_num_threads(2)


def run():
    sources=dict(economic_base=ROOT/'mixed-bc-results/best-batch-mean.pt',bc4=ROOT/'industry-bc-results/epoch-04.pt',
        A200=ROOT/'ab-dynamic-ppo-results/segments/001/work/ck-0025-A.pt',
        B200=ROOT/'ab-dynamic-ppo-results/segments/001/work/ck-0025-B.pt')
    weights={};rows={}
    for name,path in sources.items():
        ck=torch.load(path,map_location='cpu',weights_only=True);weights[name]={k:v.clone() for k,v in ck['model'].items() if k.startswith('value.')}
        rows[name]=dict(critic_parameters=sum(v.numel() for v in weights[name].values()),
            has_ppo_optimizer=bool(ck.get('optimizer')),finite=all(torch.isfinite(v).all().item() for v in ck['model'].values()))
    equal=all(torch.equal(v,weights['bc4'][k]) for k,v in weights['economic_base'].items())
    ck=torch.load(sources['A200'],map_location='cpu',weights_only=True);model=Policy(**ck['cfg'])
    model.load_state_dict(ck['model']);model.value(torch.randn(4,300)).sum().backward()
    gradient_names=[name for name,p in model.named_parameters() if p.grad is not None]
    assert all(name.startswith('value.') for name in gradient_names)
    updates=[]
    for path in sorted((ROOT/'ab-dynamic-ppo-results/segments/001/work').glob('batch-*.json')):
        r=json.loads(path.read_text())
        for identity,record in r['updates'].items():
            updates.append(dict(batch=r['batch'],games=r['games'],policy=identity,kl=record['approx_kl'],
                                clip_frac=record['clip_frac'],sampler_dlogp=record['sampler_dlogp']))
    output=dict(models=rows,bc4_critic_identical_to_economic_base=equal,
        critic_gradient_parameters=gradient_names,value_gradient_enters_actor_or_gru=False,
        shared_global_gradient_norm_clipping=True,
        explanation='Actor and critic parameter graphs are separate, but clip_grad_norm_(all parameters, 1) couples their update scale.',
        production_updates=updates,
        diagnostics_missing=['critic MSE separately','critic explained variance','Monte Carlo return calibration',
                             'actor/critic gradient norms separately','exact frozen-policy post-update KL',
                             'accepted/rejected update evaluation'],
        limitation='No retained on-policy full-horizon rollout for the A200/B200 pair. This audit does not establish critic calibration or the historical collapse cause.')
    Path(__file__).with_name('critic-results.json').write_text(json.dumps(output,indent=2))
    print(json.dumps({k:v for k,v in output.items() if k!='production_updates'},indent=2))


if __name__=='__main__':run()
