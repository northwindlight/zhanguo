"""通用 Mod 装载、依赖排序、阶段结算与工具转换；不包含战国规则。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
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
from weakref import WeakKeyDictionary

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
    # 只读面板等内部工具不出现在模型工具表；脚本/规则 AI 不走工具名。
    hidden: bool = False
    content_parameters: tuple[str, ...] = ()
    # tool 层字段：owner 是提供玩家 API 的能力名；None 表示沿用「哪个模块返回」。
    owner: str | None = None
    api: tuple[str, ...] = ()
    # 组合工具额外覆盖的其他能力方法（如 foreign 组合 intelligence/diplomacy）：
    # 只用于装载期覆盖与读写一致性检查，工具仍由 owner 的上下文执行。
    covers: tuple[tuple[str, str], ...] = ()
    fact: bool = False
    panel: bool = False
    groups: tuple[str, ...] = ()
    schema_for_actor: Callable | None = None

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
        parameters=self._schema_value['function']['parameters']
        if parameters.get('additionalProperties') is False:
            allowed = set(parameters.get('properties') or ())
            unknown = sorted(k for k in arguments if k not in allowed)
            if unknown:
                raise ValueError('未知参数：' + '、'.join(unknown))
        missing=[key for key in parameters.get('required',()) if key not in arguments]
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
class Action:
    """A domain action adapter inside the tool layer; never a model-facing Tool.

    The engine registers only its name/capability/method/read_only/groups so the
    umbrella can dispatch through ``Runtime.call_action``. The schema and handler
    stay in the tool layer for argument adaptation and legacy host calls.
    """
    name: str
    handler: Callable
    owner: str = ''
    api: tuple[str, ...] = ()
    description: str = ""
    adapter: Callable | None = None
    parameters: dict | None = None
    argument_types: dict = field(default_factory=dict)
    read_only: bool = False
    content_parameters: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    schema_for_actor: Callable | None = None

    @cached_property
    def signature(self):return inspect.signature(self.handler)

    @cached_property
    def hints(self):return get_type_hints(self.handler)

    def prepare(self, world, actor, arguments):
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("动作参数须为对象")
        if self.adapter:
            arguments = self.adapter(world, actor, dict(arguments))
        arguments=dict(arguments)
        parameters=self._schema_value['function']['parameters']
        if parameters.get('additionalProperties') is False:
            allowed = set(parameters.get('properties') or ())
            unknown = sorted(k for k in arguments if k not in allowed)
            if unknown:
                raise ValueError('未知参数：' + '、'.join(unknown))
        missing=[key for key in parameters.get('required',()) if key not in arguments]
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

    def player_api(self, world):
        """玩家 API 对象；tool 层通过它调用动作与只读事实。"""
        return None

    def schema_for_actor(self, world, actor, schema):
        return schema

    def stages(self) -> tuple[Stage, ...]:
        return ()

    def state_keys(self): return tuple(getattr(self, 'declaration', {}).get('State', {}).get('Owns', ()))
    def needs(self): return tuple(getattr(self, 'declaration', {}).get('Capabilities', {}).get('Needs', ()))
    def optional(self): return tuple(getattr(self, 'declaration', {}).get('Capabilities', {}).get('Optional', ()))
    """可选依赖：能力在时可以用（仍需在 Interfaces 里声明方法），不在时不拦住装载。"""
    def provides(self): return tuple(getattr(self, 'declaration', {}).get('Capabilities', {}).get('Provides', ()))
    def numbers(self): return {}
    def presentations(self): return {}
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
    def __init__(self, units, distribution=None, *, enforce_player_apis=True):
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
            declaration = getattr(mod, 'declaration', {})
            for section, key, values in (('State', 'Owns', mod.state_keys()),
                    ('Capabilities', 'Needs', mod.needs()), ('Capabilities', 'Optional', mod.optional()),
                    ('Capabilities', 'Provides', mod.provides())):
                declared = declaration.get(section, {}).get(key)
                if declared is None:continue
                if (not isinstance(declared, (list, tuple)) or any(not isinstance(v, str) or not v for v in declared)
                        or len(set(declared)) != len(declared)):
                    raise ModError(f'{name} 的 {section}.{key} 声明非法')
                # 资源提供者可随新增内容增加能力；已声明的能力仍不能凭空消失。
                matches = set(declared) <= set(values) if key == 'Provides' else set(declared) == set(values)
                if not matches:raise ModError(f'{name} 的 {section}.{key} 声明与实现漂移')
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
        self.tool_groups = {}
        self.actions, self.action_groups = {}, {}
        self.api_readonly, self.api_writes = {}, {}
        self.tool_layer = None
        self._tool_apis = WeakKeyDictionary()
        for name in self.order:
            mod = self.units[name].mod
            for tool in mod.tools():
                self._register_tool(tool, mod)
            for stage in mod.stages():
                if stage.name in self.stage_map:
                    raise ModError(f"结算阶段冲突：{stage.name}")
                self.stage_map[stage.name] = stage
                self.stage_owners[stage.name] = name
        self._load_tool_layer()
        # 策略组由工具自己的 groups 派生：政体声明 group.<组名> 即整组禁用，
        # 新增一个成员工具不需要再改政体名单。
        # API 的读写契约属于模块，不从模型工具反推。工具只校验是否与它一致。
        for capability, module_id in self.capabilities.items():
            cfg = getattr(self.units[module_id].mod, 'declaration', {}).get('PlayerAPI', {})
            methods, reads = cfg.get('Methods', ()), cfg.get('ReadOnly', ())
            if (not isinstance(methods, (list, tuple)) or not isinstance(reads, (list, tuple))
                    or any(not isinstance(m, str) or not m or m.startswith('_') for m in (*methods, *reads))
                    or len(set(methods)) != len(methods) or len(set(reads)) != len(reads)
                    or not set(reads) <= set(methods)):
                raise ModError(f'{module_id} 的 [PlayerAPI] Methods/ReadOnly 非法')
            for field in ('ContentParameters', 'Actions'):
                values = cfg.get(field, {})
                if not isinstance(values, dict) or not set(values) <= set(methods):
                    raise ModError(f'{module_id} 的 [PlayerAPI] {field} 非法')
                for value in values.values():
                    if field == 'Actions':
                        valid = isinstance(value, str) and bool(value)
                    else:
                        valid = (isinstance(value, list) and all(isinstance(v, str) and v and v != 'actor' for v in value)
                                 and len(set(value)) == len(value))
                    if not valid:raise ModError(f'{module_id} 的 [PlayerAPI] {field} 非法')
            self.api_readonly[capability] = set(reads)
            self.api_writes[capability] = set(methods) - set(reads)
        for key, tool in self.tools.items():
            if key != tool.name:
                continue
            for group in tool.groups:
                self.tool_groups.setdefault(group, []).append(tool)
            for capability, method in (*((tool.owner, m) for m in tool.api), *tool.covers):
                if method not in self.api_readonly.get(capability, ()) and tool.read_only:
                    raise ModError(f'只读工具 {tool.name} 覆盖了写 API：{capability}.{method}')
                if method in self.api_readonly.get(capability, ()) and not tool.read_only:
                    raise ModError(f'写工具 {tool.name} 覆盖了只读 API：{capability}.{method}')
        for action in self.actions.values():
            for group in action.groups:
                self.action_groups.setdefault(group, []).append(action)
            if action.read_only != (action.api[0] in self.api_readonly.get(action.owner, ())):
                raise ModError(f'动作 {action.name} 与 API 读写声明不一致')
        # 只读面板收敛：发行版声明一个分发器后，其余只读工具照旧注册（事实扇出与
        # query 分发仍按名调用），但不再出现在模型工具表里。
        panel = self.distribution.get('ReadOnlyPanel')
        if panel:
            if not isinstance(panel, str) or panel not in self.tools or not self.tools[panel].read_only:
                raise ModError('ReadOnlyPanel must reference a registered read-only tool')
            keep = set(self.distribution.get('ReadOnlyPanelKeep') or ())
            for key, tool in list(self.tools.items()):
                if tool.read_only and tool.name not in keep and tool.name != panel:
                    self.tools[key] = replace(tool, hidden=True)
        # 只读面板就是只读工具：Tool(panel=True) 登记进面板表，不再由模块另报一份
        # Panel 处理器。Runtime.panel 只负责作用域/策略分发，数据仍走 Tool.call。
        self.panel_map = {tool.name: tool for key, tool in self.tools.items()
                          if key == tool.name and tool.panel}
        self.panel_owners = {name: self.tool_owners[name] for name in self.panel_map}
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
        if enforce_player_apis:
            self._validate_player_apis()

    def _register_tool(self, tool, owner):
        for key in (tool.name, *tool.aliases):
            if key in self.tools:
                raise ModError(f"工具名称/别名冲突：{key}")
            self.tools[key] = tool
            self.tool_owners[key] = owner

    def _load_tool_layer(self):
        spec = self.distribution.get('Tools')
        if spec is None:
            return
        if not isinstance(spec, dict) or set(spec) - {'Entry', 'Version'} or not isinstance(spec.get('Entry'), str):
            raise ModError('Distribution.Tools 必须声明 Entry')
        entry = spec['Entry']
        if entry.count(':') != 1:
            raise ModError('Distribution.Tools.Entry 非法')
        module_name, attribute = entry.split(':')
        try:
            factory = getattr(import_module(module_name), attribute)
        except (ImportError, AttributeError) as exc:
            raise ModError(f'无法加载 Distribution.Tools.Entry：{entry}') from exc
        if not callable(factory):
            raise ModError('Distribution.Tools.Entry 必须可调用')
        try:
            layer = factory(self)
        except ModError:
            raise
        except Exception as exc:
            raise ModError(f'Distribution.Tools 装配失败：{type(exc).__name__}') from exc
        if not callable(getattr(layer, 'tools', None)):
            raise ModError('Distribution.Tools 必须提供 tools()')
        self.tool_layer = layer
        for tool in layer.tools():
            if not isinstance(tool, Tool):
                raise ModError('Distribution.Tools 必须返回 Tool')
            if tool.owner is None:
                raise ModError(f'tool 层工具缺少 owner 能力：{tool.name}')
            self._register_tool(tool, self._owner_for_capability(tool.owner, tool.name))
        actions = getattr(layer, 'actions', None)
        for action in (actions() if callable(actions) else ()):
            self._register_action(action)

    def _register_action(self, action):
        if not isinstance(action, Action):
            raise ModError('Distribution.Tools.actions() 必须返回 Action')
        if action.owner not in self.capabilities:
            raise ModError(f'动作 {action.name} 的 owner 能力无提供者：{action.owner}')
        if not action.api or len(action.api) != 1:
            raise ModError(f'动作 {action.name} 必须声明恰好一个 API 方法')
        module_id = self.capabilities[action.owner]
        methods = self.units[module_id].mod.declaration.get('PlayerAPI', {}).get('Methods', ())
        if action.api[0] not in methods:
            raise ModError(f'动作 {action.name} 引用了未声明的方法：{module_id}.{action.api[0]}')
        if action.name in self.tools or action.name in self.actions:
            raise ModError(f'动作/工具名称冲突：{action.name}')
        self.actions[action.name] = action

    def _owner_for_capability(self, capability, tool_name):
        if capability not in self.capabilities:
            raise ModError(f'工具 {tool_name} 的 owner 能力无提供者：{capability}')
        module_id = self.capabilities[capability]
        if module_id not in self.units:
            raise ModError(f'工具 {tool_name} 的 owner 模块未加载：{module_id}')
        return self.units[module_id].mod

    def player_api(self, world, capability):
        """Resolve a player API object through its capability, cached per world."""
        if capability not in self.capabilities:
            raise ModError(f'玩家 API 能力无提供者：{capability}')
        module_id = self.capabilities[capability]
        cache = self._tool_apis.get(world)
        if cache is None:
            cache = {}
            self._tool_apis[world] = cache
        if module_id not in cache:
            mod = self.units[module_id].mod
            with unit_context(module_id):
                api = mod.player_api(world)
            if api is None:
                raise ModError(f'模块未提供玩家 API：{module_id}')
            cache[module_id] = api
        return cache[module_id]

    def player_facts(self):
        declared = self.distribution.get('PlayerState')
        if declared is not None:
            return tuple(declared)
        exclude = set(self.distribution.get('PlayerStateExclude') or ())
        include = set(self.distribution.get('PlayerStateInclude') or ())
        names = [tool.name for key, tool in self.tools.items()
                 if key == tool.name and tool.fact and tool.name not in exclude]
        if include:
            names = [name for name in names if name in include]
        return tuple(names)

    def _validate_player_apis(self):
        declarations = {}
        for name in self.order:
            mod = self.units[name].mod
            methods = getattr(mod, 'declaration', {}).get('PlayerAPI', {}).get('Methods', ())
            if not isinstance(methods, (list, tuple)) or any(not isinstance(m, str) or not m for m in methods) or len(set(methods)) != len(methods):
                raise ModError(f'{name} 的 [PlayerAPI] Methods 非法')
            if not methods:
                continue
            if type(mod).player_api is GameMod.player_api:
                raise ModError(f'{name} 声明了 [PlayerAPI] 但未实现 player_api')
            if mod.tools():
                raise ModError(f'{name} 声明了 [PlayerAPI] 但仍在 tools() 返回工具')
            declarations[name] = set(methods)
        covered = {name: set() for name in declarations}
        for key, tool in self.tools.items():
            if key != tool.name:
                continue
            if (tool.fact or tool.panel) and not tool.read_only:
                raise ModError(f'工具 {tool.name} 的 fact/panel 必须只读')
            if not tool.api:
                continue
            if tool.owner is None:
                raise ModError(f'工具 {tool.name} 声明了 api 但没有 owner')
            module_id = self.capabilities.get(tool.owner)
            if module_id is None:
                raise ModError(f'工具 {tool.name} 的 owner 能力无提供者：{tool.owner}')
            if module_id not in declarations:
                raise ModError(f'工具 {tool.name} 的 owner 模块未声明 [PlayerAPI]：{module_id}')
            for method in tool.api:
                if method not in declarations[module_id]:
                    raise ModError(f'工具 {tool.name} 引用了未声明的方法：{module_id}.{method}')
                covered[module_id].add(method)
            for capability, method in tool.covers:
                cover_owner = self.capabilities.get(capability)
                if cover_owner is None:
                    raise ModError(f'工具 {tool.name} 的 covers 能力无提供者：{capability}')
                if cover_owner not in declarations:
                    raise ModError(f'工具 {tool.name} 的 covers 模块未声明 [PlayerAPI]：{cover_owner}')
                if method not in declarations[cover_owner]:
                    raise ModError(f'工具 {tool.name} 的 covers 引用了未声明的方法：{cover_owner}.{method}')
                covered[cover_owner].add(method)
        for action in self.actions.values():
            module_id = self.capabilities.get(action.owner)
            if module_id is None:
                raise ModError(f'动作 {action.name} 的 owner 能力无提供者：{action.owner}')
            if module_id not in declarations:
                raise ModError(f'动作 {action.name} 的 owner 模块未声明 [PlayerAPI]：{module_id}')
            covered[module_id].add(action.api[0])
        for name, methods in declarations.items():
            missing = methods - covered[name]
            if missing:
                raise ModError(f'{name} 的 [PlayerAPI] 方法未封装：' + '、'.join(sorted(missing)))

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
        """Host/internal dispatch: Tools keep their protocol, actions go to the API."""
        if name not in self.tools:
            if name in self.actions:
                return self.call_action(world, actor, name, arguments)
            raise ValueError(f"未知工具：{name}")
        owner = self.tool_owners[name]
        self.require_tool(world, actor, self.tools[name].name)
        unit = next(n for n,u in self.units.items() if u.mod is owner)
        with unit_context(unit):
            owner.before_tool(world,actor,self.tools[name].name,arguments)
            result=self.tools[name].call(world,actor,arguments)
            return owner.after_tool(world,actor,self.tools[name].name,result)

    def schemas(self):
        return [self.tools[n].schema() for n in sorted(self.tools)
                if n == self.tools[n].name and not self.tools[n].hidden]

    def player(self, world, actor, *, lock=None, on_write=None, on_fault=None, memory_query=None):
        from .player import PlayerSession
        return PlayerSession(self, world, actor, lock, on_write, on_fault, memory_query)

    def dependency_manifest(self):
        """严格匹配 Mod 版本、依赖和阶段契约；实现升级由 Mod 作者提升 Version。"""
        distribution = dict(self.distribution)
        distribution.pop('Tools', None)
        return {"api": 3, "distribution": distribution, "mods": [{"id": n, "version": self.units[n].version,
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
                body = self.rule_text(section.body)
                result.append(RuleSection(section.title, body, section.keywords,
                                          section.summary, section.player_visible))
        order = self.distribution.get('ManualOrder', ())
        result.sort(key=lambda section: order.index(section.title) if section.title in order else len(order))
        return result

    def manual(self):
        return '# 规则纲要\n\n由已加载 Mod 提供规则内容；结算顺序由阶段依赖自动生成。\n\n' + '\n\n'.join(
            '## ' + s.title + '\n\n' + s.body.strip() for s in self.rule_sections()) + '\n'

    def rule_text(self, body):
        """Resolve rule-text placeholders against declared numbers."""
        body = body.replace('{{SETTLEMENT_STAGES}}', self.settlement_table())
        # 手册数字与模块 numbers() 同源：改参数就会改手册与生成说明。
        return re.sub(r'\{\{number:([a-zA-Z0-9_.:-]+)\}\}',
                      lambda match: str(self.number(match.group(1))), body)

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

    def number(self, key, default=None):
        """Read a declared numeric constant from its owning module.

        Cross-module rule text and manuals use this instead of importing another
        module's ``parameters``. Unknown keys are load-order bugs, not defaults.
        """
        owner = self.number_owners.get(key)
        if owner is None:
            if default is not None:
                return default
            raise ModError(f'未知数值：{key}')
        values = self.units[owner].mod.numbers()
        if key not in values:
            if default is not None:
                return default
            raise ModError(f'{owner} 未登记数值：{key}')
        return values[key]

    def actor_policy(self, world, actor):
        tag = getattr(world, 'polity', {}).get(actor, '')
        return next((m for n, m in self.policies.items()
            if tag == n or tag in m.declaration['Mod'].get('Aliases', ())), None)

    def permits(self, world, actor, capability):
        policy = self.actor_policy(world, actor)
        cfg = policy.declaration.get('Policy', {}) if policy else {}
        return (capability in self._policy_capabilities(policy, cfg, 'Enable')
                or capability not in self._policy_capabilities(policy, cfg, 'Disable'))

    def _policy_capabilities(self, policy, cfg, kind):
        """把 Policy.Enable/Disable 的 group.<名> 展开成具体能力键。

        group.diplomacy.alliance 会展开为组内每个工具的 tool.<名>、action.<名>，
        以及工具声明的其他组名（如 accept.bloc / communication.gift）——后者正是
        模块对象自己做能力检查时用的键，所以一套组声明同时覆盖工具门与领域门。
        发行版子集里可能没有组内工具（模块未装载），此时该组是空集，不报错。
        """
        cache = self.__dict__.setdefault('_policy_groups_cache', {})
        key = (id(policy), kind)
        if key in cache:
            return cache[key]
        raw = cfg.get(kind, ())
        if not any(isinstance(entry, str) and entry.startswith('group.') for entry in raw):
            cache[key] = raw
            return raw
        result = set(raw)
        for entry in raw:
            if not (isinstance(entry, str) and entry.startswith('group.')):
                continue
            result.discard(entry)
            group = entry[len('group.'):]
            for tool in self.tool_groups.get(group, ()):
                result.add('tool.' + tool.name)
                result.add('action.' + tool.name)
                result.update(tool.groups)
            for action in self.action_groups.get(group, ()):
                result.add('action.' + action.name)
                result.update(action.groups)
        cache[key] = frozenset(result)
        return cache[key]

    def in_scope(self, world, actor, unit):
        mod = self.units[unit].mod if isinstance(unit, str) else unit
        return getattr(mod, 'declaration', {}).get('Mod', {}).get('Kind') != 'actor' or self.actor_policy(world, actor) is mod

    def tool_available(self, world, actor, name):
        return self.in_scope(world, actor, self.tool_owners[name]) and self.permits(world, actor, 'tool.' + self.tools[name].name)

    def action_available(self, world, actor, name):
        action = self.actions.get(name)
        if action is None:
            return False
        module_id = self.capabilities[action.owner]
        return (self.in_scope(world, actor, self.units[module_id].mod)
                and self.permits(world, actor, 'action.' + name)
                and self.permits(world, actor, 'tool.' + name))

    def call_action(self, world, actor, name, arguments):
        """Execute a tool-layer action spec by name (umbrella / host helper)."""
        action = self.actions.get(name)
        if action is None:
            raise ValueError(f'未知动作：{name}')
        module_id = self.capabilities[action.owner]
        if not self.in_scope(world, actor, self.units[module_id].mod):
            raise ValueError('该动作不属于行动者的作用域')
        self._require_request(world, actor, name, read_only=action.read_only)
        with unit_context(module_id):
            return action.call(world, actor, arguments)

    def _require_request(self, world, actor, name, *, read_only):
        """模型工具、动作适配器和 API 共用实际执行的权限门。"""
        if any(not self.permits(world, actor, key + name) for key in ('tool.', 'action.')):
            policy = self.actor_policy(world, actor)
            raise ValueError(policy.declaration.get('Policy', {}).get('Rejection', '政体禁用该能力'))
        if self.distribution:
            collection = getattr(world, self.distribution.get('ActorCollection', 'actors'), {})
            if actor not in collection:raise ValueError('行动者不存在')
            if not read_only:
                if world.phase != '行动':raise ValueError('仅行动阶段可以提交动作')
                gate = self.distribution.get('ActionGate')
                # 权限门不是当前调用模块的跨域业务请求。
                with unit_context(None):
                    if gate and not self.object_call(world, gate['Capability'], gate['Method'], actor):
                        raise ValueError('行动者已结束本轮行动')

    def require_tool(self, world, actor, name):
        if not self.in_scope(world, actor, self.tool_owners[name]):
            raise ValueError('该工具不属于行动者的作用域')
        self._require_request(world, actor, name, read_only=self.tools[name].read_only)

    def api_is_write(self, capability, method):
        if capability not in self.capabilities:
            raise ValueError(f'未知 API 能力：{capability}')
        unit = self.units[self.capabilities[capability]]
        methods = unit.mod.declaration.get('PlayerAPI', {}).get('Methods', ())
        if method not in methods:
            raise ValueError(f'{capability} 未声明方法：{method}')
        return method not in unit.mod.declaration.get('PlayerAPI', {}).get('ReadOnly', ())

    def api_call(self, world, actor, capability, method, arguments=None):
        """Trusted in-process API call: capability + declared method, never a tool name.

        Tools are the LLM-facing schemas; scripts and the rule AI call this path.
        Read methods stay available after ending actions; writes run the same
        actor/phase/action-gate/policy checks as a tool call.
        """
        module_id = self.capabilities.get(capability)
        if module_id is None:
            raise ValueError(f'未知 API 能力：{capability}')
        unit = self.units[module_id]
        methods = unit.mod.declaration.get('PlayerAPI', {}).get('Methods', ())
        if method not in methods:
            raise ValueError(f'{capability} 未声明方法：{method}')
        if method.startswith('_'):
            raise ValueError('API 方法不能以下划线开头')
        if not self.in_scope(world, actor, unit.mod):
            raise ValueError('该 API 不属于行动者的作用域')
        arguments = {} if arguments is None else dict(arguments)
        if 'actor' in arguments:
            raise ValueError('API 参数不能覆盖行动者')
        cfg = unit.mod.declaration['PlayerAPI']
        with unit_context(module_id):
            api = self.player_api(world, capability)
            handler = getattr(api, method)
            try:
                for key in cfg.get('ContentParameters', {}).get(method, ()):
                    if arguments.get(key) is not None:
                        arguments[key] = self.content.resolve(arguments[key])
                bound = inspect.signature(handler).bind(actor, **arguments)
            except (ValueError, TypeError) as exc:
                raise ToolArgumentError(str(exc)) from exc
        key = cfg.get('Actions', {}).get(method, method)
        if method == 'submit' and isinstance(arguments.get('action'), str):key = arguments['action']
        self._require_request(world, actor, key, read_only=not self.api_is_write(capability, method))
        with unit_context(module_id):
            return handler(*bound.args, **bound.kwargs)

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
        tool = self.panel_map.get(name)
        if tool is None: raise ModError(f'未知面板：{name}')
        if not self.in_scope(world,actor,self.panel_owners[name]): raise ModError('该面板不属于行动者的作用域')
        if not self.permits(world,actor,'panel.'+name): raise ModError('该面板被作用域声明禁用')
        return self.call(world,actor,tool.name,arguments or {})
