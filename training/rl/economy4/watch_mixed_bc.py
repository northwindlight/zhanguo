"""Back up a 1,000-world mixed BC run and restore it on Colab runtime loss."""

import argparse
import fcntl
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from zipfile import ZipFile

import requests

from watch_fp32_day import colab, pull, publish, SESSION, sha256 as digest


HERE = Path(__file__).resolve().parent
REMOTE = 'zhanguo-mixed-bc/'
GDP = HERE/'fp32-day-results'


def upload(config, source, name):
    result = colab(config, 'upload', '-s', SESSION, str(source), f'content/{name}',
                   timeout=180, direct=True)
    if result.returncode:
        raise RuntimeError(f'Runtime upload failed for {name}')


def launch(config):
    script = HERE/'launch_mixed_bc.py'
    for direct in (False, True):
        result = colab(config, 'exec', '-s', SESSION, '-f', str(script), '--timeout', '22',
                       timeout=50, direct=direct)
        if result.returncode == 0:
            print(result.stdout.strip(), flush=True)
            return
    raise RuntimeError('Could not connect to the Colab launcher')


def restore(config, out):
    upload(config, out/'code.zip', 'zhanguo-mixed-bc-code.zip')
    for suffix, base in (('base', 'best-batch-mean.pt'), ('native', 'best-batch-mean-native.pt')):
        with (out/base).open('rb') as source, tempfile.TemporaryDirectory(dir=out) as directory:
            for index in range(16):
                part = source.read(7*1024*1024)
                if not part:
                    break
                path = Path(directory)/f'{suffix}.{index:03}'
                path.write_bytes(part)
                upload(config, path, 'zhanguo-mixed-'+path.name)
                path.unlink()
            if source.read(1):
                raise ValueError('Recovery checkpoint too large')
    for archive in sorted((out/'chunks').glob('chunk-*.zip')):
        upload(config, archive, 'zhanguo-mixed.'+archive.name)
    launch(config)


def sync_gdp(session, runtime, out):
    marker = out/'paused-gdp.json'
    if marker.exists():
        return
    temporary = out/'paused-gdp.download'
    if not pull(session, runtime, REMOTE+marker.name, temporary):
        return
    state = json.loads(temporary.read_text())
    checkpoint = GDP/state['latest']
    if not checkpoint.exists() or digest(checkpoint) != state['sha256']:
        pull(session, runtime, 'codex-market4-selfplay/results-selfplay-60/'+checkpoint.name,
             checkpoint, checkpoint=True)
    if checkpoint.is_file() and digest(checkpoint) == state['sha256']:
        publish(GDP/'backup-state.json', state)
        temporary.replace(marker)
        print('Verified last GDP checkpoint', checkpoint.name, flush=True)


def sync(session, config, out):
    runtime = json.loads(config.read_text()).get(SESSION)
    if not runtime:
        return None, False
    progress = out/'progress.json'
    pull(session, runtime, REMOTE+'driver.log', out/'driver.log')
    if not pull(session, runtime, REMOTE+progress.name, progress):
        return None, False
    state = json.loads(progress.read_text())
    for name in state['chunks']:
        if not re.fullmatch(r'chunk-\d{4}\.zip', name):
            raise ValueError('Unexpected mixed BC archive name')
        local = out/'chunks'/name
        if local.is_file():
            continue
        staged = local.with_suffix('.download')
        if not pull(session, runtime, REMOTE+'chunks/'+name, staged):
            continue
        with ZipFile(staged) as archive:
            if archive.testzip() is not None or 'summary.json' not in archive.namelist():
                raise ValueError(f'Invalid archive {name}')
        staged.replace(local)
        print('Backed up', name, flush=True)
    sync_gdp(session, runtime, out)
    if state['state'] != 'completed':
        return state, False
    report = out/'done.download'
    if not pull(session, runtime, REMOTE+'done.json', report):
        return state, False
    done = json.loads(report.read_text())
    for key, sha in (('checkpoint', 'sha256'), ('native', 'native_sha256')):
        target = out/done[key]
        if not target.is_file() or digest(target) != done[sha]:
            pull(session, runtime, REMOTE+target.name, target, checkpoint=True)
        if not target.is_file() or digest(target) != done[sha]:
            return state, False
    if not all((out/'chunks'/name).is_file() for name in done['chunks']):
        return state, False
    pull(session, runtime, REMOTE+'bc.log', out/'bc.log')
    report.replace(out/'done.json')
    return state, True


def watch(config, out):
    failures, last_progress, last_retry = 0, time.monotonic(), 0
    prior = None
    pending = out/'recovery-pending'
    with requests.Session() as session:
        session.trust_env = False
        launch(config)
        while True:
            try:
                status, done = sync(session, config, out)
                if done:
                    print('Mixed BC complete; dataset and weights verified', flush=True)
                    return
                if status and status != prior:
                    print(json.dumps(status), flush=True)
                    last_progress = time.monotonic()
                    prior = status
                failures = (failures+1) if status is None else 0
                if failures >= 2:
                    active = colab(config, 'sessions', timeout=45)
                    if active.returncode == 0 and f'[{SESSION}]' not in active.stdout:
                        allocated = colab(config, 'new', '-s', SESSION, '--gpu', 'L4', timeout=120)
                        if allocated.returncode:
                            raise RuntimeError('Could not allocate a replacement Colab L4')
                        pending.touch()
                    if active.returncode == 0 and pending.exists():
                        restore(config, out)
                        pending.unlink()
                now = time.monotonic()
                if status is not None and now-last_progress > 900 and now-last_retry > 900:
                    last_retry = now
                    launch(config)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                print('Watcher retry', type(error).__name__, str(error), flush=True)
                # Temporary Colab resource and transport failures often persist for several minutes.
                time.sleep(90)
            time.sleep(45)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with (args.output/'.watch-lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        watch(args.config, args.output)
