from engine.runtime import GameMod, RuleSection, Stage
from .api import DiplomacyAPI
from .objects import Diplomacy


ALLIANCE_COMMITMENT=('入盟后联盟是一个外交实体：保障、共同防御、宣战、议和须联盟表决，不能个人决定；'
 '原个人保障/共同防御全部作废，退盟不恢复；盟内互不攻击、共享视野、互通领土。'
 '任一成员交战，全盟不能退盟或解散；盟主须先转让才能退出。')



class DiplomacyMod(GameMod):
    def mixins(self):return ()
    def numbers(self):return {'diplomacy.'+key:value for key,value in self.declaration['Diplomacy'].items()}
    def initialize_world(self, world):
        cfg=self.declaration['Diplomacy']
        world.diplomacy = Diplomacy(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,cap:self.runtime.permits(world,actor,cap),cfg['Fee'],cfg['MinimumFee'],cfg['NameMax'],cfg['FallTruce'],found_fee=cfg['FoundFee'],
            ordinary_tribute=cfg['OrdinaryTribute'])
    def objects(self, world):return {'native:diplomacy':world.diplomacy}
    def save_state(self, world):return world.diplomacy.to_save()
    def load_state(self, world, state):world.diplomacy.restore(state)
    def pending_archive(self,world):return world.diplomacy.pending_archive()
    def archive_committed(self,world):world.diplomacy.archive_committed()
    def restore_archive(self,world,rows):world.diplomacy.restore_archive(rows)
    def player_api(self, world):return DiplomacyAPI(world)
    def stages(self):return (Stage('native:diplomacy.production-shares',self._production_shares,hook='buildings.production',before=('native:buildings.produce',),
                                  description='读取实际附庸贡率，建筑毛产出先扣贡，贡品在全体生产后统一到账。'),
                            Stage('native:diplomacy.tally',self._tally,hook='cycle.start',provides=('votes.counted',)),
                            Stage('native:diplomacy.resolve',self._resolve,hook='relations',needs=('votes.counted',),provides=('relations.applied',)))
    @staticmethod
    def _production_shares(world,context):context['production_shares']=world.diplomacy.production_shares()
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
                    'category':'administration','inputs':((world.runtime.object_call(world,'native:resource-inspection','currency'),result.fee),)})
        for index,effect in enumerate(effects):
            if effect[0]=='charge':
                _,owner,amount=effect
                period=world.runtime.object_call(world,'native:government','period')
                context.setdefault('economic_records',[]).append({'id':f'native:subject-alliance-fee/{period}/{index}',
                    'owner':owner,'period':period,'category':'administration',
                    'inputs':((world.runtime.object_call(world,'native:resource-inspection','currency'),amount),)})
    def rule_sections(self, world=None):
        return (
            RuleSection('附庸与生产上贡',
                f'普通附庸每期将实际生产的{self.declaration["Diplomacy"]["OrdinaryTribute"]}%无条件交给宗主，'
                '军队、国土和经济仍自行经营，但完全没有独立外交权，无表决权；随宗主参战，宗主代表处理条约和议和。'
                '和平时宗主可 release_vassal 释放，附庸不能和平自行脱离，只能 independence_war 发动真实独立战争。'
                '独立战争立即脱离旧战线、停止上贡，与宗主及其其他附庸交战，不能借此令其他国家停战；外交费、战斗与补给照常。'
                'propose_vassalage 由宗主邀请，submit_vassalage 主动称臣，普通关系对方 accept_proposal 后成立；宗主未入盟时可以带其普通附庸加入或创建联盟，普通附庸不占联盟表决席位；附庸关系禁止成环；普通宗主灭国或成为别国普通附庸时，原附庸在回合结算释放，恢复独立外交并停止上贡。'
                '普通附庸外交绑定宗主的外交实体，不是只禁止按键；独立战争在回合末爆发后立即视为独立国家、恢复全部外交权。'
                'offer_peace 的 kind=submit 为本国向对方称臣，kind=subjugate 为要求对方成为本国附庸，均须对方接受、无额外赔款；仍由回合末统一结算。'
                '所有外交关系变更和自动拒绝在全体国家行动结束后生效；宣战在其他外交命令后最后处理，并按届时实际阵营校验。'
                '剧本可通过轻量钩子为指定宗主配置特殊附庸联盟、贡率和原附庸自动移交转换；特殊附庸保留正常盟友全部权力。'
                '上贡逐项从实际建筑产出中先扣，余量才可供本期后续生产链使用；每国每期各类资源按全国实际总产出乘贡率，小数按附庸、宗主及资源跨期累计，累计额向下取整扣取，余数继续结转；不对单座建筑分别取整。同一双方的余额随存档保存；关系解除期间不新增、不扣取，恢复同一双方关系后继续累计；更换宗主分别记账。'
                '贡品在本期生产完成后统一到宗主账户，本期不能用来生产。库存、市场交易、赠礼、贷款、利息和收到的贡品不重复计贡，电力不计贡。'
                '停工无产出就无上贡；新附庸关系从下期生产开始收费，退出生效前本期已扣贡品不返还。'
                '生产/GDP按实际毛产出记录，上贡和收贡另列转移，不重复计为生产、消费或贸易。',
                keywords=('附庸','特殊附庸','上贡','朝贡','独立战争','宗主','vassal','propose_vassalage','release_vassal','independence_war')),
            RuleSection('外交实体与提交','独立国家、联盟是条约实体。保障、撤销保障、共同防御、解除共同防御、宣战、求和的 target 接受国家键、联盟 id 或 bloc/联盟键、nation/国家键，系统按当前代表办理；称臣、附庸及转让盟主仍须具体国家键。外交邀约的 response_actor 指明回应代表，can_accept/can_reject 标明本国是否可直接回应，vote_ids 给出本国相关内部表决编号；普通联盟成员不能直接接受或拒绝和平。外交在行动期提交并预留费用，在外交挂点重新校验后生效或退款。'
                f'创盟提议固定收取{self.declaration["Diplomacy"]["FoundFee"]}金，不受双方外交中心减免；成功发布后即使被拒也不退费。'
                '接受和拒绝共同防御、创盟或和平邀约均免费，议和赔款另按条款结算；本期收到且可回应的邀约在收件国成功结束回合时，未回应的提交免费自动拒绝，在全体国家行动结束后的外交阶段生效；已提交的回应和已接受的创盟邀约保留，不要求每回合重复回应。对联盟的外部邀约由其代表负责，普通成员只处理内部表决。新邀约结算送达后等待收件国下一次行动，不因其本期已结束而立即拒绝。'
                '其他费用以回执为准，不能只拿手续费衡量入盟代价。联盟表决在 T 发起、T+1 开放投票并在该周期末统一计票；内部议案及未生效的宣战不自动告知联盟外目标国；已收到的正式入盟申请被否决会向申请外交实体答复，已收到的条约/和平邀约被否决则由本方外交代表正式拒绝并关闭邀约，双方实体收到结果，但不收到对方内部票单；开票前只能看到自己的选票，他国票向、是否已投和累计票数均隐藏；开票后向全体选民公开记名选票、赞成/反对/弃权票数与表决结果。外交信件可自由拉票和承诺，承诺不代替选票；发起人没有自动赞成票，也无盟主否决或提前结票，本期结束回合仍未投票则自动反对，显式弃权保留，赞成票多于反对票通过。',keywords=('diplomacy','外交','投票','外交费','手续费','创盟费用')),
            RuleSection('联盟权责与退出',ALLIANCE_COMMITMENT+'创盟须所有受邀国明确接受，任何受邀国拒绝则该邀约取消；尚未全部接受时仅是待定提议，已经接受也不视为入盟。发起国撤回、参与国开战或入盟资格失效时提议关闭，向全体国家公告联盟创建失败及原因；提议关闭后其他回复会失败，应重新查看 diplomacy 和 news。申请加入既有联盟须现成员表决。个人休战在盟内暂停适用，双方恢复独立后仍未到期的才按原到期日继续适用，期限不重置。退出后向原盟任一成员宣战，仍须面对其全盟；原个人保障作废不等于联盟防守失效。联盟成员的经济、国土和军队仍分属各国。结盟是否构成胜利，由当前剧本的胜利程序决定，不能自行宣布已锁定胜利。',keywords=('diplomacy','外交','联盟','结盟','创盟','建盟','联盟失败','入盟','退盟','bloc_found','bloc_join','bloc_leave','accept_proposal')),
            RuleSection('战争义务与议和','向联盟任一成员宣战，双方整个外交实体参战；防守联盟无需另投票。保障条约 A→B 表示 A 是保障人、B 是受保护实体：第三方宣战 B 时 A 承担防守义务，宣战 A 不会因此召来 B，B 主动进攻也不因此召来 A。共同防御才是双向防守义务，都不替伙伴主动进攻；仅原防守实体的直接条约传导一跳，不递归。援军与攻方有休战时，休战优先，不强拉参战。后续 attack 不重复触发条约；新的合法宣战仍可能向既有战线增援。求和与接受/拒绝和平只能由双方战争主导实体的代表办理，联盟还须表决，普通跟随方不能单独议和；接受后整条战线停战。休战 period < until 时有效，到 until 当期已到期。',keywords=('diplomacy','外交','战争','保障','共同防御','宣战','求和','议和','休战','战争代表','declare_war','offer_peace')),
        )
