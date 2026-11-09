"""Native TorchTPU compatibility probe; never exports a trained policy.

Uses the runtime's existing Python in an isolated venv, not Python/XLA or a
specific Python minor version. The PPO step uses synthetic rewards solely to
check operator/backward support; its weights cannot enter the training chain.
"""
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import traceback
import venv

OUT=Path('/kaggle/working')


def save(result):
    (OUT/'tpu-probe-result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)


def inner():
    import torch
    import torch_tpu
    torch.set_num_threads(1)
    torch.set_float32_matmul_precision('highest')
    device=torch.device('tpu')
    marker=torch.ones((4,4),device=device)
    result=dict(kind='native TorchTPU operator/PPO smoke; synthetic rewards; no policy promotion',
                promotable=False,python=sys.version,torch=str(torch.__version__),
                torch_tpu=importlib.metadata.version('torch_tpu'),device=str(marker.device),
                tensor_sum=float(marker.sum().cpu()))
    print(json.dumps(dict(event='native_tpu_verified',**result)),flush=True)
    manifests=list(Path('/kaggle/input').rglob('bundle-manifest.json'))
    if len(manifests)!=1:raise ValueError('Expected one pinned private model bundle')
    source=manifests[0].parent;manifest=json.loads(manifests[0].read_text())
    root=Path('/tmp/zhanguo-tpu-model');root.mkdir(exist_ok=True)
    for name in ('runtime.bin','recovery.bin'):
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=manifest[name]['sha256']:
            raise ValueError('Input bundle checksum mismatch')
        with tarfile.open(source/name) as archive:archive.extractall(root,filter='data')
    os.chdir(root);sys.path.insert(0,str(root))
    subprocess.run([sys.executable,'-m','ruleai.v13.build'],check=True)
    from host import GameHost
    from ruleai.v11plus.ledger import Ledger
    from rl.economy.scenarios import config
    from rl.economy.projects import InvestmentPlanner,sample
    from rl.economy.model import load_policy,collate,prepare
    from rl.economy.online import ppo_update
    paths=sorted((root/'rl/runs/ppo-cycle').glob('round-*/candidate.pt'))
    model,saved=load_policy(paths[-1]);anchor=copy.deepcopy(model)
    host=GameHost.new(config(8101,size=20))
    actor=next(a for a,c in host.clients.items() if c.version=='v13plus')
    ledger=Ledger(host.player(actor));ledger.do('plan',text='isolated native TPU API probe')
    planner=InvestmentPlanner(ledger,host.clients[actor].state)
    encoded=planner.encode();row=sample(encoded,0)
    with torch.inference_mode():
        logits,values,_=model(collate([row,row]),return_value=True)
        actions=logits[0].argsort(descending=True)[:2].tolist()
        old=[float(logits.log_softmax(-1)[i,a]) for i,a in enumerate(actions)]
        previous_values=values.tolist()
    model.to(device)
    started=time.time()
    with torch.inference_mode():
        actual=model(prepare(collate([row,row]),device),return_value=True)[0].cpu()
    error=max(abs(float(actual.log_softmax(-1)[i,a])-old[i]) for i,a in enumerate(actions))
    result.update(forward_seconds=time.time()-started,max_logp_difference=error)
    if error>1e-4:raise ValueError(f'Native TPU logp differs from CPU: {error}')
    streams=[[dict(row=row,action=a,logp=old[i],value=previous_values[i],reward=1.+i,period=i)]
             for i,a in enumerate(actions)]
    started=time.time()
    training=ppo_update(model,streams,anchor,epochs=1,batch_size=2,update_batch_size=2)
    gradient_norm=float(model.score[-1].weight.grad.norm().cpu())
    if training['updates']!=1 or training['coverage']!=1.:
        raise RuntimeError('Native TPU probe did not consume both PPO decisions')
    if not math.isfinite(gradient_norm) or gradient_norm<=0:
        raise RuntimeError('Native TPU probe did not produce a finite, nonzero policy gradient')
    result.update(status='passed',update_seconds=time.time()-started,updates=training['updates'],
                  coverage=training['coverage'],policy_gradient_norm=gradient_norm)
    save(result)


def main():
    OUT.mkdir(exist_ok=True)
    try:
        if '--inner' in sys.argv:
            inner();return
        environment=Path('/tmp/zhanguo-native-tpu-env')
        venv.EnvBuilder(with_pip=True,system_site_packages=True).create(environment)
        python=environment/'bin/python'
        print(json.dumps(dict(event='install_native_torch_tpu',python=sys.version)),flush=True)
        installed=subprocess.run([str(python),'-m','pip','install','--quiet','torch_tpu==0.2.0',
                                  '--extra-index-url','https://download.pytorch.org/whl/cpu'],
                                 capture_output=True,text=True,timeout=600)
        if installed.returncode:
            raise RuntimeError('TorchTPU install failed: '+re.sub(r'https?://\S+','[URL]',installed.stdout+installed.stderr)[-1500:])
        completed=subprocess.run([str(python),str(Path(__file__).resolve()),'--inner'],timeout=900)
        if completed.returncode and not (OUT/'tpu-probe-result.json').exists():
            raise RuntimeError('Native TPU probe subprocess failed')
    except Exception as error:
        save(dict(status='failed',promotable=False,error=re.sub(r'https?://\S+','[URL]',str(error)),
                  traceback=re.sub(r'https?://\S+','[URL]',traceback.format_exc())[-4000:]))


if __name__=='__main__':main()
