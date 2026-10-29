"""Trace real cash flows and unmodified v13 investment rejection branches offline."""
import argparse
import ast
from collections import Counter, defaultdict
from copy import deepcopy
import inspect
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from host import GameHost
from ruleai.v13 import economy
from ruleai.v13.economy import Planner, stocks

GOLD = 'native:gold'


def run(seed=73, turns=100):
    cfg = {'map_size': 24, 'seed': seed, 'nations': [
        {'id': f'scenario:n{i}', 'name': f'国{i}', 'client': 'rule',
         'rule_ai': 'v13', 'rule_seed': seed+i} for i in range(8)]}
    host = GameHost.new(cfg)
    actors = host.actors()
    cash = lambda actor: next(r['amount'] for r in host.player(actor).observe()['facts']['econ']['stocks'] if r['id'] == GOLD)
    initial = {a: cash(a) for a in actors}
    totals = {a: Counter() for a in actors}
    history, blackouts, choices, funding = [], [], [], []
    original_choose = Planner.choose
    original_fund = economy.fund
    source, first_line = inspect.getsourcelines(original_choose)
    tree = ast.parse(textwrap.dedent(''.join(source)))
    branches = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and len(node.body) == 1 and isinstance(node.body[0], ast.Continue):
            branches[first_line+node.body[0].lineno-1] = ast.unparse(node.test)
    fund_source, fund_first = inspect.getsourcelines(original_fund)
    fund_tree = ast.parse(textwrap.dedent(''.join(fund_source)))
    fund_branches = {}
    for node in ast.walk(fund_tree):
        if isinstance(node, ast.If) and len(node.body) == 1 and isinstance(node.body[0], ast.Return) and isinstance(node.body[0].value, ast.Constant) and node.body[0].value.value is False:
            fund_branches[fund_first+node.body[0].lineno-1] = ast.unparse(node.test)

    def traced_choose(planner):
        # Only two checkpoints use tracing; the exact original function runs.
        if planner.obs['period'] not in {turns//2-1, turns-1}:
            return original_choose(planner)
        rejected, examples = Counter(), defaultdict(list)
        summary = {}

        def local_trace(frame, event, arg):
            values = frame.f_locals
            if event == 'line' and frame.f_lineno in branches:
                reason = branches[frame.f_lineno]
                rejected[reason] += 1
                if len(examples[reason]) < 2:
                    keys = ['kind', 'costs']
                    if 'margin' in reason or 'cash_cost' in reason:
                        keys += ['margin', 'normal', 'crowded', 'capex']
                    if 'cash_cost' in reason:
                        keys += ['recipe']
                    elif 'capex' in reason:
                        keys += ['capex']
                    sample = {k: deepcopy(values[k]) for k in keys if k in values}
                    sample['site'] = values['site']['id']
                    examples[reason].append(sample)
            if event == 'return':
                summary.update(actor=planner.player.actor, period=planner.obs['period'],
                    profile=planner.state.profile, cash=stocks(planner.ledger)[GOLD],
                    reserve=values['reserve'], power=values['power'], demand=values['demand'],
                    candidates=len(values['candidate_rows']), ranked=len(values['ranked']),
                    chosen=None if arg is None else {'site': arg[2]['id'], 'kind': arg[3]},
                    rejection_counts=dict(rejected), examples=dict(examples))
            return local_trace

        def global_trace(frame, event, arg):
            return local_trace if frame.f_code is original_choose.__code__ else None

        previous = sys.gettrace()
        try:
            sys.settrace(global_trace)
            result = original_choose(planner)
        finally:
            sys.settrace(previous)
        choices.append(summary)
        return result

    def traced_fund(ledger, targets, reserve=0):
        period = ledger.player.observe()['period']
        if period != turns-1:
            return original_fund(ledger, targets, reserve)
        caller = sys._getframe(1).f_code.co_name
        summary = {'actor': ledger.player.actor, 'period': period, 'caller': caller,
                   'reserve': reserve, 'reason': ''}

        def local_trace(frame, event, arg):
            values = frame.f_locals
            if event == 'line' and frame.f_lineno in fund_branches:
                summary['reason'] = fund_branches[frame.f_lineno]
            if event == 'return':
                summary.update(result=arg, cash=values['inventory'][GOLD],
                               gaps=deepcopy(values['gaps']), sold=sorted(values['sold']))
                if 'cost' in values:
                    summary['package_budget'] = values['cost']
            return local_trace

        def global_trace(frame, event, arg):
            return local_trace if frame.f_code is original_fund.__code__ else None

        previous = sys.gettrace()
        try:
            sys.settrace(global_trace)
            result = original_fund(ledger, targets, reserve)
        finally:
            sys.settrace(previous)
        funding.append(summary)
        return result

    def receipt(actor, action, arguments, result):
        if not result['ok']:
            totals[actor]['failed_actions'] += 1
            return
        data = result['data']
        if action in ('buy', 'sell'):
            totals[actor]['purchases' if action == 'buy' else 'sales'] += data['gold']
        elif action == 'build':
            totals[actor]['construction_gold'] += dict(data['costs']).get(GOLD, 0)

    original_produce = host.world.buildings.produce

    def produce(*args, **kwargs):
        result = original_produce(*args, **kwargs)
        for record in result.records:
            if record.owner not in totals:
                continue
            totals[record.owner]['production_gold'] += dict(record.outputs).get(GOLD, 0)-dict(record.inputs).get(GOLD, 0)
            if record.status == 'energy-shortage':
                blackouts.append({'period': result.period, 'owner': record.owner,
                                  'kind': record.kind, 'site': record.site})
        return result

    with patch('ruleai.v11plus.entry.military.run'), patch.object(Planner, 'choose', traced_choose), patch.object(economy, 'fund', traced_fund), patch.object(host.world.buildings, 'produce', side_effect=produce):
        for turn in range(turns):
            host.cycle(on_result=receipt)
            if (turn+1) % 10 == 0:
                history.append({'turn': turn+1, 'cash': {a: cash(a) for a in actors},
                                'flows': {a: dict(totals[a]) for a in actors}})
                print(f'seed={seed} turn={turn+1} cash={sum(cash(a) for a in actors)}', flush=True, file=sys.stderr)
    accounts = []
    for actor in actors:
        row = {'actor': actor, 'profile': host.clients[actor].state.profile,
               'initial_cash': initial[actor], 'final_cash': cash(actor), **totals[actor]}
        predicted = initial[actor]+row.get('sales', 0)-row.get('purchases', 0)+row.get('production_gold', 0)-row.get('construction_gold', 0)
        row['unattributed_cash_delta'] = row['final_cash']-predicted
        assert abs(row['unattributed_cash_delta']) < 1e-8, row
        accounts.append(row)
    return {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'seed': seed, 'turns': turns, 'method': 'same v13 eight-nation fixed-land economy as run.py; no strategy changes',
        'accounts': accounts, 'blackouts': blackouts, 'choices': choices, 'funding': funding, 'history': history}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=73)
    parser.add_argument('--turns', type=int, default=100)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.seed, args.turns)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
