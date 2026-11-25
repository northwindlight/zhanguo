"""Back up a bounded FP32 run and restore it if its Colab runtime expires."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from zipfile import ZIP_DEFLATED, ZipFile

import requests

from continue_market4 import sha256
from train_market4 import publish

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from sync_run import pull


SESSION = 'w3-probe'
REMOTE = 'codex-market4-selfplay/'
RESULTS = REMOTE + 'results-selfplay-60/'
MILESTONES = ('best-batch-mean', 'first-batch-mean-over-target', 'day-end')
RESTORE_FILE = 'zhanguo-fp32-day-restore.zip'
RESTORE = """from pathlib import Path
import fcntl
import hashlib
import json
import subprocess
import sys
from zipfile import ZipFile

content = Path('/content')
root = content / 'codex-market4-selfplay'
results = root / 'results-selfplay-60'
results.mkdir(parents=True, exist_ok=True)
with (results / '.lock').open('a') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('FP32 driver already running', flush=True)
    else:
        with ZipFile(content / 'zhanguo-fp32-day-restore.zip') as archive:
            archive.extractall(content)
        state = json.loads((results / 'state.json').read_text())
        day = json.loads((results / 'fp32-day.json').read_text())
        with (results / state['latest']).open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == state['sha256']
        with (root / 'driver.log').open('ab') as log:
            command = [sys.executable, '-u', str(root / 'continue_market4.py'),
                       '--self-play', '--turns', '60', '--fp32',
                       '--mean-gdp-target', str(day['mean_gdp_target']), '--deadline', str(day['deadline'])]
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        print('Restored FP32 driver', child.pid, state['latest'], flush=True)
"""


def colab(config, *args, timeout=90, direct=False):
    env = os.environ.copy()
    if direct:
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
            env.pop(key, None)
        env['COLAB_NO_PROXY'] = '1'
    return subprocess.run(['colab', '--config', str(config), *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def verified(path, digest):
    return path.is_file() and sha256(path) == digest


def sync_milestone(session, runtime, out, work, tag):
    report = work / f'{tag}.json'
    if not pull(session, runtime, RESULTS + report.name, report):
        return False
    row = json.loads(report.read_text())
    local = out / report.name
    if local.is_file() and local.read_bytes() == report.read_bytes():
        return True
    for name, digest in (('saved', 'sha256'), ('native', 'native_sha256')):
        target = work / row[name]
        if not verified(target, row[digest]):
            pull(session, runtime, RESULTS + row[name], target, checkpoint=True)
        if not verified(target, row[digest]):
            return False
    for field in ('saved', 'native'):
        (work / row[field]).replace(out / row[field])
    report.replace(local)
    print('Saved milestone', tag, row.get('batch'), row.get('mean_fixed_gdp'), flush=True)
    return True


def sync(session, runtime, out, work, day):
    report = work / 'state.json'
    if not pull(session, runtime, RESULTS + report.name, report):
        return False, False
    state = json.loads(report.read_text())
    live = pull(session, runtime, RESULTS + 'live.json', out / 'live.json')
    pull(session, runtime, REMOTE + 'driver.log', out / 'driver.log')
    backup = out / 'backup-state.json'
    previous = json.loads(backup.read_text()) if backup.is_file() else {}
    if state['batches'] >= day['start_batch'] and state.get('sha256') != previous.get('sha256'):
        path = out / state['latest']
        if not verified(path, state['sha256']):
            pull(session, runtime, RESULTS + state['latest'], path, checkpoint=True)
        if verified(path, state['sha256']):
            publish(backup, state)
            print('Verified checkpoint', state['latest'], state['games'], flush=True)
            older = out / f"ck-{state['batches']-2:06}.pt"
            older.unlink(missing_ok=True)
    last = False
    for tag in MILESTONES:
        copied = sync_milestone(session, runtime, out, work, tag)
        if tag == 'day-end':
            last = copied and json.loads((out/'day-end.json').read_text())['deadline'] == day['deadline']
    return live, last


def restore(config, out, work, day):
    state = json.loads((out/'backup-state.json').read_text())
    path = out / state['latest']
    if not verified(path, state['sha256']):
        raise ValueError('Recovery checkpoint does not match its saved digest')
    archive = work / RESTORE_FILE
    with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
        for name in ('continue_market4.py', 'train_market4.py'):
            bundle.write(ROOT / name, REMOTE + name)
        for path in sorted((ROOT/'cpp').iterdir()):
            if path.suffix in ('.cpp', '.hpp', '.py') or path.name == 'CMakeLists.txt':
                bundle.write(path, REMOTE + 'cpp/' + path.name)
        python_root = ROOT.parent/'python'
        for path in sorted(python_root.rglob('*.py')):
            relative = path.relative_to(python_root)
            if '.git' not in relative.parts and 'difftest' not in relative.parts:
                bundle.write(path, 'zl/' + relative.as_posix())
        bundle.writestr(RESULTS + 'state.json', json.dumps(state))
        bundle.writestr(RESULTS + 'fp32-day.json', json.dumps(day))
        bundle.write(out / state['latest'], RESULTS + state['latest'])
        for tag in MILESTONES[:2]:
            report = out / (tag + '.json')
            if report.is_file():
                bundle.write(report, RESULTS + report.name)
    uploaded = colab(config, 'upload', '-s', SESSION, str(archive), 'content/' + RESTORE_FILE,
                     timeout=240, direct=True)
    if uploaded.returncode:
        raise RuntimeError('Recovery archive upload failed')
    launch = work/'restore.py'
    launch.write_text(RESTORE)
    started = colab(config, 'exec', '-s', SESSION, '-f', str(launch), '--timeout', '25', direct=True)
    if started.returncode:
        raise RuntimeError('Recovery driver launch failed')
    print(started.stdout.strip(), flush=True)


def watch(config, out):
    day = json.loads((out/'fp32-day.json').read_text())
    last_refresh = time.monotonic()
    errors = 0
    pending = out / 'recovery-pending.json'
    with requests.Session() as session, tempfile.TemporaryDirectory(dir=out, prefix='.sync-') as directory:
        session.trust_env = False
        session.headers['Accept-Encoding'] = 'identity'
        work = Path(directory)
        while time.time() < day['deadline'] + 900:
            try:
                runtime = json.loads(config.read_text()).get(SESSION)
                reachable, done = sync(session, runtime, out, work, day) if runtime else (False, False)
                if done:
                    print('Day complete; final artifacts verified', flush=True)
                    return
                if reachable and pending.exists():
                    pending.unlink()
                errors = 0 if reachable else errors+1
                refresh = errors >= 2 or time.monotonic()-last_refresh >= 600
                if refresh:
                    listing = colab(config, 'sessions', timeout=45)
                    last_refresh = time.monotonic()
                    if listing.returncode == 0 and time.time() < day['deadline']:
                        if f'[{SESSION}]' not in listing.stdout:
                            if not (out/'backup-state.json').is_file():
                                raise RuntimeError('No verified recovery checkpoint')
                            created = colab(config, 'new', '-s', SESSION, '--gpu', 'L4', timeout=120)
                            if created.returncode:
                                raise RuntimeError('Colab could not allocate an L4')
                            publish(pending, dict(created=time.time()))
                        if pending.exists():
                            restore(config, out, work, day)
            except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError) as error:
                print('Watcher retry', type(error).__name__, str(error), flush=True)
                time.sleep(90)
            time.sleep(45)
    print('Day window ended; latest verified checkpoint is in backup-state.json', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with (args.output/'.watch-lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        watch(args.config, args.output)


if __name__ == '__main__':
    main()
