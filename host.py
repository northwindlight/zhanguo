"""Game host: scenario assembly, actor scheduling, clients and boundary saves."""
from copy import deepcopy
from importlib import import_module
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile
import threading

from engine.runtime import Runtime
from engine.player import wire
from engine.identifiers import content_id
from clients.llm import LLMClient,LLMState
import rule_ai

ROOT=Path(__file__).resolve().parent
SAVE_FORMAT='zhanguo.session/1'
START_RES={'native:gold':1500,'native:food':20,'native:wood':60,'native:ore':10,
           'native:oil':0,'native:equipment':5,'native:supply':10}


class SaveFormatError(ValueError):pass


def nation_id(item):
    ident=item.get('id')
    if ident is None:ident='scenario:nation-'+hashlib.sha256(item['name'].encode()).hexdigest()[:12]
    return content_id(ident)


def normalize_config(cfg):
    cfg=deepcopy(cfg)
    if not isinstance(cfg.get('nations'),list) or not cfg['nations']:raise ValueError('nations must be a nonempty list')
    ids=set();names=set()
    rule_ai.resolve(cfg.get('rule_ai'))
    for item in cfg['nations']:
        if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'].strip():raise ValueError('Nation requires a display name')
        item['id']=nation_id(item)
        if item['id'] in ids or item['name'] in names:raise ValueError('Duplicate configured nation')
        ids.add(item['id']);names.add(item['name'])
        kind=item.get('client') or ('llm' if item.get('api_key') else 'rule')
        if kind not in ('llm','rule'):raise ValueError('Unknown client kind')
        item['client']=kind
        rule_ai.resolve(item.get('rule_ai',cfg.get('rule_ai')))
        # Context settings and transport defaults are inherited without storing
        # credentials in the session save.
        for key,value in cfg.items():
            if key.startswith('ctx_') or key in ('rule_ai','provider','max_steps','parliament','language','ui_catalog','vision','map_max_px','map_estimated_tokens','ui_mods'):
                item.setdefault(key,value)
    return cfg


def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    fd,temporary=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


