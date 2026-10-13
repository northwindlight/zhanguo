"""Council service owns reviewer defaults and validation."""
from engine.config import Field,Schema
from clients.config import normalize as llm_config,LLM_SCHEMA
from ui import configuration_provider

SCHEMA=Schema({'parliament':Field((dict,))})
SETTINGS=Schema({'reviewer':Field((dict,)), 'requirements':Field((str,)),
                 'requirements_file':Field((str,))})


def normalize(cfg,*,defaults=None,overrides=None,path='config',runtime=None):
    result=SCHEMA.normalize(cfg,defaults=defaults,overrides=overrides,path=path)
    settings=SETTINGS.normalize(result.get('parliament',{}),path=path+'.parliament')
    review_defaults={**(defaults or {}),**result}
    reviewer=llm_config(settings.get('reviewer',{}),defaults=review_defaults,overrides=overrides,path=path+'.parliament.reviewer')
    if 'ui_mods' in result:reviewer.setdefault('ui_mods',result['ui_mods'])
    ui_provider=configuration_provider(reviewer,runtime=runtime)
    if ui_provider:reviewer=ui_provider(reviewer,defaults=result,overrides=overrides,path=path+'.parliament.reviewer')
    settings['reviewer']=reviewer
    result['parliament']=settings
    return result


normalize.fields={**LLM_SCHEMA.fields,**SCHEMA.fields}
