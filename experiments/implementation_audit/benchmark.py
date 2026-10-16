import sys,time,json,hashlib
from pathlib import Path
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[2];sys.path.insert(0,str(root))
from host import GameHost
points=[(4,4),(12,4),(20,4),(4,12),(20,12),(4,20),(12,20),(20,20)]
cfg={'map_size':24,'seed':73,'nations':[{'id':f'scenario:n{i}','name':f'国{i}','client':'rule','position':list(point)} for i,point in enumerate(points)]}
h=GameHost.new(cfg)
for actor in h.actors():
 for fact in h.world.territory.owned(actor):
  h.world.buildings.seed(fact.tile.id,'native:watchtower')
  h.world.buildings.seed(fact.tile.id,'native:wood-power-plant',3)
 position=next(item['position'] for item in cfg['nations'] if item['id']==actor)
 h.world.military.seed(actor,'native:infantry',*position,14)
 h.world.military.seed(actor,'native:cavalry',*position,14)
p=h.player('scenario:n0');p.observe()
counts={};original=h.runtime.object_call
def counted(world,cap,method,*args,**kwargs):
 key=cap+'.'+method;counts[key]=counts.get(key,0)+1
 return original(world,cap,method,*args,**kwargs)
h.runtime.object_call=counted
start=time.perf_counter()
for _ in range(3):obs=p.observe()
elapsed=time.perf_counter()-start
observation_counts=dict(counts)
view=obs['facts']['view']
for row in view:
 if row['owner'] is None:row['name']=''
canonical={'view':view,'army':obs['facts']['army'],'econ':obs['facts']['econ']}
from clients.context import est_tokens
ui=h.ui.llm(h.cfg);ui.rules(p.rules('all'));schemas=ui.actions(p.actions());tail,_=ui.state(obs,p.events()['items'])
actor=h.actors()[0];unit=h.world.military.snapshot(actor)[0]
p.submit('move',{'aid':unit.id,'x':4,'y':5})
delta=ui.refresh(p.observe()) or ''
print(json.dumps({'state_tokens':est_tokens(tail),'system_tokens':est_tokens(ui.system()),'tools_tokens':est_tokens(json.dumps(schemas,ensure_ascii=False)),'update_tokens':est_tokens(delta),'root':str(root),'observations':3,'elapsed_seconds':elapsed,'object_calls':sum(observation_counts.values()),'counts':dict(sorted(observation_counts.items())),'facts_sha256':hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()},ensure_ascii=False))
