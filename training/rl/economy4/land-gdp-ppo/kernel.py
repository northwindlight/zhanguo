"""A finite Kaggle segment; consumes a verified checkpoint and exits after real work."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
from zipfile import ZipFile

EXPECTED_START_GAMES = 8 # SCHEDULER_EXPECTED_START
EXPECTED_INPUT_SHA = 'f9aa47b00564d2b4bb52921f3c2d5cabf6d48f2c55f3d443b3ed27342e0a78c5' # SCHEDULER_EXPECTED_SHA


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    os.environ.update(NVIDIA_TF32_OVERRIDE='0',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='4')
    root=Path('/kaggle/working');work=root/'work';work.mkdir(exist_ok=True)
    bundles=list(Path('/kaggle/input').rglob('land-gdp-code.bundle'))
    states=list(Path('/kaggle/input').rglob('resume-state.json'))
    originals=list(Path('/kaggle/input').rglob('handoff.bundle'))
    if len(bundles)!=1 or len(states)!=1 or len(originals)!=1:raise ValueError('Ambiguous PPO inputs')
    with ZipFile(bundles[0]) as z:z.extractall(root/'code')
    config_path=root/'code/run-config.json';config=json.loads(config_path.read_text())
    state=json.loads(states[0].read_text());source=states[0].parent/state['latest']
    if state['games']!=EXPECTED_START_GAMES or (EXPECTED_INPUT_SHA and state['sha256']!=EXPECTED_INPUT_SHA):
        raise ValueError('Kaggle attached a stale checkpoint dataset version')
    if sha(source)!=state['sha256'] or state['contract']!=config:raise ValueError('PPO resume contract/checksum mismatch')
    shutil.copyfile(source,work/state['latest']);(work/'state.json').write_text(json.dumps(state,indent=2))
    baseline=Path('/tmp/land-gdp-baseline');baseline.mkdir(exist_ok=True)
    with ZipFile(originals[0]) as z:
        manifest=json.loads(z.read('handoff-manifest.json'))
        for name in ('work/epoch-04.pt','work/epoch-04-native.pt'):
            path=baseline/Path(name).name;path.write_bytes(z.read(name))
            if sha(path)!=manifest['files'][name]:raise ValueError('Original BC baseline checksum mismatch')
    import torch
    if not torch.cuda.is_available():raise RuntimeError('PPO segment requires an allocated GPU')
    hardware=dict(torch=str(torch.__version__),gpus=[torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
                  cpus=os.cpu_count(),scratch_free_gb=shutil.disk_usage('/tmp').free/2**30)
    if hardware['scratch_free_gb']<12:raise RuntimeError('Insufficient scratch space for the bounded raw rollout batch')
    (work/'launch.json').write_text(json.dumps(dict(hardware=hardware,start_games=state['games'],contract=config),indent=2))
    print(json.dumps(dict(stage='ppo_segment_start',games=state['games'],goal=config['games'],**hardware)),flush=True)
    command=[sys.executable,'-u',str(root/'code/land_gdp_ppo_run.py'),'--work',str(work),
             '--baseline',str(baseline/'epoch-04-native.pt'),'--config',str(config_path),
             '--stage-games',str(config['segment_games'])]
    with (work/'driver.log').open('w') as log:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in process.stdout:
            print(line,end='',flush=True);log.write(line);log.flush()
        rc=process.wait()
    if rc:raise RuntimeError('PPO segment failed: '+str(rc))
    end=json.loads((work/'segment.json').read_text())
    if sha(work/end['latest'])!=end['sha256']:raise ValueError('Completed checkpoint checksum mismatch')
    print(json.dumps(dict(stage='ppo_segment_complete',**end)),flush=True)


if __name__=='__main__':main()
