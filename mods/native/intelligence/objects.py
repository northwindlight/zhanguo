"""通信、物资运输、间谍和事件视野快照。"""
from copy import deepcopy
from dataclasses import dataclass, replace, asdict
import math

from engine.building import integer
from engine.identifiers import content_id
from engine.player import wire
from engine.runtime import ModError, DomainRejection
from engine.state import active_unit, freeze
from .knowledge import MapKnowledge


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
        self._knowledge=MapKnowledge()
        self._surveys={};self._purchases=set();self._map_fees=[]
        self._world,self._domain=None,None
    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Intelligence already bound')
        self._world,self._domain=world,world.runtime.state_owners[key];return self
    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use intelligence interface')
    def _ids(self):return self._call('native:nation-inspection','ids')

    def capture_maps(self,owners=None):
        """Trusted write boundary, never called by a read-only player query."""
        self._write()
        owners=tuple(self._knowledge.cells) if owners is None else tuple(owners)
        if not owners:return
        snap=self._call('native:buildings','snapshot')
        kinds=set(self._call('native:buildings','kinds_with_role','capital')) | set(self._call('native:buildings','kinds_with_role','castle'))
        for owner in owners:
            if owner not in self._ids():continue
            order=self._knowledge.order()
            for tile in self._call('native:vision','tiles',owner):
                row={'position':list(tile.position),'terrain':tile.terrain,'owner':tile.owner,
                     'name':tile.name,'buildings':dict(tile.buildings)}
                full=tile.owner==owner
                if full:
                    row['resources']=dict(tile.resources)
                    site=next((s.id for s in snap.sites if s.owner==owner and s.position==tile.position),None)
                    row['construction']={}
                    for b in snap.instances:
                        if b.site==site and b.remaining:row['construction'][b.kind]=row['construction'].get(b.kind,0)+1
                self._knowledge.merge(owner,[row],sampled=self._period(),order=order,
                    source='vision',acquired=self._period(),building_kinds=() if full else kinds,complete=full)

    def knowledge(self,owner):
        self._call('native:nation-inspection','get',owner)
        return self._knowledge.snapshot(owner)

    def publish_maps(self):
        """A dated national survey, frozen at a complete turn boundary."""
        self._write()
        for owner in self._ids():
            self._surveys[owner]={'sampled':self._period(),'order':self._knowledge.order(),'tiles':self._map(owner)}

    def map_offers(self,owner):
        self._call('native:nation-inspection','get',owner)
        return [{'target':target,'sampled':survey['sampled'],'tiles':len(survey['tiles']),
                 'price':self._rules['MapBase']+self._rules['MapPerTile']*len(survey['tiles']),
                 'purchased':(owner,target,survey['order']) in self._purchases,
                 'source':'national-survey'}
                for target,survey in self._surveys.items() if target!=owner and target in self._ids()]

    def buy_map(self,owner,target):
        self._write()
        if self._world is not None and self._world.phase!='行动':raise ValueError('Buy only during action phase')
        offer=next((row for row in self.map_offers(owner) if row['target']==target),None)
        if offer is None:raise ValueError('没有可购买的已发布地图')
        if offer['purchased']:raise ValueError('已经购买这份地图，等待新的测绘版本')
        if not self._permits(owner,'communication.map'):raise ValueError('当前政体不能购买地图')
        survey=self._surveys[target]
        self._call('native:resource-exchange','exchange',owner,{self._currency():offer['price']},{})
        message=Message(self._next_dispatch,target,owner,'map',survey['sampled'],
                        {'tiles':deepcopy(survey['tiles']),'sampled_order':survey['order'],
                         'source':'national-survey','price':offer['price']})
        self._next_dispatch+=1;self._purchases.add((owner,target,survey['order']))
        self._map_fees.append({'id':f'native:map-purchase/{message.id}','owner':owner,'period':self._period(),
                              'category':'administration','inputs':((self._currency(),offer['price']),)})
        self._remember(message)
        return {'target':target,'sampled':survey['sampled'],'price':offer['price'],
                'tiles':offer['tiles'],'message':message.id,'status':'delivered'}
    def _targets(self):
        rows=(self._call('native:nation-inspection','get',ident) for ident in self._ids())
        return '、'.join(f'{row.name}={row.id}' for row in rows)
    def letter_fee(self, owner, target, text, *, length=None):
        """Read-only letter quote; the send path uses the same formula."""
        if owner not in self._ids():raise ValueError('发信方不是存活国家')
        if target not in self._ids():
            raise DomainRejection(f'未识别的国家或已亡国：{target}；可用：'+self._targets(),
                                  {'reason':'unknown_nation','value':target,'candidates':self._targets()})
        if target == owner:raise DomainRejection('不能给自己发信；请选另一个存活国家：'+self._targets(),
                                                  {'reason':'unknown_nation','value':target})
        if length is None:length=len(text or '')
        if length<=0:raise ValueError('信件正文不能为空：写清诉求与预期收益再发，按字数计费')
        base=self._rules['LetterAlly'] if self._call('native:diplomacy','allied',owner,target) else self._rules['LetterBase']
        fee=max(self._rules['LetterMinimum'],base-self._centers(owner)*self._rules['CenterDiscount'])
        fee+=math.ceil(max(0,length-self._rules['FreeCharacters'])/self._rules['CharactersPerGold'])
        return {'target':target,'length':length,'fee':fee,'currency':self._currency(),
                'budget':{s.id:s.amount for s in self._call('native:resource-inspection','snapshot',owner)}}
    def _currency(self):
        if not hasattr(self,'_currency_id'):
            ids=self._call('native:resource-inspection','role_ids','currency')
            if len(ids)!=1:raise ValueError(f'货币资源必须恰好有一个：{ids}')
            self._currency_id=ids[0]
        return self._currency_id
    def _period(self):return self._call('native:government','period')
    def _centers(self, owner):
        snap=self._call('native:buildings','snapshot');sites={s.id:s.owner for s in snap.sites}
        return sum(b.remaining == 0 and b.kind=='native:diplomacy-center' and sites[b.site]==owner for b in snap.instances)
    def submit(self, owner, target, kind, *, text='', resource=None, quantity=0):
        self._write()
        if self._world is not None and self._world.phase!='行动':raise ValueError('Submit only during action phase')
        if owner not in self._ids():raise ValueError('发信方不是存活国家')
        if target == owner:raise DomainRejection('不能给自己发信；请选另一个存活国家：'+self._targets(),
                                                  {'reason':'unknown_nation','value':target})
        if target not in self._ids():
            try:
                self._call('native:nation-inspection','get',target)
                message=f'{target} 已亡国；可用：'+self._targets()
            except Exception:
                message=f'未识别的国家：{target}；可用：'+self._targets()
            raise DomainRejection(message, {'reason':'unknown_nation','value':target,
                                            'candidates':self._targets()})
        if (not self._call('native:diplomacy','diplomatic_authority',owner)
            and not self._call('native:diplomacy','allied',owner,target)):
            raise ValueError('普通附庸没有对外外交权，只能在宗主外交实体内部通信')
        if kind not in ('letter','gift','map','spy') or not self._permits(owner,'communication.'+kind):raise ValueError('这种通信不可用：kind 须为 letter/gift/map/spy 之一，且须为政体未禁用的动作')
        integer(quantity)
        fee=self._rules['Fee']
        if kind=='letter':
            if not isinstance(text,str) or not text.strip():raise ValueError('信件正文不能为空：写清诉求与预期收益再发，按字数计费')
            fee=self.letter_fee(owner,target,text)['fee']
        elif kind=='spy':fee=self._rules['SpyFee']
        else:
            if self._call('native:diplomacy','allied',owner,target):fee=0
            else:
                for _ in range(self._centers(owner)):fee=max(1,fee//2)
        reserved={self._currency():fee} if fee else {}
        if kind=='gift':
            content_id(resource);integer(quantity,1);reserved[resource]=reserved.get(resource,0)+quantity
        elif resource is not None or quantity:raise ValueError('只有 gift 能随信附物资：letter/map/spy 不接受 resource 与 quantity')
        stocks={s.id:s.amount for s in self._call('native:resource-inspection','snapshot',owner)}
        for key,value in reserved.items():
            if key not in stocks or stocks[key]<value:raise DomainRejection('通信资源不足',{'reason':'insufficient_resource','resource':key,'required':value,'available':stocks.get(key,0)})
        record=Dispatch(self._next_dispatch,owner,target,kind,text,resource,quantity,fee,self._period(),self._period()+(self._rules['SpyDelay'] if kind=='spy' else 1))
        if reserved:self._call('native:resource-exchange','exchange',owner,reserved,{})
        self._next_dispatch+=1;self._pending.append(record);return record
    def _refund(self, d, fee=True):
        credits={}
        if fee and d.fee:credits[self._currency()]=d.fee
        if d.kind=='gift':credits[d.resource]=credits.get(d.resource,0)+d.quantity
        if credits:self._call('native:resource-exchange','exchange',d.owner,{},credits)
    def resolve(self):
        self._write();facts=list(self._map_fees);self._map_fees=[];results=[]
        for d in sorted(self._pending,key=lambda d:(d.kind,d.owner,d.id)):
            if (d.owner not in self._ids() or d.target not in self._ids() or not self._permits(d.owner,'communication.'+d.kind)
                or not self._call('native:diplomacy','diplomatic_authority',d.owner)
                and not self._call('native:diplomacy','allied',d.owner,d.target)):
                self._refund(d);result=replace(d,status='cancelled')
            else:
                delay=self._rules['SpyDelay'] if d.kind=='spy' else 1
                if d.kind=='map':self._maps[d.id]={'sampled':self._period(),'order':self._knowledge.order(),'tiles':self._map(d.owner)}
                result=replace(d,status='in-transit');self._transit.append(result)
                if d.fee:facts.append({'id':f'native:communication-fee/{d.id}', 'owner':d.owner,'period':self._period(),'category':'administration','inputs':((self._currency(),d.fee),)})
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
            elif d.kind=='map':payload={'tiles':deepcopy(map_snapshot['tiles']),'sampled_order':map_snapshot['order']}
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
        if message.kind in ('map','spy') and isinstance(message.payload.get('tiles'),list):
            order=message.payload.get('sampled_order') or self._knowledge.order()
            self._knowledge.merge(message.target,message.payload['tiles'],sampled=message.sampled,order=order,
                source=f'{message.kind}/{message.source}/{message.id}',acquired=max(self._period(),message.sampled),complete=True)
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
        if not isinstance(text,str) or not text.strip():raise ValueError('信件正文不能为空：写清诉求与预期收益再发，按字数计费')
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
                'next_dispatch':self._next_dispatch,'next_event':self._next_event,
                'knowledge':self._knowledge.to_save(),'surveys':deepcopy(self._surveys),
                'purchases':[list(row) for row in sorted(self._purchases)],'map_fees':deepcopy(self._map_fees)}
    def restore(self, data):
        self._write()
        expected={'pending','transit','results','maps','messages','events','next_dispatch','next_event','knowledge','surveys','purchases','map_fees'}
        if not isinstance(data,dict) or set(data)!=expected:raise ValueError('Invalid intelligence save')
        for key in expected-{'next_dispatch','next_event','knowledge','surveys'}:
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
            if not isinstance(row,dict) or set(row)!={'id','sampled','order','tiles'}:raise ValueError('Invalid map snapshot')
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
            integer(row['order'],1)
            maps[row['id']]={'sampled':row['sampled'],'order':row['order'],'tiles':deepcopy(row['tiles'])}
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
        knowledge=MapKnowledge()
        size,_=self._call('native:territory','dimensions')
        knowledge.restore(data['knowledge'],lambda owner:self._call('native:nation-inspection','get',owner),size,self._period())
        if not isinstance(data['surveys'],dict):raise ValueError('Invalid survey catalogue')
        for owner,row in data['surveys'].items():
            self._call('native:nation-inspection','get',owner)
            if not isinstance(row,dict) or set(row)!={'sampled','order','tiles'}:raise ValueError('Invalid survey')
            integer(row['sampled']);integer(row['order'],1)
            if row['sampled']>self._period():raise ValueError('Future survey')
            if row['order']>=knowledge.next_order or not isinstance(row['tiles'],list):raise ValueError('Invalid survey order')
            check=MapKnowledge()
            check.next_order=knowledge.next_order
            check.merge(owner,row['tiles'],sampled=row['sampled'],order=row['order'],source='survey',acquired=row['sampled'],complete=True)
            check.restore(check.to_save(),lambda actor:self._call('native:nation-inspection','get',actor),size)
        purchases=set()
        for row in data['purchases']:
            if not isinstance(row,list) or len(row)!=3:raise ValueError('Invalid map purchase')
            owner,target,order=row
            self._call('native:nation-inspection','get',owner);self._call('native:nation-inspection','get',target)
            integer(order,1)
            if owner==target or order>=knowledge.next_order or tuple(row) in purchases:raise ValueError('Invalid map purchase')
            purchases.add(tuple(row))
        self._knowledge=knowledge;self._surveys=deepcopy(data['surveys']);self._purchases=purchases
        fees=[];fee_ids=set()
        for row in data['map_fees']:
            if not isinstance(row,dict) or set(row)!={'id','owner','period','category','inputs'}:raise ValueError('Invalid map fee')
            self._call('native:nation-inspection','get',row['owner']);integer(row['period'])
            if row['category']!='administration' or not isinstance(row['id'],str) or not row['id'].startswith('native:map-purchase/'):
                raise ValueError('Invalid map fee')
            if not isinstance(row['inputs'],(list,tuple)) or len(row['inputs'])!=1:raise ValueError('Invalid map fee input')
            resource,amount=row['inputs'][0];content_id(resource);integer(amount,1)
            if row['id'] in fee_ids or row['period']!=self._period() or resource!=self._currency():raise ValueError('Invalid map fee identity')
            fee_ids.add(row['id'])
            fees.append({**deepcopy(row),'inputs':((resource,amount),)})
        self._map_fees=fees
    def __deepcopy__(self,memo):return self.to_save()
