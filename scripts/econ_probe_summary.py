"""Summarize completed synthetic causal probes without promoting a policy."""
import argparse
import hashlib
import json
from pathlib import Path


def summarize(path, actor):
    result = json.loads(path.read_text())
    end = result['arguments']['turns']
    if not result['history'] or result['history'][-1]['period'] != end:
        raise ValueError(f'Incomplete history: {path}')
    countries = {}
    for key, final in result['final'].items():
        history = {r['period']: r['countries'][key] for r in result['history']}
        tail = None
        if end-50 in history:
            tail = (final['S']-history[end-50]['S'])/50
        actions = (None if result['arguments'].get('intervention') == 'teacher'
                   and key == result['arguments']['focal'] else final['actions'])
        countries[key] = dict(S=final['S'], increment=final['S']-result['initial_S'][key],
            tail_mean=tail, army=final['army'], buildings=final['buildings'], actions_since_start=actions,
            capacity=final['energy']['capacity'], demand=final['energy']['demand'],
            tail_grid_short_turns=sum(bool(row['energy']['previous_short'])
                for period, row in history.items() if period > end-50))
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        start=result['start_period'], end=end, policy_sha256=result['policy_sha256'],
        arguments=result['arguments'], total_S=sum(v['S'] for v in countries.values()),
        initial_S=result['initial_S'], countries=countries, focal=countries[actor])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--actor', default='scenario:n0')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--figure', type=Path)
    args = p.parse_args()
    results = {}
    for path in sorted(args.root.glob('*/result.json')):
        data = json.loads(path.read_text())
        if data['arguments']['turns'] != 150:
            continue
        results[path.parent.name] = summarize(path, args.actor)
    branches = {name: data for name, data in results.items() if name.startswith('p80-')}
    if branches:
        origins = [r['initial_S'] for r in branches.values()]
        if not all(origin == origins[0] for origin in origins):
            raise ValueError('Interventions did not start from identical consumption ledgers')
    payload = dict(actor=args.actor, accepted=False, results=results)
    checks = {}
    for label, relative in (
        ('final_results', 'final-result-check.json'),
        ('platform_control', 'control-platform-check.json'),
        ('checkpoints', 'checkpoint-control-check.json'),
        ('reward_ledger', 'training/reward-ledger-check.json'),
    ):
        path = args.root/relative
        if path.exists():
            checks[label] = json.loads(path.read_text())
    payload['checks'] = checks
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
    for name, result in results.items():
        focal = result['focal']
        tail = f"{focal['tail_mean']:.2f}" if focal['tail_mean'] is not None else 'NA'
        print(f"{name}: S={focal['S']:.2f}, tail={tail}, "
              f"power={focal['capacity']}/{focal['demand']}, all4_S={result['total_S']:.2f}")
    if args.figure:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        names = ['r6-greedy', 'p80-bc', 'p80-power', 'p80-save_for_power',
                 'p80-teacher', 'p80-corrective', 'p80-prior0-focal']
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
        for name in names:
            if name not in results:
                continue
            result = results[name]
            raw = json.loads(Path(result['path']).read_text())
            series = [(r['period'], r['countries'][args.actor]['S']) for r in raw['history'] if r['period'] >= 80]
            if result['start'] == 80:
                series.insert(0, (80, raw['initial_S'][args.actor]))
            periods, values = zip(*series)
            axes[0].plot(periods, values, label=name)
            energy = [(r['period'], r['countries'][args.actor]['energy']['demand']-
                       r['countries'][args.actor]['energy']['capacity']) for r in raw['history'] if r['period'] >= 80]
            axes[1].plot(*zip(*energy), label=name)
        axes[0].set(xlabel='Completed periods', ylabel='Cumulative S', title=f'{args.actor}: remaining economy')
        axes[1].set(xlabel='Completed periods', ylabel='Demand minus capacity', title='Nominal power balance')
        axes[1].axhline(0, color='black', linewidth=.6)
        for ax in axes:
            ax.grid(alpha=.2)
        axes[0].legend(fontsize=7)
        args.figure.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.figure)
        if args.figure.suffix.lower() == '.svg':
            args.figure.write_text('\n'.join(
                line.rstrip() for line in args.figure.read_text().splitlines())+'\n')
        plt.close(fig)


if __name__ == '__main__':
    main()
