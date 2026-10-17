from engine.runtime import GameMod, Panel, RuleSection, Stage
from .api import DiplomacyAPI
from .objects import Diplomacy


ALLIANCE_COMMITMENT=('入盟后联盟是一个外交实体：保障、共同防御、宣战、议和须联盟表决，不能个人决定；'
 '原个人保障/共同防御全部作废，退盟不恢复；盟内互不攻击、共享视野、互通领土。'
 '任一成员交战，全盟不能退盟或解散；盟主须先转让才能退出。')



class DiplomacyMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('diplomacy',)
    def needs(self):return ('native:nation-inspection','native:government','native:resource-inspection','native:resource-exchange','native:buildings','native:territory')
    def provides(self):return ('native:diplomacy',)
    def numbers(self):return {'diplomacy.'+key:value for key,value in self.declaration['Diplomacy'].items()}
    def initialize_world(self, world):
        cfg=self.declaration['Diplomacy']
        world.diplomacy = Diplomacy(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,cap:self.runtime.permits(world,actor,cap),cfg['Fee'],cfg['MinimumFee'],cfg['NameMax'],cfg['FallTruce'],found_fee=cfg['FoundFee'])
    def objects(self, world):return {'native:diplomacy':world.diplomacy}
    def save_state(self, world):return world.diplomacy.to_save()
    def load_state(self, world, state):world.diplomacy.restore(state)
    def pending_archive(self,world):return world.diplomacy.pending_archive()
    def archive_committed(self,world):world.diplomacy.archive_committed()
    def restore_archive(self,world,rows):world.diplomacy.restore_archive(rows)
    def player_api(self, world):return DiplomacyAPI(world)
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
        return (
            RuleSection('外交实体与提交','独立国家、联盟是条约实体。外交在行动期提交并预留费用，在外交挂点重新校验后生效或退款。'
                f'创盟提议固定收取{self.declaration["Diplomacy"]["FoundFee"]}金，不受双方外交中心减免；成功发布后即使被拒也不退费。'
                '接受和拒绝共同防御、创盟或和平邀约均免费，议和赔款另按条款结算；本期收到且可回应的邀约在收件国成功结束回合时，未回应的免费自动拒绝；已提交的回应和已接受的创盟邀约保留，不要求每回合重复回应。对联盟的外部邀约由其代表负责，普通成员只处理内部表决。新邀约结算送达后等待收件国下一次行动，不因其本期已结束而立即拒绝。'
                '其他费用以回执为准，不能只拿手续费衡量入盟代价。联盟表决在 T 发起、T+1 开放投票并在该周期末统一计票；发起人没有自动赞成票，也无盟主否决或提前结票，本期结束回合仍未投票则自动反对，显式弃权保留，赞成票多于反对票通过。',keywords=('diplomacy','外交','投票','外交费','手续费','创盟费用')),
            RuleSection('联盟权责与退出',ALLIANCE_COMMITMENT+'创盟须所有受邀国明确接受，任何受邀国拒绝则该邀约取消；尚未全部接受时仅是待定提议，已经接受也不视为入盟。发起国撤回、参与国开战或入盟资格失效时提议关闭，向全体国家公告联盟创建失败及原因；提议关闭后其他回复会失败，应重新查看 diplomacy 和 news。申请加入既有联盟须现成员表决。个人休战在盟内暂停适用，双方恢复独立后仍未到期的才按原到期日继续适用，期限不重置。退出后向原盟任一成员宣战，仍须面对其全盟；原个人保障作废不等于联盟防守失效。联盟成员的经济、国土和军队仍分属各国。结盟是否构成胜利，由当前剧本的胜利程序决定，不能自行宣布已锁定胜利。',keywords=('diplomacy','外交','联盟','结盟','创盟','建盟','联盟失败','入盟','退盟','bloc_found','bloc_join','bloc_leave','accept_proposal')),
            RuleSection('战争义务与议和','向联盟任一成员宣战，双方整个外交实体参战；防守联盟无需另投票。保障是单向防守义务，共同防御是双向防守义务，都不替伙伴主动进攻；仅原防守实体的直接条约传导一跳，不递归。援军与攻方有休战时，休战优先，不强拉参战。后续 attack 不重复触发条约；新的合法宣战仍可能向既有战线增援。求和与接受/拒绝和平只能由双方战争主导实体的代表办理，联盟还须表决，普通跟随方不能单独议和；接受后整条战线停战。休战 period < until 时有效，到 until 当期已到期。',keywords=('diplomacy','外交','战争','保障','共同防御','宣战','求和','议和','休战','战争代表','declare_war','offer_peace')),
        )
