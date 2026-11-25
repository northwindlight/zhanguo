"""Run the existing durable backup machinery against the industrial curriculum."""
import fcntl
from pathlib import Path
import sys
import watch_expansion_v2 as worker

worker.REMOTE='content/zhanguo-industry-bc/'
worker.REMOTE_ROOT='/content/zhanguo-industry-bc'
worker.LAUNCHER='launch_industry_bc.py'
worker.CODE_STAGE='zhanguo-industry-bc-code.zip'
worker.RESTORE_STAGE='zhanguo-industry-bc-restore.json'

if __name__=='__main__':
    config,out=map(Path,sys.argv[1:])
    out.mkdir(parents=True,exist_ok=True)
    with (out/'.watch-lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        worker.watch(config,out)
