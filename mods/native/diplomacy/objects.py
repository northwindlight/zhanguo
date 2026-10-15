"""实体条约、联盟、投票与延迟命令；只通过声明接口读取其他领域。"""
from copy import copy, deepcopy
from dataclasses import dataclass, replace
import inspect

from engine.building import integer
from engine.identifiers import content_id
from engine.runtime import ModError
from engine.state import active_unit, freeze


@dataclass(frozen=True)
class Alliance:
    id: str
    name: str
    members: tuple
    chief: str


@dataclass(frozen=True)
class Pact:
    kind: str
    a: str
    b: str


@dataclass(frozen=True)
class War:
    id: int
    a: str
    b: str
    attackers: tuple
    defenders: tuple


@dataclass(frozen=True)
class Offer:
    id: int
    action: str
    actor: str
    target: str
    arguments: tuple
    parties: tuple
    accepted: tuple = ()


@dataclass(frozen=True)
class Vote:
    id: int
    alliance: str
    actor: str
    action: str
    arguments: tuple
    voters: tuple
    ballots: tuple
    deadline: int
    parties: tuple


@dataclass(frozen=True)
class Command:
    id: int
    actor: str
    action: str
    arguments: tuple
    period: int
    fee: int
    entities: tuple
    status: str = 'submitted'
    message: str = ''


@dataclass(frozen=True)
class DiplomaticSnapshot:
    alliances: tuple
    pacts: tuple
    wars: tuple
    truces: tuple
    offers: tuple
    votes: tuple
    pending: tuple
    results: tuple


PRIORITIES = {'accept_peace':10,'reject_peace':11,'offer_peace':12,
              'bloc_dissolve':20,'bloc_leave':21,'bloc_transfer':22,'bloc_rename':23,
              'bloc_found':24,'bloc_join':25,'cancel_guarantee':30,'break_defense':31,
              'guarantee':32,'propose_defense':33,'reject_proposal':34,'accept_proposal':35,'declare_war':40}


