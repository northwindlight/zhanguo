"""Rule client with the experimental combat-probability service."""
from . import RuleClient, RuleState


class ResearchRuleClient(RuleClient):
    plan_text = '研究模式：按经济收益建设并维持补给，使用当前可见战局的概率推演集中编组；推演不保证实际战果'

    def __init__(self, cfg, *, record=None, services):
        if record is not None and (record.get('kind') != 'rule' or record.get('version') != self.version):
            raise ValueError('Unsupported research rule client state')
        self.research = True
        self._research = services['combat']
        super().__init__(RuleState.from_save(record['state']) if record else None,
                         forecast=lambda player, cell, units: self._research('assess', [u['id'] for u in units], *cell))
