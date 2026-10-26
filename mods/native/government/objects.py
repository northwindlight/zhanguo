"""回合、国策和玩家小结的所有者；不代持经济或外交状态。"""
from dataclasses import dataclass
from engine.building import integer
from engine.runtime import ModError
from engine.state import active_unit


@dataclass(frozen=True)
class Plan:
    owner: str
    text: str
    period: int
    valid_until: int


@dataclass(frozen=True)
class Summary:
    owner: str
    period: int
    text: str


@dataclass(frozen=True)
class GovernmentSnapshot:
    period: int
    plan: Plan | None
    ended: bool
    summaries: tuple


class Government:
    def __init__(self, nation_call, plan_span, summary_min):
        self._nation = nation_call
        self._span, self._minimum = integer(plan_span,1), integer(summary_min,1)
        self._period, self._plans, self._summaries, self._ended = 0, {}, [], set()
        self._humans = set()
        self._world, self._domain = None, None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Government already bound')
        self._world, self._domain = world, world.runtime.state_owners[key]
        return self

    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use government interface')

    def period(self):return self._period

    def set_human(self, owner):
        """Trusted session binding; never a player-selected exemption."""
        self._write();self._actor(owner);self._humans.add(owner)

    def human_actors(self):return frozenset(self._humans)

    def _actor(self, owner):
        if owner not in self._nation('ids'):raise ValueError('Nation is not alive')

    def plan(self, owner, text):
        self._write();self._actor(owner)
        if not isinstance(text,str) or not text.strip():raise ValueError('Plan cannot be empty')
        if owner in self._ended:raise ValueError('Nation already ended its action')
        current=self._plans.get(owner)
        # 同样的正文再交一遍不该重置「第 N 回合制定」与到期日：模型偶尔会为了
        # 「确保国策有效」重复提交，原实现会白白续期，还会（实验版）再开一轮议会审核。
        if current is not None and current.text.strip()==text.strip() and current.valid_until>=self._period:
            return current
        record = Plan(owner,text,self._period,self._period+self._span-1)
        self._plans[owner] = record
        return record

    def end_turn(self, owner, text):
        self._write();self._actor(owner)
        if owner in self._ended:raise ValueError('Nation already ended its action')
        if owner in self._humans:
            self._ended.add(owner)
            return {'owner':owner,'period':self._period,'ended':True,'control':'human'}
        plan = self._plans.get(owner)
        if plan is None or plan.valid_until<self._period:raise ValueError('Update the national plan first')
        if not isinstance(text,str) or len(text.strip())<self._minimum:raise ValueError('Summary is too short')
        result = Summary(owner,self._period,text)
        self._summaries.append(result);self._ended.add(owner)
        return result

    def can_act(self, owner):return owner in self._nation('ids') and owner not in self._ended

    def snapshot(self, owner):
        self._nation('get',owner)
        return GovernmentSnapshot(self._period,self._plans.get(owner),owner in self._ended,
                                  tuple(row for row in self._summaries if row.owner==owner))

    def advance(self):
        self._write();self._period += 1;self._ended.clear()
        return self._period

    def to_save(self):
        return {'period':self._period,
                'plans':[{'owner':p.owner,'text':p.text,'period':p.period,'valid_until':p.valid_until} for p in self._plans.values()],
                'summaries':[{'owner':s.owner,'period':s.period,'text':s.text} for s in self._summaries],
                'ended':sorted(self._ended),'humans':sorted(self._humans)}

    def restore(self, data):
        self._write()
        if not isinstance(data,dict) or set(data)!={'period','plans','summaries','ended','humans'}:raise ValueError('Invalid government save')
        period = integer(data['period'])
        if any(not isinstance(data[k],list) for k in ('plans','summaries','ended','humans')):raise ValueError('Invalid government lists')
        humans=set()
        for owner in data['humans']:
            self._nation('get',owner)
            if owner in humans:raise ValueError('Duplicate human actor')
            humans.add(owner)
        plans,summaries,ended = {},[],set()
        for row in data['plans']:
            if not isinstance(row,dict) or set(row)!={'owner','text','period','valid_until'}:raise ValueError('Invalid plan')
            p = Plan(**row);self._nation('get',p.owner);integer(p.period);integer(p.valid_until)
            if p.owner in plans or p.period>period or p.valid_until!=p.period+self._span-1 or not isinstance(p.text,str) or not p.text.strip():raise ValueError('Invalid plan values')
            plans[p.owner] = p
        seen = set()
        for row in data['summaries']:
            if not isinstance(row,dict) or set(row)!={'owner','period','text'}:raise ValueError('Invalid summary')
            s = Summary(**row);self._nation('get',s.owner);integer(s.period)
            if s.period>period or (s.owner,s.period) in seen or not isinstance(s.text,str) or len(s.text.strip())<self._minimum:raise ValueError('Invalid summary values')
            seen.add((s.owner,s.period));summaries.append(s)
        for owner in data['ended']:
            self._nation('get',owner)
            if owner in ended or owner not in humans and (owner,period) not in seen:raise ValueError('Invalid ended actors')
            ended.add(owner)
        self._period,self._plans,self._summaries,self._ended = period,plans,summaries,ended
        self._humans=humans

    def __deepcopy__(self,memo):return self.to_save()
