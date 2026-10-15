"""Offline accounting probes and v11plus trajectories for the consumption paper.

All worlds are synthetic. No credentials, external model calls or existing saves.
Run from repository root: .venv/bin/python experiments/consumption_metric/probe.py
"""
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine.runtime import Runtime
from host import GameHost

EXPENSES = {'construction', 'recruit', 'military', 'administration'}


def make_world(*, fixed=False, bank=False, wood=100):
    runtime = Runtime.load(ROOT / ('mods/economic_system' if bank else 'mods/economy_acceptance'))
    if fixed:
        units = [u for u in runtime.units.values() if u.name != 'native:market-pricing']
        units += list(Runtime.load(ROOT / 'mods/examples/fixed_market_pricing').units.values())
        runtime = Runtime(units, runtime.distribution)
    world = runtime.world_class()()
    world.resources.open('Qin', {'native:gold':10000, 'native:wood':wood,
                                'native:food':100, 'native:ore':100, 'native:oil':100})
    world.resources.open('Chu', {'native:gold':1000, 'native:wood':100-wood})
    world.buildings.register_site('example:qin', 'Qin', (0,0), resources={
        'native:gold':20, 'native:farmland':20, 'native:wood':20, 'native:ore':20})
    return world


def latest(w, owner='Qin'):
    return w.economy.turns(owner)[-1]


def audit(w):
    facts = w.economy.snapshot().events
    sums = defaultdict(float)
    quantities = defaultdict(lambda: defaultdict(float))
    for row in facts:
        if row.fact.category in EXPENSES:
            if not row.inputs.complete:
                raise AssertionError('Unpriced final use')
            sums[row.fact.owner] += row.inputs.total
            for resource, amount in row.fact.inputs:
                quantities[row.fact.owner][resource] += amount
    residuals = {}
    for owner in w.resources.owners():
        reports = w.economy.turns(owner)
        if reports:
            residuals[owner] = reports[-1].cumulative_spending.total-sums[owner]
    if any(abs(value)>1e-7 for value in residuals.values()):
        raise AssertionError(residuals)
    return {'residuals':residuals, 'final_use_quantities':dict(quantities),
            'categories':dict(Counter(row.fact.category for row in facts)),
            'facts':len(facts)}


def accounting_probes():
    results = {}
    w=make_world()
    before=w.resources.balance('Qin','native:wood')+w.market.inventory['native:wood']
    for _ in range(100):
        w.market.buy('Qin','native:wood',1)
        w.market.sell('Qin','native:wood',1)
    w.resolve_turn()
    after=w.resources.balance('Qin','native:wood')+w.market.inventory['native:wood']
    results['circulation']={'trades':200,'wood_before':before,'wood_after':after,
        'cash_change':w.resources.balance('Qin','native:gold')-10000,
        'spending':latest(w).cumulative_spending.total,'audit':audit(w)}
    assert before==after and latest(w).cumulative_spending.total==0

    sources=[]
    for source in ('initial','market','transfer'):
        w=make_world(fixed=True,wood=100 if source=='initial' else 0)
        if source=='market':w.market.buy('Qin','native:wood',30)
        if source=='transfer':
            w.resources.exchange('Chu',{'native:wood':30},{})
            w.resources.exchange('Qin',{}, {'native:wood':30})
        receipt=w.buildings.build('Qin','example:qin','native:watchtower')
        w.resolve_turn()
        sources.append({'source':source,'construction':asdict(receipt),
                        'spending':latest(w).cumulative_spending.total,'audit':audit(w)})
    assert len({r['spending'] for r in sources})==1
    results['source_invariance']=sources

    w=make_world()
    for kind in ('native:farm','native:mine','native:wood-power-plant','native:supply-factory'):
        w.buildings.seed('example:qin',kind)
    w.resolve_turn()
    report=latest(w)
    results['intermediate']={'production':asdict(report.production),'inputs':asdict(report.inputs),
        'gdp':asdict(report.gdp),'spending':report.cumulative_spending.total,
        'supply_stock':w.resources.balance('Qin','native:supply'),'audit':audit(w)}
    assert report.inputs.total>0 and report.cumulative_spending.total==0

    bank=[]
    for borrow in (False,True):
        w=make_world(bank=True)
        w.buildings.seed('example:qin','native:gold-mine')
        w.resolve_turn()
        start=w.resources.balance('Qin','native:gold')
        loan=w.bank.borrow('Qin') if borrow else None
        path=[]
        for _ in range(5):
            w.resolve_turn()
            path.append(asdict(w.bank.position('Qin')))
        bank.append({'borrow':borrow,'loan':asdict(loan) if loan else None,
            'initial_cash':start,'final_cash':w.resources.balance('Qin','native:gold'),
            'spending':latest(w).cumulative_spending.total,
            'positions':path,'finance':asdict(w.bank.snapshot()),'audit':audit(w)})
    assert bank[0]['spending']==bank[1]['spending']==0
    assert bank[1]['final_cash']<bank[0]['final_cash']
    results['bank_cycle']=bank
    return results


