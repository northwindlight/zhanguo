"""Wait for verified BC10, then launch the validated finite global PPO chain."""
import json
from pathlib import Path
import time

import watch_ab_ppo as runner
from prepare_global_ppo import prepare,source_digest
from mixed_bc_run import publish

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'script-global-ppo';OUT=ROOT/'script-global-ppo-results'


def main():
    OUT.mkdir(exist_ok=True)
    while not (ROOT/'script-bc-kaggle-results/next-stage-ready.json').exists() or not (STAGE/'validated-code.json').exists():
        publish(OUT/'live.json',dict(state='waiting_for_verified_BC10_and_global_code',time=time.time()));time.sleep(30)
    if source_digest()!=json.loads((STAGE/'validated-code.json').read_text())['source_sha256']:raise ValueError('Global PPO implementation changed after validation')
    prepare()
    runner.STAGE=STAGE;runner.OUT=OUT;runner.KERNEL='northwindlight/zhanguo-script-global-ppo';runner.source_digest=source_digest
    runner.shared.STAGE=STAGE;runner.shared.OUT=OUT;runner.shared.KERNEL=runner.KERNEL
    runner.main()


if __name__=='__main__':
    try:main()
    except Exception as e:
        OUT.mkdir(exist_ok=True);publish(OUT/'failure.json',dict(error=type(e).__name__,message=str(e),time=time.time()));print(type(e).__name__,str(e),flush=True)
