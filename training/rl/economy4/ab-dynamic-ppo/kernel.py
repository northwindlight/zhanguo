"""Finite A/B segment on two T4s. Both inference and PPO use the two GPUs."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
from zipfile import ZipFile

EXPECTED_GAMES = 200 # AB_EXPECTED_GAMES
EXPECTED_PAIR = {'A': '1952ea463e5421670c2b245e610568f988a66d7f30d8e471adb5e4750d96e4c0', 'B': '4ba8b8be85883220cf4dcf6c9f8bd54d392d8816a6df72bd8fd0afddf2cea132'} # AB_EXPECTED_PAIR


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    root=Path('/kaggle/working');work=root/'work';work.mkdir(exist_ok=True)
    os.environ.update(OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='1',NVIDIA_TF32_OVERRIDE='0')
    inputs=Path('/kaggle/input');code=list(inputs.rglob('ab-code.bundle'));states=list(inputs.rglob('resume-state.json'));original=list(inputs.rglob('handoff.bundle'))
    if len(code)!=1 or len(states)!=1 or len(original)!=1:raise ValueError('Ambiguous A/B inputs')
    with ZipFile(code[0]) as z:z.extractall(root/'code')
    config_path=root/'code/run-config.json';config=json.loads(config_path.read_text());state=json.loads(states[0].read_text())
    if state['games']!=EXPECTED_GAMES or {i:r['sha256'] for i,r in state['models'].items()}!=EXPECTED_PAIR:
        raise ValueError('Stale A/B checkpoint dataset version')
    if state['contract']!=config:raise ValueError('Wrong A/B reward/training contract')
    for identity,row in state['models'].items():
        source=states[0].parent/row['latest']
        if sha(source)!=row['sha256']:raise ValueError('Corrupt A/B input model')
        shutil.copyfile(source,work/row['latest'])
    (work/'state.json').write_text(json.dumps(state,indent=2))
    baseline=Path('/tmp/ab-baseline.pt')
    with ZipFile(original[0]) as z:
        manifest=json.loads(z.read('handoff-manifest.json'));baseline.write_bytes(z.read('work/epoch-04-native.pt'))
        if sha(baseline)!=manifest['files']['work/epoch-04-native.pt']:raise ValueError('Corrupt evaluation baseline')
    import torch
    if torch.cuda.device_count()!=2:raise RuntimeError('A/B training requires two allocated GPUs')
    hardware=dict(torch=str(torch.__version__),gpus=[torch.cuda.get_device_name(i) for i in range(2)],cpus=os.cpu_count(),
                  scratch_free_gb=shutil.disk_usage('/tmp').free/2**30)
    if hardware['scratch_free_gb']<15:raise RuntimeError('Insufficient bounded rollout scratch')
    (work/'launch.json').write_text(json.dumps(dict(hardware=hardware,games=state['games'],goal=config['games'],contract=config),indent=2))
    print(json.dumps(dict(stage='ab_segment_start',games=state['games'],goal=config['games'],**hardware)),flush=True)
    args=[sys.executable,'-u',root/'code/ab_ppo_run.py','--work',work,'--config',config_path,'--baseline',baseline]
    with (work/'driver.log').open('w') as log:
        process=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in process.stdout:print(line,end='',flush=True);log.write(line);log.flush()
        rc=process.wait()
    if rc:raise RuntimeError('A/B segment failed: '+str(rc))
    segment=json.loads((work/'segment.json').read_text())
    for row in segment['models'].values():
        if sha(work/row['latest'])!=row['sha256']:raise ValueError('Corrupt A/B output model')
    print(json.dumps(dict(stage='ab_segment_complete',**segment)),flush=True)


if __name__=='__main__':main()
