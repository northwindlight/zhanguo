from pathlib import Path
import subprocess
import sys
import zipfile

root = Path('/content/codex-market4-continuous')
root.mkdir(exist_ok=True)
with zipfile.ZipFile('/content/codex-market4-continuous.zip') as source:
    source.extractall(root)
with (root/'driver.log').open('ab') as log:
    job = subprocess.Popen([sys.executable, '-u', root/'continue_market4.py'], stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(f'Continuous Market4 PID {job.pid}', flush=True)
