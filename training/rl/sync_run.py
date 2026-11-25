import argparse
import json
import os
from pathlib import Path
import re
import time
import zipfile

import requests


ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / 'results'
REMOTE = 'codex-takeover/'
BASE_LOGS = ('bcT1.log', 'build.log', 'pipe.log', 'export.log', 'ev_w3.log', 'ev_rule.log',
             REMOTE + 'stage.log', REMOTE + 'rebuild-native.log', REMOTE + 'ppo.log',
             REMOTE + 'cpu-demo-manifest.json')


def pull(session, cfg, relative, target, checkpoint=False):
    url = cfg['url'].rstrip('/') + '/files/content/' + relative
    params = {'authuser': '0', 'colab-runtime-proxy-token': cfg['token']}
    stage = target.with_suffix(target.suffix + '.part')
    try:
        with session.get(url, params=params, stream=True, timeout=(15, 45)) as response:
            if response.status_code == 404:
                return False
            if response.status_code != 200:
                print(f'{relative}: HTTP {response.status_code}', flush=True)
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            with stage.open('wb') as stream:
                for block in response.iter_content(1024 * 1024):
                    stream.write(block)
        if checkpoint:
            with zipfile.ZipFile(stage) as archive:
                if archive.testzip() is not None:
                    raise zipfile.BadZipFile(relative)
        stage.replace(target)
        return True
    except (requests.RequestException, zipfile.BadZipFile) as error:
        print(f'{relative}: {type(error).__name__}', flush=True)
        return False
    finally:
        stage.unlink(missing_ok=True)


def sync(session, cfg, count):
    for name in BASE_LOGS:
        pull(session, cfg, name, ARTIFACTS / Path(name).name)
    bc_log = ARTIFACTS / 'bcT1.log'
    if bc_log.exists():
        epochs = re.findall(r'^epoch (\d+):', bc_log.read_text(), re.M)
        if epochs:
            output = ARTIFACTS / f'bc-epoch{int(epochs[-1]) + 1:02d}.pt'
            if not output.exists() and pull(session, cfg, 'zl/runs/bcT1/bc.pt', output, True):
                print(f'Copied {output.name}', flush=True)
    for update in range(1, count + 1):
        name = f'update{update:04d}.json'
        path = ARTIFACTS / 'ppo' / name
        if not path.exists() and not pull(session, cfg, REMOTE + 'ppo/' + name, path):
            break
        if update != 1 and update != count and update % 5:
            continue
        name = f'ck{update:04d}.pt'
        path = ARTIFACTS / 'ppo' / name
        if not path.exists() and pull(session, cfg, REMOTE + 'ppo/' + name, path, True):
            print(f'Copied {name}', flush=True)
    for name in ('done.json', 'final-w3.json', 'final-rule.json'):
        path = ARTIFACTS / 'ppo' / name
        if not path.exists():
            pull(session, cfg, REMOTE + 'ppo/' + name, path)
    if (ARTIFACTS / 'ppo' / 'done.json').exists():
        for remote, local in [('bcT1-native.pt', 'bc-native.pt'),
                              (REMOTE + 'ppo/ppo-native.pt', 'ppo/ppo-native.pt')]:
            path = ARTIFACTS / local
            if not path.exists():
                pull(session, cfg, remote, path, True)
    return all((ARTIFACTS / name).exists() for name in
               ('bc-epoch08.pt', 'bc-native.pt', 'ppo/done.json', 'ppo/final-w3.json',
                'ppo/final-rule.json', f'ppo/ck{count:04d}.pt', 'ppo/ppo-native.pt'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--updates', type=int, default=30)
    args = parser.parse_args()
    with requests.Session() as session:
        session.headers.update({'Accept-Encoding': 'identity'})
        while True:
            cfg = json.loads(args.config.read_text())['w3-probe']
            if sync(session, cfg, args.updates) or not args.watch:
                return
            time.sleep(45)


if __name__ == '__main__':
    main()
