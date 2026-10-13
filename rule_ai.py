"""Maintained rule-client registry. Retired strategies are available in Git history."""
from ruleai.v11plus import RuleClient, RuleState, expand_rule_turn_v11plus

DEFAULT_RULE_AI = 'v11plus'
UNLIMITED_ACTIONS = None


def versions():return (DEFAULT_RULE_AI,)


def resolve(name=None):
    version=str(name or DEFAULT_RULE_AI).strip().lower()
    if version!=DEFAULT_RULE_AI:
        raise ValueError(f'规则 AI {version!r} 已退役或不存在；当前只维护 v11plus')
    return version,expand_rule_turn_v11plus


def create(name=None,state=None):
    resolve(name)
    return RuleClient(RuleState.from_save(state) if state is not None else None)
