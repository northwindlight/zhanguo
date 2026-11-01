"""Heterogeneous normal-resource markets, with actual v11plus expansion."""
from collections import Counter
import hashlib
from pathlib import Path
import random

from host import GameHost
from ruleai.v13.profiles import PROFILES


def source_hash():
    root=Path(__file__).resolve().parents[2]
    digest=hashlib.sha256()
    for folder in ('engine','mods/native','mods/economic_system','ruleai','rl/economy'):
        for path in sorted((root/folder).rglob('*')):
            if path.suffix not in ('.py','.toml'):continue
            digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()


def config(seed, *, nations=8, size=24, candidate='v13plus', swap=False):
    if nations<4:raise ValueError('Economic training requires at least four countries')
    rng=random.Random(seed)
    profiles=list(PROFILES);rng.shuffle(profiles)
    # Half the seats compare v13/teacher; remaining seats mix v11plus and v13.
    versions=[candidate if i%2 else 'v13' for i in range(nations)]
    versions[0]='v11plus'
    if swap:versions=[candidate if v=='v13' else 'v13' if v==candidate else v for v in versions]
    return dict(map_size=size,seed=seed,nations=[dict(id=f'scenario:n{i}',name=f'国{i}',
        client='rule',rule_ai=v,rule_strategy=profiles[i%len(profiles)],rule_seed=seed+i)
        for i,v in enumerate(versions)])


def snapshot(host,failures):
    result={}
    for actor,client in host.clients.items():
        facts=host.player(actor).observe()['facts'];econ=facts['econ']
        report=econ.get('latest_report') or {}
        # Evaluator only; never enters the actor's model inputs. Use the formal
        # cumulative ledger, which includes checkpoints after event compaction.
        spending=host.world.economy.spending(actor)
        if any(not value.complete for value in spending.values()):
            raise ValueError('Unpriced consumption cannot be compared')
        consumption=sum(spending[k].known for k in ('recruit','military','administration') if k in spending)
        result[actor]=dict(version=client.version,profile=getattr(client.state,'profile',None),
            gold=next(s['amount'] for s in econ['stocks'] if s['id']=='native:gold'),
            land=len(facts['building_sites']['sites']),buildings=len(facts['building_sites']['instances']),
            army=sum(u['owner']==actor for u in facts['army']),
            report=report,consumption=consumption,investment=spending['construction'].known if 'construction' in spending else 0,
            failures=dict(failures[actor]),
            truncated=getattr(client,'truncated',0),
            economic_actions=dict(getattr(client,'economic_actions',{})))
    return result
