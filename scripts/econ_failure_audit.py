"""Read-only audit of the economic PPO chain (2026-11-11 report).

Three subcommands, none of which trains, mutates a run directory or writes to
``--live``:

  trajectories   采样构成 / 重复观测 / 回合内 return 共享 / 优势方差分解
  weights        参数轨迹 vs 随机游走 / 固定状态集上的 argmax 合法率、族分布、熵
  logs           8,237 条更新记录的梯度范数、分项损失、KL β

The original trajectories are pruned to the last two rounds by the trainer
(``ppo_cycle.prune``); pass one or more ``round-NNNNNN`` directories.  Only
NumPy is needed for ``trajectories`` and ``logs``; ``weights`` additionally
needs torch to load ``candidate.pt``.

Example (numbers quoted in docs/reviews/经济RL二十轮失败归因审计-20261111.md):

  python3 scripts/econ_failure_audit.py trajectories \
      --rounds archive/previews/ppo-continue-20261107/live/round-000020 \
      --work /tmp/econ-audit
  python3 scripts/econ_failure_audit.py weights \
      --live archive/previews/ppo-continue-20261107/live \
      --bc archive/previews/econ14-20261107/live/epoch-15.pt --work /tmp/econ-audit
  python3 scripts/econ_failure_audit.py logs \
      --live archive/previews/ppo-continue-20261107/live
"""
import argparse
import glob
import gzip
import hashlib
import json
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------- trajectories

def expand_world(path, work):
    """Flatten one trajectory into a compact npz: per-step scalars only."""
    out = Path(work) / (Path(path).stem.replace('trajectory-', 'steps-') + '.npz')
    if out.exists():
        return out
    with gzip.open(path, 'rb') as handle:
        streams = pickle.load(handle)
    rows = []
    for index, stream in enumerate(streams):
        previous = None
        for step in stream:
            row = step['row']
            key = hashlib.blake2b(row['context'].tobytes() + row['candidates'].tobytes(),
                                  digest_size=8).digest()
            legal = row['legal']
            rows.append(dict(stream=index, period=step['period'], reward=step['reward'],
                             value=step['value'], action=step['action'],
                             kind=step['action_kind'], executed=str(step['executed']),
                             logp=step['logp'], ncand=len(legal), nlegal=int(legal.sum()),
                             legal_chosen=bool(legal[step['action']]),
                             dup=int(key == previous)))
            previous = key
    for index in range(len(streams)):
        future = 0.
        for row in reversed([r for r in rows if r['stream'] == index]):
            future += row['reward']
            row['ret'] = future
    np.savez_compressed(
        out,
        stream=np.array([r['stream'] for r in rows]),
        period=np.array([r['period'] for r in rows]),
        reward=np.array([r['reward'] for r in rows]),
        value=np.array([r['value'] for r in rows]),
        ret=np.array([r['ret'] for r in rows]),
        action=np.array([r['action'] for r in rows]),
        kind=np.array([r['kind'] for r in rows]),
        executed=np.array([r['executed'] for r in rows]),
        logp=np.array([r['logp'] for r in rows]),
        ncand=np.array([r['ncand'] for r in rows]),
        nlegal=np.array([r['nlegal'] for r in rows]),
        legal_chosen=np.array([r['legal_chosen'] for r in rows]),
        dup=np.array([r['dup'] for r in rows]))
    return out


