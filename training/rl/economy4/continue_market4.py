import argparse
from dataclasses import dataclass, replace
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import torch

from train_market4 import BUILD, CPP, MAP_SIZE, OUT, PYTHON_ROOT, REWARD_SCALE, SAMPLER, TURNS
from train_market4 import initialize, native, publish, run, sample_stats


START = Path('/content/codex-market4-200/results/update-03.pt')


@dataclass(frozen=True)
class MarketRun:
    out: Path
    turns: int
    games: int
    seed_start: int
    self_play: bool
    fp32: bool = False
    mean_gdp_target: float | None = None
    objective: str = 'gdp'
    gae_lambda: float = 0.95

    @property
    def seats(self):
        return 4 if self.self_play else 1

    @property
    def header(self):
        return b'ZGRL6\0\0\0' if self.self_play else b'ZGRL5\0\0\0'

    @property
    def stop_gdp(self):
        return 60 if self.self_play else 30

    @property
    def stopped_name(self):
        return 'stopped-at-60' if self.self_play else 'stopped-over-30'

    def reached(self, gdp):
        return gdp >= self.stop_gdp if self.self_play else gdp > self.stop_gdp


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshot(source, target):
    temporary = target.with_suffix('.link')
    temporary.unlink(missing_ok=True)
    os.link(source, temporary)
    temporary.replace(target)


def game_record(line, batch, completed, checkpoint, campaign):
    row = dict(re.findall(r'(\w+)=(\S+)', line))
    mode = 'market4_selfplay' if campaign.self_play else 'market4'
    if row['opp'] != mode or int(row['turns']) != campaign.turns:
        raise ValueError(f'Unexpected completed game: {line}')
    mean_gdp = float(row['reward']) / campaign.turns
    if not math.isfinite(mean_gdp):
        raise ValueError(f'Invalid GDP in {line}')
    return dict(seed=int(row['seed']), seat=int(row['seat']), first=int(row['first']),
                mean_fixed_gdp=mean_gdp, mean_current_gdp=float(row['current_gdp'])/campaign.turns,
                regular=int(row['regular']), land=int(row['land']), blackout_turns=int(row['blackout']),
                cash=float(row['cash']), supply=float(row['supply']), batch=batch,
                completed_game=completed, checkpoint=checkpoint.name)


def milestone(out, name, row, checkpoint, policy):
    report = out / f'{name}.json'
    if report.exists() and name not in ('best', 'best-batch-mean'):
        return
    saved = out / f'{name}.pt'
    native_saved = out / f'{name}-native.pt'
    snapshot(checkpoint, saved)
    snapshot(policy, native_saved)
    value = dict(**row, saved=saved.name, native=native_saved.name,
                 sha256=sha256(saved), native_sha256=sha256(native_saved))
    publish(report, value)
    print(name, json.dumps(value), flush=True)


def process_games(lines, state, checkpoint, policy, campaign):
    out = campaign.out
    peak_path = out/'best.json'
    peak = json.loads(peak_path.read_text())['mean_fixed_gdp'] if peak_path.exists() else float('-inf')
    rows = []
    for line in lines:
        if not line.startswith('game '):
            continue
        completed = state['games'] + len(rows)//campaign.seats + 1
        row = game_record(line, state['batches']+1, completed, checkpoint, campaign)
        rows.append(row)
        if len(rows) % campaign.seats:
            continue
        row = max(rows[-campaign.seats:], key=lambda item: item['mean_fixed_gdp'])
        gdp = row['mean_fixed_gdp']
        if campaign.mean_gdp_target is None and gdp > peak:
            milestone(out, 'best', row, checkpoint, policy)
            peak = gdp
        if not campaign.self_play and gdp > 10:
            milestone(out, 'first-over-10', row, checkpoint, policy)
        live = dict(state='sampling', games=row['completed_game'], batch=row['batch'], lr=state['lr'],
                    turns=campaign.turns, objective=campaign.objective)
        if campaign.mean_gdp_target is None:
            live['peak'] = peak
        publish(out/'live.json', live)
    return rows


