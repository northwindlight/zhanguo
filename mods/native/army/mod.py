from engine.player import wire
from engine.runtime import GameMod, RuleSection, Stage
from .api import ArmyAPI
from .content import native_unit_definitions, unit_definitions
from .objects import Units
from .domain_objects import Military
from .parameters import ARMY_STARVE_DAMAGE, ARMY_HEAL_PER_TURN, HEAL_EQUIP_COST, COMBAT_DIE_MOD, RETREAT_ATK_PENALTY, RETREAT_DEF_COVER


class ArmyMod(GameMod):
    def mixins(self):return ()
    def numbers(self):return {'units':wire(unit_definitions(self.runtime.content)),'army.starvation':ARMY_STARVE_DAMAGE,'army.heal':ARMY_HEAL_PER_TURN,'army.heal-equipment':HEAL_EQUIP_COST,'army.combat-die':dict(COMBAT_DIE_MOD),'army.retreat.attack-penalty':RETREAT_ATK_PENALTY,'army.retreat.defender-cover':RETREAT_DEF_COVER}
    def content_definitions(self):
        # 地形在单位定义里直接用内容 ID；中文地形名只留在 map_generation 边界。
        return native_unit_definitions()
    def initialize_world(self, world):
        size,_=self.runtime.object_call(world,'native:territory','dimensions')
        world.units=Units(size,definitions=unit_definitions(self.runtime.content))
        world.military=Military(world.units,
            lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,key,default:self.runtime.parameter(world,actor,key,default))
    def objects(self, world):return {'native:army':world.military}
    def player_api(self, world):return ArmyAPI(world)
    def save_state(self, world):return {'units':{**world.units.to_save(),'engage_sequence':world.units.engage_seq},'military':world.military.to_save()}
    def load_state(self, world, state):
        if not isinstance(state,dict) or set(state)!={'units','military'}:raise ValueError('Invalid army save')
        size,_=self.runtime.object_call(world,'native:territory','dimensions')
        candidate=Units.from_save(state['units'],size=size,definitions=world.units.definitions)
        sequence=state['units'].get('engage_sequence')
        if type(sequence) is not int or sequence<candidate.engage_seq:raise ValueError('Invalid engagement sequence')
        owners=self.runtime.object_call(world,'native:nation-inspection','ids')
        if any(u.owner!='野人' and u.owner not in owners for u in candidate):raise ValueError('Unit owner is not a living nation')
        for u in candidate:
            for point in ((u.x,u.y),) + ((u.retreat_to,) if u.retreat_to is not None else ()):
                if not self.runtime.object_call(world,'native:territory','terrain',*point).passable:
                    raise ValueError('Army or retreat position is on impassable terrain')
        world.military.restore(state['military'],units=candidate);world.units.size=size;world.units.restore(state['units']);world.units.engage_seq=sequence
    def stages(self):
        return (Stage('native:army.supply',self._supply,hook='military.supply',needs=('relations.applied','buildings.produced'),provides=('military.supplied',)),
                Stage('native:army.battle',lambda w,c:c.update(battles=w.military.battle()),hook='military.battle',needs=('military.supplied',),provides=('battles.resolved',)),
                Stage('native:army.withdraw',lambda w,c:c.update(withdrawals=w.military.withdrawals()),hook='military.withdraw',needs=('battles.resolved',),provides=('withdrawals.resolved',)),
                Stage('native:army.claims',self._claims,hook='military.claims',needs=('withdrawals.resolved',),provides=('claims.resolved',)),
                Stage('native:army.repatriate',self._repatriate,hook='military.repatriate',needs=('actors.eliminated',),provides=('military.repatriated',)))
    @staticmethod
    def _supply(world, context):
        world.military.record_grid(context['building_production'].energy)
        report,facts=world.military.supply(context.get('diplomacy_effects',()))
        context['military_supply']=report;context.setdefault('economic_records',[]).extend(facts)
    @staticmethod
    def _claims(world, context):
        context['claims']=world.military.claims();context.setdefault('domain_events',[]).extend(world.military.drain_events())
    @staticmethod
    def _repatriate(world, context):
        context['repatriations']=world.military.repatriate()
        # 脱离清扫收在军事序列的末尾：claims 要读 engaged，而 0.2 的 repatriate 没有
        # 0.1 那条「与交战国之间的滞留是合法的」豁免，清早一步会把合法驻扎在敌国地上的
        # 军队一并送回国。放这里只差一个回合，不会误伤。
        context['disengaged']=world.military.clear_disengaged()
        world.military.cleanup_march_orders()
        assert not world.military.engaged_without_foes(), 'Engaged units without foes after settlement'
        context.setdefault('domain_events',[]).extend(world.military.drain_events())
    def rule_sections(self, world=None):
        definitions=dict(self.runtime.content.definitions())
        barracks=getattr(definitions.get('native:barracks'),'EFFECTS',{})
        camp=getattr(definitions.get('native:militia-camp'),'EFFECTS',{})
        body='\n\n'.join((
            '行动：所有坐标统一 0-based。移动、异地进攻、撤退共用每支军队每期一次行动额度；新兵当期不能行动，原地进攻不消耗位移额度。'
            'move 只调动、不占地；attack 才会占领或进入交战。reachable 给出可见合法路线和代价，路程不能超过兵种移动力。'
            '高山和海洋不可通行、进攻或占领，也不能从障碍侧面的斜角挤过；原有山地仍可通行。unit_catalog 的移动代价 null 表示不可通行。'
            '本国和盟国领土可通行；攻击他国必须已处于战争，外交 submitted 不代表战争已生效。',
            '连续军令：military(action=queue_orders,ids=[编号],nodes=[{mode:move或attack,position:[x,y]}])追加路线，每军最多64个节点，append=false替换剩余路线。'
            '当前合法部分开始，之后在轮到本国的行动前按原移动力、新兵和战争规则继续；攻击路线碰到敌地先接敌、交战时等待，己方／盟方位置用移动。'
            'auto_heal开启时，某军低于绝对30生命会独立停下，正常补给恢复满血才继续；不会额外治疗或强制撤退。'
            'query(panel=march_orders)查看本国路线与原因，military(action=cancel_orders,ids=[编号],node=可选序号)删指定剩余节点或取消路线。'
            '直接移动、攻击、撤退会替换该军剩余路线，取消不会撤销已发生的动作。预览只用玩家已知事实，未知段仅示意，不返回隐藏经过格。',
            '征兵：正规军只能在自有已落成兵营征召，停电时不能征召；民兵只能在自有已落成军屯征召。'
            '同格兵营的每期额度相加；民兵还受全国军屯编制上限限制。编制上限和每期征召额度是两个不同数值，查 building_catalog 的效果。'
            f'当前目录中，每座兵营每期可征{barracks.get("recruit_cap",0)}支正规军；每座军屯提供{camp.get("militia_cap",0)}支全国民兵编制、该格每期可征{camp.get("militia_per_turn",0)}支民兵。'
            '费用、满编HP、攻击、移动力和每支补给量查 unit_catalog；材料或额度不足时按可征数量缩减，返回实际新兵。'
            '在建兵营、军屯本期不能征兵；建兵营的已有建筑门槛统计该格现有建筑数量（含在建），不是地块可容纳的建筑位上限；每格每期仍只能动工一次。'
            'queue_recruit 在落成设施入队，立即预付全部费用，按设施额度分期生成，待征民兵预占全国编制；停电等条件变化则等待。recruitment_queue 查进度，cancel_recruit_order 只全退尚未生成部分，已生成军队不退款；失地或亡国取消待生成部分并退原付款国。',
            f'补给：统一结算时先补给与回血、再战斗、再撤退落地。新兵当期也要补给，伤兵仍按一支计。'
            '驻守本国军屯格的本国民兵，在该格军屯民兵容量内免耗补给，正规军不免供。'
            f'补给不足时，所有非免供军队各损失 max(1, {ARMY_STARVE_DAMAGE}×缺口÷需求向下取整) HP；完全断供每支损失{ARMY_STARVE_DAMAGE}，交战军队也会断粮，HP归零阵亡。'
            'logistics 军需面板逐军队列补给需求、免供、断粮损失、回血装备需求与预计消耗、预计恢复HP及阻碍原因，再汇总补给与装备库存、需求、消耗与缺口。不计后续生产、行动与外交变化，不实际扣资源。补给全国统扣，缺口使所有非免供军队受损，不按军队先后分粮。',
            f'伤兵恢复：本国本期补给全部满足，且军队所在格没有任何交战标记时，存活伤兵每支自动恢复至多{ARMY_HEAL_PER_TURN}HP，不超过该兵种满编。'
            f'正规军伤兵平均分享装备提供的恢复量：每件装备提供{ARMY_HEAL_PER_TURN}HP总量，最多均分给本期合格伤兵，每支至多恢复{ARMY_HEAL_PER_TURN}HP；全军按实际恢复量向上取整扣装备。民兵独立免费恢复。满血、断粮或交战部队不进入回血分配。战斗结束在补给阶段之后，本期刚打完的伤兵不会追补回血；下期满足条件再恢复。',
            '战斗：工具提交进攻后不会立即结算有守军的战斗；同一格各方在统一结算时同时承伤。'
            '本方基础输出为存活军队攻击力之和，伤兵只要存活就保留该兵种基础攻击，不按剩余HP线性折算。'
            '每国每场战斗掷一次1—6点骰，对应输出修正'+ '、'.join(f'{COMBAT_DIE_MOD[die]:+d}%' for die in sorted(COMBAT_DIE_MOD))+'。'
            '多敌国时输出均分给敌方；守方享受地形减伤，领土所有者另享城堡减伤，两种减伤乘法叠加，进攻方不因站在目标格而取得守方地形待遇。'
            '伤害在同方军队间分摊，阵亡后的剩余伤害继续分配；静态输出不是胜率或必赢承诺。守军清除后才裁决占领，以战报和占领事件为准。',
            f'撤退：本期未行动、不是新兵，且正在交战或受攻击的军队，才能申请撤至相邻合法格。申请占用行动，本期仍参加战斗，输出降低{RETREAT_ATK_PENALTY}%；'
            f'守方撤退只承受分配伤害的{RETREAT_DEF_COVER}%，攻方撤退承受全部分配伤害。幸存军队在战斗后落地；原目标受阻会寻找相邻合法格，无处可退则原地留守。'
            '在本国地块上参战者按守方撤退规则处理。交战或受到进攻的军队不能遣散；合法遣散立即移除军队、停止维持，不返还材料。',
        ))
        return (RuleSection('军队',body,keywords=('army','battle','move','attack','retreat','disband','logistics','heal','军队','战斗','撤退','遣散','裁军','移动','攻击','速度','征兵','补给','断粮','回血','伤兵','恢复','军屯','兵营')),)
