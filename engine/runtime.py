"""通用 Mod 装载、依赖排序、阶段结算与工具转换；不包含战国规则。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib import import_module
import inspect
import json
import re
from pathlib import Path
import tomllib
from .state import active_unit, unit_context, own, freeze, Owned, references
from functools import wraps, cached_property
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_read_views = ContextVar("read_views", default=None)
from typing import Any, Callable, get_args, get_origin, get_type_hints
from .content import ContentRegistry


class ModError(ValueError):
    pass


class ToolArgumentError(ValueError):
    """Invalid transport arguments, before domain behavior is invoked."""
    pass


class DomainRejection(ValueError):
    """业务拒绝：动作本身合法、世界状态不允许（钱不够、条约不允许、越界）。

    与 ToolArgumentError 的区别是「错在谁」：ToolArgumentError 是调用方把参数
    写错了，DomainRejection 是引擎按规则拒绝了这次动作。两者都只让这一个动作
    作废，游戏继续；调用方异常的异常（模块内部 bug）走 internal_fault。
    """
    def __init__(self,message,details=None):
        super().__init__(message);self.details=details


def dependency_order(nodes, *, requires, after, before):
    """Requires 负责存在性，After/Before 负责顺序；同级按 ID 稳定排序。"""
    edges = {name: set() for name in nodes}
    for name in nodes:
        for dep in requires(name):
            if dep not in nodes:
                raise ModError(f"{name} 缺少依赖 {dep}")
        for dep in after(name):
            if dep in nodes:
                edges[name].add(dep)
        for dep in before(name):
            if dep in nodes:
                edges[dep].add(name)
    result = []
    while edges:
        ready = sorted(n for n, deps in edges.items() if not deps)
        if not ready:
            raise ModError(f"依赖存在环：{', '.join(sorted(edges))}")
        for name in ready:
            result.append(name)
            del edges[name]
        for deps in edges.values():
            deps.difference_update(ready)
    return result


def _convert(value, annotation):
    if annotation in (inspect.Parameter.empty, Any):
        return value
    if annotation is bool:
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "yes", "1", "是", "接受"):
            return True
        if str(value).lower() in ("false", "no", "0", "否", "拒绝"):
            return False
        raise ValueError("需要布尔值")
    if annotation is int:
        if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
            raise ValueError("需要整数")
        return int(value)
    if annotation in (str, float):
        return annotation(value)
    origin = get_origin(annotation)
    if origin is list:
        if not isinstance(value, list):
            raise ValueError("需要数组")
        return [_convert(v, get_args(annotation)[0]) for v in value]
    if origin is not None and type(None) in get_args(annotation):
        if value is None:
            return None
        return _convert(value, next(a for a in get_args(annotation) if a is not type(None)))
    return value


def _schema(annotation):
    if annotation is dict or get_origin(annotation) is dict:
        result={"type":"object"}
        args=get_args(annotation)
        if args:result['additionalProperties']=_schema(args[1])
        return result
    if get_origin(annotation) is list:
        return {"type": "array", "items": _schema(get_args(annotation)[0])}
    if get_origin(annotation) is not None and type(None) in get_args(annotation):
        return {"anyOf": [_schema(a) if a is not type(None) else {"type": "null"}
                          for a in get_args(annotation)]}
    return {"type": {str: "string", int: "integer", float: "number", bool: "boolean"}.get(annotation, "string")}


@dataclass(frozen=True)
class Tool:
    name: str
    handler: Callable
    aliases: tuple[str, ...] = ()
    description: str = ""
    # adapter 是 Mod 自己的坐标/旧参数转换，不属于通用引擎。
    adapter: Callable | None = None
    parameters: dict | None = None
    argument_types: dict = field(default_factory=dict)
    read_only: bool = False
    content_parameters: tuple[str, ...] = ()

    @cached_property
    def signature(self):return inspect.signature(self.handler)

    @cached_property
    def hints(self):return get_type_hints(self.handler)

    def prepare(self, world, actor, arguments):
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("工具参数须为对象")
        if self.adapter:
            arguments = self.adapter(world, actor, dict(arguments))
        arguments=dict(arguments)
        # 声明即契约：schema 写了 additionalProperties=false，运行时就得真的拒收
        # 多余参数，否则它们会一路渗进 handler 变成未捕获的 TypeError。
        if self.parameters and self.parameters.get('additionalProperties') is False:
            allowed = set(self.parameters.get('properties') or ())
            unknown = sorted(k for k in arguments if k not in allowed)
            if unknown:
                raise ValueError('未知参数：' + '、'.join(unknown))
        missing=[key for key in (self.parameters or {}).get('required',()) if key not in arguments]
        if missing:raise ValueError('缺少参数：'+'、'.join(missing))
        for key in self.content_parameters:
            if key in arguments:arguments[key]=world.runtime.content.resolve(arguments[key])
        arguments = {k: _convert(v, self.argument_types.get(k, Any)) for k, v in arguments.items()}
        sig = self.signature
        bound = sig.bind(world, actor, **arguments)
        hints = self.hints
        for name, value in list(bound.arguments.items())[2:]:
            bound.arguments[name] = _convert(value, hints.get(name, sig.parameters[name].annotation))
        return bound

    def call(self, world, actor, arguments):
        try:
            bound=self.prepare(world,actor,arguments)
        except (ValueError,TypeError) as exc:
            raise ToolArgumentError(str(exc)) from exc
        return self.handler(*bound.args, **bound.kwargs)

    def schema(self):return deepcopy(self._schema_value)

    @cached_property
    def _schema_value(self):
        sig = self.signature
        hints = self.hints
        props, required = {}, []
        for p in list(sig.parameters.values())[2:]:
            if p.kind == inspect.Parameter.VAR_KEYWORD:
                for key, annotation in self.argument_types.items():
                    props[key] = _schema(annotation)
                continue
            props[p.name] = _schema(hints.get(p.name, p.annotation))
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
        params = self.parameters or {"type": "object", "properties": props,
                                     "required": required, "additionalProperties": False}
        return {"type": "function", "function": {"name": self.name,
                "description": self.description or inspect.getdoc(self.handler) or self.name,
                "parameters": params}}


@dataclass(frozen=True)
class Panel:
    name: str
    handler: Callable
    title: str = ''


@dataclass(frozen=True)
class Stage:
    name: str
    handler: str | Callable
    requires: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()
    title: str = ""
    description: str = ""
    hook: str = ""
    needs: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    scope: str = "world"


@dataclass(frozen=True)
class RuleSection:
    title: str
    body: str
    keywords: tuple[str, ...] = ()
    summary: str = ""
    player_visible: bool = True


class GameMod(ABC):
    """玩法模块虚类。只注册能力；加载与调度由 Runtime 完成。"""
    @abstractmethod
    def mixins(self) -> tuple[type, ...]:
        raise NotImplementedError

    def tools(self) -> tuple[Tool, ...]:
        return ()

    def schema_for_actor(self, world, actor, schema):
        return schema

    def stages(self) -> tuple[Stage, ...]:
        return ()

    def state_keys(self): return ()
    def needs(self): return ()
    def optional(self): return ()
    """可选依赖：能力在时可以用（仍需在 Interfaces 里声明方法），不在时不拦住装载。"""
    def provides(self): return ()
    def numbers(self): return {}
    def presentations(self): return {}
    def panels(self): return ()
    def objects(self, world): return {}
    def content_definitions(self): return {}

    def apply_state_change(self, world, key, operation):
        if key not in self.state_keys(): raise ModError(f'无写入所有权：{key}')
        return operation()

    def before_tool(self, world, actor, name, arguments):
        pass

    def after_tool(self, world, actor, name, result):
        return result

    def configure(self, declaration, path):
        self.declaration = declaration
        self.declaration_path = path

    def rule_sections(self, world=None):
        return ()

    def initialize_world(self, world):
        pass

    def save_state(self, world):
        return {}

    def load_state(self, world, state):
        if state:
            raise ModError(f'{type(self).__name__} 不支持这些存档状态')


@dataclass
class Unit:
    name: str
    mod: GameMod
    requires: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()
    version: str = "1"


class Runtime:
    def __init__(self, units, distribution=None):
        self.distribution = distribution or {}
        hooks = self.distribution.get('Hooks', [])
        if not isinstance(hooks, list): raise ModError('Hooks 必须是发行版的有序列表')
        self.hook_definitions = {}
        for hook in hooks:
            if not isinstance(hook, dict) or not isinstance(hook.get('Id'), str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]+', hook['Id']):
                raise ModError('挂点 Id 非法')
            if hook['Id'] in self.hook_definitions: raise ModError(f'重复的挂点 Id：{hook["Id"]}')
            self.hook_definitions[hook['Id']] = dict(hook)
        self.hooks = tuple(self.hook_definitions)
        self.content = ContentRegistry()
        self.presentations, self.state_owners, self.capabilities, self.number_owners = {}, {"polity": "$engine", "phase": "$engine"}, {}, {}
        self.command_actors = {}
        self.panel_map, self.panel_owners = {}, {}
        units = list(units)
        import threading
        self._player_lock=threading.RLock()
        self.units = {u.name: u for u in units}
        if len(self.units) != len(units):
            raise ModError("重复的 Mod ID")
        if '$engine' in self.units:raise ModError('保留的运行时状态 ID：$engine')
        self.order = dependency_order(self.units, requires=lambda n: self.units[n].requires,
                                      after=lambda n: self.units[n].after,
                                      before=lambda n: self.units[n].before)
        # Collect all domains before any consumer enumerates their content.
        for name in self.order:
            self.units[name].mod.runtime = self
        for name in self.order:
            self.register_content(name, self.units[name].mod.content_definitions())
        for name in self.order:
            mod = self.units[name].mod
            mod.runtime = self
            for panel in mod.panels():
                if panel.name in self.panel_map: raise ModError(f"面板名称冲突：{panel.name}")
                self.panel_map[panel.name] = panel
                self.panel_owners[panel.name] = name
            for key in mod.state_keys():
                if key in self.state_owners:
                    raise ModError(f"state 有多个写入所有者：{key}")
                self.state_owners[key] = name
            for key in mod.provides():
                if key in self.capabilities:
                    raise ModError(f"能力有多个提供方：{key}")
                self.capabilities[key] = name
            for key in mod.numbers():
                if key in self.number_owners:
                    raise ModError(f"数值有多个所有者：{key}")
                self.number_owners[key] = name
            for key, value in mod.presentations().items():
                if key in self.presentations:
                    raise ModError(f"只读展示接口冲突：{key}")
                @wraps(value) if callable(value) else (lambda f:f)
                def display(*args, _fn=value, _owner=name, **kwargs):
                    with unit_context(_owner): return _fn(*args,**kwargs)
                self.presentations[key] = display if callable(value) else value
        for name in self.order:
            declared = self.units[name].mod
            for need in declared.needs():
                if need not in self.capabilities:
                    raise ModError(f"{name} 所需能力无提供者：{need}（state 无主或挂点未装配）")
        missing = set(self.distribution.get('RequiredState', ())) - self.state_owners.keys()
        if missing:
            raise ModError(f"state 无主：{', '.join(sorted(missing))}")
        gate=self.distribution.get('ActionGate')
        if gate and (not isinstance(gate,dict) or set(gate)!={'Capability','Method'} or gate['Capability'] not in self.capabilities or not isinstance(gate['Method'],str) or gate['Method'].startswith('_')):
            raise ModError('行动门声明缺少公共能力或方法')
        # 发行版的只读转口由模块内容组成，不从界面层取内容。
        for mod in (u.mod for u in units):
            install = getattr(mod, 'install_presentations', None)
            if install: install(self.presentations)
        self.policies = {name: self.units[name].mod for name in self.order
                         if getattr(self.units[name].mod, 'declaration', {}).get('Mod', {}).get('Kind') == 'actor'}
        for mod in (u.mod for u in units):
            self.command_actors.update(getattr(mod, 'command_actors', lambda: {})())
        self.tools, self.stage_map, self.tool_owners, self.stage_owners = {}, {}, {}, {}
        for name in self.order:
            mod = self.units[name].mod
            for tool in mod.tools():
                for key in (tool.name, *tool.aliases):
                    if key in self.tools:
                        raise ModError(f"工具名称/别名冲突：{key}")
                    self.tools[key] = tool
                    self.tool_owners[key] = mod
            for stage in mod.stages():
                if stage.name in self.stage_map:
                    raise ModError(f"结算阶段冲突：{stage.name}")
                self.stage_map[stage.name] = stage
                self.stage_owners[stage.name] = name
        clock=self.distribution.get('PlayerClock')
        if clock and (not isinstance(clock,dict) or set(clock)!={'Capability','Method'} or clock['Capability'] not in self.capabilities or not isinstance(clock['Method'],str) or not clock['Method'] or clock['Method'].startswith('_')):
            raise ModError('玩家时钟必须引用公共能力和方法')
        player_state=self.distribution.get('PlayerState',())
        if not isinstance(player_state,(list,tuple)) or any(not isinstance(name,str) or name not in self.tools or not self.tools[name].read_only for name in player_state) or len(set(player_state))!=len(player_state):
            raise ModError('玩家状态必须声明唯一的只读工具')
        events=self.distribution.get('PlayerEvents')
        if events and (not isinstance(events,str) or events not in self.tools or not self.tools[events].read_only):raise ModError('玩家事件必须引用只读工具')
        from .player import BUILTINS
        if any(name in self.tools for name in BUILTINS):raise ModError('模块工具不能占用玩家 API 保留名称')
        if self.hooks or any(stage.hook for stage in self.stage_map.values()):
            condition_providers = {}
            for name, stage in self.stage_map.items():
                if stage.hook not in self.hooks:
                    raise ModError(f"未知挂点：{stage.hook}")
                for condition in stage.provides:
                    if condition in condition_providers:
                        raise ModError(f"条件有多个提供方：{condition}")
                    condition_providers[condition] = name
            dependencies = {}
            for name, stage in self.stage_map.items():
                dependencies[name] = []
                for required in stage.requires:
                    if required not in self.stage_map: raise ModError(f'{name} 缺少阶段依赖：{required}')
                for condition in stage.needs:
                    if condition not in condition_providers:
                        raise ModError(f"{name} 挂点条件无人提供：{condition}")
                    provider = condition_providers[condition]
                    if self.hooks.index(self.stage_map[provider].hook) > self.hooks.index(stage.hook):
                        raise ModError(f"条件位于消费挂点之后：{condition}")
                    dependencies[name].append(provider)
                for provider in stage.after:
                    if provider not in self.stage_map: raise ModError(f'{name} 缺少阶段依赖：{provider}')
                    dependencies[name].append(provider)
            for name, stage in self.stage_map.items():
                for target in stage.before:
                    if target not in self.stage_map: raise ModError(f'{name} 缺少阶段依赖：{target}')
                    dependencies[target].append(name)
            for name, providers in dependencies.items():
                for provider in providers:
                    if self.hooks.index(self.stage_map[provider].hook) > self.hooks.index(self.stage_map[name].hook):
                        raise ModError(f'阶段依赖违背发行版挂点顺序：{provider} → {name}')
            self.stage_order = []
            for hook in self.hooks:
                nodes = {n for n, stage in self.stage_map.items() if stage.hook == hook}
                self.stage_order.extend(dependency_order(nodes,
                    requires=lambda n: (), after=lambda n: [p for p in dependencies[n] if p in nodes], before=lambda n: ()))
        else:
            self.stage_order = dependency_order(self.stage_map,
                requires=lambda n: self.stage_map[n].requires,
                after=lambda n: self.stage_map[n].after,
                before=lambda n: self.stage_map[n].before)

    @classmethod
    def load(cls, directory):
        units = []
        directory = Path(directory)
        if directory.is_file():
            config = tomllib.loads(directory.read_text(encoding='utf-8'))
            paths = [directory.parent / package / 'mod.toml' for package in config['Load']['Packages']]
        else:
            paths = sorted(directory.glob('*.toml'))
        distribution = {}
        expanded = []
        for path in paths:
            data = tomllib.loads(path.read_text(encoding='utf-8'))
            if 'Distribution' in data:
                if distribution: raise ModError('同时加载多个发行版')
                distribution = {**data['Distribution'], 'Hooks': data.get('Hooks', [])}
                expanded.extend(path.parent / member for member in distribution['Members'])
            else:
                expanded.append(path)
        declarations=[]
        for path in expanded:
            if not path.is_file(): raise ModError(f'模块声明缺失：{path}（挂点或 state 无主）')
            declarations.append((path,tomllib.loads(path.read_text(encoding='utf-8'))))
        if distribution:
            declared_states={'polity','phase'}
            for path,data in declarations:
                declared_states.update(data.get('State',{}).get('Owns',()))
            missing_states=set(distribution.get('RequiredState',()))-declared_states
            if missing_states: raise ModError(f'state 无主：{", ".join(sorted(missing_states))}')
        for path in expanded:
            declaration = tomllib.loads(path.read_text(encoding='utf-8'))
            data = declaration['Mod']
            module, name = data['Entry'].split(':')
            mod = getattr(import_module(module), name)()
            if not isinstance(mod, GameMod):
                raise ModError(f"{path} 未实现 GameMod")
            mod.configure(declaration, path)
            units.append(Unit(data['Id'], mod, tuple(data.get('Requires', [])),
                              tuple(data.get('After', [])), tuple(data.get('Before', [])),
                              str(data['Version'])))
        if not units:
            raise ModError(f"没有 Mod 声明：{directory}")
        return cls(units, distribution)

    def register_content(self, owner, definitions):
        if owner not in self.units: raise ModError(f'内容注册者未加载：{owner}')
        try:
            self.content.register(owner, definitions)
        except ValueError as exc:
            raise ModError(str(exc)) from exc

    def world_class(self, name="World"):
        runtime = self
        bases = tuple(m for n in self.order for m in self.units[n].mod.mixins())
        names = {}
        for base in bases:
            for method in base.__dict__:
                if not method.startswith("__") and method in names:
                    raise ModError(f"Mod 方法冲突：{method} ({names[method]} / {base.__name__})")
                names[method] = base.__name__
        initializers = [b.__dict__['__init__'] for b in bases if '__init__' in b.__dict__]
        if len(initializers) > 1:
            raise ModError('多个 Mod 定义世界构造器；扩展应使用 initialize_world')
        def initialize(world, *args, **kwargs):
            if initializers:
                initial_owner = next(n for n in runtime.order if any('__init__' in b.__dict__ for b in runtime.units[n].mod.mixins()))
                with unit_context(initial_owner):
                    initializers[0](world,*args,**kwargs)
            else:
                object.__init__(world)
            if not getattr(world, "_mod_initialized", False):
                runtime.initialize_state(world)
            for mod_name in runtime.order:
                finish = getattr(runtime.units[mod_name].mod, 'finish_world', None)
                if finish: finish(world)
        def assign(world, key, value):
            if key in runtime.state_owners:
                return runtime.write_state(world,key,lambda:object.__setattr__(world,key,own(references(value),world,key)))
            descriptor = getattr(type(world), key, None)
            if isinstance(descriptor, property) and descriptor.fset:
                return descriptor.fset(world,value)
            if active_unit.get() and not key.startswith('_'):
                raise ModError(f'state 无主：{key}')
            return object.__setattr__(world,key,value)
        def read(world,key):
            value=object.__getattribute__(world,key)
            if active_unit.get() is None and isinstance(value,Owned): return value._raw
            if active_unit.get() is None: return value
            if key in runtime.state_owners:
                wrapped=own(value,world,key)
                if wrapped is not value:object.__setattr__(world,key,wrapped)
                return wrapped
            return value
        namespace={"runtime":runtime,'__init__':initialize,'__setattr__':assign,'__getattribute__':read,
            "resolve_turn":lambda w:runtime.settle(w),
            "call_tool":lambda w,actor,tool,args:runtime.call(w,actor,tool,args)}
        def wrap(method, owner):
            @wraps(method)
            def invoke(*args,**kwargs):
                caller=active_unit.get()
                with unit_context(owner): result=method(*args,**kwargs)
                return result._raw if caller is None and isinstance(result,Owned) else result
            return invoke
        for owner in runtime.order:
            for base in runtime.units[owner].mod.mixins():
                for key, method in base.__dict__.items():
                    if key.startswith('__'):continue
                    if isinstance(method,classmethod):namespace[key]=classmethod(wrap(method.__func__,owner))
                    elif isinstance(method,staticmethod):namespace[key]=staticmethod(wrap(method.__func__,owner))
                    elif isinstance(method,property):namespace[key]=property(wrap(method.fget,owner),method.fset,method.fdel)
                    elif callable(method):namespace[key]=wrap(method,owner)
        return type(name,bases or (object,),namespace)

    def read_state(self, world, key):
        if key not in self.state_owners: raise ModError(f'state 无主：{key}')
        return freeze(getattr(world,key))

    def write_state(self, world, key, operation):
        owner=self.state_owners.get(key)
        if owner is None: raise ModError(f'state 无主：{key}')
        requester=active_unit.get()
        if requester and requester != owner:
            requests=getattr(self.units[requester].mod,'declaration',{}).get('State',{}).get('Requests',())
            if key not in requests: raise ModError(f'{requester} 未声明向 {owner} 请求写入 state：{key}')
        if owner == "$engine": return operation()
        with unit_context(owner):
            return self.units[owner].mod.apply_state_change(world,key,operation)

    @contextmanager
    def read_scope(self, world):
        """Share derived reads only within one immutable observation, never across actions."""
        current=_read_views.get()
        if current is not None and current[0] is world:
            yield
            return
        token=_read_views.set((world, {}))
        try:yield
        finally:_read_views.reset(token)

    def derived(self, world, key, calculate):
        current=_read_views.get()
        if current is None or current[0] is not world:return calculate()
        if key not in current[1]:current[1][key]=calculate()
        return current[1][key]

    def object_call(self, world, capability, method, *args, **kwargs):
        """调用声明过的对象接口；所有者负责字段变更与校验。"""
        if method.startswith('_'): raise ModError('对象接口不能调用私有方法')
        owner = self.capabilities.get(capability)
        if owner is None: raise ModError(f'对象接口无提供者：{capability}')
        caller = active_unit.get()
        if caller and caller != owner:
            declaration = self.units[caller].mod.declaration
            mod = self.units[caller].mod
            if capability not in (*mod.needs(), *mod.optional()) or method not in declaration.get('Interfaces',{}).get(capability,()):
                raise ModError(f'{caller} 未声明对象接口调用：{capability}.{method}')
        objects = self.derived(world,('objects',owner),lambda:getattr(self.units[owner].mod, 'objects', lambda w: {})(world))
        target = objects.get(capability)
        if target is None: raise ModError(f'对象接口未注册：{capability}')
        with unit_context(owner): return getattr(target,method)(*args,**kwargs)

    def save_mod_state(self, world):
        return {'$engine':{'phase':getattr(world,'phase','行动'),'polity':dict(getattr(world,'polity',{}))},
                **{name: self.units[name].mod.save_state(world) for name in self.order}}

    def load_mod_state(self, world, states):
        if not isinstance(states,dict) or set(states) != set(self.units)|{'$engine'}:
            raise ModError('存档 Mod 状态清单与当前环境不相同')
        system=states['$engine']
        if not isinstance(system,dict) or set(system)!={'phase','polity'} or not isinstance(system['polity'],dict):raise ModError('非法运行时存档状态')
        if system['phase'] not in ('行动','已结算','结算失败',*self.hooks,*self.stage_map):raise ModError('存档阶段不存在')
        if any(not isinstance(actor,str) or not isinstance(tag,str) or tag and self.find_policy(tag) is None for actor,tag in system['polity'].items()):raise ModError('存档引用未加载的作用域模块')
        for name in self.order:
            with unit_context(name): self.units[name].mod.load_state(world, states[name])
        collection=getattr(world,self.distribution.get('ActorCollection','actors'),{})
        if any(actor not in collection for actor in system['polity']):raise ModError('作用域状态引用不存在的行动者')
        with unit_context(None):
            world.phase=system['phase'];world.polity=dict(system['polity'])
        for tag in getattr(world, 'polity', {}).values():
            if tag and self.find_policy(tag) is None: raise ModError(f'存档引用未加载的作用域模块：{tag}')

    def call(self, world, actor, name, arguments):
        if name not in self.tools:
            raise ValueError(f"未知工具：{name}")
        owner = self.tool_owners[name]
        self.require_tool(world, actor, self.tools[name].name)
        unit = next(n for n,u in self.units.items() if u.mod is owner)
        with unit_context(unit):
            owner.before_tool(world,actor,self.tools[name].name,arguments)
            result=self.tools[name].call(world,actor,arguments)
            return owner.after_tool(world,actor,self.tools[name].name,result)

    def schemas(self):
        return [self.tools[n].schema() for n in sorted(self.tools) if n == self.tools[n].name]

    def player(self, world, actor, *, lock=None, on_write=None, on_fault=None, memory_query=None):
        from .player import PlayerSession
        return PlayerSession(self, world, actor, lock, on_write, on_fault, memory_query)

    def dependency_manifest(self):
        """严格匹配 Mod 版本、依赖和阶段契约；实现升级由 Mod 作者提升 Version。"""
        return {"api": 3, "distribution": self.distribution, "mods": [{"id": n, "version": self.units[n].version,
            "requires": sorted(self.units[n].requires), "after": sorted(self.units[n].after),
            "before": sorted(self.units[n].before),
            "declaration": getattr(self.units[n].mod, 'declaration', {}),
            "entry": type(self.units[n].mod).__module__ + ":" + type(self.units[n].mod).__name__}
            for n in sorted(self.units)], "stages": [{"id": n,
            "requires": sorted(self.stage_map[n].requires), "after": sorted(self.stage_map[n].after),
            "before": sorted(self.stage_map[n].before), "hook": self.stage_map[n].hook,
            "needs": list(self.stage_map[n].needs), "provides": list(self.stage_map[n].provides), "scope": self.stage_map[n].scope} for n in self.stage_order],
            "state_owners": self.state_owners,
            "tools": self.schemas(), "panels": sorted(self.panel_map),
            "content": self.content.manifest()}

    def check_save_dependencies(self, manifest):
        if manifest != self.dependency_manifest():
            raise ModError("存档 Mod 依赖/版本/接口与当前加载环境不相同，拒绝加载")

    def settlement_table(self):
        lines = ['| 次序 | 阶段 | 内容 |', '|---|---|---|']
        for i, name in enumerate(self.stage_order, 1):
            stage = self.stage_map[name]
            hook = self.hook_definitions.get(stage.hook, {})
            lines.append(f'| {i} | {hook.get("Name", stage.title or name)} | {stage.description or hook.get("Description", hook.get("Name", ""))} |')
        return '\n'.join(lines)

    def rule_sections(self, world=None, *, player=False):
        result = []
        for name in self.order:
            for section in self.units[name].mod.rule_sections(world):
                if player and not section.player_visible:
                    continue
                body = section.body.replace('{{SETTLEMENT_STAGES}}', self.settlement_table())
                result.append(RuleSection(section.title, body, section.keywords,
                                          section.summary, section.player_visible))
        order = self.distribution.get('ManualOrder', ())
        result.sort(key=lambda section: order.index(section.title) if section.title in order else len(order))
        return result

    def manual(self):
        return '# 规则纲要\n\n由已加载 Mod 提供规则内容；结算顺序由阶段依赖自动生成。\n\n' + '\n\n'.join(
            '## ' + s.title + '\n\n' + s.body.strip() for s in self.rule_sections()) + '\n'

    def rules(self, world, topic=''):
        sections = self.rule_sections(world, player=True)
        topic = str(topic or '').strip().lower()
        matches = []
        for i, section in enumerate(sections):
            for keyword in (section.title, *section.keywords):
                if topic and keyword.lower() in topic:
                    matches.append((len(keyword), i))
        if matches:
            best = max(length for length, _ in matches)
            indices = sorted({i for length, i in matches if length == best})
            return '\n\n'.join('【' + sections[i].title + '】\n' + sections[i].body for i in indices)
        return '【规则书目录】（rules(topic=主题)）：\n' + '\n'.join(
            f'- {s.title}' + (f'：{s.summary}' if s.summary else '') for s in sections)

    def settle(self, world):
        if getattr(world, "phase", "行动") not in ("行动", "已结算"):
            raise RuntimeError("不能重入世界结算")
        context = {}
        try:
            for name in self.stage_order:
                world.phase = self.stage_map[name].hook or name
                stage = self.stage_map[name]
                with unit_context(self.stage_owners[name]):
                    if stage.scope == 'actor':
                        for actor in self.participants(world,stage):
                            if isinstance(stage.handler,str): getattr(world,stage.handler)(actor,context)
                            else: stage.handler(world,actor,context)
                    elif stage.scope == 'world':
                        if isinstance(stage.handler,str): getattr(world,stage.handler)(context)
                        else: stage.handler(world,context)
                    else:
                        raise ModError(f'未知作用域：{stage.scope}')

        except Exception:
            world.phase = "结算失败"
            raise
        world.phase = "已结算"
        return context.get("result", context)

    def initialize_state(self, world):
        with unit_context(None):
            world.polity = {}
            world.phase = "行动"
        for name in self.order:
            with unit_context(name):
                self.units[name].mod.initialize_world(world)
        world._mod_initialized = True

    def enumerate_numbers(self):
        from copy import deepcopy
        return deepcopy({name: dict(self.units[name].mod.numbers()) for name in self.order})

    def actor_policy(self, world, actor):
        tag = getattr(world, 'polity', {}).get(actor, '')
        return next((m for n, m in self.policies.items()
            if tag == n or tag in m.declaration['Mod'].get('Aliases', ())), None)

    def permits(self, world, actor, capability):
        policy = self.actor_policy(world, actor)
        cfg = policy.declaration.get('Policy', {}) if policy else {}
        return capability in cfg.get('Enable', ()) or capability not in cfg.get('Disable', ())

    def in_scope(self, world, actor, unit):
        mod = self.units[unit].mod if isinstance(unit, str) else unit
        return getattr(mod, 'declaration', {}).get('Mod', {}).get('Kind') != 'actor' or self.actor_policy(world, actor) is mod

    def tool_available(self, world, actor, name):
        return self.in_scope(world, actor, self.tool_owners[name]) and self.permits(world, actor, 'tool.' + self.tools[name].name)

    def require_tool(self, world, actor, name):
        if not self.in_scope(world, actor, self.tool_owners[name]):
            raise ValueError('该工具不属于行动者的作用域')
        if not self.permits(world, actor, 'tool.' + name):
            raise ValueError(self.actor_policy(world, actor).declaration.get("Policy",{}).get("Rejection","政体禁用该能力"))
        if self.distribution:
            collection = getattr(world, self.distribution.get('ActorCollection', 'actors'), {})
            if actor not in collection: raise ValueError('行动者不存在')
            if world.phase != '行动' and not self.tools[name].read_only:
                raise ValueError('仅行动阶段可以提交动作')
            gate=self.distribution.get('ActionGate')
            if gate and not self.tools[name].read_only and not self.object_call(world,gate['Capability'],gate['Method'],actor):
                raise ValueError('行动者已结束本轮行动')

    def parameter(self, world, actor, key, default=None):
        if not self.permits(world,actor,'number.'+key): raise ModError('该数值口径被作用域声明禁用')
        policy = self.actor_policy(world, actor)
        return getattr(policy, 'parameter_overrides', lambda: policy.declaration.get('Policy',{}).get('Overrides',{}))().get(key, default) if policy else default

    def find_policy(self, policy_id):
        return next((m for n,m in self.policies.items() if policy_id == n or policy_id in m.declaration['Mod'].get('Aliases', ())), None)

    def activate_actor(self, world, actor, policy_id, **kwargs):
        policy = self.find_policy(policy_id)
        if policy is None: raise ModError(f'未加载所需政体模块：{policy_id}')
        if actor not in getattr(world,self.distribution.get('ActorCollection','actors'),{}): raise ModError('作用对象不存在')
        if self.actor_policy(world, actor) is policy: return False
        unit=next(n for n,m in self.policies.items() if m is policy)
        with unit_context(unit): policy.activate_actor(world,actor,**kwargs)
        world.polity[actor] = policy.declaration.get('Policy',{}).get('Tag',policy.declaration['Mod']['Id'])
        return True

    def deactivate_actor(self, world, actor):
        return self.write_state(world,'polity',lambda:world.polity.pop(actor,None))

    def actor_prompt(self, world, actor, default):
        policy = self.actor_policy(world, actor)
        return policy.prompt(world, actor) if policy and hasattr(policy,'prompt') else default(world, actor)

    def participants(self, world, stage):
        collection = getattr(world,self.distribution.get('ActorCollection','actors'),{})
        return [actor for actor in collection
            if self.in_scope(world,actor,self.stage_owners[stage.name])
            and self.permits(world,actor,'stage.'+stage.name)
            and self.permits(world,actor,'hook.'+(stage.hook or stage.name))]

    def initial_actors(self, declarations):
        return [item['name'] for item in declarations if item.get('enabled',True)
            and (not item.get('polity') or self.find_policy(item['polity'])
                 and item.get('appearance')=='initial')]

    def actor_schedule(self, world, declarations, *, seed=None):
        import random
        collection=getattr(world,self.distribution.get('ActorCollection','actors'),{})
        result={}
        for item in declarations:
            name,policy=item.get('name'),item.get('polity')
            if not name or not policy or not item.get('enabled',True) or name in collection or not self.find_policy(policy):continue
            lo=int(item.get('enable_turn') or 0)
            hi=int(item.get('enable_turn_max') or lo)
            rng=random.Random(f'{getattr(world,"seed",0) if seed is None else seed}:{name}')
            result[name]=rng.randint(lo,hi) if hi>=lo else lo
        return result

    def panel(self,world,actor,name,arguments=None):
        if name not in self.panel_map: raise ModError(f'未知面板：{name}')
        if not self.in_scope(world,actor,self.panel_owners[name]): raise ModError('该面板不属于行动者的作用域')
        if not self.permits(world,actor,'panel.'+name): raise ModError('该面板被作用域声明禁用')
        with unit_context(self.panel_owners[name]):
            return self.panel_map[name].handler(world,actor,**(arguments or {}))