def rollout(state, checkpoint, campaign):
    out = campaign.out
    batch = state['batches']+1
    seed = campaign.seed_start + state['games']
    log = out / f'sample-{batch:06d}.log'
    tracks = out / f'roll-{batch:06d}'
    policy = native(checkpoint, fp32=campaign.fp32)
    completed_tracks = list(tracks.glob('*.zgrl'))
    expected = campaign.games * campaign.seats
    if (log.exists() and completed_tracks and
            all(read_header(path) == campaign.header for path in completed_tracks)):
        raw = log.read_text()
        if any(line.startswith('stop_gdp ') for line in raw.splitlines()) and raw.splitlines()[-1].startswith('completed='):
            rows = process_games(raw.splitlines(), state, checkpoint, policy, campaign)
            save_stopped_run(raw, rows, tracks, checkpoint, policy, state, campaign)
            return None, tracks
        try:
            stats = sample_stats(raw, campaign.games, seed, campaign.turns, campaign.self_play)
        except (ValueError, IndexError, TypeError):
            pass
        else:
            process_games(raw.splitlines(), state, checkpoint, policy, campaign)
            return stats, tracks
    if tracks.exists():
        shutil.rmtree(tracks)
    publish(out/'live.json', dict(state='sampling', games=state['games'], batch=batch, lr=state['lr'],
                                  turns=campaign.turns, objective=campaign.objective))
    mode = '--market4-selfplay' if campaign.self_play else '--market4'
    precision = 'fp32' if campaign.fp32 else 'native'
    stop_args = ['--stop-gdp', campaign.stop_gdp] if campaign.mean_gdp_target is None and campaign.objective == 'gdp' else []
    args = [SAMPLER, 'sample', policy, 'cuda', campaign.games, campaign.games, MAP_SIZE, campaign.turns,
            seed, 10, precision, mode, *stop_args, tracks]
    args = [str(item) for item in args]
    print('Starting batch', batch, 'seed', seed, 'total', state['games'], 'lr', state['lr'],
          'objective', campaign.objective, 'turns', campaign.turns, flush=True)
    with log.open('w') as output, subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                   text=True, bufsize=1) as process:
        def stream():
            for line in process.stdout:
                output.write(line)
                if line.startswith(('game ', 'stop_gdp ')):
                    output.flush()
                yield line

        rows = process_games(stream(), state, checkpoint, policy, campaign)
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, args)
    raw = log.read_text()
    if any(line.startswith('stop_gdp ') for line in raw.splitlines()):
        save_stopped_run(raw, rows, tracks, checkpoint, policy, state, campaign)
        return None, tracks
    stats = sample_stats(raw, campaign.games, seed, campaign.turns, campaign.self_play)
    if len(list(tracks.glob('*.zgrl'))) != expected:
        raise ValueError('Missing market trajectories')
    return stats, tracks


def save_stopped_run(raw, rows, tracks, checkpoint, policy, state, campaign):
    last_game = rows[-campaign.seats:]
    if not last_game or len(rows) % campaign.seats:
        raise ValueError('Missing completed seats in the stopping game')
    winner = max(last_game, key=lambda row: row['mean_fixed_gdp'])
    markers = [dict(re.findall(r'(\w+)=(\S+)', line)) for line in raw.splitlines() if line.startswith('stop_gdp ')]
    if (len(markers) != 1 or int(markers[0]['seed']) != winner['seed'] or
            int(markers[0].get('seat', winner['seat'])) != winner['seat'] or
            not campaign.reached(winner['mean_fixed_gdp'])):
        raise ValueError('PPO stop was not confirmed by the sampler')
    if len(list(tracks.glob('*.zgrl'))) != len(rows):
        raise ValueError('The sampler did not finalize completed games before stopping')
    if campaign.self_play:
        game_log = campaign.out / 'stopped-at-60-game.log'
        with game_log.open('w') as output:
            for line in raw.splitlines():
                if line.startswith(('market ', 'game ', 'stop_gdp ')) and f"seed={winner['seed']} " in line:
                    output.write(line + '\n')
        winner = dict(winner, game_log=game_log.name, game_log_sha256=sha256(game_log))
    milestone(campaign.out, campaign.stopped_name, winner, checkpoint, policy)
    publish_stopped(campaign, winner, state['lr'])
    shutil.rmtree(tracks)


def publish_stopped(campaign, row, lr):
    publish(campaign.out/'live.json', dict(state='stopped', games=row['completed_game'],
                                          peak=row['mean_fixed_gdp'], batch=row['batch'], lr=lr))


def read_header(path):
    with path.open('rb') as stream:
        return stream.read(8)


