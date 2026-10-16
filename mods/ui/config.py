"""Standard UI owns language, catalogs and map presentation options."""
from engine.config import ConfigError,Field,Schema
from .i18n import Translator

SCHEMA=Schema({'model_state_tokens':Field((int,),minimum=1024), 'opening_guide':Field((bool,)), 'language':Field((str,)), 'ui_catalog':Field((str,dict)),
               'ui_mods':Field((str,)), 'vision':Field((bool,)),
               'map_max_px':Field((int,),minimum=1),
               'map_estimated_tokens':Field((int,),minimum=0),
               'map_image_tokens':Field((int,),minimum=0)})


def normalize(cfg,*,defaults=None,overrides=None,path='config'):
    result=SCHEMA.normalize(cfg,defaults=defaults,overrides=overrides,path=path)
    # Validate catalog contents before any world or save is created.
    try:Translator.from_config(result)
    except (ValueError,TypeError):raise ConfigError(f'{path}.language/ui_catalog: invalid UI configuration') from None
    return result

normalize.fields=SCHEMA.fields
