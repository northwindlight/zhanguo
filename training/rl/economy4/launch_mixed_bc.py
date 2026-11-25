"""Colab entry point for a prepared mixed BC run."""

from pathlib import Path
import subprocess
import sys


ROOT = Path('/content/zhanguo-mixed-bc')
ROOT.mkdir(exist_ok=True)
WORKER = '''from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from zipfile import ZipFile

deck = Path('/content')
root = deck/'zhanguo-mixed-bc'
code = root/'code'
old_root = deck/'codex-market4-selfplay'
old = old_root/'results-selfplay-60'

def old_driver():
    pids = []
    for file in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = file.read_bytes().split(b'\\0')
        except OSError:
            continue
        if len(args)>=4 and args[1]==b'-u' and args[2]==os.fsencode(old_root/'continue_market4.py'):
            pids.append(int(file.parent.name))
    if len(pids)>1:
        raise RuntimeError('Multiple GDP drivers found')
    return pids[0] if pids else None

with (root/'.schedule-lock').open('a') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('Mixed BC is already scheduled', flush=True)
        raise SystemExit
    if (root/'done.json').exists():
        print('Mixed BC already completed', flush=True)
        raise SystemExit
    code.mkdir(exist_ok=True)
    with ZipFile(deck/'zhanguo-mixed-bc-code.zip') as archive:
        archive.extractall(code)
    for target, stem, expected in (
            ('base.pt', 'base', 'e92f0e927b53f125a337afc93cb824c957eeb3bf6b4325149253e246c5f4d7d7'),
            ('base-native.pt', 'native', 'e52ae96ef03d1d1b28c6864184f839439c11db3d3307fe2959c4b2ed7cb98574')):
        dest = root/target
        if not dest.exists():
            partial=dest.with_suffix('.partial')
            with partial.open('wb') as output:
                for part in sorted(deck.glob('zhanguo-mixed-'+stem+'.[0-9][0-9][0-9]')):
                    with part.open('rb') as source:
                        shutil.copyfileobj(source, output)
            with partial.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest()!=expected:
                    raise RuntimeError('Incomplete base checkpoint restore')
            partial.replace(dest)
        with dest.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest()!=expected:
                raise RuntimeError('Base checkpoint verification failed')
    chunks=root/'chunks'
    chunks.mkdir(exist_ok=True)
    for archive in deck.glob('zhanguo-mixed.chunk-*.zip'):
        dest=chunks/archive.name.removeprefix('zhanguo-mixed.')
        if dest.exists():
            continue
        with ZipFile(archive) as package:
            if package.testzip() is not None:
                raise RuntimeError('Corrupted mixed BC dataset archive')
        archive.replace(dest)
    pid=old_driver()
    if pid is not None:
        if os.getpgid(pid)!=pid:
            raise RuntimeError('Unexpected GDP driver process group')
        state_path=old/'state.json'
        next_batch=json.loads(state_path.read_text())['batches']+1
        print('Waiting for GDP batch',next_batch,flush=True)
        limit=time.monotonic()+1800
        while old_driver()==pid and json.loads(state_path.read_text())['batches']<next_batch:
            if time.monotonic()>=limit or (root/'cancel-handoff').exists():
                raise RuntimeError('Mixed BC handoff cancelled or GDP did not reach a boundary')
            time.sleep(0.25)
        if (root/'cancel-handoff').exists():
            raise RuntimeError('Mixed BC handoff cancelled')
        if old_driver()==pid:
            os.killpg(pid, signal.SIGTERM)
        state=json.loads(state_path.read_text())
        report=root/'paused-gdp.tmp'
        report.write_text(json.dumps(state))
        report.replace(root/'paused-gdp.json')
        print('GDP checkpoint preserved',state['latest'],flush=True)
    cmd=[sys.executable,'-u',str(code/'mixed_bc_run.py'),
         '--output',str(root),'--python-root',str(code/'python')]
    subprocess.run(cmd,check=True)
'''

script = ROOT/'full-worker.py'
script.write_text(WORKER)
with (ROOT/'driver.log').open('ab') as log:
    process = subprocess.Popen([sys.executable, '-u', str(script)], stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print('Mixed BC supervisor PID', process.pid, flush=True)
