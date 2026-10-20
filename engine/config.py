"""Configuration assembly; field semantics belong to declared providers."""
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
import json
import math
from pathlib import Path
from .runtime import ModError


class ConfigError(ValueError):
    """A field-path diagnostic that never includes the supplied value."""


@dataclass(frozen=True)
class Field:
    types: tuple
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple | None = None
    nullable: bool = False

    def check(self, value, path):
        if value is None and self.nullable:return
        valid=type(value) in self.types
        if isinstance(value,float) and not math.isfinite(value):valid=False
        if valid and self.minimum is not None:valid=value>=self.minimum
        if valid and self.maximum is not None:valid=value<=self.maximum
        if valid and self.choices is not None:valid=value in self.choices
        if not valid:raise ConfigError(f'{path}: invalid configuration field')


class Schema:
    def __init__(self, fields):self.fields=dict(fields)
    def normalize(self,cfg,*,defaults=None,overrides=None,path='config'):
        if not isinstance(cfg,dict):raise ConfigError(f'{path}: expected object')
        result=deepcopy(cfg)
        for key in self.fields:
            if defaults is not None and key in defaults:result.setdefault(key,deepcopy(defaults[key]))
            if overrides is not None and key in overrides:result[key]=deepcopy(overrides[key])
            if key in result:self.fields[key].check(result[key],f'{path}.{key}')
        return result


RUN_SCHEMA=Schema({'mods':Field((str,)), 'save':Field((str,)),
                   'journal':Field((str,)), 'max_turns':Field((int,),minimum=0)})


def read_config(path):
    try:data=json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ConfigError(f'config: invalid JSON at line {exc.lineno}, column {exc.colno}') from None
    if not isinstance(data,dict):raise ConfigError('config: expected object')
    return data


def entrypoint(entry):
    if not isinstance(entry,str) or entry.count(':')!=1:raise ModError('Invalid Configuration entry')
    module,name=entry.split(':')
    try:target=getattr(import_module(module),name)
    except (ImportError,AttributeError) as exc:raise ModError('Cannot load Configuration entry') from exc
    if not callable(target):raise ModError('Configuration entry must be callable')
    return target


def component(cfg,spec,*,defaults=None,overrides=None,path='config',runtime=None):
    """Invoke a component-owned normalizer, without guessing its fields."""
    entry=spec.get('Configuration')
    if entry is None:return deepcopy(cfg)
    result=entrypoint(entry)(cfg,defaults=defaults,overrides=overrides,path=path,runtime=runtime)
    if not isinstance(result,dict):raise ModError('Configuration entry must return an object')
    return result


@dataclass(frozen=True)
class PreparedConfig:
    runtime: object
    data: dict


def prepare(cfg,*,runtime,overrides=None):
    overrides=deepcopy(overrides or {})
    data=RUN_SCHEMA.normalize(cfg,overrides=overrides)
    spec=runtime.distribution.get('Configuration')
    if spec is not None:
        if not isinstance(spec,dict):raise ModError('Distribution.Configuration must be an object')
        data=entrypoint(spec.get('Entry'))(data,runtime=runtime,overrides=overrides)
        if not isinstance(data,dict):raise ModError('Distribution.Configuration.Entry must return an object')
    elif set(overrides)-set(RUN_SCHEMA.fields):
        raise ConfigError('config: distribution does not declare these overrides')
    # Providers may transform their own fields; runner fields retain their contract.
    data.update(overrides)
    data=RUN_SCHEMA.normalize(data)
    return PreparedConfig(runtime,deepcopy(data))