class Diplomacy:
    def __init__(self, call, permits, fee=10, minimum_fee=1, name_max=12, fall_truce=10):
        self._call,self._permits = call,permits
        self._fee,self._minimum,self._name_max,self._fall_truce = fee,minimum_fee,name_max,fall_truce
        self._alliances,self._pacts,self._wars,self._truces = {},[],[],{}
        self._offers,self._votes,self._approved,self._pending,self._results = [],[],[],[],[]
        self._next_command,self._next_offer,self._next_vote,self._next_war = 1,1,1,1
        self._effects = []
        self._world,self._domain = None,None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Diplomacy already bound')
        self._world,self._domain = world,world.runtime.state_owners[key]
        return self

    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use diplomacy interface')

    def _ids(self):return self._call('native:nation-inspection','ids')
    def _period(self):return self._call('native:government','period')
    def _living(self, actor):
        if actor not in self._ids():raise ValueError('Nation is not alive')
    def alliance(self, actor):return next((b for b in self._alliances.values() if actor in b.members),None)
    def entity(self, actor):
        self._living(actor);bloc=self.alliance(actor)
        return 'bloc/'+bloc.id if bloc else 'nation/'+actor
    def members(self, entity):
        if entity.startswith('nation/'):return (entity[7:],) if entity[7:] in self._ids() else ()
        bloc = self._alliances.get(entity[5:]) if entity.startswith('bloc/') else None
        return bloc.members if bloc else ()
    def representative(self, entity):
        if not self.members(entity):raise ValueError('Diplomatic entity no longer exists')
        return self._alliances[entity[5:]].chief if entity.startswith('bloc/') else entity[7:]
    def entities(self):return tuple(sorted({self.entity(n) for n in self._ids()}))
    def allied(self, a, b):return a in self._ids() and b in self._ids() and self.entity(a)==self.entity(b)
    def war_between(self, a, b):return any((a in w.attackers and b in w.defenders) or (b in w.attackers and a in w.defenders) for w in self._wars)
    def at_war(self, actor):return any(actor in w.attackers+w.defenders for w in self._wars)
    def _entity_war(self, entity):return any(self.at_war(n) for n in self.members(entity))
    def passage(self, actor, owner):return owner is None or self.allied(actor,owner) or self.war_between(actor,owner)
    def active_truce(self, a, b):return self._truces.get(tuple(sorted((self.entity(a),self.entity(b)))),-1)>=self._period()
    def _truce(self, a, b, until):
        if a!=b:
            key = tuple(sorted((a,b)));self._truces[key]=max(self._truces.get(key,-1),until)

    def _peaceful(self, *entities):
        if any(self._entity_war(e) for e in entities):raise ValueError('Treaties and membership are frozen during war')
    def _pair(self, actor, target):
        self._living(actor);self._living(target);a,b=self.entity(actor),self.entity(target)
        if a==b:raise ValueError('Both nations belong to the same diplomatic entity')
        return a,b
    def _name(self, name):
        if not isinstance(name,str) or not 1<=len(name)<=self._name_max or any(c.isspace() for c in name):raise ValueError('Invalid alliance display name')
        if any(b.name==name for b in self._alliances.values()):raise ValueError('Alliance display name already exists')
    def _policy(self, actor, action):
        if not self._permits(actor,'action.'+action):raise ValueError('Actor policy disables this action')
    def _offer(self, action, actor, target, arguments, parties):
        if any(o.action==action and o.actor==actor and o.target==target and o.arguments==tuple(arguments.items()) for o in self._offers):raise ValueError('Offer already exists')
        offer = Offer(self._next_offer,action,actor,target,tuple(arguments.items()),tuple(sorted(set(parties))))
        self._next_offer += 1;self._offers.append(offer)
        return offer
    def _vote(self, actor, action, arguments, bloc, parties):
        if any(v.alliance==bloc.id and v.action==action and v.arguments==tuple(arguments.items()) for v in self._votes+self._approved):raise ValueError('Vote already exists')
        vote = Vote(self._next_vote,bloc.id,actor,action,tuple(arguments.items()),bloc.members,(),self._period()+1,tuple(sorted(set(parties)|set(bloc.members))))
        self._next_vote += 1;self._votes.append(vote)
        return vote
    def _route(self, actor, action, arguments, approved, parties):
        bloc = self.alliance(actor)
        if bloc and not approved:return self._vote(actor,action,arguments,bloc,parties)
        return None
    def _drop_entity_pacts(self, entity):self._pacts=[p for p in self._pacts if entity not in (p.a,p.b)]

    def seed_pact(self, kind, source, target):
        """Administrative scenario initialization; never a player action."""
        self._write();a,b=self._pair(source,target);self._peaceful(a,b)
        if kind not in ('guarantee','defense') or self.alliance(source) or self.alliance(target):raise ValueError('Initial pact requires independent nations')
        if not self._permits(source,'accept.treaties') or not self._permits(target,'accept.treaties'):raise ValueError('Policy rejects initial treaty')
        if any(p.kind==kind and ((p.a,p.b)==(a,b) or kind=='defense' and (p.a,p.b)==(b,a)) for p in self._pacts):raise ValueError('Initial pact already exists')
        pact=Pact(kind,a,b);self._pacts.append(pact);return pact

    def _apply_guarantee(self, actor, target, *, approved=False):
        a,b = self._pair(actor,target);self._peaceful(a,b)
        if not self._permits(target,'accept.treaties'):raise ValueError('Target policy rejects treaties')
        if any(p.kind=='defense' and {p.a,p.b}=={a,b} or p.kind=='guarantee' and (p.a,p.b)==(a,b) for p in self._pacts):raise ValueError('Treaty already exists')
        routed=self._route(actor,'guarantee',{'target':target},approved,self.members(b))
        if routed:return routed
        pact=Pact('guarantee',a,b);self._pacts.append(pact);return pact

    def _apply_cancel_guarantee(self, actor, target, *, approved=False):
        a,b=self._pair(actor,target);self._peaceful(a,b)
        pact=Pact('guarantee',a,b)
        if pact not in self._pacts:raise ValueError('Guarantee does not exist')
        routed=self._route(actor,'cancel_guarantee',{'target':target},approved,self.members(b))
        if routed:return routed
        self._pacts.remove(pact);return pact

    def _apply_propose_defense(self, actor, target, *, approved=False):
        a,b=self._pair(actor,target);self._peaceful(a,b)
        if not self._permits(target,'accept.treaties'):raise ValueError('Target policy rejects treaties')
        if any(p.kind=='defense' and {p.a,p.b}=={a,b} for p in self._pacts):raise ValueError('Defense pact already exists')
        routed=self._route(actor,'propose_defense',{'target':target},approved,self.members(b))
        if routed:return routed
        return self._offer('defense',actor,b,{'a':a,'b':b},self.members(a)+self.members(b))

    def _apply_accept_proposal(self, actor, offer: int, *, approved=False):
        integer(offer,1)
        o=next((o for o in self._offers if o.id==offer and o.action in ('defense','found')),None)
        if o is None:raise ValueError('Offer does not exist')
        args=dict(o.arguments)
        if o.action=='found':
            if actor not in o.parties or actor==o.actor or self.alliance(actor):raise ValueError('Not an eligible founding invitee')
            for n in o.parties:
                self._living(n)
                if self.alliance(n) or not self._permits(n,'accept.bloc'):raise ValueError('Founding party is no longer eligible')
            self._peaceful(*(self.entity(n) for n in o.parties))
            accepted=tuple(dict.fromkeys((*o.accepted,actor)))
            if set(accepted)|{o.actor}==set(o.parties):
                self._name(args['name']);bloc=Alliance(args['bloc'],args['name'],tuple(args['members']),o.actor)
                if bloc.id in self._alliances:raise ValueError('Alliance ID already exists')
                for n in bloc.members:self._drop_entity_pacts(self.entity(n))
                self._alliances[bloc.id]=bloc;self._offers.remove(o);return bloc
            self._offers[self._offers.index(o)]=replace(o,accepted=accepted);return o
        if self.entity(actor)!=o.target or self.entity(o.actor)!=args['a']:raise ValueError('Offer entity has changed')
        self._peaceful(args['a'],args['b'])
        routed=self._route(actor,'accept_proposal',{'offer':offer},approved,o.parties)
        if routed:return routed
        self._pacts=[p for p in self._pacts if not (p.kind=='guarantee' and {p.a,p.b}=={args['a'],args['b']})]
        pact=Pact('defense',args['a'],args['b']);self._pacts.append(pact);self._offers.remove(o);return pact

    def _apply_reject_proposal(self, actor, offer: int, *, approved=False):
        integer(offer,1)
        o=next((o for o in self._offers if o.id==offer and o.action in ('defense','found')),None)
        if o is None or (actor not in o.parties if o.action=='found' else self.entity(actor)!=o.target):raise ValueError('Not a recipient of this offer')
        self._offers.remove(o);return o

    def _apply_break_defense(self, actor, target, *, approved=False):
        a,b=self._pair(actor,target);self._peaceful(a,b)
        p=next((p for p in self._pacts if p.kind=='defense' and {p.a,p.b}=={a,b}),None)
        if p is None:raise ValueError('Defense pact does not exist')
        routed=self._route(actor,'break_defense',{'target':target},approved,self.members(b))
        if routed:return routed
        self._pacts.remove(p);return p

    def _apply_bloc_found(self, actor, bloc: str, name: str, tos: list[str], *, approved=False):
        content_id(bloc);self._name(name)
        if not isinstance(tos,(list,tuple)) or not tos or actor in tos or len(set(tos))!=len(tos):raise ValueError('Invalid founding members')
        members=(actor,*tos)
        if bloc in self._alliances or any(o.action=='found' and dict(o.arguments)['bloc']==bloc for o in self._offers):raise ValueError('Alliance ID already exists')
        for n in members:
            self._living(n)
            if self.alliance(n) or not self._permits(n,'accept.bloc'):raise ValueError('Nation cannot found alliance')
        self._peaceful(*(self.entity(n) for n in members))
        return self._offer('found',actor,bloc,{'bloc':bloc,'name':name,'members':members},members)

    def _apply_bloc_join(self, actor, bloc: str, *, approved=False):
        b=self._alliances.get(bloc)
        if b is None or self.alliance(actor) or not self._permits(actor,'accept.bloc'):raise ValueError('Cannot join alliance')
        self._peaceful(self.entity(actor),'bloc/'+bloc)
        if not approved:return self._vote(actor,'bloc_join',{'bloc':bloc},b,(actor,))
        self._drop_entity_pacts(self.entity(actor));self._alliances[bloc]=replace(b,members=(*b.members,actor))
        return self._alliances[bloc]

    def _apply_bloc_leave(self, actor, *, approved=False):
        b=self.alliance(actor)
        if b is None:raise ValueError('Nation is not in an alliance')
        self._peaceful('bloc/'+b.id)
        members=tuple(n for n in b.members if n!=actor)
        if not members:
            self._drop_entity_pacts('bloc/'+b.id);del self._alliances[b.id]
        else:self._alliances[b.id]=replace(b,members=members,chief=members[0] if b.chief==actor else b.chief)
        return b

    def _chief(self, actor):
        b=self.alliance(actor)
        if b is None or b.chief!=actor:raise ValueError('Alliance chief permission required')
        return b

    def _apply_bloc_transfer(self, actor, target, *, approved=False):
        b=self._chief(actor)
        if target not in b.members or target==actor:raise ValueError('Invalid successor')
        self._alliances[b.id]=replace(b,chief=target);return self._alliances[b.id]

    def _apply_bloc_rename(self, actor, name, *, approved=False):
        b=self._chief(actor);self._name(name)
        self._alliances[b.id]=replace(b,name=name);return self._alliances[b.id]

    def _apply_bloc_dissolve(self, actor, *, approved=False):
        b=self._chief(actor);self._peaceful('bloc/'+b.id)
        self._drop_entity_pacts('bloc/'+b.id);del self._alliances[b.id];return b

    def _apply_declare_war(self, actor, target, *, approved=False):
        a,b=self._pair(actor,target)
        if self.war_between(actor,target):raise ValueError('Already at war')
        if any(self.active_truce(x,y) for x in self.members(a) for y in self.members(b)):raise ValueError('Active truce blocks war')
        routed=self._route(actor,'declare_war',{'target':target},approved,self.members(b))
        if routed:return routed
        attackers=list(self.members(a));defenders=list(self.members(b))
        # One hop from the original defending entity, no recursive propagation.
        for entity in self.entities():
            if entity in (a,b):continue
            obliged=any(p.kind=='guarantee' and (p.a,p.b)==(entity,b) or p.kind=='defense' and {p.a,p.b}=={entity,b} for p in self._pacts)
            if obliged and not any(self.active_truce(x,y) for x in self.members(entity) for y in attackers):defenders.extend(self.members(entity))
        war=War(self._next_war,a,b,tuple(attackers),tuple(dict.fromkeys(defenders)))
        self._next_war+=1;self._wars.append(war)
        self._pacts=[p for p in self._pacts if not ({p.a,p.b}=={a,b})]
        self._void_pending(False)
        return war

    def _apply_offer_peace(self, actor, target, kind: str='white', gold: int=0, truce: int=0, note: str='', *, approved=False):
        integer(gold);integer(truce)
        if not isinstance(note,str):raise ValueError('Invalid peace note')
        a,b=self._pair(actor,target)
        w=next((w for w in self._wars if actor in w.attackers and target in w.defenders or actor in w.defenders and target in w.attackers),None)
        if w is None:raise ValueError('Nations are not at war')
        original_a,original_b=(w.a,w.b) if actor in w.attackers else (w.b,w.a)
        if a!=original_a or b!=original_b or actor!=self.representative(a) or target!=self.representative(b):raise ValueError('Only war-leading entity representatives may negotiate')
        if kind not in ('white','pay','demand') or (kind!='white' and not gold) or (kind=='white' and gold):raise ValueError('Invalid peace terms')
        routed=self._route(actor,'offer_peace',{'target':target,'kind':kind,'gold':gold,'truce':truce,'note':note},approved,w.attackers+w.defenders)
        if routed:return routed
        return self._offer('peace',actor,b,{'war':w.id,'kind':kind,'gold':gold,'truce':truce,'note':note},w.attackers+w.defenders)

    def _apply_accept_peace(self, actor, offer: int, *, approved=False):
        integer(offer,1)
        o=next((o for o in self._offers if o.id==offer and o.action=='peace'),None)
        if o is None or self.entity(actor)!=o.target or actor!=self.representative(o.target):raise ValueError('Not the peace representative')
        args=dict(o.arguments);w=next((w for w in self._wars if w.id==args['war']),None)
        if w is None:raise ValueError('War already ended')
        routed=self._route(actor,'accept_peace',{'offer':offer},approved,o.parties)
        if routed:return routed
        if args['gold']:
            source,target=(o.actor,actor) if args['kind']=='pay' else (actor,o.actor)
            self._effects.append(('transfer',source,target,args['gold']))
        self._wars.remove(w);self._offers=[p for p in self._offers if not (p.action=='peace' and dict(p.arguments)['war']==w.id)]
        if args['truce']:
            for x in w.attackers:
                for y in w.defenders:self._truce(self.entity(x),self.entity(y),self._period()+args['truce'])
        self._effects.append(('peace',w.attackers+w.defenders))
        return w

    def _apply_reject_peace(self, actor, offer: int, *, approved=False):
        integer(offer,1)
        o=next((o for o in self._offers if o.id==offer and o.action=='peace' and o.target==self.entity(actor)),None)
        if o is None:raise ValueError('Peace offer does not exist')
        self._offers.remove(o);return o

    def _fork(self):
        draft=copy(self)
        for name in ('_alliances','_pacts','_wars','_truces','_offers','_votes','_approved','_pending','_results'):
            setattr(draft,name,deepcopy(getattr(self,name)))
        draft._effects=[]
        return draft

    def _dispatch(self, actor, action, arguments, *, approved=False):
        self._living(actor);self._policy(actor,action)
        if action not in PRIORITIES:raise ValueError('Unknown diplomatic action')
        method=getattr(self,'_apply_'+action)
        inspect.signature(method).bind(actor,**arguments,approved=approved)
        return method(actor,**arguments,approved=approved)

    def _cost(self, actor, action, arguments):
        if action in ('bloc_rename','bloc_transfer','bloc_dissolve'):return 0
        target=arguments.get('target')
        if target and self.allied(actor,target):return 0
        snapshot=self._call('native:buildings','snapshot')
        sites={s.id:s.owner for s in snapshot.sites}
        count=sum(b.kind=='native:diplomacy-center' and b.remaining == 0 and sites[b.site]==actor for b in snapshot.instances)
        if target and action in ('propose_defense','offer_peace') and any(b.kind=='native:diplomacy-center' and b.remaining == 0 and sites[b.site]==target for b in snapshot.instances):return 0
        fee=self._fee
        for _ in range(count):fee=max(self._minimum,fee//2)
        return fee

    def submit(self, actor, action, **arguments):
        self._write()
        if self._world is not None and self._world.phase!='行动':raise ValueError('Submit only during action phase')
        self._living(actor)
        for c in self._pending:
            if (c.actor,c.action,c.arguments)==(actor,action,tuple(arguments.items())):return freeze(c)
        draft=self._fork();draft._dispatch(actor,action,arguments)
        fee=self._cost(actor,action,arguments)
        stocks={s.id:s.amount for s in self._call('native:resource-inspection','snapshot',actor)}
        if stocks['native:gold']<fee:raise ValueError('Insufficient cash for diplomatic fee')
        targets=[actor]+([arguments['target']] if arguments.get('target') else [])
        if action=='bloc_found':targets.extend(arguments['tos'])
        if arguments.get('offer'):
            targets.extend(n for o in self._offers if o.id==arguments['offer'] for n in o.parties)
        if action=='bloc_join':targets.extend(self._alliances[arguments['bloc']].members)
        entities=tuple((n,self.entity(n)) for n in dict.fromkeys(targets))
        command=Command(self._next_command,actor,action,tuple(deepcopy(arguments).items()),self._period(),fee,entities)
        if fee:self._call('native:resource-exchange','exchange',actor,{'native:gold':fee},{})
        self._next_command+=1;self._pending.append(command)
        return freeze(command)

    def vote(self, actor, vote: int, accept: bool):
        self._write();self._living(actor);integer(vote,1)
        if type(accept) is not bool:raise ValueError('Vote requires boolean ballot')
        v=next((v for v in self._votes if v.id==vote),None)
        if v is None or actor not in v.voters or self._period()!=v.deadline:raise ValueError('Vote is unavailable this period')
        ballots=dict(v.ballots)
        if actor in ballots:raise ValueError('Ballot already cast')
        ballots[actor]=accept;self._votes[self._votes.index(v)]=replace(v,ballots=tuple(ballots.items()))
        return freeze(self._votes[self._votes.index(v)]) if v in self._votes else freeze(replace(v,ballots=tuple(ballots.items())))

    def _result(self, c, status, message):
        result=replace(c,status=status,message=message);self._results.append(result);return result

    def _vote_entities(self, vote):
        return tuple((n,self.entity(n) if n in self._ids() else 'nation/'+n) for n in dict.fromkeys((*vote.voters,*vote.parties)))

    def _void_pending(self, all_kinds):
        # Offers and votes become invalid at war onset; queue commands are
        # revalidated separately and receive their own result/refund.
        self._offers=[o for o in self._offers if o.action=='peace' and not all_kinds]
        kept=[]
        for v in self._votes+self._approved:
            if not all_kinds and v.action in ('declare_war','offer_peace','accept_peace'):kept.append(v)
            else:
                c=Command(self._next_command,v.actor,'vote:'+v.action,v.arguments,self._period(),0,self._vote_entities(v))
                self._next_command+=1;self._result(c,'cancelled','Vote invalidated by war or forced peace')
        self._votes=[v for v in kept if v in self._votes];self._approved=[v for v in kept if v in self._approved]

    def tally(self):
        self._write();results=[]
        for v in tuple(self._votes):
            if v.deadline>self._period():continue
            self._votes.remove(v);bloc=self._alliances.get(v.alliance)
            valid=bloc is not None and bloc.members==v.voters and all(n in self._ids() for n in v.parties)
            yes=sum(value is True for _,value in v.ballots);no=sum(value is False for _,value in v.ballots)
            if valid and yes>no:self._approved.append(v)
            else:
                c=Command(self._next_command,v.actor,'vote:'+v.action,v.arguments,self._period(),0,self._vote_entities(v))
                self._next_command+=1;results.append(self._result(c,'failed' if valid else 'cancelled','Vote rejected or invalidated'))
        return tuple(freeze(r) for r in results)

    def resolve(self):
        self._write();effects=[];results=[]
        tasks=[(PRIORITIES[c.action],c.actor,c.id,c,False) for c in self._pending]
        for v in self._approved:
            c=Command(self._next_command,v.actor,v.action,v.arguments,self._period(),0,self._vote_entities(v))
            self._next_command+=1;tasks.append((PRIORITIES[v.action],v.actor,c.id,c,True))
        self._pending=[];self._approved=[]
        for _,_,_,c,approved in sorted(tasks,key=lambda row:row[:3]):
            try:
                if any(n not in self._ids() or self.entity(n)!=entity for n,entity in c.entities):raise ValueError('Party or diplomatic entity changed')
                draft=self._fork();outcome=draft._dispatch(c.actor,c.action,dict(c.arguments),approved=approved)
                operations=[]
                for effect in draft._effects:
                    if effect[0]=='transfer':
                        _,source,target,amount=effect
                        cash={s.id:s.amount for s in self._call('native:resource-inspection','snapshot',source)}['native:gold']
                        if cash<amount:raise ValueError('Cannot afford peace payment')
                        operations.extend(({'owner':source,'debits':{'native:gold':amount},'credits':{}},
                                           {'owner':target,'debits':{},'credits':{'native:gold':amount}}))
                if operations:self._call('native:resource-exchange','exchange_batch',operations)
                for name in ('_alliances','_pacts','_wars','_truces','_offers','_votes','_approved','_results','_next_command','_next_offer','_next_vote','_next_war'):
                    setattr(self,name,getattr(draft,name))
                effects.extend(draft._effects)
                message='Vote opened; treaty pending' if isinstance(outcome,Vote) else 'Offer opened; acceptance pending' if isinstance(outcome,Offer) else 'Diplomatic action applied'
                results.append(self._result(c,'effective',message))
            except (ValueError,KeyError,TypeError) as exc:
                if c.fee:self._call('native:resource-exchange','exchange',c.actor,{}, {'native:gold':c.fee})
                results.append(self._result(c,'cancelled' if c.actor not in self._ids() else 'failed',str(exc)))
        return tuple(freeze(r) for r in results),tuple(effects)

    def eliminate(self, owner):
        self._write()
        ended_wars=tuple(self._wars)
        ended_participants=tuple(dict.fromkeys(
            nation for war in ended_wars for nation in (*war.attackers,*war.defenders)
            if nation!=owner and nation in self._ids()))
        bloc=self.alliance(owner)
        self._drop_entity_pacts('nation/'+owner)
        if bloc:
            members=tuple(n for n in bloc.members if n!=owner)
            if not members:self._drop_entity_pacts('bloc/'+bloc.id);del self._alliances[bloc.id]
            else:self._alliances[bloc.id]=replace(bloc,members=members,chief=members[0] if bloc.chief==owner else bloc.chief)
        self._wars=[];self._void_pending(True)
        survivors=[n for n in self._ids() if n!=owner]
        if ended_participants:self._call('native:territory','snapshot_cores',ended_participants)
        for i,a in enumerate(survivors):
            for b in survivors[i+1:]:self._truce(self.entity(a),self.entity(b),self._period()+self._fall_truce)
        return tuple(survivors)

    def snapshot(self, actor):
        self._living(actor)
        return DiplomaticSnapshot(tuple(self._alliances.values()),tuple(self._pacts),tuple(self._wars),
            tuple((a,b,until) for (a,b),until in sorted(self._truces.items())),
            tuple(freeze(o) for o in self._offers if actor in o.parties),
            tuple(freeze(v) for v in self._votes if actor in v.voters),
            tuple(freeze(c) for c in self._pending if c.actor==actor),
            tuple(freeze(c) for c in self._results if c.actor==actor or actor in dict(c.entities)))

    def to_save(self):
        def command(c):return {'id':c.id,'actor':c.actor,'action':c.action,'arguments':dict(c.arguments),
            'period':c.period,'fee':c.fee,'entities':dict(c.entities),'status':c.status,'message':c.message}
        def vote(v):return {'id':v.id,'alliance':v.alliance,'actor':v.actor,'action':v.action,'arguments':dict(v.arguments),
            'voters':list(v.voters),'ballots':dict(v.ballots),'deadline':v.deadline,'parties':list(v.parties)}
        return {'alliances':[{'id':b.id,'name':b.name,'members':list(b.members),'chief':b.chief} for b in self._alliances.values()],
            'pacts':[{'kind':p.kind,'a':p.a,'b':p.b} for p in self._pacts],
            'wars':[{'id':w.id,'a':w.a,'b':w.b,'attackers':list(w.attackers),'defenders':list(w.defenders)} for w in self._wars],
            'truces':[{'a':a,'b':b,'until':until} for (a,b),until in sorted(self._truces.items())],
            'offers':[{'id':o.id,'action':o.action,'actor':o.actor,'target':o.target,'arguments':dict(o.arguments),
                       'parties':list(o.parties),'accepted':list(o.accepted)} for o in self._offers],
            'votes':[vote(v) for v in self._votes],'approved':[vote(v) for v in self._approved],
            'pending':[command(c) for c in self._pending],'results':[command(c) for c in self._results],
            'next_command':self._next_command,'next_offer':self._next_offer,'next_vote':self._next_vote,'next_war':self._next_war}

    def restore(self, data):
        self._write()
        expected={'alliances','pacts','wars','truces','offers','votes','approved','pending','results','next_command','next_offer','next_vote','next_war'}
        if not isinstance(data,dict) or set(data)!=expected:raise ValueError('Invalid diplomacy save')
        draft=self._fork()
        for key in expected-{'next_command','next_offer','next_vote','next_war'}:
            if not isinstance(data[key],list):raise ValueError('Invalid diplomacy records')
        def fields(row, expected):
            if not isinstance(row,dict) or set(row)!=set(expected):raise ValueError('Invalid diplomacy record fields')
        def nations(values):
            if not isinstance(values,list) or not values or len(set(values))!=len(values):raise ValueError('Invalid nation list')
            for n in values:self._living(n)
            return tuple(values)
        draft._alliances={};occupied=set()
        for row in data['alliances']:
            fields(row,('id','name','members','chief'));content_id(row['id'])
            members=nations(row['members']);draft._name(row['name'])
            if row['id'] in draft._alliances or row['chief'] not in members or occupied.intersection(members):raise ValueError('Invalid alliance membership')
            occupied.update(members);draft._alliances[row['id']]=Alliance(row['id'],row['name'],members,row['chief'])
        draft._pacts=[]
        for row in data['pacts']:
            fields(row,('kind','a','b'));p=Pact(**row)
            if p.kind not in ('guarantee','defense') or p.a==p.b or not draft.members(p.a) or not draft.members(p.b) or p in draft._pacts:raise ValueError('Invalid pact')
            draft._pacts.append(p)
        draft._wars=[]
        for row in data['wars']:
            fields(row,('id','a','b','attackers','defenders'));integer(row['id'],1)
            a,b=nations(row['attackers']),nations(row['defenders'])
            if set(a)&set(b) or not draft.members(row['a']) or not draft.members(row['b']):raise ValueError('Invalid war sides')
            draft._wars.append(War(row['id'],row['a'],row['b'],a,b))
        draft._truces={}
        for row in data['truces']:
            fields(row,('a','b','until'));integer(row['until'])
            if not all(isinstance(row[k],str) and row[k].startswith(('nation/','bloc/')) for k in ('a','b')) or row['a']==row['b']:raise ValueError('Invalid truce')
            pair=tuple(sorted((row['a'],row['b'])))
            if pair in draft._truces:raise ValueError('Duplicate truce')
            draft._truces[pair]=row['until']
        draft._offers=[]
        for row in data['offers']:
            fields(row,('id','action','actor','target','arguments','parties','accepted'));integer(row['id'],1);self._living(row['actor'])
            if row['action'] not in ('found','defense','peace') or not isinstance(row['arguments'],dict):raise ValueError('Invalid offer action')
            parties=nations(row['parties'])
            if not isinstance(row['accepted'],list) or len(set(row['accepted']))!=len(row['accepted']) or set(row['accepted'])-set(parties):raise ValueError('Invalid accepted members')
            args=deepcopy(row['arguments'])
            if row['action']=='found':args['members']=tuple(args['members'])
            draft._offers.append(Offer(row['id'],row['action'],row['actor'],row['target'],tuple(args.items()),parties,tuple(row['accepted'])))
        for key in ('votes','approved'):
            records=[]
            for row in data[key]:
                fields(row,('id','alliance','actor','action','arguments','voters','ballots','deadline','parties'));integer(row['id'],1);integer(row['deadline'])
                if row['action'] not in PRIORITIES or not isinstance(row['arguments'],dict) or not isinstance(row['ballots'],dict):raise ValueError('Invalid vote action')
                voters=nations(row['voters']);parties=nations(row['parties'])
                if row['alliance'] not in draft._alliances or set(row['ballots'])-set(voters) or any(type(v) is not bool for v in row['ballots'].values()):raise ValueError('Invalid vote electorate')
                records.append(Vote(row['id'],row['alliance'],row['actor'],row['action'],tuple(deepcopy(row['arguments']).items()),voters,tuple(row['ballots'].items()),row['deadline'],parties))
            setattr(draft,'_'+key,records)
        for key in ('pending','results'):
            records=[]
            for row in data[key]:
                fields(row,('id','actor','action','arguments','period','fee','entities','status','message'))
                integer(row['id'],1);integer(row['period']);integer(row['fee']);self._call('native:nation-inspection','get',row['actor'])
                if row['status'] not in ('submitted','effective','failed','cancelled') or not isinstance(row['arguments'],dict) or not isinstance(row['entities'],dict) or not isinstance(row['message'],str):raise ValueError('Invalid command')
                if key=='pending' and (row['status']!='submitted' or row['action'] not in PRIORITIES):raise ValueError('Invalid pending action')
                if not row['action'].removeprefix('vote:') in PRIORITIES:raise ValueError('Invalid command action')
                records.append(Command(row['id'],row['actor'],row['action'],tuple(deepcopy(row['arguments']).items()),row['period'],row['fee'],tuple(row['entities'].items()),row['status'],row['message']))
            setattr(draft,'_'+key,records)
        for group,next_key in ((draft._pending+draft._results,'next_command'),(draft._offers,'next_offer'),(draft._votes+draft._approved,'next_vote'),(draft._wars,'next_war')):
            integer(data[next_key],1)
            if len({r.id for r in group})!=len(group) or any(r.id>=data[next_key] for r in group):raise ValueError('Invalid diplomacy sequence')
            setattr(draft,'_'+next_key,data[next_key])
        for name in ('_alliances','_pacts','_wars','_truces','_offers','_votes','_approved','_pending','_results','_next_command','_next_offer','_next_vote','_next_war'):
            setattr(self,name,getattr(draft,name))

    def __deepcopy__(self,memo):return self.to_save()