def record_batch_mean(state, checkpoint, stats, campaign):
    if campaign.mean_gdp_target is None or campaign.objective != 'gdp':
        return
    out = campaign.out
    gdp = stats['fixed_gdp']
    record = dict(batch=state['batches']+1, games=state['games']+campaign.games,
                  mean_fixed_gdp=gdp, episodes=stats['episodes'], checkpoint=checkpoint.name,
                  target=campaign.mean_gdp_target, turns=campaign.turns)
    best = out/'best-batch-mean.json'
    if not best.is_file() or gdp > json.loads(best.read_text())['mean_fixed_gdp']:
        milestone(out, 'best-batch-mean', record, checkpoint, native(checkpoint, fp32=campaign.fp32))
    if gdp > campaign.mean_gdp_target and not (out/'first-batch-mean-over-target.json').exists():
        milestone(out, 'first-batch-mean-over-target', record, checkpoint, native(checkpoint, fp32=campaign.fp32))


def update(state, previous, tracks, stats, campaign):
    out = campaign.out
    batch = state['batches']+1
    target = out / f'ck-{batch:06d}.pt'
    log = out / f'update-{batch:06d}.log'
    live = dict(state='updating', games=state['games']+campaign.games, batch=batch, lr=state['lr'],
                turns=campaign.turns, objective=campaign.objective, mean_land=stats['land'])
    publish(out/'live.json', live)
    if target.exists():
        raw = log.read_text()
    else:
        precision = [] if campaign.fp32 else ['--amp']
        reward_args = ['--reward-scale', REWARD_SCALE]
        if campaign.objective == 'land':
            territory = tracks/'terminal-land.json'
            publish(territory, stats['land_by_seat'])
            reward_args = ['--reward-scale', 10, '--terminal-land', territory]
        raw = run(log, [sys.executable, CPP/'ppo_update.py', previous, tracks, target,
                        '--python-root', PYTHON_ROOT, '--seed', stats['seed'], '--lr', state['lr'],
                        '--minibatch', 32, '--subsample', 0.25, '--max-batches', 768,
                        '--clip', 0.2, '--gae-lambda', campaign.gae_lambda, '--gamma', 1, '--target-kl', 'inf',
                        *reward_args, '--invalid-penalty', 0,
                        '--aux-weight', 0, '--exec-weight', 0, '--kl-ref', 0, *precision])
    result = json.loads(raw.splitlines()[-1])
    checkpoint = torch.load(target, map_location='cpu', weights_only=True)
    if (result['episodes'] != stats['episodes'] or result['decisions'] != stats['decisions'] or
            (state.get('curriculum') and (result.get('objective') != campaign.objective or
                                         result.get('gae_lambda') != campaign.gae_lambda)) or
            checkpoint['cfg']['exec_prior_weight'] != 0 or
            not all(t.isfinite().all().item() for t in checkpoint['model'].values())):
        raise ValueError('Invalid continuing PPO update')
    publish(out/f'batch-{batch:06d}.json', dict(sample=stats, update=result, checkpoint=target.name,
                                             turns=campaign.turns, objective=campaign.objective))
    state = dict(state, batches=batch, games=state['games']+campaign.games,
                 latest=target.name, sha256=sha256(target))
    if campaign.objective == 'land':
        state['curriculum'] = advance_land_stage(state['curriculum'], batch, stats['land'])
    publish(out/'state.json', state)
    shutil.rmtree(tracks)
    # Retain the two latest updates and milestone snapshots.
    old = out/f'ck-{batch-2:06d}.pt'
    if batch > 2:
        old.unlink(missing_ok=True)
        old.with_name(old.stem+'-native.pt').unlink(missing_ok=True)
        old.with_name(old.stem+'-native-fp32.pt').unlink(missing_ok=True)
    print('Batch complete', batch, 'games', state['games'], 'GDP', stats['fixed_gdp'],
          'max', max(stats['per_game_gdp']), 'KL', result['approx_kl'],
          'objective', campaign.objective, 'turns', campaign.turns, 'mean_land', stats['land'],
          'supply_short_turns', stats.get('supply_short_turns'), 'starved_units', stats.get('starved_units'), flush=True)
    if campaign.objective == 'land' and state['curriculum']['objective'] == 'gdp':
        print('Territory target reached; next batch objective GDP', state['curriculum'], flush=True)
    return state