def audit_trajectories(args):
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    files = []
    for folder in args.rounds:
        files.extend(sorted(glob.glob(str(Path(folder) / 'trajectory-*.pkl.gz'))))
    if not files:
        raise SystemExit('No trajectories found; the trainer keeps only the last two rounds')
    shards = [np.load(expand_world(path, work)) for path in files]
    cat = lambda key: np.concatenate([shard[key] for shard in shards])
    stream, period = cat('stream'), cat('period')
    reward, value, ret = cat('reward'), cat('value'), cat('ret')
    kind, executed = cat('kind'), cat('executed')
    ncand, nlegal = cat('ncand'), cat('nlegal')
    legal_chosen, dup = cat('legal_chosen'), cat('dup')
    logp = cat('logp')
    # file-local stream ids
    gid = np.concatenate([np.full(len(shard['stream']), i * 4) + shard['stream']
                          for i, shard in enumerate(shards)])
    failed, ok = executed == 'False', executed == 'True'
    advantage = ret - value
    advantage = (advantage - advantage.mean()) / max(advantage.std(), 1e-9)

    print(json.dumps(dict(
        worlds=len(files), steps=int(len(stream)), streams=int(len(set(gid.tolist()))),
        kinds=dict(Counter(kind.tolist())),
        executed=dict(Counter(executed.tolist())),
        duplicate_observations=round(float(dup.mean()), 4),
        sampled_legal_rate=round(float(legal_chosen.mean()), 4),
        legal_fraction_of_candidates=round(float((nlegal / np.maximum(ncand, 1)).mean()), 4),
        legal_fraction_median=round(float(np.median(nlegal / np.maximum(ncand, 1))), 5),
        failures_that_were_legal=int((failed & legal_chosen).sum()),
        failures_that_were_illegal=int((failed & ~legal_chosen).sum()),
        logp_median=round(float(np.median(logp)), 3)), ensure_ascii=False, indent=1))

    # one return per (country, turn): every decision in the turn shares it
    groups = defaultdict(list)
    for i, key in enumerate(zip(gid.tolist(), period.tolist())):
        groups[key].append(i)
    steps_per_turn = np.array([len(v) for v in groups.values()])
    cell = np.empty_like(ret)
    for key, index in groups.items():
        cell[index] = ret[index].mean()
    cell_r2 = 1 - ((ret - cell) ** 2).sum() / ((ret - ret.mean()) ** 2).sum()

    # advantage variance decomposition
    stream_mean = np.array([advantage[gid == g].mean() for g in sorted(set(gid.tolist()))])
    residual = advantage.copy()
    for g in set(gid.tolist()):
        mask = gid == g
        design = np.vstack([np.ones(mask.sum()), period[mask]]).T
        residual[mask] -= design @ np.linalg.lstsq(design, advantage[mask], rcond=None)[0]
    turn_success = np.array([ok[index].mean() > 0 for index in groups.values()])
    turn_adv = np.array([advantage[index][0] for index in groups.values()])
    print(json.dumps(dict(
        turns=len(groups), steps_per_turn_mean=round(float(steps_per_turn.mean()), 3),
        steps_per_turn_median=float(np.median(steps_per_turn)),
        turns_hitting_96=int((steps_per_turn >= 96).sum()),
        return_cell_mean_r2=round(float(cell_r2), 6),
        advantage_between_stream_share=round(float(stream_mean.var() / advantage.var()), 4),
        advantage_residual_after_stream_and_period=round(float(residual.var() / advantage.var()), 4),
        mean_advantage_success=round(float(advantage[ok].mean()), 4),
        mean_advantage_failed=round(float(advantage[failed].mean()), 4),
        mean_advantage_finish=round(float(advantage[executed == 'None'].mean()), 4),
        corr_turn_success_vs_advantage=round(float(np.corrcoef(turn_success, turn_adv)[0, 1]), 4),
    ), ensure_ascii=False, indent=1))

    # action -> downside/upside association inside a country and turn trend
    table = defaultdict(list)
    for key, index in groups.items():
        index = np.array(index)
        table['ds'].append(reward[index].sum())
        table['build'].append(ok[index][kind[index] == 'build'].sum())
        table['recruit'].append(ok[index][kind[index] == 'recruit'].sum())
    rows = np.array([(k[0], k[1], table['ds'][i], table['build'][i], table['recruit'][i])
                     for i, k in enumerate(groups)], dtype=float)
    g, p, ds, build, recruit = rows.T

    def detrend(y, x):
        ry, rx = y.copy(), x.copy()
        for one in np.unique(g):
            mask = g == one
            design = np.vstack([np.ones(mask.sum()), p[mask]]).T
            ry[mask] -= design @ np.linalg.lstsq(design, y[mask], rcond=None)[0]
            rx[mask] -= design @ np.linalg.lstsq(design, x[mask], rcond=None)[0]
        return ry, rx

    print(json.dumps({
        f'corr_{name}_vs_turn_dS': round(float(np.corrcoef(*detrend(ds, column))[0, 1]), 3)
        for name, column in (('build', build), ('recruit', recruit))}, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------- weights

def weight_vectors(live, bc):
    import torch
    paths = [('BC15', Path(bc))] + [
        (f'R{i:02d}', Path(live) / f'round-{i:06d}' / 'candidate.pt')
        for i in range(1, 21) if (Path(live) / f'round-{i:06d}' / 'candidate.pt').exists()]
    vectors = {}
    for name, path in paths:
        state = torch.load(path, map_location='cpu', weights_only=True)['model']
        keys = [k for k in state if not k.startswith('critic')]
        vectors[name] = torch.cat([state[k].reshape(-1).float() for k in keys])
    return vectors


def audit_weights(args):
    import torch
    vectors = weight_vectors(args.live, args.bc)
    names = list(vectors)
    deltas = [vectors[b] - vectors[a] for a, b in zip(names, names[1:])]
    random_walk = float(sum(float((d ** 2).sum()) for d in deltas) ** .5)
    total = float((vectors[names[-1]] - vectors[names[0]]).norm())
    step = 1. if len(deltas) < 2 else float(torch.nn.functional.cosine_similarity(deltas[0], deltas[1], dim=0))
    print(json.dumps(dict(
        checkpoints=names,
        norm_bc=round(float(vectors[names[0]].norm()), 3),
        displacement=round(total, 3),
        random_walk_expectation=round(random_walk, 4),
        ratio=round(total / random_walk, 4),
        first_cosine=round(step, 4),
        consecutive_cosine=[round(float(torch.nn.functional.cosine_similarity(a, b, dim=0)), 4)
                            for a, b in zip(deltas, deltas[1:])]), ensure_ascii=False, indent=1))

    if not args.states:
        return
    from rl.economy.model import Policy, collate, prepare
    torch.set_num_threads(1)
    states = pickle.load(open(args.states, 'rb'))
    summary = {}
    for name in names:
        state = torch.load(Path(args.live) / f'round-{int(name[1:]):06d}' / 'candidate.pt'
                           if name.startswith('R') else args.bc, map_location='cpu', weights_only=True)
        model = Policy(**state['config'])
        model.load_state_dict(state['model'])
        model.eval()
        legal, kinds, entropy, mass = [], [], [], []
        with torch.inference_mode():
            for item in states:
                batch = prepare(collate([item['row']]))
                logits, _, _ = model(batch, return_value=True)
                probabilities = logits[0].softmax(-1)
                index = int(logits[0].argmax())
                legal.append(int(batch['legal'][0][index]))
                kinds.append(int(batch['candidates'][0][index, 5:10].argmax()))
                entropy.append(float(-(probabilities * probabilities.clamp_min(1e-12).log()).sum()))
                mass.append(float(probabilities[batch['legal'][0]].sum()) if batch['legal'][0].any() else 0.)
        summary[name] = dict(legal_argmax=round(float(np.mean(legal)), 4),
                             entropy=round(float(np.mean(entropy)), 3),
                             legal_mass=round(float(np.mean(mass)), 4),
                             kinds=dict(Counter(kinds)))
        print(json.dumps({name: summary[name]}, ensure_ascii=False), flush=True)


# ------------------------------------------------------------------------ logs

def audit_logs(args):
    rows = []
    for folder in sorted(glob.glob(str(Path(args.live) / 'round-*'))):
        path = Path(folder) / 'updates.jsonl'
        if not path.exists():
            continue
        records = [json.loads(line) for line in path.open()]
        norm = np.array([r['gradient_norm'] for r in records])
        policy = np.array([r['policy'] for r in records])
        rows.append(dict(round=int(Path(folder).name.split('-')[-1]), updates=len(records),
                         grad_median=round(float(np.median(norm)), 2),
                         grad_max=round(float(norm.max()), 2),
                         clipped=round(float((norm > args.clip).mean()), 3),
                         policy_mean=round(float(policy.mean()), 5),
                         policy_std=round(float(policy.std()), 5),
                         policy_negative=round(float((policy < 0).mean()), 3),
                         policy_se_over_mean=round(float(policy.std() / len(policy) ** .5 / abs(policy.mean())), 2)
                         if policy.mean() else None,
                         value_mean=round(float(np.mean([r['value'] for r in records])), 4),
                         beta_floor=round(float(np.mean([r['kl_beta'] <= 1e-4 for r in records])), 2)))
    print(json.dumps(rows, ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    t = sub.add_parser('trajectories')
    t.add_argument('--rounds', nargs='+', required=True)
    t.add_argument('--work', default='/tmp/econ-audit')
    t.set_defaults(func=audit_trajectories)
    w = sub.add_parser('weights')
    w.add_argument('--live', required=True)
    w.add_argument('--bc', required=True)
    w.add_argument('--states', help='pickle of [{"row": ...}] fixed states for argmax/legality')
    w.set_defaults(func=audit_weights)
    l = sub.add_parser('logs')
    l.add_argument('--live', required=True)
    l.add_argument('--clip', type=float, default=.5)
    l.set_defaults(func=audit_logs)
    args = parser.parse_args()
    if not Path(args.live if hasattr(args, 'live') else '.').exists():
        raise SystemExit('run directory not found')
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
