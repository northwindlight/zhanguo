"""Council service owns reviewer defaults and validation."""
from engine.config import Field,Schema
from clients.config import normalize as llm_config,LLM_SCHEMA
from ui import configuration_provider

SCHEMA=Schema({'parliament':Field((dict,))})
SETTINGS=Schema({'reviewer':Field((dict,)), 'requirements':Field((str,)),
                 'requirements_file':Field((str,))})


def normalize(cfg,*,defaults=None,overrides=None,path='config',runtime=None):
    result=SCHEMA.normalize(cfg,defaults=defaults,overrides=overrides,path=path)
    # 默认议长 = 该 LLM 玩家自己的模型与接入；只有玩家条目显式写了
    # parliament.reviewer 才另换。顶层 parliament.reviewer 只作为非 LLM
    # （规则客户端）的兜底，不再覆盖每个 LLM 玩家。
    raw=cfg.get('parliament',{}) if isinstance(cfg,dict) else {}
    if not isinstance(raw,dict):raise ValueError(f'{path}.parliament: expected object')
    inherited=(defaults or {}).get('parliament',{}) if isinstance(defaults,dict) else {}
    if not isinstance(inherited,dict):inherited={}
    settings=SETTINGS.normalize({**{key:value for key,value in inherited.items() if key!='reviewer'},**raw},
                                path=path+'.parliament')
    review_defaults={**(defaults or {}),**result}
    explicit=raw.get('reviewer')
    if explicit is None and cfg.get('client')!='llm':
        explicit=inherited.get('reviewer')
    reviewer=llm_config(explicit or {},defaults=review_defaults,overrides=overrides,path=path+'.parliament.reviewer')
    if 'ui_mods' in result:reviewer.setdefault('ui_mods',result['ui_mods'])
    ui_provider=configuration_provider(reviewer,runtime=runtime)
    if ui_provider:reviewer=ui_provider(reviewer,defaults=result,overrides=overrides,path=path+'.parliament.reviewer')
    settings['reviewer']=reviewer
    result['parliament']=settings
    return result


normalize.fields={**LLM_SCHEMA.fields,**SCHEMA.fields}
