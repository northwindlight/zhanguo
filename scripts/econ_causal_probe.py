"""Diagnostic interventions on synthetic economies; never changes training rules.

Capture checkpoints at formal turn boundaries, then change one focal country's
decision/continuation while keeping the saved world and all other policies fixed.
Run with PYTHONPATH=. from the repository root. Outputs belong in archive/previews.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import pickle
import random
import time

import numpy as np
import torch

from host import GameHost
from rl.economy.model import collate, load_policy, prepare, device_of
from rl.economy.online import consumption_total
from rl.economy.projects import InvestmentPlanner, LearnedProjects, sample
from rl.economy.provenance import contracts, require_compatible
from rl.economy.scenarios import config
from rl.economy.trajectories import model_digest
from ruleai.v13plus.entry import RuleClient as Teacher


POWER = {'native:wood-power-plant', 'native:oil-power-plant'}


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def groups(encoded, logits):
    probabilities = logits.softmax(-1).cpu().numpy()
    result = {}
    for kind in ('finish', 'build', 'recruit', 'power'):
        if kind == 'power':
            ids = [i for i, (action, args) in enumerate(encoded['actions'])
                   if action == 'build' and args.get('kind') in POWER]
        else:
            ids = [i for i, (action, _) in enumerate(encoded['actions']) if action == kind]
        legal = [i for i in ids if encoded['legal'][i]]
        best = max(ids, key=lambda i: float(logits[i])) if ids else None
        best_legal = max(legal, key=lambda i: float(logits[i])) if legal else None
        result[kind] = dict(count=len(ids), legal=len(legal), mass=float(probabilities[ids].sum()),
                            legal_mass=float(probabilities[legal].sum()),
                            best=best, best_legal=best_legal,
                            max_logit=float(logits[best]) if best is not None else None,
                            max_legal_logit=float(logits[best_legal]) if best_legal is not None else None)
    return result


class ProbeClient(LearnedProjects):
    # State is exactly v13plus state. Use its factory for formal boundary loads;
    # each resumed experiment explicitly reinstalls the recorded learned policy.
    version = 'v13plus'

    def __init__(self, *args, sink, row_sink, mode='greedy', focal='scenario:n0',
                 intervention=None, alternative=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sink, self.row_sink, self.mode = sink, row_sink, mode
        self.focal, self.intervention, self.alternative = focal, intervention, alternative
        self.fired = False
        self.intervention_checked = False

    def run_economy(self, ledger):
        period = ledger.player.observe()['period']
        actor = ledger.player.actor
        ledger.economy_market = ledger.query('market')
        # Persistent continuation controls are diagnostics, not deployed policies.
        if actor == self.focal and self.intervention in ('teacher', 'bc'):
            if self.intervention == 'teacher':
                return Teacher.run_economy(self, ledger)
        model = self.alternative if actor == self.focal and self.intervention == 'bc' else self.model
        planner = InvestmentPlanner(ledger, self.state)
        try:
            planner.operations()
            with torch.inference_mode():
                for attempt in range(self.max_economy_actions):
                    if ledger.full():
                        break
                    encoded = planner.encode()
                    logits, value, _ = model(prepare(collate([encoded]), device_of(model)), return_value=True)
                    logits = logits[0]
                    natural = int(logits.argmax()) if self.mode == 'greedy' else int(
                        torch.distributions.Categorical(logits=logits).sample())
                    selected = natural
                    stats = groups(encoded, logits)
                    if actor == self.focal and not self.intervention_checked and self.intervention in ('recruit', 'power', 'finish'):
                        self.intervention_checked = True
                        choice = stats[self.intervention]['best_legal']
                        if choice is not None:
                            selected = choice
                            self.fired = True
                    if actor == self.focal and self.intervention in ('repair_power', 'save_for_power'):
                        current = ledger.player.observe()['facts']['econ']['preview']['energy']
                        pending_power = sum(planner.catalog[b['kind']]['energy_output'] for b in planner.buildings())
                        pending_demand = sum(planner.catalog[b['kind']]['energy_use'] for b in planner.buildings())
                        if current['shortfall'] > 0 or pending_demand > pending_power:
                            if self.intervention == 'save_for_power':
                                # Once pending plants cover nominal demand, wait
                                # for settlement rather than ordering more plants
                                # for a shortfall that only completed plants see.
                                selected = (stats['power']['best_legal']
                                    if pending_demand > pending_power and stats['power']['best_legal'] is not None
                                    else 0)
                            elif stats['power']['best_legal'] is not None:
                                selected = stats['power']['best_legal']
                    action, args = encoded['actions'][selected]
                    row = dict(actor=actor, period=period, attempt=attempt, natural=natural,
                               selected=selected, action=action, args=args, groups=stats,
                               natural_action=encoded['actions'][natural], value=float(value[0]),
                               intervened=selected != natural)
                    if attempt == 0:
                        row['energy'] = ledger.player.observe()['facts']['econ']['preview']['energy']
                    if actor == self.focal and (attempt == 0 or period % 10 == 0):
                        self.row_sink.append(dict(**row, row=sample(encoded, selected), actions=encoded['actions']))
                    if action == 'finish':
                        row['executed'] = None
                        self.sink.write(json.dumps(row, ensure_ascii=False) + '\n')
                        break
                    before = len(ledger.acts)
                    success = planner.execute(encoded, selected)
                    row['executed'] = bool(success)
                    self.sink.write(json.dumps(row, ensure_ascii=False) + '\n')
                    if success:
                        self.economic_actions[action] += 1
                    else:
                        self.project_failures += 1
                        if self.mode == 'greedy' and len(ledger.acts) == before:
                            self.project_failures += self.max_economy_actions - attempt - 1
                            self.truncated += 1
                            break
                else:
                    self.truncated += 1
        finally:
            planner.finish()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--seed', type=int, default=8102)
    p.add_argument('--size', type=int, default=24)
    p.add_argument('--swap', action='store_true')
    p.add_argument('--turns', type=int, default=150)
    p.add_argument('--mode', choices=('greedy', 'sample'), default='greedy')
    p.add_argument('--rng', type=int, default=0)
    p.add_argument('--device', default='cpu')
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--prior', type=float)
    p.add_argument('--load', type=Path)
    p.add_argument('--save-at', type=int, nargs='*', default=[])
    p.add_argument('--save-every', type=int, default=0)
    p.add_argument('--focal', default='scenario:n0')
    p.add_argument('--intervention', choices=('recruit','power','finish','teacher','bc','repair_power','save_for_power'))
    p.add_argument('--alternative', type=Path)
    a = p.parse_args()
    probe_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    boundary_sha256 = hashlib.sha256(a.load.read_bytes()).hexdigest() if a.load else None
    if a.save_every < 0:
        p.error('save-every must be nonnegative')
    if a.intervention == 'bc' and a.alternative is None:
        p.error('bc continuation requires --alternative')
    if a.prior is not None and (not math.isfinite(a.prior) or a.prior < 0):
        p.error('prior must be finite and nonnegative')
    if (a.out/'result.json').exists():
        p.error('completed output already exists; use a new experiment directory')
    a.out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(a.threads)
    torch.set_num_interop_threads(1)
    random.seed(a.rng); np.random.seed(a.rng); torch.manual_seed(a.rng)
    model, metadata = load_policy(a.checkpoint)
    require_compatible(metadata)
    if a.prior is not None:
        model.config['exec_prior_weight'] = a.prior
    model.to(a.device).eval()
    alternative = load_policy(a.alternative)[0].to(a.device).eval() if a.alternative else None
    cfg = config(a.seed, size=a.size, swap=a.swap)
    if a.load:
        host = GameHost.load(a.load, cfg)
    else:
        host = GameHost.new(cfg)
    start_period = host.player(host.actors()[0]).observe()['period']
    initial = {actor: consumption_total(host, actor) for actor in host.actors()}
    start = time.monotonic(); rows = []; history = []; focal = []
    with (a.out/'decisions.jsonl').open('w', buffering=1) as sink, (a.out/'history.jsonl').open('w', buffering=1) as history_sink:
        for actor, client in list(host.clients.items()):
            if client.version == 'v13plus':
                focal.append(actor)
                host.clients[actor] = ProbeClient(state=client.state, model=model, sink=sink, row_sink=rows,
                    mode=a.mode, focal=a.focal, intervention=a.intervention, alternative=alternative)
        for turn in range(start_period, a.turns):
            if turn in a.save_at or (a.save_every and turn % a.save_every == 0):
                write(a.out/f'boundary-{turn:03}.json', host.to_save())
            host.cycle()
            countries = {}
            for actor in focal:
                facts = host.player(actor).observe()['facts']
                sites = facts['building_sites']; energy = facts['econ']['preview']['energy']
                countries[actor] = dict(S=consumption_total(host, actor),
                    land=len(sites['sites']), buildings=len(sites['instances']),
                    army=sum(u['owner'] == actor for u in facts['army']),
                    energy=energy, actions=dict(host.clients[actor].economic_actions),
                    failures=host.clients[actor].project_failures,
                    kinds=dict(Counter(b['kind'] for b in sites['instances'])))
            history.append(dict(period=turn+1, countries=countries))
            history_sink.write(json.dumps(history[-1], ensure_ascii=False)+'\n')
            write(a.out/'progress.json', dict(period=turn+1, seconds=time.monotonic()-start, countries=countries))
            if (turn+1) % 10 == 0:
                print(json.dumps(dict(period=turn+1, seconds=round(time.monotonic()-start,1),
                    S={actor:r['S'] for actor,r in countries.items()})), flush=True)
        if a.turns in a.save_at or (a.save_every and a.turns % a.save_every == 0):
            write(a.out/f'boundary-{a.turns:03}.json', host.to_save())
    with gzip.open(a.out/'rows.pkl.gz', 'wb') as output:
        pickle.dump(rows, output, protocol=5)
    result = dict(arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},
        probe_sha256=probe_sha256, boundary_sha256=boundary_sha256,
        contracts=contracts(), checkpoint_sha256=hashlib.sha256(a.checkpoint.read_bytes()).hexdigest(),
        policy_sha256=model_digest(model), model_config=model.config, torch=str(torch.__version__),
        start_period=start_period, initial_S=initial, seconds=time.monotonic()-start,
        history=history, final=history[-1]['countries'],
        intervention_fired={actor:host.clients[actor].fired for actor in focal})
    write(a.out/'result.json', result)


if __name__ == '__main__':
    main()
