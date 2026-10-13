from engine.runtime import GameMod, Panel, Stage, Tool
from .objects import Intelligence


class IntelligenceMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('intelligence',)
    def needs(self):return ('native:nation-inspection','native:government','native:resource-exchange','native:resource-inspection','native:buildings','native:diplomacy','native:vision','native:army')
    def provides(self):return ('native:intelligence',)
    def numbers(self):return {'intelligence.'+key:value for key,value in self.declaration['Intelligence'].items()}
    def initialize_world(self, world):
        world.intelligence=Intelligence(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,cap:self.runtime.permits(world,actor,cap),self.declaration['Intelligence'])
    def objects(self, world):return {'native:intelligence':world.intelligence}
    def save_state(self, world):return world.intelligence.to_save()
    def load_state(self, world, state):world.intelligence.restore(state)
    def tools(self):
        return (Tool('send_letter',self._letter),Tool('gift',self._gift),Tool('share_map',self._map),Tool('spy',self._spy),
                Tool('intel',lambda w,a:w.intelligence.messages(a),read_only=True),
                Tool('news',lambda w,a:w.intelligence.events(a),read_only=True),
                Tool('dispatches',lambda w,a:w.intelligence.pending(a),read_only=True))
    @staticmethod
    def _letter(world, actor, target: str, text: str):return world.intelligence.submit(actor,target,'letter',text=text)
    @staticmethod
    def _gift(world, actor, target: str, resource: str, quantity: int):return world.intelligence.submit(actor,target,'gift',resource=resource,quantity=quantity)
    @staticmethod
    def _map(world, actor, target: str):return world.intelligence.submit(actor,target,'map')
    @staticmethod
    def _spy(world, actor, target: str):return world.intelligence.submit(actor,target,'spy')
    def panels(self):return (Panel('intel',lambda w,a:w.intelligence.messages(a)),Panel('news',lambda w,a:w.intelligence.events(a)))
    def stages(self):return (Stage('native:intelligence.deliver',lambda w,c:c.update(deliveries=w.intelligence.deliver()),hook='cycle.start'),
        Stage('native:intelligence.dispatch',self._dispatch,hook='relations',needs=('relations.applied',),provides=('dispatches.started',)),
        Stage('native:intelligence.events',self._events,hook='cycle.end',needs=('actors.eliminated',),before=('native:government.advance',)))
    @staticmethod
    def _dispatch(world, context):
        results,facts=world.intelligence.resolve();context['dispatch_results']=results;context.setdefault('economic_records',[]).extend(facts)
    @staticmethod
    def _events(world, context):
        for c in context.get('vote_results',()):world.intelligence.emit('diplomacy',c.message,parties=tuple(n for n,_ in c.entities)+(c.actor,))
        for c in context.get('diplomacy_results',()):world.intelligence.emit('diplomacy',c.message,parties=tuple(n for n,_ in c.entities)+(c.actor,),public=c.status=='effective' and c.message=='Diplomatic action applied' and c.action in ('declare_war','accept_peace','accept_proposal','guarantee','cancel_guarantee','break_defense','bloc_leave','bloc_dissolve','bloc_rename','bloc_transfer'))
        for event in context.get('domain_events',()):world.intelligence.emit(**event)
        for owner in context.get('extinct_nations',()):world.intelligence.emit('extinction',owner,public=True)
