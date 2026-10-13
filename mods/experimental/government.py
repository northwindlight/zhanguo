"""Parliament government owns drafts and decisions; review orchestration belongs to the distribution session."""
from copy import deepcopy
from engine.player import wire
from engine.runtime import Tool,RuleSection
from mods.native.government.mod import GovernmentMod
from mods.native.government.objects import Government,Summary


class ParliamentGovernment(Government):
    def __init__(self,*args):
        super().__init__(*args)
        self._reviews={}

    def enroll(self,owner):
        self._write();self._actor(owner)
        self._reviews.setdefault(owner,{'submitted':None,'draft':None,'approved':None,
            'reason':'','explanation':'','streak':0,'last_rejection':None,'due':None,
            'reign':1,'transition':None})

    def plan(self,owner,text):
        self._write();self._actor(owner)
        if owner in self._ended:raise ValueError('Nation already ended its action')
        if not isinstance(text,str) or not text.strip():raise ValueError('Plan cannot be empty')
        # One submission per country and period, including rule clients.
        row=self._reviews.get(owner)
        if row is None:raise ValueError('发行版尚未绑定议会服务，不能直接批准国策')
        if row['submitted']==self._period:raise ValueError('每回合只能提交一次国策；下一回合再提交')
        row.update(submitted=self._period,draft=text,approved=None,reason='',explanation='')
        return {'text':text,'period':self._period,'status':'submitted','message':'国策已提交议会审核'}

    def decide(self,owner,approved,reason):
        self._write();self._actor(owner)
        row=self._reviews[owner]
        if type(approved) is not bool or not isinstance(reason,str) or not reason.strip():raise ValueError('Invalid council decision')
        if row['submitted']!=self._period or row['approved'] is not None:raise ValueError('No unreviewed draft')
        row.update(approved=approved,reason=reason)
        if approved:
            super().plan(owner,row['draft'])
            row.update(streak=0,last_rejection=None,due=None)
            message='议会通过了国策。'
        else:
            row['streak']=row['streak']+1 if row['last_rejection']==self._period-1 else 1
            row.update(last_rejection=self._period,due=self._period+1)
            message='议会没有批准君主的国策，贵族们对这份国策不满：'+reason+'。要求君主解释，并在下回合给出新的国策。'
        return {'approved':approved,'reason':reason,'streak':row['streak'],'message':message}

    def explain(self,owner,text):
        self._write();self._actor(owner)
        row=self._reviews.get(owner)
        if not row or row['submitted']!=self._period or row['approved'] is not False:raise ValueError('没有本回合被打回的国策')
        if not isinstance(text,str) or len(text.strip())<self._minimum:raise ValueError('解释不能为空或过短')
        row['explanation']=text
        return {'text':text,'message':'议会已收到君主解释；解释不替代下回合的新国策'}

    def replace(self,owner):
        self._write();self._actor(owner)
        row=self._reviews[owner]
        if row['streak']!=3 or row['approved'] is not False:raise ValueError('君主尚未达到罢免条件')
        self._plans.pop(owner,None)
        row.update(streak=0,last_rejection=None,reign=row['reign']+1,transition=self._period,
                   draft=None,explanation='',due=self._period+1)
        return {'reign':row['reign'],'message':'上一任君主被愤怒的贵族们罢免，你被推举为新的君主，全权接管这个国家，现在是你的回合。'}

    def end_turn(self,owner,text):
        row=self._reviews.get(owner)
        if row is None:return super().end_turn(owner,text)
        self._write();self._actor(owner)
        if owner in self._ended:raise ValueError('Nation already ended its action')
        if row['submitted']==self._period and row['approved'] is None:raise ValueError('等待议会审核国策')
        transition=row['transition']==self._period
        rejected=row['submitted']==self._period and row['approved'] is False
        if rejected and not transition and not row['explanation']:raise ValueError('议会要求君主先 explain_plan 解释未获批准的国策')
        if not (transition or rejected):
            if row['due'] is not None and row['due']<=self._period:raise ValueError('本回合必须提交新国策供议会审核')
            return super().end_turn(owner,text)
        if not isinstance(text,str) or len(text.strip())<self._minimum:raise ValueError('Summary is too short')
        result=Summary(owner,self._period,text)
        self._summaries.append(result);self._ended.add(owner)
        return result

    def snapshot(self,owner):
        result=wire(super().snapshot(owner))
        result['parliament']=deepcopy(self._reviews.get(owner))
        review=self._reviews.get(owner)
        if review and review['transition'] is not None:
            result['summaries']=[s for s in result['summaries'] if s['period']>=review['transition']]
        return result

    def to_save(self):return {'government':super().to_save(),'reviews':deepcopy(self._reviews)}

    def restore(self,data):
        if not isinstance(data,dict) or set(data)!={'government','reviews'} or not isinstance(data['reviews'],dict):raise ValueError('Invalid parliament save')
        super().restore(data['government'])
        fields={'submitted','draft','approved','reason','explanation','streak','last_rejection','due','reign','transition'}
        for owner,row in data['reviews'].items():
            self._nation('get',owner)
            if not isinstance(row,dict) or set(row)!=fields:raise ValueError('Invalid review record')
            if type(row['streak']) is not int or not 0<=row['streak']<=3 or type(row['reign']) is not int or row['reign']<1:raise ValueError('Invalid council counters')
            for key in ('submitted','last_rejection','due','transition'):
                if row[key] is not None and (type(row[key]) is not int or row[key]<0 or row[key]>self._period+(key=='due')):raise ValueError('Invalid council period')
            if row['approved'] is not None and type(row['approved']) is not bool:raise ValueError('Invalid approval')
            if any(not isinstance(row[key],str) for key in ('reason','explanation')) or (row['draft'] is not None and not isinstance(row['draft'],str)):raise ValueError('Invalid council text')
        self._reviews=deepcopy(data['reviews'])


class ParliamentGovernmentMod(GovernmentMod):
    def initialize_world(self,world):
        cfg=self.declaration['Government']
        world.government=ParliamentGovernment(lambda method,*args:self.runtime.object_call(world,'native:nation-inspection',method,*args),cfg['PlanSpan'],cfg['SummaryMin'])

    def tools(self):
        return (*super().tools(),Tool('explain_plan',lambda w,a,text:w.government.explain(a,text)))

    def rule_sections(self,world=None):
        return (*super().rule_sections(world),RuleSection('议会国策审核',
            '每国每回合只能提交一次国策。第一版直接生效，只告知君主存在议会；从第一次修订起，议会按旧国策及实际行为审核，再审新版。未通过必须 explain_plan 解释，下回合重提。连续三个回合未通过罢免君主。续档或接任不恢复第一版豁免，接任不重置本回合提交次数。'))
