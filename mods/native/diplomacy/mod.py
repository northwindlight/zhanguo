from engine.runtime import GameMod, Panel, RuleSection, Stage, Tool
from .objects import Diplomacy


ARGUMENTS = {
    'guarantee':{'target':str}, 'cancel_guarantee':{'target':str},
    'propose_defense':{'target':str}, 'break_defense':{'target':str},
    'accept_proposal':{'offer':int}, 'reject_proposal':{'offer':int},
    'bloc_found':{'bloc':str,'name':str,'tos':list[str]}, 'bloc_join':{'bloc':str},
    'bloc_leave':{}, 'bloc_dissolve':{}, 'bloc_transfer':{'target':str}, 'bloc_rename':{'name':str},
    'declare_war':{'target':str}, 'offer_peace':{'target':str,'kind':str,'gold':int,'truce':int,'note':str},
    'accept_peace':{'offer':int}, 'reject_peace':{'offer':int},
}


DESCRIPTIONS={
 'guarantee':'保障对方外交实体：对方被宣战时承担直接防守参战义务；双方须和平，联盟须先表决。',
 'cancel_guarantee':'取消本方对目标实体的保障，战时条约冻结；联盟须先表决。',
 'propose_defense':'提出共同防御邀约；双方实体须和平。联盟先表决，接受后才缔结，主动进攻不因此召来伙伴。',
 'break_defense':'解除与目标实体的共同防御，战时不能解除；联盟须先表决。',
 'accept_proposal':'接受 diplomacy.offers 的共同防御或创始邀约，offer 为整数编号；联盟创始须所有受邀国接受，共同防御可能先转联盟表决。',
 'reject_proposal':'拒绝收到的共同防御或创始邀约，offer 为 diplomacy.offers 的整数编号。',
 'bloc_found':'邀请独立、和平国家创立联盟；bloc 是自定且唯一的命名空间键（如 scenario:union），name 是无空白显示名，tos 是别国国家键数组，不能含自己。全体接受后成立。',
 'bloc_join':'申请加入 diplomacy.alliances 中的联盟，bloc 使用该联盟 id；双方须和平，现成员投票通过后才入盟。',
 'bloc_leave':'退出当前联盟；战时不能退出，退盟并非脱离战争的办法。',
 'bloc_dissolve':'盟主解散和平时期的联盟；战时不能解散。',
 'bloc_transfer':'盟主把领导权转交给现有其他成员，target 使用成员的国家键。',
 'bloc_rename':'盟主修改联盟显示名；不能有空白或与其他联盟重名。',
 'declare_war':'向另一外交实体宣战；休战期间不能宣战，联盟先表决。生效时扩展双方实体成员及原防守实体的直接保障/共同防御；之后 attack 不重复传播宣战。',
 'offer_peace':'向敌方战争主导实体代表求和，双方主导者都可发起；普通跟随方不能代谈。kind=white 白和、pay 本方赔款、demand 对方赔款；后两种 gold>0，白和 gold=0；truce 为休战周期数。联盟先表决，接受后整条战线停战。',
 'accept_peace':'由收到提议的战争代表接受 diplomacy.offers 中的和平提议，offer 为整数编号；联盟可能先表决。',
 'reject_peace':'拒绝收到的和平提议，offer 为 diplomacy.offers 中的整数编号，战争继续。',
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
        for key,prop in properties.items():
            if key in ('offer',):prop.update(minimum=1,description='diplomacy.offers 中的提议编号')
            elif key=='target':prop['description']='countries 中的别国 id；求和须使用其战争代表'
            elif key=='bloc':prop['description']='联盟键：加入时用现有联盟 id；创建时自定唯一命名空间键'
            elif key in ('gold','truce'):prop['minimum']=0
        if action=='offer_peace':properties['kind'].update(enum=['white','pay','demand'],default='white')
        if action=='offer_peace':properties['note']['description']='可选和平提议正文，不改变赔款或休战规则'
        required=[key for key in arguments if not (action=='offer_peace' and key in ('kind','gold','truce','note'))]
        return Tool(action,submit,argument_types=arguments,parameters={'type':'object','properties':properties,'required':required,'additionalProperties':False},
                    description=DESCRIPTIONS[action]+f' 提交成功仅预留费用并登记待办，等待外交结算或投票；基准费用{self.declaration["Diplomacy"]["Fee"]}金，减免与实际费用看回执。')
    def tools(self):
        return (*(self._tool(action,args) for action,args in ARGUMENTS.items()),
                Tool('vote',self._vote,description='对 diplomacy.votes 中本国有资格且当前到期的投票表态：vote 为整数编号，accept=true 赞成、false 反对。每国一票，未投弃权，赞成多于反对通过；投票不等于条约已生效。'),Tool('diplomacy',lambda w,a:w.diplomacy.snapshot(a),read_only=True,description='一次查询本国相关联盟、条约、战争、休战、提议、投票、待结算命令及结果。offers/votes 的 id 用于回应和投票；submitted 不代表关系已改变。'))
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
        return (RuleSection('外交实体与提交','独立国家、联盟是条约实体。外交在行动期提交并预留费用，在外交挂点重新校验后生效或退款。联盟表决在发起后下一周期末统一计票，未投票弃权，赞成票多于反对票通过；宣战仅传导原防守实体的直接保障与共同防御。',keywords=('diplomacy','外交','保障','共同防御','宣战','求和','休战','联盟','投票','结盟','战争代表')),)
