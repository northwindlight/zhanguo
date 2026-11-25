import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import requests


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from sync_run import pull


DEST = ROOT / 'continuous-results'
REMOTE = 'codex-market4-continuous/'
RESULTS = 'results'


def fetch(config, name, remote=None, checkpoint=False):
    with requests.Session() as session:
        session.headers.update({'Accept-Encoding': 'identity'})
        return pull(session, config, REMOTE + (remote or f'{RESULTS}/{name}'), DEST / name,
                    checkpoint=checkpoint)


def report(name):
    path = DEST / name
    return json.loads(path.read_text()) if path.exists() else {}


def verify(path, expected):
    if not path.exists():
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == expected


def download(config, name, expected, checkpoint=True):
    path = DEST / name
    if verify(path, expected):
        return True
    if fetch(config, name, checkpoint=checkpoint) and verify(path, expected):
        print('verified', name, expected, flush=True)
        return True
    return False


def download_milestone(config, files):
    for filename, digest, checkpoint in files:
        if not download(config, filename, digest, checkpoint):
            return False
    return True


def save_latest(config, state):
    latest = state['latest']
    if download(config, latest, state['sha256']):
        temporary = DEST / 'backup-state.tmp'
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
        temporary.replace(DEST / 'backup-state.json')
        for old in DEST.glob('ck-*.pt'):
            if old.name < latest:
                old.unlink()


def refresh(config_path):
    try:
        result = subprocess.run(['colab', '--config', config_path, 'sessions'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=45, check=False)
        if result.returncode:
            print('Colab config refresh failed', result.returncode, flush=True)
    except subprocess.TimeoutExpired:
        print('Colab config refresh timed out', flush=True)


def main():
    global DEST, REMOTE, RESULTS
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('--self-play-turns', type=int)
    args = parser.parse_args()
    reports = ('live.json', 'state.json', 'best.json')
    if args.self_play_turns:
        DEST = ROOT / f'selfplay-{args.self_play_turns}-results'
        REMOTE = 'codex-market4-selfplay/'
        RESULTS = f'results-selfplay-{args.self_play_turns}'
        first_report = None
        stop_report = 'stopped-at-60.json'
    else:
        first_report = 'first-over-10.json'
        stop_report = 'stopped-over-30.json'
        reports += (first_report,)
    reports += (stop_report,)
    previous = None
    last_refresh = time.monotonic()
    backup = None
    with ThreadPoolExecutor(max_workers=7) as pool:
        while True:
            if time.monotonic() - last_refresh >= 600:
                refresh(args.config)
                last_refresh = time.monotonic()
            config = json.loads(args.config.read_text()).get('w3-probe')
            if not config:
                print('Colab runtime unavailable', flush=True)
                time.sleep(45)
                continue
            jobs = [pool.submit(fetch, config, name) for name in reports]
            jobs.append(pool.submit(fetch, config, 'driver.log', 'driver.log'))
            for job in jobs:
                job.result()
            live, state, peak = map(report, reports[:3])
            first = report(first_report) if first_report else {}
            stopped = report(stop_report)
            log = DEST / 'driver.log'
            lines = log.read_text().splitlines() if log.exists() else []
            last = lines[-1] if lines else ''
            status = dict(live=live, state=state, best=peak, first_over_10=first, stopped=stopped,
                          driver_error=last if 'Error:' in last else None)
            if status != previous:
                print(json.dumps(status, separators=(',', ':')), flush=True)
                previous = status
            if backup is not None and backup.done():
                backup.result()
                backup = None
            event = stopped or first
            if event and backup is None:
                files = [('saved', 'sha256', True), ('native', 'native_sha256', True)]
                if 'game_log' in event:
                    files.append(('game_log', 'game_log_sha256', False))
                missing = [(event[file], event[digest], checkpoint) for file, digest, checkpoint in files
                           if not verify(DEST / event[file], event[digest])]
                if missing:
                    backup = pool.submit(download_milestone, config, missing)
                elif stopped and live.get('state') == 'stopped':
                    print('Stopped run copied and verified', flush=True)
                    return
            backup_interval = 2 if args.self_play_turns else 5
            if backup is None and state.get('batches', 0) and state['batches'] % backup_interval == 0:
                backup_state = report('backup-state.json')
                if any(backup_state.get(key) != state[key] for key in ('latest', 'sha256', 'lr')):
                    backup = pool.submit(save_latest, config, state)
            time.sleep(12)


if __name__ == '__main__':
    main()
