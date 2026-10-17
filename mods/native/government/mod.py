from engine.runtime import GameMod, Panel, RuleSection, Stage
from .api import GovernmentAPI
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
    def player_api(self, world):return GovernmentAPI(world)
    def panels(self):return (Panel('government',lambda world,actor:world.government.snapshot(actor)),)
    def stages(self):return (Stage('native:government.advance',lambda w,c:c.update(period=w.government.advance()),hook='cycle.end',needs=('economy.reported',)),)
    def rule_sections(self, world=None):
        return (RuleSection('国策与回合','end_turn 结束本国行动；世界结算仍由宿主统一调用。国策写跨回合的经济、军事、情报与外交战略，到期或战略变化时更新；回合小结只用一两句记录实际执行结果，详细行动已有回执，不重复长期国策。小结须满足清单声明长度。',keywords=('plan','国策','summary','小结','回合','end_turn')), )
