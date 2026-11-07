"""Single-environment teacher comparison (see docs/reviews/经济BC交接-20261107.md).

Run from the repo root; writes a teacher cache under --cache and prints S ratios.
"""
"""Evaluate a checkpoint against a freshly-run (and cached) teacher on one environment."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from rl.economy.acceptance import compare
from rl.economy.evaluate import run
from rl.economy.model import load_policy
from rl.economy.provenance import require_compatible

p = argparse.ArgumentParser()
p.add_argument('--checkpoint', type=Path, required=True)
p.add_argument('--seed', type=int, required=True)
p.add_argument('--size', type=int, default=24)
p.add_argument('--turns', type=int, default=150)
p.add_argument('--swap', action='store_true')
p.add_argument('--cache', type=Path, default=Path('/tmp/econrev/teachers'))
p.add_argument('--out', type=Path)
a = p.parse_args()
torch.set_num_threads(1)
a.cache.mkdir(parents=True, exist_ok=True)
key = f"{a.seed}-{a.size}-{a.turns}-{int(a.swap)}"
teacher_path = a.cache / f'{key}.json'
if teacher_path.exists():
    teacher = json.loads(teacher_path.read_text()); require_compatible(teacher)
else:
    teacher = run(a.seed, a.turns, size=a.size, swap=a.swap)
    teacher_path.write_text(json.dumps(teacher, ensure_ascii=False))
model, _ = load_policy(a.checkpoint)
student = run(a.seed, a.turns, size=a.size, swap=a.swap, model=model)
result = compare(teacher, student)
print(json.dumps(dict(key=key, total_ratio=round(result['total_ratio'], 4),
                      worst_ratio=round(result['worst_ratio'], 4),
                      worst_tail=round(result['worst_tail_ratio'], 4),
                      near=result['near'], surpass=result['surpass'],
                      seats={s['profile']: (round(s['S_ratio'], 3), round(s['tail_ratio'], 3))
                             for s in result['seats']}), ensure_ascii=False))
if a.out:
    a.out.write_text(json.dumps(dict(result=result, student=student), ensure_ascii=False, indent=1))
