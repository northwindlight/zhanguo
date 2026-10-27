"""Standard UI owns language, catalogs and map presentation options."""
import re
from engine.config import ConfigError,Field,Schema
from presentation.i18n import Translator

SCHEMA=Schema({'model_state_tokens':Field((int,),minimum=1024), 'language':Field((str,)), 'ui_catalog':Field((str,dict)),
               'ui_mods':Field((str,)), 'vision':Field((bool,)),
               'map_max_px':Field((int,),minimum=1),
               'map_estimated_tokens':Field((int,),minimum=0),
               'map_image_tokens':Field((int,),minimum=0), 'color':Field((str,)),
               'desktop_mode':Field((str,),choices=('player','observer'))})


def normalize(cfg,*,defaults=None,overrides=None,path='config'):
    result=SCHEMA.normalize(cfg,defaults=defaults,overrides=overrides,path=path)
    if 'color' in result:
        if re.fullmatch(r'#?[0-9a-fA-F]{6}',result['color']) is None:raise ConfigError(f'{path}.color: expected RGB hex color')
        result['color']='#'+result['color'].lstrip('#').lower()
    # Validate catalog contents before any world or save is created.
    try:Translator.from_config(result)
    except (ValueError,TypeError):raise ConfigError(f'{path}.language/ui_catalog: invalid UI configuration') from None
    return result

normalize.fields=SCHEMA.fields
