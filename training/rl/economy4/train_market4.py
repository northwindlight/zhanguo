import fcntl
from collections import Counter
import json
from pathlib import Path
import re
import shutil
from statistics import mean, median
import subprocess
import sys
import time

import torch


ROOT = Path(__file__).resolve().parent
CPP = ROOT / 'cpp'
PYTHON_ROOT = Path('/content/zl')
OUT = ROOT / 'results'
BUILD = ROOT / 'build'
SAMPLER = BUILD / 'zhanguo_torch'
TURNS = 60
MAP_SIZE = 24
REWARD_SCALE = 3000
RANDOM_SEED = 82251
TOTAL_GAMES = 200


def publish(target, value):
    partial = target.with_suffix('.tmp')
    partial.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    partial.replace(target)


def run(log, args):
    args = [str(item) for item in args]
    print(log.name, ' '.join(args), flush=True)
    start = time.monotonic()
    with log.open('w') as stream:
        subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, check=True)
    print(log.name, round(time.monotonic() - start, 1), 's', flush=True)
    return log.read_text()


def initialize(init):
    if init.exists():
        checkpoint = torch.load(init, map_location='cpu', weights_only=True)
        if checkpoint.get('random_seed') != RANDOM_SEED or checkpoint['cfg']['exec_prior_weight'] != 0:
            raise ValueError('Unexpected initial policy')
        return
    sys.path.insert(0, str(PYTHON_ROOT))
    from rl.model import Policy

    torch.manual_seed(RANDOM_SEED)
    model = Policy(d=192, layers=4, heads=8, arch='xattn', cand_layers=2, cand_self=True, exec_prior_weight=0)
    temporary = init.with_suffix('.tmp')
    torch.save(dict(cfg=model.cfg, model=model.state_dict(), random_seed=RANDOM_SEED), temporary)
    temporary.replace(init)


def native(checkpoint, *, fp32=False):
    suffix = '-native-fp32.pt' if fp32 else '-native.pt'
    policy = checkpoint.with_name(checkpoint.stem + suffix)
    if not policy.exists():
        temporary = policy.with_suffix('.tmp.pt')
        precision = [] if fp32 else ['--native-bf16', '--bf16-tol', 1.0]
        run(policy.with_suffix('.log'), [sys.executable, CPP/'export_policy.py', checkpoint, temporary,
                                        '--python-root', PYTHON_ROOT, *precision])
        temporary.replace(policy)
    return policy


