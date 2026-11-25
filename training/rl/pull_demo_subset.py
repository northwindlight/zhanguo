from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import time

import requests

from sync_run import pull

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'results/cpu-demo-manifest.json').read_text())
on_pi = set((root / 'from-rpi3/demo-names.txt').read_text().splitlines())
needed = [row for row in manifest if row['name'] not in on_pi]


def valid(path, row):
    if not path.is_file() or path.stat().st_size != row['size']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256']


def copy(row):
    config = Path(sys.argv[1])
    name = row['name']
    path = root / 'demos/w3tC' / name
    with requests.Session() as session:
        for attempt in range(6):
            if valid(path, row):
                return name
            cfg = json.loads(config.read_text())['w3-probe']
            pull(session, cfg, 'codex-takeover/demo-cache/' + name, path)
            if valid(path, row):
                return name
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'Could not verify {name}')


with ThreadPoolExecutor(max_workers=2) as pool:
    for index, name in enumerate(pool.map(copy, needed), start=1):
        print(f'{index}/{len(needed)} {name}', flush=True)
