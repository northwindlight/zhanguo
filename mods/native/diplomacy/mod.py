from engine.runtime import GameMod, Panel, RuleSection, Stage, Tool
from .objects import Diplomacy


ARGUMENTS = {
    'guarantee':{'target':str}, 'cancel_guarantee':{'target':str},
    'propose_defense':{'target':str}, 'break_defense':{'target':str},
    'accept_proposal':{'offer':int}, 'reject_proposal':{'offer':int},
    'bloc_found':{'bloc':str,'name':str,'tos':list[str]}, 'bloc_join':{'bloc':str},
    'bloc_leave':{}, 'bloc_dissolve':{}, 'bloc_transfer':{'target':str}, 'bloc_rename':{'name':str},
    'declare_war':{'target':str}, 'offer_peace':{'target':str,'kind':str,'gold':int,'truce':int},
    'accept_peace':{'offer':int}, 'reject_peace':{'offer':int},
}


class DiplomacyMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('diplomacy',)
    def needs(self):return ('native:nation-inspection','native:government','native:resource-inspection','native:resource-exchange','native:buildings')
    def provides(self):return ('native:diplomacy',)
    def numbers(self):return {'diplomacy.'+key:value for key,value in self.declaration['Diplomacy'].items()}
    def initialize_world(self, world):
        cfg=self.declaration['Diplomacy']
        world.diplomacy = Diplomacy(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,cap:self.runtime.permits(world,actor,cap),cfg['Fee'],cfg['MinimumFee'],cfg['NameMax'],cfg['FallTruce'])
    def objects(self, world):return {'native:diplomacy':world.diplomacy}
    def save_state(self, world):return world.diplomacy.to_save()
    def load_state(self, world, state):world.diplomacy.restore(state)
    def _tool(self, action, arguments):
        def submit(world, actor, **args):return world.diplomacy.submit(actor,action,**args)
        properties={key:({'type':'array','items':{'type':'string'}} if value==list[str] else {'type':'integer' if value is int else 'string'}) for key,value in arguments.items()}
        required=[key for key in arguments if not (action=='offer_peace' and key in ('kind','gold','truce'))]
        return Tool(action,submit,argument_types=arguments,parameters={'type':'object','properties':properties,'required':required,'additionalProperties':False},
                    description='Submit for diplomatic settlement; accepted requests are not yet effective')
    def tools(self):
        return (*(self._tool(action,args) for action,args in ARGUMENTS.items()),
                Tool('vote',self._vote),Tool('diplomacy',lambda w,a:w.diplomacy.snapshot(a),read_only=True))
    @staticmethod
    def _vote(world, actor, vote: int, accept: bool):return world.diplomacy.vote(actor,vote,accept)
    def panels(self):return (Panel('diplomacy',lambda w,a:w.diplomacy.snapshot(a)),)
    def stages(self):return (Stage('native:diplomacy.tally',self._tally,hook='cycle.start',provides=('votes.counted',)),
                            Stage('native:diplomacy.resolve',self._resolve,hook='relations',needs=('votes.counted',),provides=('relations.applied',)))
    @staticmethod
    def _tally(world, context):context['vote_results']=world.diplomacy.tally()
    @staticmethod
    def _resolve(world, context):
        results,effects=world.diplomacy.resolve()
        context['diplomacy_results']=results;context['diplomacy_effects']=effects
        for result in results:
            if result.status=='effective' and result.fee:
                context.setdefault('economic_records',[]).append({'id':f'native:diplomatic-fee/{result.id}',
                    'owner':result.actor,'period':world.runtime.object_call(world,'native:government','period'),
                    'category':'administration','inputs':(('native:gold',result.fee),)})
    def rule_sections(self, world=None):
        return (RuleSection('外交实体与提交','独立国家、联盟是条约实体。外交在行动期提交并预留费用，在外交挂点重新校验后生效或退款。联盟表决在发起后下一周期末统一计票，未投票弃权，赞成票多于反对票通过；宣战仅传导原防守实体的直接保障与共同防御。'),)
