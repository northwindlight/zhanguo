from pathlib import Path
import subprocess
import sys

root = Path('/content/codex-takeover')
secret = root / '.cpu-transfer'
source = secret.read_bytes()
secret.unlink()
archiving = globals().get('archive_mode', False)
filename = 'cpu-archive.log' if archiving else 'stage.log'
args = ['--archive-extras'] if archiving else []
with (root / filename).open('ab') as log:
    job = subprocess.Popen([sys.executable, '-u', root / 'stage_l4.py', *args],
                           stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                           start_new_session=True)
    with job.stdin:
        job.stdin.write(source)
print(f'Staging job PID {job.pid}', flush=True)
