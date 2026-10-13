"""Enumerable, additive namespaced content. The engine does not interpret definitions."""
from collections.abc import Mapping
from types import MappingProxyType

from .identifiers import content_id


def _readonly(value):
    # Protect data containers without inspecting or copying opaque definitions.
    if isinstance(value, Mapping):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_readonly(item) for item in value)
    if isinstance(value, set):
        return frozenset(value)
    return value


class ContentRegistry(Mapping):
    def __init__(self):
        self._namespaces = {}
        self._owners = {}

    def register(self, owner, definitions):
        if not isinstance(definitions, Mapping):
            raise ValueError('Content declarations must be a mapping')
        pending = []
        for key, definition in definitions.items():
            content_id(key)
            if key in self._owners:
                raise ValueError(f'Duplicate content ID: {key}')
            namespace, local_id = key.split(':', 1)
            pending.append((key, namespace, local_id, _readonly(definition)))
        # Validation/freezing completes before any part of this batch is added.
        for key, namespace, local_id, definition in pending:
            self._namespaces.setdefault(namespace, {})[local_id] = definition
            self._owners[key] = owner

    def definition(self, key):
        namespace, local_id = content_id(key).split(':', 1)
        return self._namespaces[namespace][local_id]

    def definitions(self):
        return tuple((f'{namespace}:{key}', definition)
                     for namespace, entries in self._namespaces.items()
                     for key, definition in entries.items())

    def manifest(self):
        return dict(self._owners)

    def key(self, identifier):
        local=identifier.split(':',1)[-1]
        matches=[k for k in self._owners if k.split(':',1)[-1]==local]
        return local if matches==[identifier] else identifier

    def resolve(self, key):
        if not isinstance(key,str):raise ValueError('内容键值须为字符串')
        if key in self._owners:return key
        matches=[k for k in self._owners if k.split(':',1)[-1]==key]
        if len(matches)==1:return matches[0]
        if len(matches)>1:raise ValueError('键值冲突，请指定命名空间：'+', '.join(sorted(matches)))
        raise ValueError('未知内容键值：'+str(key))

    def __getitem__(self, namespace):
        return MappingProxyType(self._namespaces[namespace])

    def __iter__(self):
        return iter(self._namespaces)

    def __len__(self):
        return len(self._namespaces)
