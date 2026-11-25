"""Finite global-event, joint-action A/B PPO from pure-script BC weights."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
from zipfile import ZipFile

EXPECTED_GAMES = 1200 # AB_EXPECTED_GAMES
EXPECTED_PAIR = {'A': '268a7922f8373d099e9ca329338ff5eff8a9b40ef481f1ecc63641e07a2df073', 'B': '29caaebd161423a9d49587c81bcca9f2ba0836382bbc0d3bd93f2656139d7173'} # AB_EXPECTED_PAIR


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    root=Path('/kaggle/working');work=root/'work';work.mkdir(exist_ok=True)
    os.environ.update(OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1',NVIDIA_TF32_OVERRIDE='0')
    inputs=Path('/kaggle/input');code=list(inputs.rglob('ab-code.bundle'));states=list(inputs.rglob('resume-state.json'));baseline=list(inputs.rglob('baseline-native.pt'))
    if any(len(x)!=1 for x in (code,states,baseline)):raise ValueError('Ambiguous global PPO inputs')
    with ZipFile(code[0]) as z:z.extractall(root/'code')
    config=root/'code/run-config.json';contract=json.loads(config.read_text());state=json.loads(states[0].read_text())
    if state['games']!=EXPECTED_GAMES or {i:r['sha256'] for i,r in state['models'].items()}!=EXPECTED_PAIR or state['contract']!=contract:
        raise ValueError('Stale global PPO state')
    for row in state['models'].values():
        source=states[0].parent/row['latest']
        if sha(source)!=row['sha256']:raise ValueError('Corrupt global PPO checkpoint')
        shutil.copyfile(source,work/row['latest'])
    (work/'state.json').write_text(json.dumps(state,indent=2))
    import torch
    if torch.cuda.device_count()!=2:raise RuntimeError('Global A/B training requires two GPUs')
    print(json.dumps(dict(stage='global_ppo_start',games=state['games'],goal=contract['games'],gpus=[torch.cuda.get_device_name(i) for i in range(2)])),flush=True)
    args=[sys.executable,'-u',root/'code/global_ppo_run.py','--work',work,'--config',config,'--baseline',baseline[0]]
    with (work/'driver.log').open('w') as f:
        p=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in p.stdout:print(line,end='',flush=True);f.write(line);f.flush()
        if p.wait():raise RuntimeError('Global PPO segment failed')
    segment=json.loads((work/'segment.json').read_text())
    print(json.dumps(dict(stage='global_ppo_segment_complete',**segment)),flush=True)


if __name__=='__main__':main()
