from pathlib import Path
import subprocess
import sys
import zipfile

root = Path('/content/codex-market4-200')
root.mkdir(exist_ok=True)
archive = Path('/content/codex-market4-200.zip')
with zipfile.ZipFile(archive) as source:
    source.extractall(root)
with (root/'driver.log').open('ab') as log:
    job = subprocess.Popen([sys.executable, '-u', root/'train_market4.py'], stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(f'Market4 job PID {job.pid}', flush=True)
