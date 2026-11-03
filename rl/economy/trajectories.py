"""Lossless, atomic snapshots of this runner's own on-policy trajectories."""
import gzip
import hashlib
import json
from pathlib import Path
import pickle

import torch

from .model import collate,prepare,device_of
from .provenance import provenance,require_compatible


def model_digest(model):
    digest=hashlib.sha256(json.dumps(getattr(model,'config',{}),sort_keys=True).encode())
    for name,value in model.state_dict().items():
        value=value.detach().cpu().contiguous()
        digest.update(name.encode());digest.update(str(value.dtype).encode());digest.update(str(tuple(value.shape)).encode())
        digest.update(value.reshape(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def save(path,streams,metadata):
    path=Path(path);temporary=path.with_suffix('.tmp')
    try:
        with gzip.open(temporary,'wb',compresslevel=3) as output:pickle.dump(streams,output,protocol=5)
        temporary.replace(path)
        manifest=dict(provenance(),**metadata,steps=sum(map(len,streams)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        sidecar=path.with_suffix('.json');tmp=sidecar.with_suffix('.tmp')
        tmp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');tmp.replace(sidecar)
    finally:temporary.unlink(missing_ok=True)


def load(path,expected):
    path=Path(path);manifest=json.loads(path.with_suffix('.json').read_text())
    require_compatible(manifest)
    if any(manifest.get(k)!=v for k,v in expected.items()):raise ValueError('Trajectory behavior/configuration mismatch')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('Trajectory checksum mismatch')
    # Only locally generated, checksum-checked runner artifacts are accepted.
    with gzip.open(path,'rb') as source:streams=pickle.load(source)
    if sum(map(len,streams))!=manifest['steps']:raise ValueError('Incomplete trajectory snapshot')
    return streams


def verify_log_probabilities(model,streams):
    steps=[step for stream in streams for step in stream]
    if not steps:raise ValueError('Empty on-policy data')
    count=min(64,len(steps));indices=[i*(len(steps)-1)//max(1,count-1)for i in range(count)]
    model.eval();error=0.
    with torch.inference_mode():
        for offset in range(0,count,4):
            chunk=[steps[i]for i in indices[offset:offset+4]]
            logits,_=model(prepare(collate([s['row']for s in chunk]),device_of(model)))
            logp=logits.log_softmax(-1)
            for j,step in enumerate(chunk):error=max(error,abs(float(logp[j,step['action']])-step['logp']))
    if error>1e-4:raise ValueError(f'Behavior log probabilities mismatch: {error}')
    return error
