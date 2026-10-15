"""Audit the completed 30-cycle prefix of the eight-country synthetic run.

The long-run probe was deliberately stopped after preserving its cycle-30
checkpoint. This script audits that completed prefix and checks one resumed
cycle twice; it does not report the uncompleted 60-cycle/four-seed plan.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from probe import GameHost, accounting_probes, audit, manifest

countries=(('qin','秦'),('wei','魏'),('han','韩'),('zhao','赵'),
           ('yan','燕'),('qi','齐'),('chu','楚'),('zhou','周'))
cfg={'mods':'mods.toml','map_size':24,'seed':0,'nations':[
    {'id':'scenario:'+ident,'name':name,'client':'rule','rule_ai':'v11plus'}
    for ident,name in countries]}
checkpoint=HERE/'synthetic_eight_country_seed0_cycle30.json'
before=manifest()
old=json.loads((HERE/'results_two_country.json').read_text())
assert before['files']==old['manifest']['files'], 'Source differs from pre-eight-country baseline'
start=time.perf_counter()
host=GameHost.load(checkpoint,cfg)
assert host.clock()==30
reports={owner:[asdict(row) for row in host.world.economy.turns(owner)]
    for owner in host.world.resources.owners() if host.world.economy.turns(owner)}
market=host.world.market.snapshot()
flows=defaultdict(lambda:{'buy':0,'sell':0,'buyers':set(),'sellers':set()})
for trade in market.trades:
    row=flows[trade.resource];row[trade.side]+=trade.quantity
    row['buyers' if trade.side=='buy' else 'sellers'].add(trade.owner)
flow={resource:{key:sorted(value) if isinstance(value,set) else value
    for key,value in row.items()} for resource,row in flows.items()}
raw=json.loads(checkpoint.read_text())
result={'method':'Completed 30-cycle prefix of probe.py four-seed/60-cycle plan; remaining plan stopped.',
    'config':cfg,'cycles':host.clock(),'manifest':before,
    'source_matches_prior_completed_probe':True,
    'checkpoint':{'path':str(checkpoint.relative_to(HERE.parent.parent)),
                  'sha256':sha256(checkpoint.read_bytes()).hexdigest()},
    'audit':audit(host.world),'reports':reports,'market':asdict(market),'market_flow':flow,
    'host_action_audit':raw['host']['audit']}
restored=GameHost.load(checkpoint,cfg)
assert host.to_save()==restored.to_save()
print('Loaded cycle 30; accounting audit passed. Checking one resumed cycle.',flush=True)
host.cycle();restored.cycle()
assert host.to_save()==restored.to_save()
result['resume_check']={'from_cycle':30,'to_cycle':31,'equal':True,
    'audit':audit(host.world)}
result['probes']=accounting_probes()
after=manifest();assert before['files']==after['files']
result['source_unchanged_during_audit']=True
result['audit_script_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
result['elapsed_seconds']=time.perf_counter()-start
out=HERE/'results_eight_country.json'
out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(json.dumps({'final_consumption':{k:v[-1]['cumulative_spending']['known'] for k,v in reports.items()},
                  'audit':result['audit'],'market_flow':flow},ensure_ascii=False),flush=True)
print('Wrote '+str(out),flush=True)
