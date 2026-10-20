"""Public UI assembly gateway. Implementation belongs to the selected UI mod."""
from functools import lru_cache
from pathlib import Path
from engine.runtime import Runtime,ModError
from engine.config import (ConfigError,Field,Schema,RUN_SCHEMA,component,entrypoint)
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


def configure_components(cfg,entries,*,runtime,overrides,path='config.actors',fields=()):
    """Distribution chooses entries; components choose inherited fields.

    Lives with the UI gateway because UI selection and presentation fields are
    part of that contract; the engine itself stays UI-free.
    """
    supported=set(RUN_SCHEMA.fields)|set(fields)|set(SELECTION_SCHEMA.fields)
    root=cfg
    selection=SELECTION_SCHEMA.normalize(root,overrides=overrides)
    for key in SELECTION_SCHEMA.fields:
        if key in selection:root[key]=selection[key]
    ui_provider=configuration_provider(root,runtime=runtime)
    if ui_provider:
        supported.update(getattr(ui_provider,'fields',()))
        normalized=ui_provider(root,overrides=overrides,path='config')
        for key,value in normalized.items():
            if root.get(key) is not entries:root[key]=value
    # Preserve the distribution's entry list while normalizing each item in place.
    for index,item in enumerate(entries):
        location=f'{path}[{index}]'
        # Select the provider before it interprets its own presentation fields.
        item=SELECTION_SCHEMA.normalize(item,defaults=root,overrides=overrides,path=location)
        provider=configuration_provider(item,runtime=runtime)
        if provider:
            supported.update(getattr(provider,'fields',()))
            item=provider(item,defaults=root,overrides=overrides,path=location)
        spec=runtime.distribution.get('ClientFactories',{}).get(item['client'],{})
        if spec.get('Configuration'):
            supported.update(getattr(entrypoint(spec['Configuration']),'fields',()))
        item=component(item,spec,defaults=root,overrides=overrides,path=location,runtime=runtime)
        for service in runtime.distribution.get('Session',{}).get('Services',{}).values():
            if service.get('Configuration'):
                supported.update(getattr(entrypoint(service['Configuration']),'fields',()))
            item=component(item,service,defaults=root,overrides=overrides,path=location,runtime=runtime)
        entries[index]=item
    unknown=set(overrides)-supported
    if unknown:raise ConfigError('config: unsupported overrides: '+', '.join(sorted(unknown)))
    return root