def advance_land_stage(curriculum, batch, mean_land):
    reached = mean_land > curriculum['target']
    streak = (curriculum['streak'] + [mean_land])[-curriculum['required_batches']:] if reached else []
    curriculum = dict(curriculum, streak=streak)
    if len(streak) == curriculum['required_batches']:
        curriculum.update(objective='gdp', qualified_batch=batch)
    return curriculum


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lr', type=float)
    parser.add_argument('--start', type=Path)
    parser.add_argument('--self-play', action='store_true')
    parser.add_argument('--turns', type=int, default=TURNS)
    parser.add_argument('--fp32', action='store_true')
    parser.add_argument('--mean-gdp-target', type=float)
    parser.add_argument('--deadline', type=float, help='Unix time to stop after completing the current batch')
    parser.add_argument('--land-curriculum', action='store_true',
                        help='Train 80-turn terminal territory until five batch means exceed ten, then GDP')
    args = parser.parse_args()
    if args.turns < 1 or (not args.self_play and args.turns != TURNS):
        parser.error('--turns may only be changed for self-play')
    out = OUT.with_name(f'results-selfplay-{args.turns}') if args.self_play else OUT
    lr = args.lr if args.lr is not None else (2e-4 if args.self_play else 3e-4)
    if not math.isfinite(lr) or lr <= 0:
        parser.error('--lr must be finite and positive')
    if args.mean_gdp_target is not None and (not args.self_play or not math.isfinite(args.mean_gdp_target)
                                             or args.mean_gdp_target <= 0):
        parser.error('--mean-gdp-target must be finite and positive and requires self-play')
    if args.deadline is not None and (not math.isfinite(args.deadline) or args.deadline <= 0):
        parser.error('--deadline must be a positive Unix timestamp')
    if args.land_curriculum and (not args.self_play or args.mean_gdp_target is None):
        parser.error('--land-curriculum requires self-play with batch-mean evaluation')
    if args.fp32:
        os.environ['NVIDIA_TF32_OVERRIDE'] = '0'
        os.environ.pop('TORCH_ALLOW_TF32_CUBLAS_OVERRIDE', None)
    campaign = MarketRun(out, args.turns, 24 if args.self_play else 64,
                         240000 if args.self_play else 150000, args.self_play, args.fp32, args.mean_gdp_target)
    out.mkdir(exist_ok=True)
    with (out/'.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stopped = out/f'{campaign.stopped_name}.json'
        if campaign.mean_gdp_target is None and stopped.exists():
            state = json.loads((out/'state.json').read_text())
            publish_stopped(campaign, json.loads(stopped.read_text()), state['lr'])
            print(f'GDP stop already recorded at {campaign.stop_gdp}', flush=True)
            return
        if (not SAMPLER.exists() or any((CPP/name).stat().st_mtime_ns > SAMPLER.stat().st_mtime_ns
                                        for name in ('trajectory.hpp', 'torch_sampler.cpp'))):
            run(out/'build.log', [sys.executable, CPP/'build_colab.py', '--build', BUILD, '--jobs', 6])
        state_path = out/'state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if args.lr is not None and state['lr'] != lr:
                previous_lr = state['lr']
                state = dict(state, lr=lr)
                publish(state_path, state)
                print('Learning rate changed', previous_lr, 'to', lr,
                      'after update', state['batches'], 'games', state['games'], flush=True)
        else:
            initial = out/'start.pt'
            if args.self_play and args.start is None:
                initialize(initial)
            else:
                temporary = initial.with_suffix('.tmp')
                shutil.copyfile(args.start or START, temporary)
                temporary.replace(initial)
            state = dict(lr=lr, batches=0, games=0, latest=initial.name, sha256=sha256(initial))
            publish(state_path, state)
        if args.land_curriculum and 'curriculum' not in state:
            state['curriculum'] = dict(turns=80, objective='land', gae_lambda=1.0, target=10,
                                       required_batches=5, streak=[], entered_after_batch=state['batches'])
            publish(state_path, state)
            print('Territory curriculum started', state['curriculum'], flush=True)
        while True:
            curriculum = state.get('curriculum')
            active = replace(campaign, turns=curriculum['turns'], objective=curriculum['objective'],
                             gae_lambda=curriculum['gae_lambda']) if curriculum else campaign
            checkpoint = out/state['latest']
            if args.deadline is not None and time.time() >= args.deadline:
                final = dict(state='completed', batch=state['batches'], games=state['games'],
                             lr=state['lr'], checkpoint=checkpoint.name, deadline=args.deadline,
                             precision='fp32' if args.fp32 else 'bf16', turns=active.turns,
                             objective=active.objective)
                milestone(out, 'day-end', final, checkpoint, native(checkpoint, fp32=campaign.fp32))
                publish(out/'live.json', final)
                return
            stats, tracks = rollout(state, checkpoint, active)
            if stats is None:
                return
            record_batch_mean(state, checkpoint, stats, active)
            state = update(state, checkpoint, tracks, stats, active)


if __name__ == '__main__':
    main()
