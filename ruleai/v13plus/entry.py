"""Economic BC teacher; combat and persistent preferences remain those of v13."""
from ruleai.v13.entry import RuleClient as BaseClient, RuleState
from . import economy


class RuleClient(BaseClient):
    version = 'v13plus'
    plan_text = ('按公开库存、正式整单报价和多国市场压力安排运营与投资；'
                 '先确定实际材料用途，按需出售筹资，投资结束后再出售剩余商品，'
                 '保留持久经营偏好与必要周转，不在同一期反复买卖同一资源。'
                 '军事继续依据公开视野、补给和合法路线集中扩张。')

    def run_economy(self, ledger):
        economy.run(ledger, self.state)


def expand_rule_turn_v13plus(player, *, state=None, **kwargs):
    return RuleClient(state).play(player, **kwargs)
