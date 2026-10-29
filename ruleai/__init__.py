"""Maintained rule-client registry. Retired strategies are available in Git history."""
from ruleai.v11plus import RuleClient, RuleState, expand_rule_turn_v11plus
from ruleai.v13 import RuleClient as V13Client, RuleState as V13State, expand_rule_turn_v13
from ruleai.v14 import RuleClient as V14Client, RuleState as V14State, expand_rule_turn_v14
from ruleai.v13.profiles import PROFILES

DEFAULT_RULE_AI = 'v13'
PROFILE_VERSIONS = ('v14','v13')
UNLIMITED_ACTIONS = None


def versions():return ('v14','v13','v11plus')


def resolve(name=None):
    version=str(name or DEFAULT_RULE_AI).strip().lower()
    if version not in versions():
        raise ValueError(f'规则 AI {version!r} 已退役或不存在；当前支持 '+ ' / '.join(versions()))
    return version,{'v14':expand_rule_turn_v14,'v13':expand_rule_turn_v13,'v11plus':expand_rule_turn_v11plus}[version]


def create(name=None,state=None,*,profile='random',seed=None,forecast=None):
    version,_=resolve(name)
    if version in ('v14','v13'):
        cls=V14Client if version=='v14' else V13Client
        decode=V14State if version=='v14' else V13State
        return cls(decode.from_save(state) if state is not None else None,profile=profile,seed=seed,forecast=forecast)
    return RuleClient(RuleState.from_save(state) if state is not None else None,forecast=forecast)


def normalize_config(cfg,*,defaults=None,overrides=None,path='config',runtime=None):
    from engine.config import Field,Schema
    result=Schema({'rule_ai':Field((str,),choices=versions()),
                   'rule_strategy':Field((str,),choices=('random',*PROFILES)),
                   'rule_seed':Field((int,),nullable=True),
                   'max_actions':Field((int,),minimum=1,nullable=True)}).normalize(
                       cfg,defaults=defaults,overrides=overrides,path=path)
    return result

normalize_config.fields=('rule_ai','rule_strategy','rule_seed','max_actions')
