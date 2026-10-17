from engine.runtime import GameMod, Panel, RuleSection, Stage, Tool
from .objects import Government


class GovernmentMod(GameMod):
    def mixins(self):return ()
    def needs(self):return ('native:nation-inspection',)
    def provides(self):return ('native:government',)
    def state_keys(self):return ('government',)
    def numbers(self):return {'government.'+key:value for key,value in self.declaration['Government'].items()}
    def initialize_world(self, world):
        cfg = self.declaration['Government']
        world.government = Government(lambda method,*args:self.runtime.object_call(world,'native:nation-inspection',method,*args),
                                      cfg['PlanSpan'],cfg['SummaryMin'])
    def objects(self, world):return {'native:government':world.government}
    def save_state(self, world):return world.government.to_save()
    def load_state(self, world, state):world.government.restore(state)
    def tools(self):
        return (Tool('plan',self._plan,description='提交持久国策：写未来多回合的经济、军事、情报与外交战略，明确目标、优先级、资源约束和调整条件，依据事实而非编造。不是本回合流水账。到期或战略变化时修订，已有有效国策无需反复提交。'),
                Tool('end_turn',self._end_turn,description='结束本国行动。summary 只用一两句概括本回合实际执行的结果与关键待结算事项；详细行动已有回执，不重复国策、逐格清单或下回合战略。必须先有有效国策。结束成功后，未回应的已收到外交提议自动拒绝，开放的联盟投票未表态则自动反对。'),
                Tool('government',lambda world,actor:world.government.snapshot(actor),read_only=True,description='查看本国有效国策、到期周期、结束状态与回合小结；无需参数。'))
    @staticmethod
    def _plan(world, actor, text: str):return world.government.plan(actor,text)
    @staticmethod
    def _end_turn(world, actor, summary: str):return world.government.end_turn(actor,summary)
    def panels(self):return (Panel('government',lambda world,actor:world.government.snapshot(actor)),)
    def stages(self):return (Stage('native:government.advance',lambda w,c:c.update(period=w.government.advance()),hook='cycle.end',needs=('economy.reported',)),)
    def rule_sections(self, world=None):
        return (RuleSection('国策与回合','end_turn 结束本国行动；世界结算仍由宿主统一调用。国策写跨回合的经济、军事、情报与外交战略，到期或战略变化时更新；回合小结只用一两句记录实际执行结果，详细行动已有回执，不重复长期国策。小结须满足清单声明长度。',keywords=('plan','国策','summary','小结','回合','end_turn')), )
