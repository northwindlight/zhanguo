"""Enumerable, additive namespaced content. The engine does not interpret definitions."""
from collections.abc import Mapping
import re
from types import MappingProxyType

from .identifiers import content_id

ROLE = re.compile(r'[a-z][a-z0-9_.-]*')


def _roles(key, definition):
    """Declared roles are opaque labels; the engine only indexes and validates them."""
    values = getattr(definition, 'ROLES', ()) or ()
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise ValueError(f'Content roles must be a tuple: {key}')
    if len(set(values)) != len(values):
        raise ValueError(f'Duplicate content role: {key}')
    for role in values:
        if not isinstance(role, str) or not ROLE.fullmatch(role):
            raise ValueError(f'Invalid content role: {key}')
    return tuple(values)


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
        self._roles = {}

    def register(self, owner, definitions):
        if not isinstance(definitions, Mapping):
            raise ValueError('Content declarations must be a mapping')
        pending = []
        for key, definition in definitions.items():
            content_id(key)
            if key in self._owners:
                raise ValueError(f'Duplicate content ID: {key}')
            namespace, local_id = key.split(':', 1)
            pending.append((key, namespace, local_id, _readonly(definition), _roles(key, definition)))
        # Validation/freezing completes before any part of this batch is added.
        for key, namespace, local_id, definition, roles in pending:
            self._namespaces.setdefault(namespace, {})[local_id] = definition
            self._owners[key] = owner
            for role in roles:
                self._roles.setdefault(role, []).append(key)

    def definition(self, key):
        namespace, local_id = content_id(key).split(':', 1)
        return self._namespaces[namespace][local_id]

    def definitions(self):
        return tuple((f'{namespace}:{key}', definition)
                     for namespace, entries in self._namespaces.items()
                     for key, definition in entries.items())

    def manifest(self):
        return dict(self._owners)

    def role(self, role):
        """Content ids declaring a role, in registration order."""
        if not isinstance(role, str) or not ROLE.fullmatch(role):
            raise ValueError(f'Invalid role: {role}')
        return tuple(self._roles.get(role, ()))

    def roles_of(self, key):
        return _roles(key, self.definition(key))

    def roles(self):
        return {role: tuple(ids) for role, ids in self._roles.items()}

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
