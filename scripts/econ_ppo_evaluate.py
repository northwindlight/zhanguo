"""Evaluate a PPO snapshot without blocking training or changing its ancestry."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rl.economy.acceptance import cases, suite
from rl.economy.model import load_policy
from rl.economy.overnight import write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    args = p.parse_args()
    torch.set_num_threads(1)
    baseline = ROOT/'archive/previews/econ-handoff-20261107/models/ppo-r3.pt'
    bc = ROOT/'archive/previews/econ14-20261107/live/epoch-15.pt'
    teachers = ROOT/'archive/previews/econ-handoff-20261107/teachers'
    subprocess.run([sys.executable, '-u', '-m', 'rl.economy.benchmark', '--baseline', str(baseline),
                    '--checkpoint', str(bc), str(args.checkpoint), '--out', str(args.cache),
                    '--cache', str(teachers), '--threads', '1'], cwd=ROOT, check=True)
    screen = json.loads((args.cache/'summary.json').read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out/'screen.json', screen)
    student = screen['evaluations'][-1]
    if not (student['target_met_on_screen'] and student['baseline_protected']):
        write_json(args.out/'summary.json', dict(phase='complete', accepted=False,
                   reason='screen_below_acceptance', screen=screen))
        return
    model, saved = load_policy(args.checkpoint)
    selection = args.out/'selection';selection.mkdir(exist_ok=True)
    result = suite(model, cases(), selection, teachers, math.inf)
    confirmed = None
    if result['surpass']:
        # Every attempted promotion gets disjoint confirmation seeds, including
        # previous failed confirmations. They never enter training data.
        index_path = args.cache/'confirmation-index.json'
        index = json.loads(index_path.read_text())['next'] if index_path.exists() else 0
        manifest = [dict(c, seed=c['seed']+100*index) for c in cases(confirm=True)]
        ancestry = set(saved.get('seen_seeds', [])) | set(saved.get('train_seeds', [])) | set(saved.get('valid_seeds', []))
        if ancestry & {c['seed'] for c in manifest}:
            raise ValueError('Confirmation overlaps training ancestry')
        write_json(index_path, dict(next=index+1))
        folder = args.out/'confirmation';folder.mkdir(exist_ok=True)
        confirmed = suite(model, manifest, folder, teachers, math.inf)
    accepted = bool(confirmed and confirmed['surpass'])
    write_json(args.out/'summary.json', dict(phase='complete', accepted=accepted,
               selection=result, confirmation=confirmed, screen=screen))
    if accepted:
        temporary = args.out/'accepted.tmp'
        saved.update(accepted=True, experimental=False, acceptance=dict(selection=result, confirmation=confirmed))
        torch.save(saved, temporary)
        temporary.replace(args.out/'accepted.pt')


if __name__ == '__main__':
    main()
