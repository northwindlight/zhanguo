"""Pair full worlds with query caching off/on; compare observations and outcomes."""
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import torch
from host import GameHost
from rl.economy.model import load_policy
from rl.economy.online import consumption_total
from rl.economy.projects import InvestmentPlanner, LearnedProjects
from rl.economy.provenance import provenance
from rl.economy.scenarios import config


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,
                                    separators=(',',':')).encode()).hexdigest()


def encoded_digest(encoded):
    value=hashlib.sha256()
    for name in ('context','candidates','limits','legal'):
        array=encoded[name]
        value.update(json.dumps([name,array.shape,str(array.dtype)]).encode())
        value.update(array.tobytes(order='C'))
    value.update(digest({k:encoded[k] for k in ('actions','packages')}).encode())
    return value.hexdigest()


def simulate(checkpoint,case,enabled,out):
    os.environ['ZHANGUO_ECON_QUERY_CACHE']='1' if enabled else '0'
    model,_=load_policy(checkpoint)
    host=GameHost.new(config(case['seed'],size=case['size'],swap=case['swap']))
    focal=[]
    for actor,client in list(host.clients.items()):
        if client.version=='v13plus':
            host.clients[actor]=LearnedProjects(state=client.state,model=model)
            focal.append(actor)
    observations=[];actions=[];timing=Counter()
    original=InvestmentPlanner.encode
    def encode(planner):
        start=time.perf_counter()
        encoded=original(planner)
        timing['encode']+=time.perf_counter()-start
        observations.append(dict(actor=planner.player.actor,turn=host.clock(),
                                 candidates=len(encoded['candidates']),sha256=encoded_digest(encoded)))
        return encoded
    def receipt(actor,action,args,result):
        actions.append(digest([actor,action,args,result]))
    started=time.perf_counter()
    with patch.object(InvestmentPlanner,'encode',encode):
        for turn in range(case['turns']):
            host.cycle(on_result=receipt)
            if (turn+1)%10==0:
                print(json.dumps(dict(cache=enabled,turn=turn+1,seconds=time.perf_counter()-started)),flush=True)
    elapsed=time.perf_counter()-started
    final=host.to_save()
    with gzip.open(out/('cached-final.json.gz' if enabled else 'baseline-final.json.gz'),'wt') as stream:
        json.dump(final,stream,sort_keys=True,ensure_ascii=False,allow_nan=False)
    comparable=deepcopy(final)
    # Each newly created session gets an independent audit-stream UUID.
    # Preserve raw saves and disclose this one excluded identity field.
    audit_stream=comparable['host'].pop('audit_stream')
    result=dict(cache=enabled,seconds=elapsed,encode_seconds=timing['encode'],observations=observations,
                actions=actions,final_sha256=digest(comparable),raw_final_sha256=digest(final),
                excluded_identity={'host.audit_stream':audit_stream},modules_sha256=digest(final['modules']),
                consumption={a:consumption_total(host,a) for a in focal},
                failures={a:dict(project=host.clients[a].project_failures,truncated=host.clients[a].truncated,
                                 actions=dict(host.clients[a].economic_actions)) for a in focal})
    path=out/('cached.json' if enabled else 'baseline.json')
    path.write_text(json.dumps(result,indent=2))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--seed',type=int,required=True)
    p.add_argument('--size',type=int,default=20)
    p.add_argument('--swap',action='store_true')
    p.add_argument('--turns',type=int,default=150)
    p.add_argument('--cached-first',action='store_true')
    a=p.parse_args()
    if a.turns<1:p.error('turns must be positive')
    torch.set_num_threads(1);torch.set_num_interop_threads(1)
    a.out.mkdir(parents=True,exist_ok=True)
    if any(a.out.glob('*.json')):raise FileExistsError('Use an empty audit output directory')
    case=dict(seed=a.seed,size=a.size,swap=a.swap,turns=a.turns)
    origin=provenance();results={}
    for enabled in ((True,False) if a.cached_first else (False,True)):
        results[enabled]=simulate(a.checkpoint,case,enabled,a.out)
    baseline,cached=results[False],results[True]
    checks={k:baseline[k]==cached[k] for k in ('observations','actions','final_sha256','modules_sha256','consumption','failures')}
    result=dict(case=case,**origin,checks=checks,equal=all(checks.values()),formal=a.turns>=150,
                checkpoint_sha256=hashlib.sha256(a.checkpoint.read_bytes()).hexdigest(),
                decisions=len(baseline['observations']),receipts=len(baseline['actions']),
                baseline_seconds=baseline['seconds'],cached_seconds=cached['seconds'],
                baseline_encode_seconds=baseline['encode_seconds'],cached_encode_seconds=cached['encode_seconds'])
    if provenance()!=origin:raise RuntimeError('Implementation changed during audit')
    (a.out/'summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
    if not result['equal']:raise SystemExit('Cache audit mismatch')


if __name__=='__main__':main()
