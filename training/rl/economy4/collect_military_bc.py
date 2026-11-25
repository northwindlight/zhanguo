"""Collect the military decisions leading to the first safe neutral capture."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import gzip
from pathlib import Path
import pickle
import random
import sys

import numpy as np


def military_action(key, world, actor):
    kind, args = key
    if kind == 'build':
        return args[2] in ('兵营', '军屯', '补给厂')
    if kind == 'buy':
        return args[0] in ('补给', '装备') and (
            world.nation_building_count(actor, '兵营') or world.nation_armies(actor))
    if kind == 'attack':
        return world.owned_by(*args[1:]) is None
    if kind == 'move':
        return world.owned_by(*args[1:]) in (None, actor)
    return kind in ('recruit', 'retreat')


def capture(job):
    python_root, output, seed, limit = job
    sys.path.insert(0, str(python_root))
    from rl.candidates import TYPES
    from rl.demo import _match_key, _recording
    from rl.env import Env
    from rl.obs import Observer, turn_features
    from rl.war_teacher import make_w3

    env = Env(24, 4, max_turns=80).reset(seed)
    world = env.world
    teacher = make_w3()
    names = [env.seat_name(seat) for seat in range(4)]
    observers = {name: Observer(world, name) for name in names}
    steps = {name: [] for name in names}
    memory = {name: [] for name in names}
    history = {name: [0]*len(TYPES) for name in names}
    rng = {name: random.Random(f'{seed}:{name}') for name in names}
    initial = {name: len(world.own_tiles(name)) for name in names}
    completed, deaths = {}, Counter()

    def record(key, seat, name, turn):
        if not military_action(key, world, name):
            return
        candidates = env.candidates(seat)
        index = _match_key(key, candidates)
        if index is None or not candidates[index].executable:
            return
        observation = observers[name].encode(world, candidates, 80)
        compact = {k: v.astype(np.float16) if v.dtype == np.float32 and k != 'nations' else v
                   for k, v in observation.items()}
        steps[name].append((compact, index, turn, key[0]))

    for turn in range(limit):
        first = (seed//4 + turn) % 4
        for offset in range(4):
            seat = (first + offset) % 4
            name = names[seat]
            if name not in world.nations:
                continue
            if name in completed:
                teacher(world, name, rng[name])
            else:
                observation = observers[name].encode(world, env.candidates(seat), 80)
                memory[name].append(turn_features(observation, history[name], 0, sum(history[name]), 24))
                action_count = [0]*len(TYPES)

                def on_key(key):
                    action_count[TYPES.index(key[0])] += 1
                    record(key, seat, name, turn)

                with _recording(world, name, on_key):
                    teacher(world, name, rng[name])
                action_count[0] += 1
                history[name] = action_count
            world.end_turn(name)
        settlement = world.resolve_turn()
        for name, (_, _, count) in settlement['famine'].items():
            if name not in completed:
                deaths[name] += count
        for seat, name in enumerate(names):
            if name in completed or name not in world.nations:
                continue
            if len(world.own_tiles(name)) > initial[name] and not deaths[name] and steps[name]:
                if any(item[3] == 'attack' for item in steps[name]):
                    completed[name] = dict(seed=seed, seat=seat, first_capture=turn+1,
                                           features=np.stack(memory[name]), steps=steps[name])
        if all(name in completed or name not in world.nations for name in names):
            break
        world.begin_turn()
    result = list(completed.values())
    with gzip.open(output/f'{seed}.pkl.gz', 'wb', compresslevel=3) as stream:
        pickle.dump(result, stream, protocol=pickle.HIGHEST_PROTOCOL)
    return seed, [(row['seat'], row['first_capture'], len(row['steps'])) for row in result]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=250100)
    parser.add_argument('--maps', type=int, default=16)
    parser.add_argument('--turns', type=int, default=45)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    jobs = [(args.python_root, args.output, seed, args.turns) for seed in range(args.seed, args.seed+args.maps)]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for seed, captures in pool.map(capture, jobs):
            print(seed, captures, flush=True)


if __name__ == '__main__':
    main()
