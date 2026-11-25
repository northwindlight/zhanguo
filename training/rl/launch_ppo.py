from pathlib import Path
import subprocess
import sys

root = Path('/content/codex-takeover')
with (root / 'ppo.log').open('ab') as log:
    job = subprocess.Popen([sys.executable, '-u', root / 'run_ppo.py'],
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                           start_new_session=True)
print(f'PPO supervisor PID {job.pid}', flush=True)
