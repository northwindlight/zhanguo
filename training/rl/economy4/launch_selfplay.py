import fcntl
from pathlib import Path
import subprocess
import sys
import zipfile


root = Path('/content/codex-market4-selfplay')
results = root / 'results-selfplay-60'
results.mkdir(parents=True, exist_ok=True)
with (results / '.lock').open('a') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Four-seat self-play already running', flush=True)
    else:
        with zipfile.ZipFile(root.with_suffix('.zip')) as source:
            source.extractall(root)
        with (root / 'driver.log').open('ab') as log:
            job = subprocess.Popen([sys.executable, '-u', root / 'continue_market4.py',
                                    '--self-play', '--turns', '60'],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        print('Four-seat self-play PID', job.pid, flush=True)
