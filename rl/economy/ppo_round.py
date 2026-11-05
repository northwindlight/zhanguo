"""One PPO round: sample fresh worlds with the current policy, update, save the candidate.

Deliberately small and explicit so a long unattended loop can chain rounds:

    python -m rl.economy.ppo_round --checkpoint cur.pt --out next.pt --worlds 2

Nothing is promoted here. The caller decides whether the candidate is better; this
module only guarantees that the round is reproducible (fixed seeds in a manifest),
that every sampled decision is used by the update, and that the metrics survive a
disconnected CLI (written to ``--metrics`` as JSON, not just printed).
"""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import random
import signal
import time

import numpy as np
import torch

from .model import load_policy
from .online import Deadline, ppo_update, rollout, validate_device, with_critic
from .provenance import provenance


def parse_seeds(values):
    return [int(value) for value in values]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--metrics', type=Path)
    p.add_argument('--worlds', type=int, default=2)
    p.add_argument('--turns', type=int, default=150)
    p.add_argument('--seed-base', type=int, default=700000)
    p.add_argument('--sizes', type=int, nargs='+', default=[20, 24, 28])
    p.add_argument('--device', default='cpu')
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--epochs', type=int, default=1)
    p.add_argument('--kl-mode', choices=('adaptive', 'legacy'), default='adaptive')
    p.add_argument('--update-batch', type=int, default=128)
    p.add_argument('--micro-batch', type=int, default=8)
    p.add_argument('--max-updates', type=int)
    p.add_argument('--deadline', help='ISO datetime with explicit UTC offset')
    a = p.parse_args()
    if a.worlds < 1 or a.turns < 1: raise ValueError('worlds/turns must be positive')
    seeds = [a.seed_base + i for i in range(a.worlds)]
    device = validate_device(a.device)
    torch.set_num_threads(a.threads)
    deadline = math.inf
    if a.deadline:
        end = datetime.fromisoformat(a.deadline)
        if end.tzinfo is None: raise ValueError('Explicit timezone required')
        deadline = end.timestamp()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(Deadline('Stop requested')))

    model, base = load_policy(a.checkpoint)
    anchor, _ = load_policy(a.checkpoint)
    model = with_critic(model)
    model.to(device)
    origin = provenance()
    started = time.time()
    trajectories, reports = [], []
    for index, seed in enumerate(seeds):
        size = a.sizes[index % len(a.sizes)]
        swap = bool(index % 2)
        random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
        rows, streams, report = rollout(model, seed, a.turns, mode='ppo', size=size, swap=swap,
                                        deadline=deadline)
        trajectories.extend(streams)
        reports.append({k: report[k] for k in ('seed', 'turns', 'size', 'swap', 'mode', 'decisions',
                                               'consumption', 'tail_consumption')})
    training = ppo_update(model, trajectories, anchor, epochs=a.epochs, kl_mode=a.kl_mode,
                          update_batch_size=a.update_batch, batch_size=a.micro_batch,
                          max_updates=a.max_updates, deadline=deadline)
    model.to('cpu')
    saved = {k: v for k, v in base.items() if k not in ('model', 'optimizer', 'rng', 'metrics')}
    saved.update(model=model.state_dict(), config=model.config, fingerprint=model.fingerprint,
                 source=origin['implementation'], **{k: origin[k] for k in ('feature_contract', 'teacher_contract')},
                 seen_seeds=sorted(set(base.get('seen_seeds', [])) | set(seeds)), ppo_round=training)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = a.out.with_suffix('.tmp')
    torch.save(saved, temporary); temporary.replace(a.out)
    payload = dict(checkpoint=str(a.checkpoint), output=str(a.out), seeds=seeds, worlds=reports,
                   training={k: v for k, v in training.items() if k != 'kl_windows'},
                   kl_windows=training.get('kl_windows', [])[-8:], seconds=round(time.time() - started, 1),
                   **{k: origin[k] for k in ('feature_contract', 'teacher_contract', 'implementation')})
    path = a.metrics or a.out.with_suffix('.json')
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({k: payload[k] for k in ('seeds', 'seconds')}, ensure_ascii=False))
    summary = {k: payload['training'].get(k) for k in
               ('updates', 'examples_used', 'coverage', 'kl_stopped', 'kl_beta', 'kl')}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
