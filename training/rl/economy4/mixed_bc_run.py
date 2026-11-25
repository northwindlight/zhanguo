"""Collect and train the two-tile mixed BC curriculum in resumable chunks."""

import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pickle
import random
import re
import subprocess
import sys
import tempfile
import time
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'cpp'))
from read_trajectory import episodes


TOTAL = 1000
SEED = 252500
PILOT = 8
CHUNK = 32
FIRST = 12
LAST = 44
HORIZON = 80
BASE_SHA = 'e92f0e927b53f125a337afc93cb824c957eeb3bf6b4325149253e246c5f4d7d7'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, sort_keys=True)+'\n')
    temporary.replace(path)


def run(label, args, log, env):
    print('Starting', label, flush=True)
    start = time.monotonic()
    with log.open('w') as output:
        subprocess.run([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT,
                       env=env, check=True)
    print('Completed', label, round(time.monotonic()-start, 1), 'seconds', flush=True)


def reported_games(log):
    return [dict(re.findall(r'(\w+)=(\S+)', line)) for line in log
            if line.startswith('game ')]


def teacher_examples(decisions, rng):
    essentials, finance = [], []
    for decision in decisions:
        if not decision['teacher']:
            continue
        obs, action, turn = (decision[key] for key in ('obs', 'a', 'ti'))
        if obs['cand_exec'][action] <= 0.5:
            raise ValueError('Recorded teacher selected an unavailable action')
        kind = int(obs['cand_type'][action])
        coded = obs['cand'][action, 6:22]
        choice = int(np.argmax(coded)) if np.any(coded > 0.5) else -1
        if kind in (3, 6, 7, 8) or (kind == 1 and choice in (6, 7, 8, 10, 15)):
            essentials.append((obs, action, turn, 'military'))
        elif kind == 4 and choice in (4, 5):
            essentials.append((obs, action, turn, 'supply' if choice == 5 else 'military'))
        elif kind in (4, 5):
            finance.append((obs, action, turn, 'finance'))
    pertinent = {row[2] for row in essentials}
    finance = [row for row in finance if row[2] in pertinent or row[2]+1 in pertinent]
    return rng.sample(essentials, min(48, len(essentials))) + rng.sample(finance, min(12, len(finance)))


def pack(directory, log, archive, offset, count):
    raw = log.read_text().splitlines()
    results = {int(row['seed']): row for line in raw if line.startswith('expansion_end ')
               for row in [dict(re.findall(r'(\w+)=(\S+)', line))]}
    if set(results) != set(range(SEED+offset, SEED+offset+count)):
        raise ValueError('Missing or duplicate two-tile takeover results')
    summary = dict(offset=offset, games=count, expanded=0, clean=0, teacher=0, anchor=0, starved_units=0)
    temporary = archive.with_suffix('.partial')
    with ZipFile(temporary, 'w', ZIP_DEFLATED, compresslevel=2) as bundle:
        recorded = set()
        for decisions, _, meta, features, _ in episodes(directory, mixed_bc=True):
            seed = meta['seed']
            if seed in recorded:
                raise ValueError('Duplicate takeover trajectory')
            recorded.add(seed)
            row = results[seed]
            rng = random.Random(seed)
            success = int(row['first_gain']) > 0
            deaths = int(row['starved_units'])
            clean = success and deaths == 0
            teach = teacher_examples(decisions, rng) if clean else []
            anchor = [(d['obs'], d['a'], d['ti'], 'anchor') for d in decisions
                      if not d['teacher'] and d['ti'] < FIRST-1]
            anchor = rng.sample(anchor, min(12, len(anchor)))
            if not anchor or (clean and not teach):
                raise ValueError(f'Missing requested labels in world {seed}')
            item = dict(seed=seed, features=np.stack(features), teacher=teach, anchor=anchor,
                        outcome=dict(reached=success, starved=deaths, settled_turns=meta['turns']))
            bundle.writestr(f'{seed}.pkl', pickle.dumps(item, protocol=pickle.HIGHEST_PROTOCOL))
            summary['expanded'] += success
            summary['clean'] += clean
            summary['teacher'] += len(teach)
            summary['anchor'] += len(anchor)
            summary['starved_units'] += deaths
        if recorded != set(results):
            raise ValueError('Missing mixed trajectory files')
        bundle.writestr('summary.json', json.dumps(summary))
        bundle.write(log, 'rollout.log')
    temporary.replace(archive)
    return summary


def evaluate(sampler, first, second, work, env):
    comparison = {}
    for name, weights in (('before', first), ('mixed', second)):
        log = work/(name+'.log')
        run(name, [sampler, 'sample', weights, 'cuda', 12, 12, 24, 40, 254000, 8,
                   'fp32', '--market4-selfplay'], log, env)
        games = reported_games(log.read_text().splitlines())
        if len(games) != 48:
            raise ValueError('Incomplete mixed BC evaluation')
        comparison[name] = dict(land=dict(Counter(int(g['land']) for g in games)),
                                gdp=sum(float(g['reward']) for g in games)/(40*len(games)),
                                starved_units=sum(int(g['starved_units']) for g in games),
                                supply_short_turns=sum(int(g['supply_short_turns']) for g in games))
    return comparison


def campaign(args):
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    chunks = work/'chunks'
    chunks.mkdir(exist_ok=True)
    model, base = work/'base-native.pt', work/'base.pt'
    if not model.is_file() or digest(base) != BASE_SHA:
        raise ValueError('Mixed BC must start from the verified economic checkpoint')
    env = os.environ.copy()
    env['NVIDIA_TF32_OVERRIDE'] = '0'
    env.pop('TORCH_ALLOW_TF32_CUBLAS_OVERRIDE', None)
    env.pop('ZHANGUO_SAMPLE_PROFILE', None)
    env.pop('ZHANGUO_SAMPLE_BUCKET_MIN', None)
    sampler = work/'build/zhanguo_torch'
    if not sampler.is_file():
        run('sampler build', [sys.executable, ROOT/'cpp/build_colab.py', '--build', work/'build', '--jobs', 6],
            work/'build.log', env)
    cumulative = Counter()
    files = []
    offset = 0
    while offset < (PILOT if args.pilot else TOTAL):
        count = PILOT if offset == 0 else min(CHUNK, TOTAL-offset)
        archive = chunks/f'chunk-{offset:04}.zip'
        if archive.is_file():
            with ZipFile(archive) as bundle:
                summary = json.loads(bundle.read('summary.json'))
            if (summary['offset'], summary['games']) != (offset, count):
                raise ValueError('Chunk does not match the scheduled seeds')
        else:
            with tempfile.TemporaryDirectory(dir=work, prefix='roll-') as temp:
                directory = Path(temp)
                log = directory/'sample.log'
                run(f'worlds {offset+1}–{offset+count}',
                    [sampler, 'sample', model, 'cuda', count, count, 24, HORIZON, SEED+offset,
                     8, 'fp32', directory, '--market4-selfplay', '--mixed-bc', FIRST, LAST], log, env)
                summary = pack(directory, log, archive, offset, count)
        cumulative.update({k:v for k,v in summary.items() if k != 'offset'})
        files.append(archive.name)
        offset += count
        publish(work/'progress.json', dict(state='pilot' if args.pilot else 'collecting',
                                          goal=TOTAL, chunks=files, **cumulative))
        print(json.dumps(summary), flush=True)
    if args.pilot:
        return
    trained = work/'mixed-military-bc.pt'
    status = dict(goal=TOTAL, chunks=files, **cumulative)
    if not trained.is_file():
        publish(work/'progress.json', dict(state='training', **status))
        learning = work/'mixed-military-bc-training.pt'
        run('mixed BC', [sys.executable, ROOT/'military_bc.py', base, chunks, chunks, learning,
                         '--mixed', '--python-root', args.python_root, '--lr', 0.0002,
                         '--epochs', 2, '--batch', 24, '--anchor-weight', 12], work/'bc.log', env)
        learning.replace(trained)
    exported = work/'mixed-military-bc-native.pt'
    if not exported.is_file():
        run('export', [sys.executable, ROOT/'cpp/export_policy.py', trained, exported,
                        '--python-root', args.python_root], work/'export.log', env)
    publish(work/'progress.json', dict(state='evaluating', **status))
    comparison = evaluate(sampler, model, exported, work, env)
    publish(work/'done.json', dict(checkpoint=trained.name, sha256=digest(trained),
                                 native=exported.name, native_sha256=digest(exported),
                                 evaluation=comparison, **status))
    publish(work/'progress.json', dict(state='completed', **status))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--python-root', required=True, type=Path)
    parser.add_argument('--pilot', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        campaign(args)


if __name__ == '__main__':
    main()
