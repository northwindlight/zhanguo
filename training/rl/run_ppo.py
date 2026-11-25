import argparse
import fcntl
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import torch

ROOT = Path('/content')
PYTHON_ROOT = ROOT / 'zl'
CPP_BUILD = ROOT / 'build-cx'
BC = PYTHON_ROOT / 'runs/bcT1/bc.pt'
WORK = ROOT / 'codex-takeover'
OUT = WORK / 'ppo'
GAMES = 96
EVAL_GAMES = 192
THREADS = 10
sys.path.insert(0, str(PYTHON_ROOT))
from rl.cxloop import sample_stats


def log_output(path, args):
    command = [str(arg) for arg in args]
    print(f'{path.name}: {" ".join(command)}', flush=True)
    start = time.monotonic()
    with path.open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    return path.read_text(), round(time.monotonic() - start, 3)


def publish(path, obj):
    partial = path.with_suffix('.tmp')
    partial.write_text(json.dumps(obj, sort_keys=True) + '\n')
    partial.replace(path)


def load_checked(path, update):
    checkpoint = torch.load(path, map_location='cpu', weights_only=True)
    if checkpoint.get('upd', 0) != update:
        raise ValueError(f'{path} contains the wrong update number')
    if any(not tensor.isfinite().all().item() for tensor in checkpoint['model'].values()):
        raise ValueError(f'{path} contains non-finite model weights')
    return checkpoint


def measure(cpp, checkpoint, policy, name, games, seed, opponent, trajectories=None):
    if not policy.is_file():
        temporary = policy.with_suffix('.tmp.pt')
        log_output(OUT / f'{name}.export.log',
                   [sys.executable, cpp / 'export_policy.py', checkpoint, temporary,
                    '--python-root', PYTHON_ROOT, '--native-bf16', '--bf16-tol', 1.0])
        temporary.replace(policy)
    cmd = [CPP_BUILD / 'zhanguo_torch', 'sample', policy, 'cuda', games, GAMES,
           13, 150, seed, THREADS, 'native']
    if trajectories:
        cmd.append(trajectories)
    cmd.extend(('--opps', f'{opponent}:1'))
    text, elapsed = log_output(OUT / f'{name}.sample.log', cmd)
    stats = sample_stats(text)
    if stats['games'] != games or stats['decisions'] <= 0 or set(stats['by_opp']) != {opponent}:
        raise ValueError(f'Incomplete or incorrect {opponent} rollouts: {stats}')
    if trajectories and len(list(trajectories.glob('*.zgrl'))) != games:
        raise ValueError(f'Expected {games} completed trajectories in {trajectories}')
    return stats, elapsed


def train(cpp, args, bc):
    for update in range(1, args.updates + 1):
        previous = OUT / f'ck{update - 1:04d}.pt'
        checkpoint = OUT / f'ck{update:04d}.pt'
        report = OUT / f'update{update:04d}.json'
        policy = OUT / f'roll{update:04d}.pt'
        trajectories = OUT / f'roll{update:04d}'
        name = f'update{update:04d}'
        seed = 120_000 + (update - 1) * GAMES
        if report.exists():
            load_checked(checkpoint, update)
            shutil.rmtree(trajectories, ignore_errors=True)
            policy.unlink(missing_ok=True)
            continue
        if checkpoint.exists():
            load_checked(checkpoint, update)
            sampling = (OUT / f'{name}.sample.log').read_text()
            stats, sample_s = sample_stats(sampling), None
            training = (OUT / f'{name}.train.log').read_text()
            update_s = None
        else:
            if trajectories.exists():
                shutil.rmtree(trajectories)
            stats, sample_s = measure(cpp, previous, policy, name, GAMES, seed, 'w3', trajectories)
            training, update_s = log_output(OUT / f'{name}.train.log',
                    [sys.executable, cpp / 'ppo_update.py', previous, trajectories, checkpoint,
                     '--python-root', PYTHON_ROOT, '--reference', bc, '--seed', seed,
                     '--lr', args.lr, '--target-kl', 0.05, '--gae-lambda', 0.95,
                     '--invalid-penalty', 0, '--amp'])
            load_checked(checkpoint, update)
        metrics = json.loads(training.strip().splitlines()[-1])
        if (metrics['episodes'] != GAMES or metrics['decisions'] != stats['decisions'] or
                not all(math.isfinite(metrics[key]) for key in ('loss', 'approx_kl', 'sampler_dlogp'))):
            raise ValueError(f'Inconsistent metrics for {name}')
        row = dict(update=update, seed=seed, sample_s=sample_s, update_s=update_s,
                   **stats, **{key: metrics[key] for key in ('loss', 'approx_kl', 'sampler_dlogp',
                                                            'minibatches', 'clip_frac')})
        publish(report, row)
        print(json.dumps(row), flush=True)
        shutil.rmtree(trajectories)
        policy.unlink(missing_ok=True)


def evaluate(cpp, final):
    policy = OUT / 'ppo-native.pt'
    for opponent in ('w3', 'rule'):
        report = OUT / f'final-{opponent}.json'
        if report.exists():
            continue
        name = f'final-{opponent}'
        stats, elapsed = measure(cpp, final, policy, name, EVAL_GAMES, 90_000, opponent)
        publish(report, dict(**stats, sample_s=elapsed))
        print(name, json.dumps(stats), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--updates', type=int, default=30)
    parser.add_argument('--lr', type=float, default=2e-5)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    with (OUT / '.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            pipe = ROOT / 'pipe.log'
            if pipe.is_file() and 'PIPE_DONE' in pipe.read_text().splitlines():
                break
            time.sleep(20)
        bc_log = (ROOT / 'bcT1.log').read_text()
        if not (re.search(r'^epoch 7:', bc_log, re.M) and re.search(r'^saved ', bc_log, re.M)):
            raise RuntimeError('Behavior cloning did not complete eight epochs')
        baseline = OUT / 'ck0000.pt'
        if not baseline.is_file():
            load_checked(BC, 0)
            partial = baseline.with_suffix('.tmp')
            shutil.copyfile(BC, partial)
            partial.replace(baseline)
        cache = (CPP_BUILD / 'CMakeCache.txt').read_text()
        cpp = Path(re.search(r'^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$', cache, re.M)[1])
        train(cpp, args, baseline)
        evaluate(cpp, OUT / f'ck{args.updates:04d}.pt')
        publish(OUT / 'done.json', dict(updates=args.updates, lr=args.lr))
        print(f'PPO complete: {args.updates} updates', flush=True)


if __name__ == '__main__':
    main()
