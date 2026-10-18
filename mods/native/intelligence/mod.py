from engine.runtime import GameMod, Stage
from .api import IntelligenceAPI
from .objects import Intelligence


class IntelligenceMod(GameMod):
    def mixins(self):return ()
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
    def player_api(self, world):return IntelligenceAPI(world)
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
        for owner,resource,quantity,loss in context.get('market_churn',()):world.intelligence.emit('market',f'{resource} 本期买卖重叠空转{quantity}单位，按成交均价折算净亏{loss:g}金（负值为获利）——同商品又买又卖＝白付两趟价差并自己推价，要买就直接买、要卖就直接卖。',parties=(owner,))
        for report in context.get('economy_publications',()):world.intelligence.emit('economy',f'第{report.start}—{report.end}期经济报表已公布，可用 report 查询本国、buy_report 购买别国。',parties=(report.owner,))
        for event in context.get('domain_events',()):world.intelligence.emit(**event)
        for owner in context.get('extinct_nations',()):world.intelligence.emit('extinction',owner,public=True)
