"""v13 retains v11plus combat and replaces its economic planner."""
from dataclasses import dataclass, field
from copy import deepcopy
import random

from ruleai.v11plus.entry import RuleClient as BaseClient, RuleState as BaseState
from .profiles import PROFILES
from .market import finite_map
from . import economy


@dataclass
class RuleState(BaseState):
    profile: str = 'balanced'
    market: dict = field(default_factory=dict)

    def to_save(self):
        return {**super().to_save(), 'profile':self.profile, 'market':deepcopy(self.market)}

    @classmethod
    def from_save(cls, data):
        if not isinstance(data, dict) or set(data) != {'targets','exact','generation','profile','market'}:
            raise ValueError('Invalid v13 state')
        base = BaseState.from_save({k:data[k] for k in ('targets','exact','generation')})
        if not isinstance(data['profile'],str) or data['profile'] not in PROFILES or not isinstance(data['market'], dict):
            raise ValueError('Invalid v13 strategy profile')
        market = data['market']
        if market:
            if (set(market) != {'period','inventory','reference','external','flow','own_net'}
                    or type(market['period']) is not int or market['period'] < 0
                    or type(market['flow']) not in (int,float) or not 0 <= market['flow'] <= 1
                    or any(not finite_map(market[k]) for k in ('inventory','reference','external','own_net'))
                    or any(v<0 for v in market['inventory'].values())
                    or any(v<=0 for v in market['reference'].values())):
                raise ValueError('Invalid v13 market memory')
        return cls(base.targets, base.exact, base.generation, data['profile'], deepcopy(market))


class RuleClient(BaseClient):
    version = 'v13'
    state_type = RuleState
    plan_text = ('按公开库存、报价和实际成交估计市场变化；保留燃料、生产原料、军需与现金储备，'
                 '依本国长期经营偏好分散投资，并检验多国同时增产后的收益。每期按实际缺口调整采购，'
                 '新增投资须能维持运营；军事依据当期可见兵力、补给和合法路线集中扩张。')

    def __init__(self, state=None, *, forecast=None, profile='random', seed=None):
        if state is None:
            if profile == 'random':
                profile = random.Random(seed).choice(tuple(PROFILES))
            if profile not in PROFILES:
                raise ValueError('Unknown v13 strategy profile')
            state = RuleState(profile=profile)
        super().__init__(state, forecast=forecast)

    def run_economy(self, ledger):
        economy.run(ledger, self.state)

    def reset_state(self, generation):
        return RuleState(generation=generation, profile=self.state.profile)


def expand_rule_turn_v13(player, *, state=None, **kwargs):
    return RuleClient(state).play(player, **kwargs)
