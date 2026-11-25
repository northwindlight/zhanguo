"""Compare the production targets with a decision-chain reference; no training."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'cpp'))
from ppo_update import episode_targets
from read_trajectory import episodes


def reference(samples,rewards,gamma,lam,scale,per_action_lambda=False):
    advantage=np.zeros(len(samples),dtype=np.float64);carry=0.
    for j in range(len(samples)-1,-1,-1):
        last=j==len(samples)-1;boundary=last or samples[j+1]['ti']!=samples[j]['ti']
        reward=rewards[samples[j]['ti']]/scale if boundary else 0.
        discount=gamma if boundary else 1.
        trace=lam if boundary or per_action_lambda else 1.
        following=0. if last else samples[j+1]['v']
        delta=reward+discount*following-samples[j]['v']
        carry=delta+discount*trace*carry
        advantage[j]=carry
    return advantage+np.array([r['v'] for r in samples])


def run():
    rng=np.random.default_rng(540000);max_error=0.;cases=0
    for gamma in (1.,.99,.95):
      for lam in (1.,.99,.95,0.):
       for _ in range(30):
        count=rng.integers(1,32,size=12);rewards=rng.uniform(-5,60,size=12)
        samples=[dict(ti=t,v=float(rng.normal())) for t,n in enumerate(count) for _ in range(n)]
        opts=SimpleNamespace(gamma=gamma,gae_lambda=lam,reward_scale=3000.)
        actual=np.array(episode_targets(samples,0.,dict(turn_rewards=rewards.tolist()),opts))
        expected=reference(samples,rewards,gamma,lam,3000.)
        max_error=max(max_error,float(np.max(np.abs(actual-expected))));cases+=1
    assert max_error<1e-6
    datasets={}
    for name,folder in [('old_native_smoke',ROOT/'build-selfplay/selfplay-tracks'),('selfplay_smoke',Path('/tmp/land-gdp-smoke/normal'))]:
      total=forced=turns=0;errors=[];max_record_error=0.;heldout=[]
      for samples,reward,summary,features,_ in episodes(folder,mmap_arrays=True):
        rewards=summary['nominal_gdp'];opts=SimpleNamespace(gamma=1.,gae_lambda=.99,reward_scale=3000.)
        actual=np.asarray(episode_targets(samples,reward,dict(turn_rewards=rewards),opts))
        expected=reference(samples,rewards,1.,.99,3000.)
        max_record_error=max(max_record_error,float(np.max(np.abs(actual-expected))))
        unique,counts=np.unique([s['ti'] for s in samples],return_counts=True)
        total+=len(samples);turns+=len(unique)
        for ti,n in zip(unique,counts):
          decisions=[s for s in samples if s['ti']==ti]
          if n==31 and int(decisions[-1]['obs']['cand_type'][decisions[-1]['a']])!=0:forced+=1
        heldout.append(dict(seed=summary['seed'],seat=summary['seat'],turns=summary['turns'],decisions=len(samples)))
      datasets[name]=dict(episodes=heldout,decisions=total,country_turns=turns,non_end_actions_replaced_at_cap=forced,max_reference_error=max_record_error,
          limitation='Historical short smoke trajectories, not the deleted post-200-world production rollout')
    # A simple mixed-time demonstration: changing event count changes event-level
    # trace attenuation even though the same amount of game time has elapsed.
    retention={str(n):.99**n for n in (1,30,120,1000)}
    output=dict(random_cases=cases,max_target_error=max_error,turn_shared_targets_equivalent_to_action_gae_with_unit_intra_turn_trace=True,
        datasets=datasets,lambda_099_after_global_events=retention,
        conclusion='Sharing a turn target is not itself a sign/GAE bug. A global clock needs explicit discount/trace semantics; it does not automatically infer action combinations.')
    Path(__file__).with_name('math-results.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))


if __name__=='__main__':run()
