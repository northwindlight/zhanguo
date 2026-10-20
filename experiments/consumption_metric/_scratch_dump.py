import json,sys
from pathlib import Path
ROOT=Path('/home/northwind/zhanguo')
sys.path.insert(0,str(ROOT))
from host import GameHost
from engine.runtime import Runtime

def attack_runtime():
    runtime=Runtime.load(ROOT/'mods.toml')
    extra={'attack':{'Entry':'experiments.consumption_metric.attack_clients:AttackClient',
                     'Configuration':'experiments.consumption_metric.attack_clients:normalize_config'}}
    factories=runtime.distribution.get('ClientFactories')
    try:
        factories.update(extra);return runtime
    except TypeError:
        merged={**runtime.distribution,'ClientFactories':{**dict(factories or {}),**extra}}
        return Runtime(list(runtime.units.values()),merged)

cfg={'mods':'mods.toml','map_size':16,'seed':0,'nations':[{'id':'scenario:qin','name':'秦','client':'attack','attack':'costly'}]}
host=GameHost.new(cfg,runtime=attack_runtime())
host.cycle()
p=host.session.player('scenario:qin')
obs=p.observe()
f=obs['facts']
print('facts keys',sorted(f))
out={}
for k in ('econ','market','building_sites','unit_catalog','nations','land','government','army','country'):
    if k in f: out[k]=f[k]
print(json.dumps(out,ensure_ascii=False,indent=1)[:16000])
