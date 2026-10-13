"""Public UI assembly gateway. Implementation belongs to the selected UI mod."""
from functools import lru_cache
from pathlib import Path
from engine.runtime import Runtime,ModError

MAP_KEY="_map_snapshot"
ROOT=Path(__file__).resolve().parent.parent

@lru_cache(maxsize=16)
def _runtime(path):
    runtime=Runtime.load(path)
    if 'ui:presentation' not in runtime.capabilities or 'ui:create' not in runtime.presentations:
        raise ModError('UI mod must provide ui:presentation and ui:create')
    return runtime


def load_ui(cfg=None,*,runtime=None):
    cfg=cfg or {}
    if runtime is not None and 'ui_mods' not in cfg and 'ui:create' in runtime.presentations:
        return runtime.presentations['ui:create'](cfg)
    path=Path(cfg.get('ui_mods',ROOT/'ui.toml')).resolve()
    return _runtime(str(path)).presentations['ui:create'](cfg)
