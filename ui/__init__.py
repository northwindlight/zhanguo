"""Public UI assembly gateway. Implementation belongs to the selected UI mod."""
from functools import lru_cache
from pathlib import Path
from engine.runtime import Runtime,ModError
from engine.config import Field,Schema
from engine.player import MAP_KEY

SELECTION_SCHEMA=Schema({'ui_mods':Field((str,))})

ROOT=Path(__file__).resolve().parent.parent

@lru_cache(maxsize=16)
def _runtime(path):
    runtime=Runtime.load(path)
    if 'ui:presentation' not in runtime.capabilities or 'ui:create' not in runtime.presentations:
        raise ModError('UI mod must provide ui:presentation and ui:create')
    return runtime


def configuration_provider(cfg,*,runtime=None):
    if runtime is not None and 'ui_mods' not in cfg and 'ui:create' in runtime.presentations:
        return runtime.presentations.get('ui:configure')
    path=Path(cfg.get('ui_mods',ROOT/'ui.toml')).resolve()
    return _runtime(str(path)).presentations.get('ui:configure')


def load_ui(cfg=None,*,runtime=None):
    cfg=cfg or {}
    if runtime is not None:
        cfg={**cfg,'_content_keys':{k:runtime.content.key(k) for k in runtime.content.manifest()}}
    if runtime is not None and 'ui_mods' not in cfg and 'ui:create' in runtime.presentations:
        return runtime.presentations['ui:create'](cfg,runtime=runtime)
    path=Path(cfg.get('ui_mods',ROOT/'ui.toml')).resolve()
    return _runtime(str(path)).presentations['ui:create'](cfg,runtime=runtime)