def trajectories(seeds=range(8), cycles=40):
    records=[]
    for seed in seeds:
        cfg={'mods':'mods.toml','map_size':12,'seed':seed,'nations':[
            {'id':'scenario:qin','name':'秦','client':'rule','rule_ai':'v11plus'},
            {'id':'scenario:chu','name':'楚','client':'rule','rule_ai':'v11plus'}]}
        host=GameHost.new(cfg)
        path=[]
        start=time.perf_counter()
        replay_equal=None
        with TemporaryDirectory(prefix='consumption-probe-') as folder:
            restored=None
            for cycle in range(cycles):
                host.cycle()
                if restored is not None:
                    restored.cycle()
                    if host.to_save()!=restored.to_save():
                        raise AssertionError(f'Resume divergence: seed={seed},cycle={cycle}')
                    replay_equal=True
                if cycle==cycles//2-1 and seed in (0,1):
                    save=Path(folder)/'synthetic.json'
                    host.save(save)
                    restored=GameHost.load(save,cfg)
                    assert host.to_save()==restored.to_save()
                path.append({'cycle':host.clock(),'spending':{
                    owner:host.world.economy.turns(owner)[-1].cumulative_spending.total
                    for owner in host.world.resources.owners() if host.world.economy.turns(owner)}})
        a=audit(host.world)
        records.append({'config':cfg,'cycles':cycles,'trajectory':path,'audit':a,
            'resume_equal':replay_equal,'elapsed_seconds':time.perf_counter()-start,
            'final_state_sha256':sha256(json.dumps(host.to_save(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()})
        print(json.dumps({'seed':seed,'final':path[-1],'facts':a['facts']},ensure_ascii=False),flush=True)
    return records


def manifest():
    hashes={}
    for folder in ('engine','mods','ruleai','clients','ui'):
        for path in sorted((ROOT/folder).rglob('*')):
            if path.is_file() and path.suffix in ('.py','.toml','.json') and '__pycache__' not in path.parts:
                hashes[str(path.relative_to(ROOT))]=sha256(path.read_bytes()).hexdigest()
    for path in (ROOT/'host.py',ROOT/'mods.toml'):
        hashes[str(path.relative_to(ROOT))]=sha256(path.read_bytes()).hexdigest()
    return {'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'working_tree':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
            'python':platform.python_version(),'files':hashes}


if __name__=='__main__':
    output=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name('results.json')
    before=manifest()
    probes=accounting_probes()
    runs=trajectories()
    after=manifest()
    if before['files']!=after['files']:raise AssertionError('Source changed during experiment')
    result={'manifest':before,'source_unchanged':True,'probes':probes,'trajectories':runs}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(f'Wrote {output}',flush=True)
