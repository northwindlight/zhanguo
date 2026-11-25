import hashlib
import json
from pathlib import Path
import sys
import tarfile
import time

import requests

from sync_run import ARTIFACTS, REMOTE, pull

config = Path(sys.argv[1])
ready = ARTIFACTS / 'cpu-archive-ready'
archive = ARTIFACTS / 'cpu-extras.tar'
manifest = ARTIFACTS / 'cpu-archive-manifest.json'
with requests.Session() as session:
    while True:
        cfg = json.loads(config.read_text())['w3-probe']
        pull(session, cfg, REMOTE + 'cpu-archive.log', ARTIFACTS / 'cpu-archive.log')
        if pull(session, cfg, REMOTE + ready.name, ready):
            break
        time.sleep(15)
    count = int(ready.read_text())
    print(f'CPU archive ready on L4: {count} extra episodes', flush=True)
    while True:
        cfg = json.loads(config.read_text())['w3-probe']
        if (manifest.exists() or pull(session, cfg, REMOTE + manifest.name, manifest)) and (
                archive.exists() or pull(session, cfg, REMOTE + archive.name, archive)):
            break
        time.sleep(20)

expected = {row['name']: row for row in json.loads(manifest.read_text())}
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    if len(members) != count or len({member.name for member in members}) != count:
        raise ValueError('CPU archive member count mismatch')
    for member in members:
        row = expected[member.name]
        with tar.extractfile(member) as source:
            if (member.size != row['size'] or
                    hashlib.file_digest(source, 'sha256').hexdigest() != row['sha256']):
                raise ValueError(f'CPU archive verification failed: {member.name}')
print(f'Copied and verified {count} additional CPU episodes', flush=True)
