"""Replace the queued environment probe with native TorchTPU validation when free."""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.econ_kaggle_watch import command,status
from scripts.econ_ppo_watch import write


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();kernel='northwindlight/zhanguo-economy-tpu-probe'
    marker=args.out/'native-tpu-probe-state.json'
    state=json.loads(marker.read_text()) if marker.exists() else dict(version=1,stage='environment_probe')
    while True:
        try:
            remote=status(f"{kernel}/{state['version']}")
            state.update(remote_status=remote);write(marker,state)
            print(json.dumps(state),flush=True)
            if state['version']==1:
                # Only one batch TPU session can be pending. The first queued
                # environment probe must finish before the full probe can run.
                if remote in ('QUEUED','RUNNING'):time.sleep(120);continue
                next_status=status(f'{kernel}/2',allow_missing=True)
                if next_status is None:
                    folder=args.out/'tpu-probe'
                    shutil.copyfile(ROOT/'scripts/econ_tpu_probe.py',folder/'probe.py')
                    response=command(['kernels','push','-p',str(folder),'--accelerator','TpuV5E8','-t','1800'],120)
                    match=re.search(r'Kernel version (\d+)',response)
                    if not match:raise RuntimeError('Full native TPU probe not accepted by scheduler')
                    version=int(match.group(1))
                else:version=2
                state=dict(version=version,stage='native_torch_tpu_probe');write(marker,state)
            elif remote not in ('QUEUED','RUNNING'):
                destination=args.out/'native-tpu-output';destination.mkdir(exist_ok=True)
                command(['kernels','output',f"{kernel}/{state['version']}",'-p',str(destination),'-o','-q',
                         '--file-pattern',r'tpu-probe-result\.json$'],180)
                results=list(destination.rglob('tpu-probe-result.json'))
                if len(results)!=1:raise RuntimeError('Native TPU job ended without its result')
                result=json.loads(results[0].read_text())
                state.update(stage='finished',result=result);write(marker,state);return
        except Exception as error:
            write(args.out/'native-tpu-probe-retry.json',dict(error=str(error)))
            print('Native TPU probe monitor retry: '+type(error).__name__,flush=True)
        time.sleep(120)


if __name__=='__main__':main()
