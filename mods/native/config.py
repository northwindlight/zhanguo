"""Native owns national scenarios; components own their inherited settings."""
import hashlib
from engine.config import ConfigError, Field, Schema, configure_components
from engine.identifiers import content_id

SCENARIO=Schema({'map_size':Field((int,),minimum=1),'seed':Field((int,),nullable=True)})
ACTOR=Schema({'name':Field((str,)), 'client':Field((str,)),
              'enabled':Field((bool,)), 'appearance':Field((str,),choices=('initial','scheduled','manual')),
              'enable_turn':Field((int,),minimum=0), 'enable_turn_max':Field((int,),minimum=0),
              'resources':Field((dict,)), 'polity':Field((str,)),
              'start_gold':Field((int,),minimum=0),'start_supply':Field((int,),minimum=0),
              'start_cavalry':Field((int,),minimum=0)})


def normalize(cfg,*,runtime,overrides):
    cfg=SCENARIO.normalize(cfg,overrides=overrides)
    entries=cfg.get('nations')
    if not isinstance(entries,list) or not entries:raise ConfigError('config.nations: expected nonempty list')
    ids=set();names=set()
    for index,item in enumerate(entries):
        path=f'config.nations[{index}]'
        item=ACTOR.normalize(item,path=path)
        if not item.get('name','').strip():raise ConfigError(f'{path}.name: expected display name')
        try:item['id']=content_id(item.get('id') or 'scenario:nation-'+hashlib.sha256(item['name'].encode()).hexdigest()[:12])
        except (TypeError,ValueError):raise ConfigError(f'{path}.id: invalid content id') from None
        if item['id'] in ids or item['name'] in names:raise ConfigError(f'{path}: duplicate configured nation')
        ids.add(item['id']);names.add(item['name'])
        item['client']=item.get('client') or ('llm' if item.get('api_key',cfg.get('api_key')) else 'rule')
        if item['client'] not in runtime.distribution.get('ClientFactories',{}) and item['client'] not in ('llm','rule'):
            raise ConfigError(f'{path}.client: factory not declared')
        if item.get('enable_turn_max',item.get('enable_turn',0))<item.get('enable_turn',0):
            raise ConfigError(f'{path}.enable_turn_max: earlier than enable_turn')
        policy=runtime.find_policy(item['polity']) if item.get('polity') else None
        if item.get('polity') and policy is None:raise ConfigError(f'{path}.polity: scope module not loaded')
        item.setdefault('appearance',policy.declaration.get('Policy',{}).get('Appearance','scheduled') if policy else 'initial')
        entries[index]=item
    return configure_components(cfg,entries,runtime=runtime,overrides=overrides,path='config.nations',fields=SCENARIO.fields)
