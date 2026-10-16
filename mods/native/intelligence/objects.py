"""通信、物资运输、间谍和事件视野快照。"""
from copy import deepcopy
from dataclasses import dataclass, replace, asdict
import math

from engine.building import integer
from engine.identifiers import content_id
from engine.player import wire
from engine.runtime import ModError, DomainRejection
from engine.state import active_unit, freeze


@dataclass(frozen=True)
class Dispatch:
    id: int
    owner: str
    target: str
    kind: str
    text: str
    resource: str | None
    quantity: int
    fee: int
    submitted: int
    arrive: int | None = None
    status: str = 'submitted'


@dataclass(frozen=True)
class Message:
    id: int
    source: str
    target: str
    kind: str
    sampled: int
    payload: dict


@dataclass(frozen=True)
class Event:
    id: int
    period: int
    kind: str
    text: str
    position: tuple | None
    recipients: tuple
    details: dict | None = None


class Intelligence:
    def __init__(self, call, permits, rules):
        self._call,self._permits,self._rules=call,permits,rules
        self._pending,self._transit,self._messages,self._results,self._events=[],[],[],[],[]
        self._next_dispatch,self._next_event=1,1
        self._maps={};self._archive=[];self._pruned_at=-1
        self._world,self._domain=None,None
    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Intelligence already bound')
        self._world,self._domain=world,world.runtime.state_owners[key];return self
    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use intelligence interface')
    def _ids(self):return self._call('native:nation-inspection','ids')
    def _period(self):return self._call('native:government','period')
    def _centers(self, owner):
        snap=self._call('native:buildings','snapshot');sites={s.id:s.owner for s in snap.sites}
        return sum(b.remaining == 0 and b.kind=='native:diplomacy-center' and sites[b.site]==owner for b in snap.instances)
    def submit(self, owner, target, kind, *, text='', resource=None, quantity=0):
        self._write()
        if self._world is not None and self._world.phase!='行动':raise ValueError('Submit only during action phase')
        if owner not in self._ids() or target not in self._ids() or owner==target:raise ValueError('Two distinct living nations required')
        if kind not in ('letter','gift','map','spy') or not self._permits(owner,'communication.'+kind):raise ValueError('Communication disabled or unknown')
        integer(quantity)
        fee=self._rules['Fee']
        if kind=='letter':
            if not isinstance(text,str) or not text.strip():raise ValueError('Letter cannot be empty')
            base=self._rules['LetterAlly'] if self._call('native:diplomacy','allied',owner,target) else self._rules['LetterBase']
            fee=max(self._rules['LetterMinimum'],base-self._centers(owner)*self._rules['CenterDiscount'])
            fee+=math.ceil(max(0,len(text)-self._rules['FreeCharacters'])/self._rules['CharactersPerGold'])
        elif kind=='spy':fee=self._rules['SpyFee']
        else:
            if self._call('native:diplomacy','allied',owner,target):fee=0
            else:
                for _ in range(self._centers(owner)):fee=max(1,fee//2)
        reserved={'native:gold':fee} if fee else {}
        if kind=='gift':
            content_id(resource);integer(quantity,1);reserved[resource]=reserved.get(resource,0)+quantity
        elif resource is not None or quantity:raise ValueError('Only gifts carry resource quantities')
        stocks={s.id:s.amount for s in self._call('native:resource-inspection','snapshot',owner)}
        for key,value in reserved.items():
            if key not in stocks or stocks[key]<value:raise DomainRejection('通信资源不足',{'reason':'insufficient_resource','resource':key,'required':value,'available':stocks.get(key,0)})
        record=Dispatch(self._next_dispatch,owner,target,kind,text,resource,quantity,fee,self._period(),self._period()+(self._rules['SpyDelay'] if kind=='spy' else 1))
        if reserved:self._call('native:resource-exchange','exchange',owner,reserved,{})
        self._next_dispatch+=1;self._pending.append(record);return record
    def _refund(self, d, fee=True):
        credits={}
        if fee and d.fee:credits['native:gold']=d.fee
        if d.kind=='gift':credits[d.resource]=credits.get(d.resource,0)+d.quantity
        if credits:self._call('native:resource-exchange','exchange',d.owner,{},credits)
    def resolve(self):
        self._write();facts=[];results=[]
        for d in sorted(self._pending,key=lambda d:(d.kind,d.owner,d.id)):
            if d.owner not in self._ids() or d.target not in self._ids() or not self._permits(d.owner,'communication.'+d.kind):
                self._refund(d);result=replace(d,status='cancelled')
            else:
                delay=self._rules['SpyDelay'] if d.kind=='spy' else 1
                if d.kind=='map':self._maps[d.id]={'sampled':self._period(),'tiles':self._map(d.owner)}
                result=replace(d,status='in-transit');self._transit.append(result)
                if d.fee:facts.append({'id':f'native:communication-fee/{d.id}', 'owner':d.owner,'period':self._period(),'category':'administration','inputs':(('native:gold',d.fee),)})
            self._results.append(result);results.append(result)
        self._pending=[];return tuple(results),tuple(facts)
    def deliver(self):
        self._write();arrived=[]
        for d in tuple(self._transit):
            if d.arrive>self._period()+1:continue
            self._transit.remove(d)
            map_snapshot=self._maps.pop(d.id,None)
            if d.target not in self._ids() or d.kind=='spy' and d.owner not in self._ids():
                self._results.append(replace(d,status='cancelled'));continue
            target=d.owner if d.kind=='spy' else d.target
            if d.kind=='letter':payload={'text':d.text}
            elif d.kind=='gift':
                self._call('native:resource-exchange','exchange',d.target,{}, {d.resource:d.quantity})
                payload={'resource':d.resource,'quantity':d.quantity}
            elif d.kind=='map':payload={'tiles':deepcopy(map_snapshot['tiles'])}
            else:
                stocks=self._call('native:resource-inspection','snapshot',d.target)
                units=self._call('native:army','snapshot',d.target);counts={}
                for u in units:counts[u.kind]=counts.get(u.kind,0)+1
                relation=self._call('native:diplomacy','snapshot',d.target)
                payload={'inventory':{s.id:s.amount for s in stocks},'unit_counts':counts,'tiles':self._map(d.target),
                         'alliances':[{'id':b.id,'name':b.name,'members':list(b.members),'chief':b.chief} for b in relation.alliances],
                         'wars':[{'id':w.id,'attackers':list(w.attackers),'defenders':list(w.defenders)} for w in relation.wars]}
                if self._world is not None and 'economy:queries' in self._world.runtime.capabilities:
                    balance=self._call('economy:queries','balance',d.target)
                    payload['economy']=wire(balance.latest_turn)
                    snap=self._call('native:buildings','snapshot')
                    payload['buildings_total']=sum(1 for b in snap.instances if any(s.id==b.site and s.owner==d.target for s in snap.sites))
            sampled=map_snapshot['sampled'] if d.kind=='map' else self._period()+1
            message=Message(d.id,d.target if d.kind=='spy' else d.owner,target,d.kind,sampled,payload)
            self._remember(message)
            arrived.append(freeze(message));self._results.append(replace(d,status='delivered'))
        return tuple(arrived)
    def _remember(self,message):
        self._messages.append(message)
        limit=3 if message.kind=='map' else 2 if message.kind=='spy' else 50
        same=[m.id for m in self._messages if m.target==message.target and m.kind==message.kind]
        keep=set(same[-limit:])
        self._messages=[m for m in self._messages if m.target!=message.target or m.kind!=message.kind or m.id in keep]

    def _map(self, owner):
        # Maps are national surveys, not copies of the alliance's shared view.
        snap=self._call('native:buildings','snapshot');by_site={}
        for b in snap.instances:by_site.setdefault(b.site,[]).append(b)
        owned=[s for s in snap.sites if s.owner==owner];positions={s.position for s in owned}
        for site in owned:
            for point in self._call('native:territory','neighbors',*site.position):
                if self._call('native:territory','owner',*point) is None:positions.add(point)
        result=[]
        for point in sorted(positions):
            t=self._call('native:territory','cell',*point);buildings={};construction={}
            for b in by_site.get(t.tile.id,()):
                counts=construction if b.remaining else buildings
                counts[b.kind]=counts.get(b.kind,0)+1
            result.append({'position':list(point),'terrain':t.tile.terrain,'owner':t.owner,'buildings':buildings,'resources':dict(t.resources),'construction':construction})
        return result
    def emit(self, kind, text, *, position=None, parties=(), public=False, snapshot_only=False, details=None):
        self._write()
        if not isinstance(kind,str) or not kind or not isinstance(text,str):raise ValueError('Invalid event')
        self._prune()
        ids=self._ids()
        if snapshot_only:
            for owner in parties:self._call('native:nation-inspection','get',owner)
            ids=tuple(dict.fromkeys((*ids,*parties)))
        recipients=set(ids if public else ())|set(n for n in parties if n in ids)
        if position is not None:
            if not isinstance(position,(tuple,list)) or len(position)!=2:raise ValueError('Invalid event position')
            position=tuple(position)
            if not snapshot_only:
                for n in ids:
                    if self._call('native:vision','visible',n,*position):recipients.add(n)
        event=Event(self._next_event,self._period(),kind,text,position,tuple(n for n in ids if n in recipients),deepcopy(details))
        self._next_event+=1;self._events.append(event);return event
    def announce(self, text):
        """公开公告：进入所有国家的观察者事件。"""
        return self.emit('announcement', text, public=True)
    def send(self, owner, text):
        """来信：只对该国家可见。"""
        self._write();self._call('native:nation-inspection','get',owner)
        if not isinstance(text,str) or not text.strip():raise ValueError('Letter cannot be empty')
        message=Message(self._next_dispatch,'observer',owner,'observer-letter',self._period(),{'sender':'神秘人','text':text})
        self._next_dispatch+=1;self._remember(message)
        return self.emit('observer-letter', '神秘人来信：'+text, parties=(owner,))
    def messages(self, owner):return tuple(freeze(m) for m in self._messages if m.target==owner)
    def events(self, owner):return tuple(replace(e,recipients=(),details=deepcopy(e.details)) for e in self._events if owner in e.recipients and e.period>=max(0,self._period()-10))
    def pending(self, owner):return tuple(d for d in self._pending+self._transit if d.owner==owner)
    def results(self, owner):return tuple(d for d in self._results if owner in (d.owner,d.target))
    def _prune(self):
        period=self._period()
        if self._pruned_at==period:return
        cutoff=max(0,period-10)
        old=[e for e in self._events if e.period<cutoff]
        self._events=[e for e in self._events if e.period>=cutoff]
        self._archive.extend({'kind':'event','period':e.period,'value':asdict(e)} for e in old)
        old=[d for d in self._results if d.submitted<cutoff]
        self._results=[d for d in self._results if d.submitted>=cutoff]
        self._archive.extend({'kind':'dispatch','period':d.submitted,'value':asdict(d)} for d in old)
        self._pruned_at=period
    def pending_archive(self):return tuple(self._archive)
    def archive_committed(self):self._write();self._archive=[]
    def restore_archive(self,rows):self._write();self._archive=deepcopy(list(rows))

    def records(self):
        return {'events':tuple(self._events),'messages':tuple(freeze(m) for m in self._messages)}

    def to_save(self):
        def dispatch(d):return {'id':d.id,'owner':d.owner,'target':d.target,'kind':d.kind,'text':d.text,'resource':d.resource,'quantity':d.quantity,'fee':d.fee,'submitted':d.submitted,'arrive':d.arrive,'status':d.status}
        return {'pending':[dispatch(d) for d in self._pending],'transit':[dispatch(d) for d in self._transit],
                'results':[dispatch(d) for d in self._results],
                'maps':[{'id':ident,**deepcopy(value)} for ident,value in self._maps.items()],
                'messages':[{'id':m.id,'source':m.source,'target':m.target,'kind':m.kind,'sampled':m.sampled,'payload':deepcopy(m.payload)} for m in self._messages],
                'events':[{'id':e.id,'period':e.period,'kind':e.kind,'text':e.text,'position':None if e.position is None else list(e.position),'recipients':list(e.recipients),'details':deepcopy(e.details)} for e in self._events],
                'next_dispatch':self._next_dispatch,'next_event':self._next_event}
    def restore(self, data):
        self._write()
        expected={'pending','transit','results','maps','messages','events','next_dispatch','next_event'}
        if not isinstance(data,dict) or set(data)!=expected:raise ValueError('Invalid intelligence save')
        for key in expected-{'next_dispatch','next_event'}:
            if not isinstance(data[key],list):raise ValueError('Invalid intelligence list')
        candidate={}
        for key in ('pending','transit','results'):
            records=[]
            for row in data[key]:
                if not isinstance(row,dict) or set(row)!={'id','owner','target','kind','text','resource','quantity','fee','submitted','arrive','status'}:raise ValueError('Invalid dispatch record')
                d=Dispatch(**row);integer(d.id,1);integer(d.quantity);integer(d.fee);integer(d.submitted)
                self._call('native:nation-inspection','get',d.owner);self._call('native:nation-inspection','get',d.target)
                if d.owner==d.target or d.kind not in ('letter','gift','map','spy') or d.status not in ('submitted','in-transit','cancelled','delivered') or not isinstance(d.text,str):raise ValueError('Invalid dispatch fields')
                if d.arrive is not None:integer(d.arrive,d.submitted+1)
                if key=='pending' and (d.status!='submitted' or d.arrive is None):raise ValueError('Invalid pending dispatch')
                if key=='transit' and (d.status!='in-transit' or d.arrive is None):raise ValueError('Invalid transit dispatch')
                if d.kind=='gift':content_id(d.resource);integer(d.quantity,1)
                elif d.resource is not None or d.quantity:raise ValueError('Unexpected dispatch resource')
                records.append(d)
            if key!='results' and len({d.id for d in records})!=len(records):raise ValueError('Duplicate dispatch')
            candidate[key]=records
        active=candidate['pending']+candidate['transit']
        if len({d.id for d in active})!=len(active):raise ValueError('Duplicate active dispatch')
        maps={}
        for row in data['maps']:
            if not isinstance(row,dict) or set(row)!={'id','sampled','tiles'}:raise ValueError('Invalid map snapshot')
            integer(row['id'],1);integer(row['sampled'])
            if row['id'] in maps or not isinstance(row['tiles'],list):raise ValueError('Invalid map snapshot')
            for tile in row['tiles']:
                if not isinstance(tile,dict) or set(tile)!={'position','terrain','owner','buildings','resources','construction'}:raise ValueError('Invalid shared tile')
                if not isinstance(tile['position'],list) or len(tile['position'])!=2:raise ValueError('Invalid shared position')
                for value in tile['position']:integer(value)
                content_id(tile['terrain'])
                if tile['owner'] is not None:self._call('native:nation-inspection','get',tile['owner'])
                for key in ('buildings','resources','construction'):
                    if not isinstance(tile[key],dict):raise ValueError('Invalid shared contents')
                    for ident,amount in tile[key].items():content_id(ident);integer(amount)
            maps[row['id']]={'sampled':row['sampled'],'tiles':deepcopy(row['tiles'])}
        if set(maps)!={d.id for d in candidate['transit'] if d.kind=='map'}:raise ValueError('Missing or orphaned map snapshot')
        messages=[]
        for row in data['messages']:
            if not isinstance(row,dict) or set(row)!={'id','source','target','kind','sampled','payload'}:raise ValueError('Invalid message record')
            m=Message(**deepcopy(row));integer(m.id,1);integer(m.sampled)
            if m.kind=='observer-letter':
                if m.source!='observer' or not isinstance(m.payload,dict) or m.payload.get('sender')!='神秘人' or not isinstance(m.payload.get('text'),str):raise ValueError('Invalid observer letter')
            else:self._call('native:nation-inspection','get',m.source)
            self._call('native:nation-inspection','get',m.target)
            if m.kind not in ('letter','gift','map','spy','observer-letter') or not isinstance(m.payload,dict):raise ValueError('Invalid message payload')
            messages.append(m)
        if len({m.id for m in messages})!=len(messages):raise ValueError('Duplicate message')
        events=[]
        for row in data['events']:
            if not isinstance(row,dict) or set(row)!={'id','period','kind','text','position','recipients','details'}:raise ValueError('Invalid event record')
            integer(row['id'],1);integer(row['period'])
            if not isinstance(row['kind'],str) or not row['kind'] or not isinstance(row['text'],str):raise ValueError('Invalid event fields')
            if not isinstance(row['recipients'],list) or len(set(row['recipients']))!=len(row['recipients']):raise ValueError('Invalid event recipients')
            for owner in row['recipients']:self._call('native:nation-inspection','get',owner)
            pos=row['position']
            if pos is not None:
                if not isinstance(pos,list) or len(pos)!=2:raise ValueError('Invalid event position')
                pos=tuple(integer(v) for v in pos)
            if row['details'] is not None and not isinstance(row['details'],dict):raise ValueError('Invalid event details')
            events.append(Event(row['id'],row['period'],row['kind'],row['text'],pos,tuple(row['recipients']),deepcopy(row['details'])))
        integer(data['next_dispatch'],1);integer(data['next_event'],1)
        all_dispatch=candidate['pending']+candidate['transit']+candidate['results']
        if any(d.id>=data['next_dispatch'] for d in all_dispatch) or any(m.id>=data['next_dispatch'] for m in messages) or any(e.id>=data['next_event'] for e in events) or len({e.id for e in events})!=len(events):raise ValueError('Invalid intelligence sequence')
        self._pending,self._transit,self._results=candidate['pending'],candidate['transit'],candidate['results']
        self._maps=maps
        self._messages,self._events=messages,events;self._next_dispatch,self._next_event=data['next_dispatch'],data['next_event']
        self._archive=[];self._pruned_at=-1
    def __deepcopy__(self,memo):return self.to_save()
