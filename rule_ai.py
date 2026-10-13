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


def normalize_config(cfg,*,defaults=None,overrides=None,path='config',runtime=None):
    from engine.config import Field,Schema
    result=Schema({'rule_ai':Field((str,),choices=versions()),
                   'max_actions':Field((int,),minimum=1,nullable=True)}).normalize(
                       cfg,defaults=defaults,overrides=overrides,path=path)
    return result

normalize_config.fields=('rule_ai','max_actions')
