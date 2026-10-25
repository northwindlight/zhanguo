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
import uuid
import hashlib
from .player import wire
from .api import BoundPlayer, api_entry
from .runtime import ModError

SAVE_FORMAT='zhanguo.session/4'
AUDIT_LIMIT=2048
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
    def __init__(self,runtime,cfg,client_factory,presentation=None):
        self.runtime=runtime;self.cfg=deepcopy(cfg);self.client_factory=client_factory
        self.world=runtime.world_class()()
        runtime.check_interfaces(self.world)
        # The composition root injects presentation; the engine never imports a UI.
        self.presentation=presentation
        self.clients={};self.roster={};self.lock=threading.RLock();self.last_results=[]
        self.scenario_text='';self._boundary=True
        self.victory_event=None
        self.scenario_id=self.cfg.get('scenario')
        self._scenario=None
        if self.scenario_id is not None:
            from .config import entrypoint
            scenarios=runtime.distribution.get('Scenarios',{})
            if not isinstance(self.scenario_id,str) or not self.scenario_id or not isinstance(scenarios,dict):
                raise ModError('Invalid distribution scenario selection')
            spec=scenarios.get(self.scenario_id)
            if not isinstance(spec,dict):raise ModError('Scenario is not declared by this distribution')
            if 'Mod' in spec:
                from .scenario_mod import ScenarioMod
                if not isinstance(spec['Mod'],str) or 'Entry' in spec:raise ModError('Scenario must select one Mod or Entry')
                unit=runtime.units.get(spec['Mod'])
                if unit is None or not isinstance(unit.mod,ScenarioMod):raise ModError('Scenario Mod must be a loaded ScenarioMod')
                factory=unit.mod.bind
            else:factory=entrypoint(spec.get('Entry'))
            try:inspect.signature(factory).bind(self.world,self.trigger_victory)
            except (TypeError,ValueError) as exc:raise ModError('Scenario program must accept (world, trigger_victory)') from exc
            self._scenario=factory(self.world,self.trigger_victory)
            self._scenario_factory=factory
            self._scenario_world=self.world
            if not callable(self._scenario):raise ModError('Scenario program must be callable')
            self.scenario_text=getattr(self._scenario,'text','')
            if not isinstance(self.scenario_text,str):raise ModError('Scenario text must be a string')
        self.audit=[];self._audit_next=1;self._audit_stream=uuid.uuid4().hex;self._audit_archived={};self.generations={};self.contexts={};self.services={}
        # 模块内部异常（internal_fault）：本局运行期的故障日志，不进存档。
        self.faults=[]
        self._callbacks=None;self._emit=None
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

    def trigger_victory(self,event):
        """The sole victory hook. Scenario programs own all conditions and awards."""
        if not isinstance(event,dict) or not event:raise ValueError('Victory event must be a nonempty JSON object')
        # Copy through JSON so neither the program nor callers retain a mutable
        # reference, and reject NaN / arbitrary Python objects before latching.
        event=json.loads(json.dumps(event,ensure_ascii=False,allow_nan=False))
        with self.lock:
            if self.victory_event is None:self.victory_event=event
            return deepcopy(self.victory_event)

    def finished(self):return self.victory_event is not None

    @api_entry
    def actors(self):
        collection=getattr(self.world,self.runtime.distribution.get('ActorCollection','actors'),())
        return tuple(collection)

    def turn_order(self):
        """本回合的行动顺序：名单不动，先手每回合轮换一格。

        固定名单顺序会让同一个国家每个回合都先动手（买货、抢地、拿无主遗址都占先），
        所以按回合数把行动顺序转一个身位；国家名单、席位与其它按名单的遍历不受影响。
        发行版的回合号起点不同（native 从 0、stock 从 1），所以按 PlayerClock
        声明的 FirstPeriod（缺省 0）扣掉起点：第一回合不转，之后每回合转一格。
        """
        order=list(self.actors())
        if not order:return ()
        first=(self.runtime.distribution.get('PlayerClock') or {}).get('FirstPeriod',0)
        shift=(self.clock()-first)%len(order)
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
        initialize=getattr(self._scenario,'new_game',None)
        if initialize:initialize()
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
        if self.victory_event is not None:raise ValueError('Cannot join an ended game')
        previous=self.world;candidate=self.runtime.world_class()()
        self.runtime.load_mod_state(candidate,self.runtime.save_mod_state(previous))
        for unit in self.runtime.units.values():
            pending=getattr(unit.mod,'pending_archive',None);restore=getattr(unit.mod,'restore_archive',None)
            if callable(pending) and callable(restore):restore(candidate,wire(pending(previous)))
        state=(dict(self.roster),dict(self.clients),dict(self.services),
               deepcopy(self.generations),deepcopy(self.contexts),deepcopy(self.audit),self._audit_next,self._boundary)
        self.world=candidate
        try:
            actor=self.register(item,position)
            self._boundary=state[-1]
            return actor
        except Exception:
            self.world=previous
            (self.roster,self.clients,self.services,self.generations,self.contexts,self.audit,self._audit_next,self._boundary)=state
            raise

    def _grants(self,item,declarations):
        grants={'ui':self.presentation,'notify':lambda text:self._emit(text) if self._emit else None}
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
        # Scenario objectives stay in the player prompt for the whole game.
        if self.scenario_text:cfg['extra_summary']=self.scenario_text+'\n'+str(cfg.get('extra_summary',''))
        return cfg


    def _dirty(self):self._boundary=False
    def _fault(self,action,exc,detail):
        self.faults.append({'period':self.clock(),'action':action,'type':type(exc).__name__,
                            'message':str(exc),'detail':detail})
    def raw_player(self,actor):return self.runtime.player(self.world,actor,lock=self.lock,on_write=self._dirty,on_fault=self._fault,memory_query=lambda query,limit:self.search_memory(actor,query,limit))
    def search_memory(self,actor,query,limit=3):
        search=getattr(self.clients.get(actor),'search_memory',None)
        if callable(search):return search(query,limit)
        matches=[]
        for row in reversed(self.audit):
            if row['actor']!=actor or row['action']=='memory_search':continue
            text=json.dumps({'action':row['action'],'arguments':row['arguments'],'message':(row.get('receipt') or {}).get('message','')},ensure_ascii=False)
            if query.casefold() in text.casefold():matches.append({'period':(row.get('receipt') or {}).get('period',0),'context':text[:600]})
            if len(matches)>=limit:break
        return matches

    def player(self,actor):
        with self.lock:
            self.raw_player(actor)
            return BoundPlayer(self,actor,self.generations[actor])
    def invalidate(self,actor,context):
        self._dirty();self.generations[actor]+=1;self.contexts[actor]=wire(context)

    def api_call(self,player,capability,method,arguments,next_call):
        """Write API calls pass through services that declare api(), not submit().

        Services keep hooking submit() for the LLM tool path (plan/end_turn are
        still individual tools); scripts and the rule AI reach plan through this
        chain, so the council reviews it regardless of the caller.
        """
        return self._service_call(player,'api',(capability,method),arguments,next_call)

    def submit(self,player,action,arguments,next_submit):
        # These are arbitrary distribution services; no review/action names here.
        return self._service_call(player,'submit',(action,),arguments,next_submit)

    def _service_call(self,player,hook,request,arguments,next_call):
        """两种入口共用服务排序、参数隔离和 continuation 一次性约束。"""
        chain=next_call
        for service in reversed(tuple(self.services[player.actor].values())):
            handler=getattr(service,hook,None)
            if handler:
                downstream=chain
                def wrap(h,n):
                    used=False
                    def once():
                        nonlocal used
                        if used:raise RuntimeError('A request continuation can execute only once')
                        used=True
                        return n()
                    return lambda:h(player,*request,deepcopy(arguments),once)
                chain=wrap(handler,downstream)
        return chain()

    @staticmethod
    def _audit_value(value):
        try:return wire(value)
        except (TypeError,ValueError):return {'invalid_type':type(value).__name__}

    def actor_model(self,actor):
        """Model that issued this actor's operation; rule clients report their kind."""
        client=self.clients.get(actor)
        cfg=getattr(client,'cfg',None)
        if isinstance(cfg,dict) and isinstance(cfg.get('model'),str) and cfg['model'].strip():
            return cfg['model']
        kind=getattr(client,'kind',None)
        return kind if isinstance(kind,str) and kind else None

    def record(self,player,action,arguments,receipt,error=None):
        row={'id':self._audit_next,'actor':player.actor,'generation':player.generation,
             'model':self.actor_model(player.actor),
             'action':self._audit_value(action),'arguments':self._audit_value(arguments),'receipt':wire(receipt)}
        if error:row['error']={'type':type(error).__name__,'message':str(error)}
        self.audit.append(row);self._audit_next+=1
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

    def cycle(self,*,on_result=None,emit=None,head=None,on_call=None,on_stream=None,on_batch=None):
        if self.victory_event is not None:raise ValueError('Game has ended with a victory event')
        def call(actor,step,receipts):
            if on_call:on_call(actor,step,receipts)
            if on_batch:on_batch(actor,step+1)
        self.scheduled();receipts=[];self._boundary=False
        self._callbacks=on_result;self._emit=emit
        try:
            for actor in self.turn_order():
                client=self.clients[actor];transitions=0
                while True:
                    player=self.player(actor);generation=player.generation
                    if not player.action_state()['can_act']:break
                    if client.kind=='rule':
                        cfg=next(n for n in self.entries() if n['id']==actor)
                        receipts.extend(client.play(player,max_actions=cfg.get('max_actions')))
                    else:
                        callback_call=(lambda step,result,a=actor:call(a,step,result)) if on_call or on_batch else None
                        receipts.extend(client.play(player,emit=emit,head=head,on_call=callback_call,
                            **({'on_stream':on_stream} if on_stream and client.kind=='llm' else {})))
                    # Rule turns are a single batch, with individual receipts
                    # still available to the presenter. Also flush automatic
                    # end_turn / clients without call callbacks before changing
                    # actor or entering settlement.
                    if on_batch:on_batch(actor,None)
                    if self.generations[actor]==generation:break
                    transitions+=1
                    if transitions>8:raise RuntimeError('Too many actor control transitions in one cycle')
            self.world.resolve_turn();self.world.phase='行动'
            if self._scenario:
                # Administrative/scheduled joins can atomically replace world.
                # The program must always inspect the current committed world.
                if self._scenario_world is not self.world:
                    self._scenario=self._scenario_factory(self.world,self.trigger_victory)
                    self._scenario_world=self.world
                self._scenario()
            for actor in self.actors():
                for service in self.services[actor].values():
                    settled=getattr(service,'on_settlement',None)
                    if settled:settled(self.player(actor))
                settled=getattr(self.clients[actor],'on_settlement',None)
                if settled:settled(self.player(actor))
            self.last_results=receipts;self._boundary=True
            return receipts
        finally:self._callbacks=None;self._emit=None

    def to_save(self):
        if not self._boundary or self.world.phase!='行动':raise SaveFormatError('Save only at an action boundary')
        self.validate_boundary()
        return {'format':SAVE_FORMAT,'dependencies':self.runtime.dependency_manifest(),
                'modules':self.runtime.save_mod_state(self.world),
                'host':{'roster':deepcopy(self.roster),'scenario_text':self.scenario_text,
                        'scenario':self.scenario_id,'victory_event':deepcopy(self.victory_event),
                        'generations':deepcopy(self.generations),'contexts':deepcopy(self.contexts),'audit':deepcopy(self.audit[-AUDIT_LIMIT:]),'audit_next':self._audit_next,'audit_stream':self._audit_stream,
                        'services':{a:{n:s.to_save() for n,s in services.items()} for a,services in self.services.items()}},
                'clients':{actor:client.to_save() for actor,client in self.clients.items()}}

    def save(self,path):
        data=self.to_save() # Validate the boundary before creating any artifacts.
        path=Path(path);archive=path.with_suffix(path.suffix+'.audit')/self._audit_stream
        cursor=self._audit_archived.get(str(path.resolve()),0)
        pending=[row for row in self.audit if row['id']>cursor]
        if pending:
            target=archive/f'{pending[0]["id"]:012d}-{pending[-1]["id"]:012d}.json'
            payload={'format':'zhanguo.audit/1','records':pending}
            if target.exists():
                if json.loads(target.read_text(encoding='utf-8'))!=payload:raise SaveFormatError('Audit archive segment conflicts with this session')
            else:write_json(target,payload)
            self._audit_archived[str(path.resolve())]=pending[-1]['id']
        archives=[]
        for name in self.runtime.order:
            mod=self.runtime.units[name].mod;pending=getattr(mod,'pending_archive',None)
            if not callable(pending):continue
            rows=wire(pending(self.world))
            if not rows:continue
            payload={'format':'zhanguo.domain-history/1','module':name,'records':rows}
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
            target=archive/name.replace(':','_')/f'{min(r["period"] for r in rows):012d}-{max(r["period"] for r in rows):012d}-{digest}.json'
            if target.exists():
                if json.loads(target.read_text(encoding='utf-8'))!=payload:raise SaveFormatError('Domain history segment conflicts with this session')
            else:write_json(target,payload)
            archives.append(mod)
        write_json(path,data)
        for mod in archives:mod.archive_committed(self.world)
        self.audit=self.audit[-AUDIT_LIMIT:]


    def restore(self,data):
        if not isinstance(data,dict) or set(data)!={'format','dependencies','modules','host','clients'} or data['format']!=SAVE_FORMAT:
            raise SaveFormatError('Unsupported session format')
        self.runtime.check_save_dependencies(data['dependencies'])
        self.runtime.load_mod_state(self.world,data['modules'])
        saved=data['host'];keys={'roster','scenario_text','scenario','victory_event','generations','contexts','audit','audit_next','audit_stream','services'}
        if not isinstance(saved,dict) or set(saved)!=keys or not isinstance(saved['scenario_text'],str):raise SaveFormatError('Invalid session state')
        # Executable code comes from the distribution declaration, never an
        # import path in a save. Resume must select the same scenario.
        if saved['scenario']!=self.scenario_id:raise SaveFormatError('Scenario differs from saved scenario')
        self.victory_event=None
        if saved['victory_event'] is not None:
            try:self.trigger_victory(saved['victory_event'])
            except (TypeError,ValueError) as exc:raise SaveFormatError('Invalid victory event') from exc
        roster=saved['roster'];clients=data['clients'];actors=set(self.identities())
        if not isinstance(roster,dict) or not isinstance(clients,dict) or set(roster)!=actors or set(clients)!=actors:
            raise SaveFormatError('Client and actor identities disagree')
        for key in ('generations','contexts','services'):
            if not isinstance(saved[key],dict) or set(saved[key])!=actors:raise SaveFormatError('Invalid actor service state')
        if any(type(g) is not int or g<0 for g in saved['generations'].values()):raise SaveFormatError('Invalid actor generation')
        if not isinstance(saved['audit_stream'],str) or len(saved['audit_stream'])!=32 or any(c not in '0123456789abcdef' for c in saved['audit_stream']):raise SaveFormatError('Invalid audit stream identity')
        self._audit_stream=saved['audit_stream']
        audit_next=saved['audit_next']
        if type(audit_next) is not int or audit_next<1 or not isinstance(saved['audit'],list) or len(saved['audit'])>AUDIT_LIMIT or len(saved['audit'])>=audit_next or any(not isinstance(r,dict) or r.get('id')!=audit_next-len(saved['audit'])+i or r.get('actor') not in actors for i,r in enumerate(saved['audit'])):
            raise SaveFormatError('Invalid audit trail')
        self.scenario_text=saved['scenario_text'];self.roster=deepcopy(roster)
        if any(not isinstance(context,dict) for context in saved['contexts'].values()):raise SaveFormatError('Invalid actor context')
        self.generations=deepcopy(saved['generations']);self.contexts=wire(saved['contexts']);self.audit=wire(saved['audit']);self._audit_next=audit_next
        config={n['id']:n for n in self.entries()}
        for actor,row in roster.items():
            if actor not in config or not isinstance(row,dict) or set(row)!={'name','joined_at'} or not isinstance(row['name'],str) or type(row['joined_at']) is not int or not 0<=row['joined_at']<=self.clock():
                raise SaveFormatError('Invalid configured actor or arrival state')
            self.services[actor]=self._services(config[actor],saved['services'][actor])
            self.clients[actor]=self._client(config[actor],clients[actor],row['joined_at'])
        self._boundary=True
        self.to_save()

    def board(self):
        rows=getattr(self,'board_rows',None)
        if not callable(rows):raise ModError('当前发行版不支持终端面板')
        if self.presentation is None:raise ModError('当前会话没有装配呈现层')
        return self.presentation.console.board(rows())
