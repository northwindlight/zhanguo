from ..base import DomainMod

class EconomyMod(DomainMod):
    package = "mods.native.economy"
    logic_class = "EconomyLogic"

    def rule_sections(self, world=None):
        from engine.runtime import RuleSection
        result=list(super().rule_sections(world))
        if world is not None and not world.bank_on():
            result=[RuleSection(s.title,s.body.split('### 世界央行')[0] if s.title.endswith('市场') else s.body,s.keywords,s.summary,s.player_visible) for s in result]
        elif world is not None:
            from .views import _bank_rules
            result.append(RuleSection('世界央行',_bank_rules()+'\n买报表：buy_report(to=国家, turn=报表回合)。',('银行','央行','贷款','利率','借款','买报表')))
        return tuple(result)
