"""Versioned, actor-bound player transport. Game facts remain in modules."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
import math
import inspect

API_VERSION = 1
BUILTINS = {
    'player_state': ('Current actor-visible facts', {}),
    'player_rules': ('Rules provided by modules in this actor scope', {'topic': {'type': 'string'}}),
    'player_events': ('Actor-visible events after a cursor', {'cursor': {'type': 'integer', 'minimum': 0}}),
}


def wire(value):
    """Return detached JSON data; never serialize module implementation objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Nonfinite public value')
        return value
    if callable(getattr(value, 'to_public', None)):
        return wire(value.to_public())
    if is_dataclass(value):
        return {f.name: wire(getattr(value, f.name)) for f in fields(value) if not f.name.startswith('_')}
    if isinstance(value, Mapping):
        return {str(k): wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    raise TypeError(f'Unsupported public value: {type(value).__name__}')


class PlayerSession:
    """Trusted in-process handle, with actor identity chosen by the host.

    Clients use only these methods. This is a permission boundary for game
    requests, not a sandbox for hostile Python running in the host process.
    """
    api_version = API_VERSION

    def __init__(self, runtime, world, actor, lock=None, on_write=None):
        self.__runtime, self.__world = runtime, world
        self.__actor = actor
        self.__lock = lock or runtime._player_lock
        self.__on_write=on_write
        self._validate()

    @property
    def actor(self):
        return self.__actor

    def _validate(self):
        collection = getattr(self.__world, self.__runtime.distribution.get('ActorCollection', 'actors'), ())
        if self.actor not in collection:
            raise ValueError('Actor is not active')

    def _period(self):
        clock = self.__runtime.distribution.get('PlayerClock')
        return self.__runtime.object_call(self.__world, clock['Capability'], clock['Method']) if clock else None

    def actions(self):
        with self.__lock:
            self._validate()
            result = [{'type':'function','function':{'name':name,'description':description,
                       'parameters':{'type':'object','properties':properties,'additionalProperties':False}}}
                      for name,(description,properties) in BUILTINS.items()]
            for schema in self.__runtime.schemas():
                name = schema['function']['name']
                try:
                    self.__runtime.require_tool(self.__world, self.actor, name)
                except ValueError:
                    continue
                owner = self.__runtime.tool_owners[name]
                result.append(wire(owner.schema_for_actor(self.__world, self.actor, deepcopy(schema))))
            return result

    def query(self, name, arguments=None):
        with self.__lock:
            self._validate()
            if arguments is not None and not isinstance(arguments,dict):raise ValueError('Arguments must be an object')
            if name in BUILTINS:
                methods={'player_state':self.observe,'player_rules':self.rules,'player_events':self.events}
                try:inspect.signature(methods[name]).bind(**(arguments or {}))
                except TypeError as exc:raise ValueError(str(exc)) from exc
                return wire(methods[name](**(arguments or {})))
            tool = self.__runtime.tools.get(name)
            if tool is None or not tool.read_only:
                raise ValueError('Query requires a registered read-only action')
            return wire(self.__runtime.call(self.__world, self.actor, name, arguments or {}))

    def observe(self):
        with self.__lock:
            self._validate()
            facts = {}
            for name in self.__runtime.distribution.get('PlayerState', ()):
                if self.__runtime.tool_available(self.__world, self.actor, name):
                    facts[name] = self.query(name)
            gate = self.__runtime.distribution.get('ActionGate')
            can_act = self.__world.phase == '行动'
            if gate:
                can_act = can_act and bool(self.__runtime.object_call(self.__world, gate['Capability'], gate['Method'], self.actor))
            return {'api': API_VERSION, 'actor': self.actor, 'period': self._period(), 'can_act': can_act, 'facts': facts}

    def submit(self, action, arguments=None):
        with self.__lock:
            base = {'api': API_VERSION, 'actor': self.actor, 'action': action, 'period': self._period()}
            collection = getattr(self.__world, self.__runtime.distribution.get('ActorCollection', 'actors'), ())
            code = 'invalid_arguments'
            try:
                if self.actor not in collection:
                    code = 'inactive_actor'
                    raise ValueError('Actor is not active')
                if not isinstance(action,str):
                    code='unknown_action';raise ValueError('Action name must be a string')
                if action in BUILTINS:
                    data=self.query(action,arguments)
                    return {**base,'ok':True,'status':'effective','code':None,'message':'','data':data}
                if action not in self.__runtime.tools:
                    code = 'unknown_action'
                    raise ValueError('Unknown action')
                code = 'unavailable_action'
                self.__runtime.require_tool(self.__world, self.actor, action)
                code = 'invalid_arguments'
                if arguments is not None and not isinstance(arguments, dict):
                    raise ValueError('Arguments must be an object')
                # ToolArgumentError distinguishes transport mistakes from
                # programming faults inside a module.
                code = 'domain_rejected'
                if not self.__runtime.tools[action].read_only and self.__on_write:self.__on_write()
                result = self.__runtime.call(self.__world, self.actor, action, arguments or {})
                data = wire(result)
            except (ValueError, KeyError) as exc:
                from .runtime import ToolArgumentError
                if isinstance(exc,ToolArgumentError):code='invalid_arguments'
                return {**base, 'ok': False, 'status': 'rejected', 'code': code, 'message': str(exc), 'data': None}
            status = data.get('status', 'effective') if isinstance(data, dict) else 'effective'
            return {**base, 'ok': status not in ('failed', 'cancelled'), 'status': status,
                    'code': 'action_failed' if status in ('failed', 'cancelled') else None,
                    'message': data.get('message', '') if isinstance(data, dict) else '', 'data': data}

    def events(self, cursor=0):
        if type(cursor) is not int or cursor < 0:
            raise ValueError('Cursor must be a nonnegative integer')
        with self.__lock:
            self._validate()
            source = self.__runtime.distribution.get('PlayerEvents')
            items = self.query(source) if source else []
            records = [e for e in items if e['id'] > cursor]
            return {'cursor': max([cursor, *(e['id'] for e in records)]), 'items': records}

    def rules(self, topic=''):
        if not isinstance(topic,str):raise ValueError('Topic must be a string')
        with self.__lock:
            self._validate()
            # Include only public sections from modules in this actor's scope.
            sections = []
            for name in self.__runtime.order:
                mod = self.__runtime.units[name].mod
                if not self.__runtime.in_scope(self.__world, self.actor, mod):
                    continue
                for section in mod.rule_sections(self.__world):
                    if not section.player_visible:
                        continue
                    if topic and str(topic).lower() not in (section.title + ' ' + ' '.join(section.keywords)).lower():
                        continue
                    sections.append({'title': section.title, 'body': section.body})
            return sections