class GameHost:
    def __init__(self,runtime,world,cfg):
        self.runtime,self.world,self.cfg=runtime,world,normalize_config(cfg)
        for item in self.cfg['nations']:
            policy=runtime.find_policy(item.get('polity')) if item.get('polity') else None
            if item.get('polity') and policy is None:raise ValueError('Configured polity module is not loaded')
            item.setdefault('appearance',policy.declaration.get('Policy',{}).get('Appearance','scheduled') if policy else 'initial')
        from ui import load_ui
        self.ui=load_ui(self.cfg,runtime=runtime)
        self.clients={};self.roster={};self.lock=threading.RLock();self.last_results=[]
        self.scenario_text='';self._boundary=True

    @classmethod
    def new(cls,cfg,*,runtime=None):
        runtime=runtime or Runtime.load(ROOT/cfg.get('mods','mods.toml'))
        host=cls(runtime,runtime.world_class()(),cfg)
        seed=cfg.get('seed')
        if seed is None:seed=random.SystemRandom().randrange(2**31)
        size=cfg.get('map_size',60);host.world.territory.configure(size,seed)
        initial=[]
        for item in host.cfg['nations']:
            policy=host.runtime.find_policy(item.get('polity')) if item.get('polity') else None
            if item.get('polity') and policy is None:raise ValueError('Configured polity module is not loaded')
            appearance=item.get('appearance') or ('scheduled' if policy else 'initial')
            if appearance not in ('initial','scheduled','manual'):raise ValueError('Unknown appearance mode')
            item['appearance']=appearance
            if appearance=='initial' and item.get('enabled',True):initial.append(item)
        if not initial:raise ValueError('Scenario requires an initial nation')
        positions=host._positions(len(initial))
        for item,position in zip(initial,positions):host._add(item,item.get('position',position))
        host.world.market.set_participants(len(host.world.nation_actors))
        return host

    def _positions(self,count):
        size,_=self.world.territory.dimensions()
        if count==1:return [(size//2,size//2)]
        points=[]
        radius=size*.31
        for i in range(count):
            angle=2*math.pi*i/count
            points.append((min(size-2,max(1,round(size/2+radius*math.cos(angle)))),
                           min(size-2,max(1,round(size/2+radius*math.sin(angle))))))
        return points

    def _client_cfg(self,item,joined_at=0):
        cfg=deepcopy(item);cfg['joined_at']=joined_at
        for key in ('extra_prompt','extra_summary'):
            if not cfg.get(key) and cfg.get(key+'_file'):
                cfg[key]=Path(cfg[key+'_file']).read_text(encoding='utf-8')
        if self.scenario_text:cfg['extra_summary']=self.scenario_text+'\n'+str(cfg.get('extra_summary',''))
        return cfg

    def _client(self,item,record=None,joined_at=0):
        kind=item['client']
        if record is not None and (not isinstance(record,dict) or set(record)!={'kind','version','state'} or record['kind']!=kind):raise SaveFormatError('客户端类型改变，须显式迁移客户端状态')
        if kind=='rule':
            if record and record['version']!='v11plus':raise SaveFormatError('Retired rule client state')
            return rule_ai.create(item.get('rule_ai'),record['state'] if record else None)
        extension=self.runtime.distribution.get('ClientFactories',{}).get(kind)
        if extension:
            module,name=extension['Entry'].split(':')
            services={'ui':self.ui}
            for alias,spec in extension.get('Services',{}).items():
                def invoke(method,*args,_spec=spec,**kwargs):
                    if method not in _spec['Methods']:raise ValueError('Unavailable client service')
                    with self.lock:
                        self._dirty()
                        return wire(self.runtime.object_call(self.world,_spec['Capability'],method,item['id'],*args,**kwargs))
                services[alias]=invoke
            return getattr(import_module(module),name)(self._client_cfg(item,joined_at),record=record,services=services)
        if record and record['version']!='1':raise SaveFormatError('Unsupported LLM client state')
        return LLMClient(self._client_cfg(item,joined_at),LLMState.from_save(record['state']) if record else None,ui=self.ui)

    def _add(self,item,position):
        actor=item['id'];size,_=self.world.territory.dimensions()
        if not isinstance(position,(tuple,list)) or len(position)!=2 or any(type(v) is not int for v in position):raise ValueError('Invalid start position')
        x,y=position;self.world.territory.check(x,y)
        points=[(x+dx,y+dy) for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1)) if 0<=x+dx<size and 0<=y+dy<size]
        if any(self.world.territory.cell(*point).tile.id for point in points):raise ValueError('Start overlaps existing territory')
        polity=item.get('polity')
        if polity and self.runtime.find_policy(polity) is None:raise ValueError('Polity module missing')
        period=self.world.government.period();client=self._client(item,joined_at=period)
        self.world.nations.create(actor,item['name'],{**START_RES,**item.get('resources',{})})
        for tx,ty in points:
            site=f'{actor}/land-{tx}-{ty}'
            self.world.territory.create(site,actor,tx,ty,terrain='native:plain' if (tx,ty)==(x,y) else None,core=actor)
            if (tx,ty)==(x,y):
                self.world.buildings.seed(site,'native:town-hall')
                capital=site
        if polity:
            start={key:item[field] for key,field in (('Gold','start_gold'),('Supply','start_supply'),('Cavalry','start_cavalry')) if field in item}
            self.runtime.activate_actor(self.world,actor,polity,site=capital,start=start)
        self.world.succession.enroll(actor)
        self.roster[actor]={'name':item['name'],'joined_at':period}
        self.clients[actor]=client
        self.world.market.set_participants(len(self.world.nation_actors))
        self.world.intelligence.emit('appearance',item['name']+' 登场',public=True)
        return actor

    def add(self,name):
        item=next((n for n in self.cfg['nations'] if name in (n['id'],n['name'])),None)
        if item is None:raise ValueError('国家未在配置中声明')
        if item['id'] in self.roster:raise ValueError('国家已登场，历史身份不能复用')
        size,seed=self.world.territory.dimensions();rng=random.Random(f'{seed}:{item["id"]}')
        positions=[(x,y) for x in range(1,size-1) for y in range(1,size-1)]
        rng.shuffle(positions)
        occupied=[t.tile.position for t in self.world.territory.snapshot()]
        position=item.get('position') or next((p for p in positions if all(max(abs(p[0]-x),abs(p[1]-y))>=3 for x,y in occupied)),None)
        if position is None:raise ValueError('没有足够空间安置新国家')
        # Build the administrative change on a disposable world; malformed
        # configuration cannot leave half a newly created country behind.
        previous=self.world;candidate=self.runtime.world_class()()
        self.runtime.load_mod_state(candidate,self.runtime.save_mod_state(previous))
        self.world=candidate
        try:return self._add(item,position)
        except Exception:
            self.world=previous
            raise

    def scheduled(self):
        size,seed=self.world.territory.dimensions();period=self.world.government.period()
        for item in self.cfg['nations']:
            if item['id'] in self.roster or not item.get('enabled',True) or item.get('appearance')!='scheduled':continue
            lo=item.get('enable_turn')
            if lo is None:continue
            hi=item.get('enable_turn_max',lo)
            if type(lo) is not int or type(hi) is not int or lo<0 or hi<lo:raise ValueError('Invalid arrival period')
            due=random.Random(f'{seed}:{item["id"]}').randint(lo,hi)
            if period>=due:self.add(item['id'])

    def _dirty(self):self._boundary=False

    def player(self,actor):return self.runtime.player(self.world,actor,lock=self.lock,on_write=self._dirty)

    def cycle(self,*,on_result=None,emit=None,head=None,on_call=None):
        self.scheduled();receipts=[];self._boundary=False
        for actor in tuple(self.world.nation_actors):
            client=self.clients[actor];player=self.player(actor)
            callback=(lambda action,args,result,a=actor:on_result(a,action,args,result)) if on_result else None
            if client.kind=='rule':
                cfg=next(n for n in self.cfg['nations'] if n['id']==actor)
                receipts.extend(client.play(player,max_actions=cfg.get('max_actions'),on_result=callback))
            else:
                callback_call=(lambda step,result,a=actor:on_call(a,step,result)) if on_call else None
                receipts.extend(client.play(player,emit=emit,head=head,on_call=callback_call,on_result=callback))
        self.world.resolve_turn();self.world.phase='行动'
        for actor in tuple(self.world.nation_actors):
            settled=getattr(self.clients[actor],'on_settlement',None)
            if settled:settled(self.player(actor))
        self.last_results=receipts;self._boundary=True
        return receipts

    def to_save(self):
        # Saves are valid only at whole-cycle boundaries: before anyone acted.
        if not self._boundary or self.world.phase!='行动':raise SaveFormatError('Save only at an action boundary')
        period=self.world.government.period()
        if any(wire(self.world.government.snapshot(a))['ended'] for a in self.world.nation_actors):raise SaveFormatError('Cannot save a partially played cycle')
        return {'format':SAVE_FORMAT,'dependencies':self.runtime.dependency_manifest(),
                'modules':self.runtime.save_mod_state(self.world),
                'host':{'roster':deepcopy(self.roster),'scenario_text':self.scenario_text},
                'clients':{actor:client.to_save() for actor,client in self.clients.items()}}

    def save(self,path):write_json(path,self.to_save())

    @classmethod
    def load(cls,path,cfg,*,runtime=None):
        runtime=runtime or Runtime.load(ROOT/cfg.get('mods','mods.toml'))
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data,dict) or set(data)!={'format','dependencies','modules','host','clients'} or data['format']!=SAVE_FORMAT:raise SaveFormatError('旧整局存档格式不兼容；原档保持原样，使用新路径开局')
        try:runtime.check_save_dependencies(data['dependencies'])
        except ValueError as exc:raise SaveFormatError(str(exc)) from exc
        world=runtime.world_class()();runtime.load_mod_state(world,data['modules']);host=cls(runtime,world,cfg)
        if not isinstance(data['host'],dict) or set(data['host'])!={'roster','scenario_text'} or not isinstance(data['host']['roster'],dict) or not isinstance(data['host']['scenario_text'],str):raise SaveFormatError('Invalid host state')
        host.scenario_text=data['host']['scenario_text']
        roster=data['host']['roster'];clients=data['clients']
        if not isinstance(clients,dict) or set(clients)!=set(roster) or set(roster)!=set(n.id for n in world.nations.snapshot()):raise SaveFormatError('Client and nation identities disagree')
        config={n['id']:n for n in host.cfg['nations']}
        for actor,row in roster.items():
            if actor not in config or not isinstance(row,dict) or set(row)!={'name','joined_at'} or not isinstance(row['name'],str) or type(row['joined_at']) is not int or not 0<=row['joined_at']<=world.government.period():raise SaveFormatError('Invalid configured actor or arrival state')
            host.clients[actor]=host._client(config[actor],clients[actor],row['joined_at'])
        host.roster=deepcopy(roster)
        host.to_save()  # Also reject non-boundary saves before returning a host.
        return host

    def board_rows(self):
        rows=[]
        for actor in self.world.nation_actors:
            obs=self.player(actor).observe();stocks={s['id']:s['amount'] for s in obs['facts']['econ']['stocks']}
            rows.append({'name':self.roster[actor]['name'],'land':len(obs['facts']['land']),
                         'army':sum(u['owner']==actor for u in obs['facts']['army']),'gold':stocks.get('native:gold',0)})
        return rows

    def board(self):
        return self.ui.console.board(self.board_rows())
