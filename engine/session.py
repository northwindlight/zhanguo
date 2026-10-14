"""Generic distribution session lifecycle and actor-bound API exposure.

Distribution implementations supply scenario and administrative behavior. This
module has no map geometry or game-domain policy. Distributions supply their
scenario hooks; grid-based games can reuse engine.scenario.GridSession.
"""
from copy import deepcopy
from importlib import import_module
import inspect
import json
import os
from pathlib import Path
import tempfile
import threading
from .player import wire
from .api import BoundPlayer, api_entry
from .runtime import ModError

SAVE_FORMAT='zhanguo.session/2'
class SaveFormatError(ValueError):pass

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


class Session:
    def __init__(self,runtime,cfg,client_factory):
        self.runtime=runtime;self.cfg=deepcopy(cfg);self.client_factory=client_factory
        self.world=runtime.world_class()()
        from ui import load_ui
        self.ui=load_ui(self.cfg,runtime=runtime)
        self.clients={};self.roster={};self.lock=threading.RLock();self.last_results=[]
        self.scenario_text='';self._boundary=True
        self.audit=[];self.generations={};self.contexts={};self.services={}
        self._callbacks=None
        for item in self.entries():
            policy=runtime.find_policy(item.get('polity')) if item.get('polity') else None
            if item.get('polity') and policy is None:raise ValueError('Configured scope module is not loaded')
            item.setdefault('appearance',policy.declaration.get('Policy',{}).get('Appearance','scheduled') if policy else 'initial')
        self._check_observer()

    def _check_observer(self):
        spec=self.runtime.distribution.get('Session',{}).get('Observer')
        if spec is None:return
        if not isinstance(spec,dict) or not isinstance(spec.get('Capability'),str) or spec['Capability'] not in self.runtime.capabilities:
            raise ModError('Session.Observer 必须引用公共能力')
        for name,arity in (('Records',0),('Announce',1),('Send',2)):
            method=spec.get(name)
            if method is not None and (not isinstance(method,str) or not method or method.startswith('_')):
                raise ModError(f'Session.Observer.{name} 必须是公开方法名')
            if method is not None:
                owner=self.runtime.capabilities[spec['Capability']]
                target=self.runtime.units[owner].mod.objects(self.world).get(spec['Capability'])
                if target is None or not callable(getattr(target,method,None)):
                    raise ModError(f'Session.Observer.{name} 引用不可调用的方法：{spec["Capability"]}.{method}')
                try:inspect.signature(getattr(target,method)).bind(*([None]*arity))
                except (TypeError,ValueError) as exc:
                    raise ModError(f'Session.Observer.{name} 参数契约不匹配：{spec["Capability"]}.{method} 须接受 {arity} 个位置参数') from exc

    # 场景策略属于发行版；无地图游戏不需要实现几何钩子。
    def entries(self):raise NotImplementedError
    def identities(self):raise NotImplementedError
    def configure(self):raise NotImplementedError
    def spawn(self,item,position):raise NotImplementedError
    def after_spawn(self):pass
    def validate_boundary(self):pass

    @api_entry
    def actors(self):
        collection=getattr(self.world,self.runtime.distribution.get('ActorCollection','actors'),())
        return tuple(collection)

    def turn_order(self):
        """本回合的行动顺序：名单不动，先手每回合轮换一格。

        固定名单顺序会让同一个国家每个回合都先动手（买货、抢地、拿无主遗址都占先），
        所以按回合数把行动顺序转一个身位；国家名单、席位与其它按名单的遍历不受影响。
        """
        order=list(self.actors())
        if not order:return ()
        shift=self.clock()%len(order)
        return tuple(order[shift:]+order[:shift])

    @api_entry
    def clock(self):
        spec=self.runtime.distribution.get('PlayerClock')
        if not spec:raise ModError('发行版须声明 PlayerClock 或实现 clock()')
        return self.runtime.object_call(self.world,spec['Capability'],spec['Method'])

    def _positions(self,count):
        raise ModError('发行版须实现 _positions() 或采用 GridSession')

    def add(self,name):
        raise ModError('当前发行版不支持手动登场')

    def scheduled(self):
        if any(item.get('enabled',True) and item.get('appearance')=='scheduled' and item['id'] not in self.roster for item in self.entries()):
            raise ModError('发行版须实现定时登场策略或采用 GridSession')

    def management(self):
        """Implemented optional administration APIs; inherited placeholders do not count."""
        observer=self.runtime.distribution.get('Session',{}).get('Observer') or {}
        result=[]
        for name,binding in (('add',None),('records','Records'),('announce','Announce'),('send','Send'),('board',None)):
            implementation=getattr(type(self),name,None)
            implemented=callable(getattr(self,name,None)) and implementation is not getattr(Session,name)
            declared=bool(binding and observer.get(binding))
            panel=name=='board' and callable(getattr(self,'board_rows',None))
            if callable(getattr(self,name,None)) and (implemented or declared or panel):
                result.append(name)
        return tuple(result)

    def _observer(self,name,caller):
        spec=self.runtime.distribution.get('Session',{}).get('Observer') or {}
        method=spec.get(name)
        if not method:raise ModError(f'发行版须声明 Session.Observer.{name} 或实现 {caller}()')
        def invoke(*args):
            with self.lock:
                result=self.runtime.object_call(self.world,spec['Capability'],method,*args)
                try:return wire(result)
                except (TypeError,ValueError) as exc:
                    if name!='Records':raise
                    raise ModError('Session.Observer.Records 返回值必须是有效 JSON 数据') from exc
        return invoke

    def records(self):
        records=self._observer('Records','records')()
        if not isinstance(records,dict):raise ModError('Session.Observer.Records 必须返回包含 events / messages 的对象')
        for collection,fields in (('events',{'id':int,'period':int,'kind':str,'text':str}),
                                  ('messages',{'id':int,'sampled':int,'source':str,'target':str,'payload':object})):
            rows=records.get(collection)
            if not isinstance(rows,list):raise ModError(f'Session.Observer.Records.{collection} 必须是列表')
            for index,row in enumerate(rows):
                location=f'Session.Observer.Records.{collection}[{index}]'
                if not isinstance(row,dict):raise ModError(f'{location} 必须是对象')
                for key,expected in fields.items():
                    if (key not in row or not isinstance(row[key],expected)
                        or expected is int and (type(row[key]) is not int or row[key]<(1 if key=='id' else 0))):
                        raise ModError(f'{location}.{key} 不符合观察者契约')
        return records
    def announce(self,text):self._observer('Announce','announce')(text)
    def send(self,actor,text):self._observer('Send','send')(actor,text)

    def start(self):
        self.configure();initial=[]
        for item in self.entries():
            appearance=item['appearance']
            if appearance not in ('initial','scheduled','manual'):raise ValueError('Unknown appearance mode')
            if appearance=='initial' and item.get('enabled',True):initial.append(item)
        if not initial:raise ValueError('Scenario requires an initial actor')
        for item,position in zip(initial,self._positions(len(initial))):
            self.register(item,item.get('position',position))
        self.after_spawn()
        self._boundary=True

    def register(self,item,position):
        self._dirty()
        actor=self.spawn(item,position);period=self.clock()
        self.roster[actor]={'name':item['name'],'joined_at':period}
        self.generations[actor]=0;self.contexts[actor]={}
        self.services[actor]=self._services(item)
        self.clients[actor]=self._client(item,joined_at=period)
        self.after_spawn()
        return actor

    def join(self,item,position):
        previous=self.world;candidate=self.runtime.world_class()()
        self.runtime.load_mod_state(candidate,self.runtime.save_mod_state(previous))
        state=(dict(self.roster),dict(self.clients),dict(self.services),
               deepcopy(self.generations),deepcopy(self.contexts),deepcopy(self.audit),self._boundary)
        self.world=candidate
        try:
            actor=self.register(item,position)
            self._boundary=state[-1]
            return actor
        except Exception:
            self.world=previous
            (self.roster,self.clients,self.services,self.generations,self.contexts,self.audit,self._boundary)=state
            raise

    def _grants(self,item,declarations):
        grants={'ui':self.ui}
        for alias,spec in declarations.items():
            capability=spec['Capability'];methods=tuple(spec['Methods'])
            owner=self.runtime.capabilities.get(capability)
            target=self.runtime.units[owner].mod.objects(self.world).get(capability) if owner else None
            if not methods or target is None or any(not isinstance(m,str) or m.startswith('_') or not callable(getattr(target,m,None)) for m in methods):
                raise ValueError('Invalid session service grant')
            def invoke(method,*args,_capability=capability,_methods=methods,**kwargs):
                if method not in _methods:raise ValueError('Unavailable session service')
                with self.lock:
                    self._dirty()
                    return wire(self.runtime.object_call(self.world,_capability,method,item['id'],*args,**kwargs))
            grants[alias]=invoke
        return grants

    def _services(self,item,records=None):
        specs=self.runtime.distribution.get('Session',{}).get('Services',{})
        restoring=records is not None
        records={} if records is None else records
        if restoring and (not isinstance(records,dict) or set(records)!=set(specs)):raise SaveFormatError('Session service identities disagree')
        result={}
        for name,spec in specs.items():
            module,cls=spec['Entry'].split(':')
            grants=self._grants(item,spec.get('Services',{}))
            grants['invalidate']=lambda context,a=item['id']:self.invalidate(a,context)
            result[name]=getattr(import_module(module),cls)(self._client_cfg(item,self.roster[item['id']]['joined_at']),
                record=records.get(name),services=grants)
        return result

    def _client(self,item,record=None,joined_at=0):
        kind=item['client']
        if record is not None and (not isinstance(record,dict) or set(record)!={'kind','version','state'} or record['kind']!=kind):
            raise SaveFormatError('Client state kind mismatch')
        spec=self.runtime.distribution.get('ClientFactories',{}).get(kind,{})
        grants=self._grants(item,spec.get('Services',{}))
        client=self.client_factory(self._client_cfg(item,joined_at),record,spec.get('Entry'),grants)
        if getattr(client,'kind',None)!=kind or not callable(getattr(client,'play',None)) or not callable(getattr(client,'to_save',None)):
            raise ValueError('Invalid declared client factory contract')
        return client

    def _client_cfg(self,item,joined_at=0):
        cfg=deepcopy(item);cfg['joined_at']=joined_at
        for key in ('extra_prompt','extra_summary'):
            if not cfg.get(key) and cfg.get(key+'_file'):
                cfg[key]=Path(cfg[key+'_file']).read_text(encoding='utf-8')
        if self.scenario_text:cfg['extra_summary']=self.scenario_text+'\n'+str(cfg.get('extra_summary',''))
        return cfg


    def _dirty(self):self._boundary=False
    def raw_player(self,actor):return self.runtime.player(self.world,actor,lock=self.lock,on_write=self._dirty)
    def player(self,actor):
        with self.lock:
            self.raw_player(actor)
            return BoundPlayer(self,actor,self.generations[actor])
    def invalidate(self,actor,context):
        self._dirty();self.generations[actor]+=1;self.contexts[actor]=wire(context)

    def submit(self,player,action,arguments,next_submit):
        # These are arbitrary distribution services; no review/action names here.
        chain=next_submit
        for service in reversed(tuple(self.services[player.actor].values())):
            handler=getattr(service,'submit',None)
            if handler:
                downstream=chain
                def wrap(h,n):
                    used=False
                    def once():
                        nonlocal used
                        if used:raise RuntimeError('A submission continuation can execute only once')
                        used=True
                        return n()
                    return lambda:h(player,action,deepcopy(arguments),once)
                chain=wrap(handler,downstream)
        return chain()

    @staticmethod
    def _audit_value(value):
        try:return wire(value)
        except (TypeError,ValueError):return {'invalid_type':type(value).__name__}

    def record(self,player,action,arguments,receipt,error=None):
        row={'id':len(self.audit)+1,'actor':player.actor,'generation':player.generation,
             'action':self._audit_value(action),'arguments':self._audit_value(arguments),'receipt':wire(receipt)}
        if error:row['error']={'type':type(error).__name__,'message':str(error)}
        self.audit.append(row)
        try:
            for service in self.services[player.actor].values():
                handler=getattr(service,'record',None)
                if handler:handler(player,deepcopy(row))
            if self._callbacks and receipt is not None:
                self._callbacks(player.actor,action,arguments,wire(receipt))
        except Exception as exc:
            self._dirty()
            row.setdefault('error',{'type':type(exc).__name__,'message':str(exc)})
            raise

    def record_dialogue(self,player,messages):
        for service in self.services[player.actor].values():
            handler=getattr(service,'record_dialogue',None)
            if handler:handler(player,wire(messages))

    def cycle(self,*,on_result=None,emit=None,head=None,on_call=None):
        self.scheduled();receipts=[];self._boundary=False;self._callbacks=on_result
        try:
            for actor in self.turn_order():
                client=self.clients[actor];transitions=0
                while True:
                    player=self.player(actor);generation=player.generation
                    if not player.observe()['can_act']:break
                    if client.kind=='rule':
                        cfg=next(n for n in self.entries() if n['id']==actor)
                        receipts.extend(client.play(player,max_actions=cfg.get('max_actions')))
                    else:
                        callback_call=(lambda step,result,a=actor:on_call(a,step,result)) if on_call else None
                        receipts.extend(client.play(player,emit=emit,head=head,on_call=callback_call))
                    if self.generations[actor]==generation:break
                    transitions+=1
                    if transitions>8:raise RuntimeError('Too many actor control transitions in one cycle')
            self.world.resolve_turn();self.world.phase='行动'
            for actor in self.actors():
                for service in self.services[actor].values():
                    settled=getattr(service,'on_settlement',None)
                    if settled:settled(self.player(actor))
                settled=getattr(self.clients[actor],'on_settlement',None)
                if settled:settled(self.player(actor))
            self.last_results=receipts;self._boundary=True
            return receipts
        finally:self._callbacks=None

    def to_save(self):
        if not self._boundary or self.world.phase!='行动':raise SaveFormatError('Save only at an action boundary')
        self.validate_boundary()
        return {'format':SAVE_FORMAT,'dependencies':self.runtime.dependency_manifest(),
                'modules':self.runtime.save_mod_state(self.world),
                'host':{'roster':deepcopy(self.roster),'scenario_text':self.scenario_text,
                        'generations':deepcopy(self.generations),'contexts':deepcopy(self.contexts),'audit':deepcopy(self.audit),
                        'services':{a:{n:s.to_save() for n,s in services.items()} for a,services in self.services.items()}},
                'clients':{actor:client.to_save() for actor,client in self.clients.items()}}

    def save(self,path):write_json(path,self.to_save())

    def restore(self,data):
        if not isinstance(data,dict) or set(data)!={'format','dependencies','modules','host','clients'} or data['format']!=SAVE_FORMAT:
            raise SaveFormatError('Unsupported session format')
        self.runtime.check_save_dependencies(data['dependencies'])
        self.runtime.load_mod_state(self.world,data['modules'])
        saved=data['host'];keys={'roster','scenario_text','generations','contexts','audit','services'}
        if not isinstance(saved,dict) or set(saved)!=keys or not isinstance(saved['scenario_text'],str):raise SaveFormatError('Invalid session state')
        roster=saved['roster'];clients=data['clients'];actors=set(self.identities())
        if not isinstance(roster,dict) or not isinstance(clients,dict) or set(roster)!=actors or set(clients)!=actors:
            raise SaveFormatError('Client and actor identities disagree')
        for key in ('generations','contexts','services'):
            if not isinstance(saved[key],dict) or set(saved[key])!=actors:raise SaveFormatError('Invalid actor service state')
        if any(type(g) is not int or g<0 for g in saved['generations'].values()):raise SaveFormatError('Invalid actor generation')
        if not isinstance(saved['audit'],list) or any(not isinstance(r,dict) or r.get('id')!=i+1 or r.get('actor') not in actors for i,r in enumerate(saved['audit'])):
            raise SaveFormatError('Invalid audit trail')
        self.scenario_text=saved['scenario_text'];self.roster=deepcopy(roster)
        if any(not isinstance(context,dict) for context in saved['contexts'].values()):raise SaveFormatError('Invalid actor context')
        self.generations=deepcopy(saved['generations']);self.contexts=wire(saved['contexts']);self.audit=wire(saved['audit'])
        config={n['id']:n for n in self.entries()}
        for actor,row in roster.items():
            if actor not in config or not isinstance(row,dict) or set(row)!={'name','joined_at'} or not isinstance(row['name'],str) or type(row['joined_at']) is not int or not 0<=row['joined_at']<=self.clock():
                raise SaveFormatError('Invalid configured actor or arrival state')
            self.services[actor]=self._services(config[actor],saved['services'][actor])
            self.clients[actor]=self._client(config[actor],clients[actor],row['joined_at'])
        self.to_save()

    def board(self):
        rows=getattr(self,'board_rows',None)
        if not callable(rows):raise ModError('当前发行版不支持终端面板')
        return self.ui.console.board(rows())
