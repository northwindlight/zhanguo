"""Bound the Colab CLI by the training deadline and independent backup health."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def health_failure(path,now,started,*,startup_grace=1800,stale_after=1800):
    try:
        status=json.loads(Path(path).read_text())
    except (OSError,ValueError):
        return 'backup_health_missing' if now-started>=startup_grace else None
    if now-status['checked_at']>=stale_after:return 'backup_health_stale'
    if status.get('consecutive_failures',0)>=2:return 'backup_failed'
    return None


def run_watched(command,*,deadline,health_path,stdout=None,poll=30,startup_grace=1800,stale_after=1800):
    started=time.time()
    if started>=deadline:return dict(reason='deadline',returncode=None)
    process=subprocess.Popen(command,stdout=stdout,stderr=subprocess.STDOUT,start_new_session=True)
    reason='completed'
    try:
        while process.poll() is None:
            now=time.time()
            reason=('deadline' if now>=deadline else
                health_failure(health_path,now,started,startup_grace=startup_grace,stale_after=stale_after))
            if reason:break
            time.sleep(min(poll,max(0,deadline-now)))
        else:reason='completed'
    finally:
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
    return dict(reason=reason,returncode=process.returncode)