def sample_stats(raw, games, seed, turns=TURNS, self_play=False):
    rows = [dict(re.findall(r'(\w+)=(\S+)', line)) for line in raw.splitlines() if line.startswith('game ')]
    markets = [line for line in raw.splitlines() if line.startswith('market ')]
    side_count = 4 if self_play else 1
    mode = 'market4_selfplay' if self_play else 'market4'
    roster = {(s, p) for s in range(seed, seed+games) for p in (range(4) if self_play else (s % 4,))}
    if (len(rows) != len(roster) or {(int(row['seed']), int(row['seat'])) for row in rows} != roster or
            any(row['opp'] != mode or int(row['turns']) != turns for row in rows) or
            len(markets) != len(roster) * turns):
        raise ValueError('Incomplete four-country rollout')
    settlements = set()
    for line in markets:
        _, game, turn, first, *_ = line.split()
        s, t, actor = (int(field.split('=')[1]) for field in (game, turn, first))
        p = int(line.rsplit(' seat=', 1)[1]) if self_play else s % 4
        if (s, p) not in roster or not 1 <= t <= turns or actor != (s // 4 + t - 1) % 4:
            raise ValueError(f'Incorrect first actor: {line}')
        settlements.add((s, t, p))
    if len(settlements) != len(markets):
        raise ValueError('Duplicate four-country settlement')
    total = raw.splitlines()[-1]
    throughput = float(re.search(r'decisions_per_second=(\S+)', total)[1])
    ordered = sorted(rows, key=lambda row: (int(row['seed']), int(row['seat'])))
    supply_stats = {key: sum(int(row[key]) for row in rows)
                    for key in ('supply_short_turns', 'starved_units') if all(key in row for row in rows)}
    return dict(games=games, episodes=games*side_count, seed=seed, decisions=sum(int(r['decisions']) for r in rows),
                fixed_gdp=mean(float(r['reward']) for r in rows)/turns,
                median_gdp=median(float(r['reward']) for r in rows)/turns,
                current_gdp=mean(float(r['current_gdp']) for r in rows)/turns,
                blackout_rate=mean(float(r['blackout']) for r in rows)/turns,
                invalid_rate=sum(int(r['invalid']) for r in rows)/sum(int(r['decisions']) for r in rows),
                regular=mean(int(r['regular']) for r in rows), land=mean(int(r['land']) for r in rows),
                median_land=median(int(r['land']) for r in rows),
                land_by_seat={f"{r['seed']}:{r['seat']}": int(r['land']) for r in ordered},
                cash=mean(float(r['cash']) for r in rows), supply=mean(float(r['supply']) for r in rows),
                seats=dict(Counter(int(r['seat']) for r in rows)), throughput=throughput,
                per_game_gdp=[float(r['reward'])/turns for r in ordered], **supply_stats)


def sample(name, checkpoint, games, seed, learn=False):
    report = OUT / f'{name}.json'
    trajectories = OUT / f'{name}.trajectories' if learn else None
    if report.exists():
        return json.loads(report.read_text()), trajectories
    log = OUT / f'{name}.sample.log'
    if trajectories and trajectories.exists():
        if len(list(trajectories.glob('*.zgrl'))) == games and log.is_file():
            try:
                stats = sample_stats(log.read_text(), games, seed)
            except (ValueError, IndexError, TypeError):
                pass
            else:
                publish(report, stats)
                return stats, trajectories
        shutil.rmtree(trajectories)
    args = [SAMPLER, 'sample', native(checkpoint), 'cuda', games, min(48, games),
            MAP_SIZE, TURNS, seed, 10, 'native', '--market4']
    if trajectories:
        args.append(trajectories)
    stats = sample_stats(run(log, args), games, seed)
    if trajectories and len(list(trajectories.glob('*.zgrl'))) != games:
        raise ValueError(f'Expected {games} completed trajectories')
    publish(report, stats)
    print(name, json.dumps({k: v for k, v in stats.items() if k != 'per_game_gdp'}), flush=True)
    return stats, trajectories


def update(name, previous, trajectories, lr, seed, stats):
    destination = OUT / f'{name}.pt'
    log = OUT / f'{name}.update.log'
    if destination.exists():
        raw = log.read_text()
    else:
        raw = run(log, [sys.executable, CPP/'ppo_update.py', previous, trajectories, destination,
                        '--python-root', PYTHON_ROOT, '--lr', lr, '--seed', seed,
                        '--minibatch', 32, '--subsample', 0.3, '--max-batches', 192,
                        '--gae-lambda', 0.95, '--gamma', 1, '--target-kl', 0.05,
                        '--reward-scale', REWARD_SCALE, '--invalid-penalty', 0,
                        '--aux-weight', 0, '--exec-weight', 0, '--kl-ref', 0, '--amp'])
    report = json.loads(raw.splitlines()[-1])
    if report['episodes'] != stats['games'] or report['decisions'] != stats['decisions'] or report['minibatches'] <= 0:
        raise ValueError(f'PPO did not consume the expected trajectories for {name}')
    checkpoint = torch.load(destination, map_location='cpu', weights_only=True)
    if checkpoint['cfg']['exec_prior_weight'] != 0 or not all(t.isfinite().all() for t in checkpoint['model'].values()):
        raise ValueError(f'Invalid PPO checkpoint {destination}')
    publish(OUT / f'{name}.update.json', report)
    return destination, report


def main():
    OUT.mkdir(exist_ok=True)
    with (OUT/'.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not SAMPLER.is_file():
            run(OUT/'build.log', [sys.executable, CPP/'build_colab.py', '--build', BUILD, '--jobs', 6])
        init = OUT/'random-init.pt'
        initialize(init)
        first, tracks = sample('train-01', init, 48, 120000, learn=True)
        results = []
        for name, lr in [('lr-3e-5', 3e-5), ('lr-1e-4', 1e-4)]:
            checkpoint, report = update(name, init, tracks, lr, 120000, first)
            results.append(dict(name=name, lr=lr, update=report, checkpoint=str(checkpoint)))
        if tracks.exists():
            shutil.rmtree(tracks)

        initial, _ = sample('compare-random', init, 16, 130000)
        for branch in results:
            branch['eval'], _ = sample('compare-' + branch['name'], Path(branch['checkpoint']), 16, 130000)
        leader = max(results, key=lambda branch: branch['eval']['fixed_gdp'])
        publish(OUT/'learning-rate.json', dict(selected=leader['name'], random=initial, branches=results))
        print('Selected learning rate', leader['lr'], flush=True)

        previous = Path(leader['checkpoint'])
        fit = [first]
        for i, seed in ((2, 120048), (3, 120088)):
            stats, tracks = sample(f'train-{i:02d}', previous, 40, seed, learn=True)
            previous, _ = update(f'update-{i:02d}', previous, tracks, leader['lr'], seed, stats)
            if tracks.exists():
                shutil.rmtree(tracks)
            fit.append(stats)
        final_initial, _ = sample('final-random', init, 12, 140000)
        final, _ = sample('final-trained', previous, 12, 140000)
        counted = fit + [initial, *(item['eval'] for item in results), final_initial, final]
        assert sum(item['games'] for item in counted) == TOTAL_GAMES
        assert {seat: sum(item['seats'].get(seat, item['seats'].get(str(seat), 0)) for item in counted)
                for seat in range(4)} == dict.fromkeys(range(4), TOTAL_GAMES // 4)
        publish(OUT/'done.json', dict(games=TOTAL_GAMES, train_games=sum(s['games'] for s in fit),
                                    map_size=MAP_SIZE, turns=TURNS, lr=leader['lr'],
                                    final_checkpoint=str(previous), final_native=str(native(previous)),
                                    training=fit, random=final_initial, final=final))
        print(f'Completed {TOTAL_GAMES} full games', flush=True)


if __name__ == '__main__':
    main()
