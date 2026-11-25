import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time

import requests

root = Path('/content')
work = root / 'codex-takeover'


def stage_demos(source, archive_extras=False):
    url = source['url'].rstrip('/') + '/files/content/'
    params = {'authuser': '0', 'colab-runtime-proxy-token': source['token']}
    data = work / 'demo-cache'
    data.mkdir(exist_ok=True)

    def download(relative, target, expected=None):
        if target.is_file() and expected:
            with target.open('rb') as cached:
                if hashlib.file_digest(cached, 'sha256').hexdigest() == expected['sha256']:
                    return
        partial = target.with_suffix('.part')
        for attempt in range(5):
            try:
                with requests.get(url + relative, params=params, stream=True,
                                  timeout=(15, 45)) as response, partial.open('wb') as out:
                    if response.status_code != 200:
                        raise RuntimeError(f'HTTP {response.status_code}')
                    digest = hashlib.sha256()
                    for chunk in response.iter_content(1024 * 1024):
                        out.write(chunk)
                        digest.update(chunk)
                if expected and (partial.stat().st_size != expected['size'] or
                                 digest.hexdigest() != expected['sha256']):
                    raise ValueError('episode checksum mismatch')
                partial.replace(target)
                return
            except (requests.RequestException, RuntimeError, ValueError) as error:
                diagnostic = str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__
                print(f'{relative}: {diagnostic}, attempt {attempt + 1}', flush=True)
                time.sleep(1 + attempt)
        raise RuntimeError(f'Could not transfer {relative}')

    manifest_path = work / ('cpu-archive-manifest.json' if archive_extras else 'cpu-demo-manifest.json')
    source_manifest = 'codex-cpu-archive-manifest.json' if archive_extras else 'codex-cpu-bc-manifest.json'
    if not manifest_path.exists():
        download(source_manifest, manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if len(manifest) < 300 or len({row['seed'] for row in manifest}) != len(manifest):
        raise ValueError('Expected at least 300 distinct winning games')
    if not all(re.fullmatch(r's\d+_k[01]\.pkl\.gz', row['name']) for row in manifest):
        raise ValueError('Unexpected episode filename in manifest')
    if archive_extras:
        current = {row['name']: row['sha256'] for row in manifest}
        base = {row['name']: row['sha256']
                for row in json.loads((work / 'cpu-demo-manifest.json').read_text())}
        if any(current.get(name) != digest for name, digest in base.items()):
            raise ValueError('The CPU archive is missing part of the BC corpus')
        manifest = [row for row in manifest if row['name'] not in base]

    def fetch(row):
        name = row['name']
        download('demos/w3t/' + name, data / name, row)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for index, _ in enumerate(pool.map(fetch, manifest), start=1):
            if index % 25 == 0 or index == len(manifest):
                print(f'Verified {index}/{len(manifest)} episodes', flush=True)
    archive = work / 'cpu-extras.tar' if archive_extras else root / 'demos_C_takeover.tar'
    partial = archive.with_suffix('.part')
    with tarfile.open(partial, 'w') as out:
        for row in manifest:
            out.add(data / row['name'], arcname=row['name'], recursive=False)
    partial.replace(archive)
    print(f'Archive verified: {len(manifest)} episodes; {archive.stat().st_size} bytes', flush=True)
    if archive_extras:
        (work / 'cpu-archive-ready').write_text(f'{len(manifest)}\n')


def build_native():
    build = root / 'build-cx'
    cache = (build / 'CMakeCache.txt').read_text()
    cpp = Path(re.search(r'^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$', cache, re.M)[1])
    for name in ('economy.cpp', 'rl_candidates.cpp'):
        patched = (work / name).read_bytes()
        target = cpp / name
        if target.read_bytes() != patched:
            target.write_bytes(patched)
        print(name, hashlib.sha256(patched).hexdigest(), flush=True)
    with (work / 'rebuild-native.log').open('a') as log:
        subprocess.run(['cmake', '--build', build, '--target', 'zhanguo_torch', '-j4'],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    print('Native rebuild complete', flush=True)


if __name__ == '__main__':
    import sys

    source = json.load(sys.stdin)
    if sys.argv[1:] == ['--archive-extras']:
        stage_demos(source, archive_extras=True)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            demos = pool.submit(stage_demos, source)
            native = pool.submit(build_native)
            native.result()
            demos.result()
        (root / 'demos_ready').touch()
        print('Released the existing BC job', flush=True)
