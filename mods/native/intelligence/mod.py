from engine.runtime import GameMod, Panel, Stage, Tool
from .objects import Intelligence


class IntelligenceMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('intelligence',)
    def needs(self):return ('native:territory','native:nation-inspection','native:government','native:resource-exchange','native:resource-inspection','native:buildings','native:diplomacy','native:vision','native:army')
    def optional(self):return ('economy:queries',)
    def provides(self):return ('native:intelligence',)
    def numbers(self):return {'intelligence.'+key:value for key,value in self.declaration['Intelligence'].items()}
    def initialize_world(self, world):
        world.intelligence=Intelligence(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,cap:self.runtime.permits(world,actor,cap),self.declaration['Intelligence'])
    def objects(self, world):return {'native:intelligence':world.intelligence}
    def save_state(self, world):return world.intelligence.to_save()
    def load_state(self, world, state):world.intelligence.restore(state)
    def pending_archive(self,world):return world.intelligence.pending_archive()
    def archive_committed(self,world):world.intelligence.archive_committed()
    def restore_archive(self,world,rows):world.intelligence.restore_archive(rows)
    def tools(self):
        return (Tool('send_letter',self._letter,description=f'向 countries 中的别国发送正文；提交时扣费，结算派发，下一周期送达 intel。起步价非盟友{self.declaration["Intelligence"]["LetterBase"]}金、盟友{self.declaration["Intelligence"]["LetterAlly"]}金，外交中心可减免；前{self.declaration["Intelligence"]["FreeCharacters"]}字免费，超出每{self.declaration["Intelligence"]["CharactersPerGold"]}字加1金，下限{self.declaration["Intelligence"]["LetterMinimum"]}金，实际费用看回执。'),Tool('gift',self._gift,content_parameters=('resource',),description='向别国赠送正整数数量的本国资源；先预留物资和费用，结算派发，下周期到账。不能向自己赠送，submitted 不等于对方已收到。'),Tool('share_map',self._map,description='把本国可见地图发给别国；结算采样，下一周期对方在 intel 收到带日期情报。盟友免基础费用，不能给自己发图。'),Tool('spy',self._spy,description=f'派间谍刺探别国，费用{self.declaration["Intelligence"]["SpyFee"]}金，延迟{self.declaration["Intelligence"]["SpyDelay"]}周期在 intel 返回有日期的库存、已知地图、外交和兵种数量，不泄露军队位置与编号。'),
                Tool('intel',self._intel,read_only=True,parameters={'type':'object','properties':{'kind':{'type':'string','enum':['letter','gift','map','spy','observer-letter']}},'additionalProperties':False},description='本国收到的信件、物资通知、共享地图、间谍情报和神秘人来信；可用 kind=letter/gift/map/spy/observer-letter 筛选，省略查全部。注意 sampled 日期，不把旧情报当当前事实。'),
                Tool('news',lambda w,a:w.intelligence.events(a),read_only=True,description='本国可见的最近10周期近讯、外交、战况和观察者公告；旧事件保存于存档旁的历史归档；player_events(cursor) 可只取游标后的新增事件。'),
                Tool('dispatches',lambda w,a:w.intelligence.pending(a),read_only=True,description='本国所有待结算和在途通信/馈赠/间谍，包含状态与预计送达周期；无需目标或坐标。'),
                Tool('countries',self._countries,read_only=True,description='一次列出除本国外的全部在世外交对象：国家 id、名称、关系、接壤、来信和战争主导实体代表。target 使用国家 id，不能对自己做外交。'))
    @staticmethod
    def _intel(world,actor,kind: str | None=None):
        if kind is not None and kind not in ('letter','gift','map','spy','observer-letter'):raise ValueError('未知情报类别')
        return tuple(m for m in world.intelligence.messages(actor) if kind is None or m.kind==kind)
    @staticmethod
    def _countries(world,actor):
        call=lambda cap,method,*args:world.runtime.object_call(world,cap,method,*args)
        relation=call('native:diplomacy','snapshot',actor);visible=call('native:vision','tiles',actor)
        sites=call('native:buildings','snapshot').sites;owned=[s.position for s in sites if s.owner==actor]
        messages=world.intelligence.messages(actor)
        def entity(n):
            bloc=next((b for b in relation.alliances if n in b.members),None)
            return ('bloc/'+bloc.id,bloc.chief) if bloc else ('nation/'+n,n)
        my_entity,_=entity(actor);result=[]
        for target in call('native:nation-inspection','ids'):
            if target==actor:continue
            nation=call('native:nation-inspection','get',target);other,chief=entity(target)
            war=next((w for w in relation.wars if actor in w.attackers and target in w.defenders or actor in w.defenders and target in w.attackers),None)
            lead=(war.b if actor in war.attackers else war.a) if war else None
            representative=next((b.chief for b in relation.alliances if 'bloc/'+b.id==lead),lead[7:] if lead and lead.startswith('nation/') else None)
            treaties=[{'kind':p.kind,'direction':'outgoing' if p.a==my_entity else 'incoming'} for p in relation.pacts
                      if {p.a,p.b}=={my_entity,other} and my_entity!=other]
            result.append({'id':target,'name':nation.name,'relation':'war' if war else 'ally' if my_entity==other else 'neutral',
                           'entity':other,'chief':chief,'war_representative':representative,
                           'treaties':treaties,'letter_count':sum(m.source==target and m.kind=='letter' for m in messages),
                           'border':any(t.owner==target and any(max(abs(t.position[0]-x),abs(t.position[1]-y))<=1 for x,y in owned) for t in visible),
                           'has_letter':any(m.source==target and m.kind=='letter' for m in messages)})
        return result
    @staticmethod
    def _letter(world, actor, target: str, text: str):return world.intelligence.submit(actor,target,'letter',text=text)
    @staticmethod
    def _gift(world, actor, target: str, resource: str, quantity: int):return world.intelligence.submit(actor,target,'gift',resource=resource,quantity=quantity)
    @staticmethod
    def _map(world, actor, target: str):return world.intelligence.submit(actor,target,'map')
    @staticmethod
    def _spy(world, actor, target: str):return world.intelligence.submit(actor,target,'spy')
    def panels(self):return (Panel('intel',lambda w,a:w.intelligence.messages(a)),Panel('news',lambda w,a:w.intelligence.events(a)))
    def stages(self):return (Stage('native:intelligence.deliver',lambda w,c:c.update(deliveries=w.intelligence.deliver()),hook='cycle.end',before=('native:government.advance',)),
        Stage('native:intelligence.dispatch',self._dispatch,hook='relations',needs=('relations.applied',),provides=('dispatches.started',)),
        Stage('native:intelligence.events',self._events,hook='cycle.end',needs=('actors.eliminated',),before=('native:government.advance',)))
    @staticmethod
    def _dispatch(world, context):
        results,facts=world.intelligence.resolve();context['dispatch_results']=results;context.setdefault('economic_records',[]).extend(facts)
    @staticmethod
    def _events(world, context):
        for c in context.get('vote_results',()):world.intelligence.emit('diplomacy',c.message,parties=tuple(n for n,_ in c.entities)+(c.actor,))
        for c in context.get('diplomacy_results',()):
            public=c.status=='effective' and c.message=='Diplomatic action applied' and c.action in ('declare_war','accept_peace','accept_proposal','guarantee','cancel_guarantee','break_defense','bloc_leave','bloc_dissolve','bloc_rename','bloc_transfer')
            details={'actor':c.actor,'action':c.action,'arguments':dict(c.arguments),'status':c.status,'message':c.message}
            if public:details['importance']='major'
            world.intelligence.emit('diplomacy',c.message,parties=tuple(n for n,_ in c.entities)+(c.actor,),
                public=public,details=details)
        for effect in context.get('diplomacy_effects',()):
            if effect[0]=='notice':world.intelligence.emit('diplomacy',effect[1],parties=effect[2],public=effect[3],
                details={'importance':'major'} if effect[3] else None)
        for owner,resource,quantity,loss in context.get('market_churn',()):world.intelligence.emit('market',f'{resource} 本期买卖重叠空转{quantity}单位，按成交均价折算净亏{loss:g}金（负值为获利）。',parties=(owner,))
        for report in context.get('economy_publications',()):world.intelligence.emit('economy',f'第{report.start}—{report.end}期经济报表已公布，可用 report 查询本国、buy_report 购买别国。',parties=(report.owner,))
        for event in context.get('domain_events',()):world.intelligence.emit(**event)
        for owner in context.get('extinct_nations',()):world.intelligence.emit('extinction',owner,public=True)
