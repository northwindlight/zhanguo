"""Audit policy-gradient signal in checksum-verified synthetic PPO trajectories."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np
import torch

from rl.economy.model import collate,load_policy,prepare
from rl.economy.online import monte_carlo
from rl.economy.provenance import require_compatible


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--round',type=int,default=9)
    p.add_argument('--samples',type=int,default=256)
    a=p.parse_args();torch.set_num_threads(1);torch.set_num_interop_threads(1)
    paths=list(a.root.glob(f'r{a.round}-trajectory-*.pkl.gz'))
    assert len(paths)==1
    path=paths[0];meta=json.loads(path.with_suffix('.json').read_text())
    require_compatible(meta)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['sha256']
    with gzip.open(path,'rb') as f:streams=pickle.load(f)
    monte_carlo(streams)
    full=[json.loads(line) for line in (a.root/f'r{a.round}-steps.jsonl').read_text().splitlines()]
    raw=np.array([s['advantage'] for s in full]);mean,std=float(raw.mean()),float(raw.std())
    model,_=load_policy(a.root/f'r{a.round}-behavior.pt');model.eval()
    selected=[]
    for stream_index,stream in enumerate(streams):
        count=min(len(stream),a.samples//len(streams))
        for index in np.linspace(0,len(stream)-1,count,dtype=int):
            selected.append((stream_index,stream[int(index)]))
    records=[];error=0.
    with torch.inference_mode():
        for stream_index,step in selected:
            row=step['row'];logits,_,_=model(prepare(collate([row]),'cpu'),return_value=True)
            logp=logits[0].log_softmax(-1);probability=logp.exp()
            error=max(error,abs(float(logp[step['action']])-step['logp']))
            kinds=row['candidates'][:,5:10].argmax(-1)
            masses={kind:float(probability[torch.from_numpy(kinds==i)].sum())
                    for i,kind in enumerate(('finish','build','recruit','buy','sell')) if i<3}
            advantage=(step['return']-step['value']-mean)/max(std,1e-6)
            signal={kind:advantage*(int(step['action_kind']==kind)-mass) for kind,mass in masses.items()}
            records.append(dict(stream=stream_index,period=step['period'],action=step['action_kind'],
                advantage=advantage,mass=masses,family_logit_ascent=signal,
                family_head=model.last_family_logits.softmax(-1)[0].tolist()))
    assert error<1e-4,error
    summary=dict(round=a.round,seed=meta['seed'],samples=len(records),logp_max_error=error,
        normalization_worlds=len(set(r['seed'] for r in full)),
        normalization_steps=len(full),
        interpretation='Derivative for adding a constant to a family logit: A*(sampled_family - family_probability). '
        'This is a sampled local direction, not a causal action value or proof of an update outcome.',
        mean_family_ascent={k:float(np.mean([r['family_logit_ascent'][k] for r in records]))
                            for k in ('finish','build','recruit')},records=records)
    (a.root/f'r{a.round}-gradient-probe.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='records'}))


if __name__=='__main__':main()
