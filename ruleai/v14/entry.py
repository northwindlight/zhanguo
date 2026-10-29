"""v14 keeps the existing military controller and coordinates economic investment."""
from dataclasses import dataclass
from ruleai.v13.entry import RuleClient as BaseClient, RuleState as BaseState
from . import economy


@dataclass
class RuleState(BaseState):
    recruit_due: int | None = None

    def to_save(self):
        return {**super().to_save(),'recruit_due':self.recruit_due}

    @classmethod
    def from_save(cls,data):
        if not isinstance(data,dict):raise ValueError('Invalid v14 state')
        raw=dict(data);due=raw.pop('recruit_due',None)
        if due is not None and (type(due) is not int or due<0):raise ValueError('Invalid recruitment deadline')
        base=BaseState.from_save(raw)
        return cls(base.targets,base.exact,base.generation,base.profile,base.market,due)


class RuleClient(BaseClient):
    version = 'v14'
    state_type=RuleState
    plan_text = ('通过绑定玩家的正式规划与报价建立跨期混合整数模型，'
                 '联合安排各地建设、交易、燃料、电网、库存与军需，先减少已知运营短缺；'
                 '在统一预算下优化期末资产与产能延续价值，仅执行本期可行方案并按回执滚动重算。'
                 '未来价格是公开市场预测，受限求解不宣称真实未来或完整博弈的全局最优；'
                 '军事仍按可见兵力、补给和合法路线集中扩张。')

    def __init__(self,state=None,**kwargs):
        super().__init__(state,**kwargs)
        if not isinstance(self.state,RuleState):self.state=RuleState.from_save(self.state.to_save())

    def reset_state(self,generation):
        return RuleState(generation=generation,profile=self.state.profile)

    def run_economy(self, ledger):
        self.last_economy=economy.run(ledger, self.state)


def expand_rule_turn_v14(player, *, state=None, **kwargs):
    return RuleClient(state).play(player, **kwargs)
