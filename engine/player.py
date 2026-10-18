"""Versioned, actor-bound player transport. Game facts remain in modules."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
import math
import inspect
import traceback

API_VERSION = 1
# Envelope key for an attached map snapshot. It is part of the persisted
# message contract, so it lives with the wire format rather than in a UI mod.
MAP_KEY = '_map_snapshot'
BUILTINS = {
    'memory_search': ('检索本客户端的历史行动与小结，不包含思考；query 为关键词，limit 默认3。', {'query':{'type':'string','minLength':1},'limit':{'type':'integer','minimum':1,'maximum':8,'default':3}}),
    'player_state': ('本国当前可见事实，按权限过滤；等价于 observe() 的 facts。', {}),
    'player_rules': ('查询本国公开规则。缺省或未知 topic 返回目录；传目录标题或主题词查正文，all 查全部。', {'topic': {'type': 'string'}}),
    'player_events': ('按游标取本国可见事件；cursor 缺省取全部，返回值带新游标。', {'cursor': {'type': 'integer', 'minimum': 0}}),
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

    def __init__(self, runtime, world, actor, lock=None, on_write=None, on_fault=None, memory_query=None):
        self.__runtime, self.__world = runtime, world
        self.__actor = actor
        self.__lock = lock or runtime._player_lock
        self.__on_write=on_write
        self.__on_fault=on_fault
        self.__memory_query=memory_query
        self._validate()

    @property
    def actor(self):
        return self.__actor

    def _validate(self):
        collection = getattr(self.__world, self.__runtime.distribution.get('ActorCollection', 'actors'), ())
        if self.actor not in collection:
            raise ValueError('该国当前不能行动：已亡国，或不在行动阶段')

    def _period(self):
        clock = self.__runtime.distribution.get('PlayerClock')
        return self.__runtime.object_call(self.__world, clock['Capability'], clock['Method']) if clock else None

    def actions(self):
        with self.__lock:
            self._validate()
            result = [{'type':'function','function':{'name':name,'description':description,
                       'parameters':{'type':'object','properties':properties,'required':['query'] if name=='memory_search' else [],'additionalProperties':False}}}
                      for name,(description,properties) in BUILTINS.items()]
            for schema in self.__runtime.schemas():
                name = schema['function']['name']
                try:
                    self.__runtime.require_tool(self.__world, self.actor, name)
                except ValueError:
                    continue
                owner = self.__runtime.tool_owners[name]
                tool = self.__runtime.tools[name]
                customize = tool.schema_for_actor or owner.schema_for_actor
                presented=wire(customize(self.__world, self.actor, deepcopy(schema)))
                for key in self.__runtime.tools[name].content_parameters:
                    prop=presented['function']['parameters'].get('properties',{}).get(key,{})
                    if 'enum' in prop:prop['enum']=[self.__runtime.content.key(k) for k in prop['enum']]
                result.append(presented)
            return result

    def query(self, name, arguments=None):
        with self.__lock:
            self._validate()
            if arguments is not None and not isinstance(arguments,dict):raise ValueError('参数必须是一个对象：{参数名: 值}')
            if name in BUILTINS:
                methods={'player_state':self.observe,'player_rules':self.rules,'player_events':self.events,'memory_search':self.memory_search}
                try:inspect.signature(methods[name]).bind(**(arguments or {}))
                except TypeError as exc:raise ValueError(str(exc)) from exc
                return wire(methods[name](**(arguments or {})))
            tool = self.__runtime.tools.get(name)
            if tool is None or not tool.read_only:
                raise ValueError('query 只能查已注册的只读动作；会改状态的动作请用 submit')
            return wire(self.__runtime.call(self.__world,self.actor,name,arguments or {}))

    def memory_search(self,query,limit=3):
        if not isinstance(query,str) or not query.strip() or type(limit) is not int or not 1<=limit<=8:raise ValueError('需要非空 query，limit 为1—8')
        return self.__memory_query(query,limit) if self.__memory_query else []

    def observe(self):
        with self.__lock, self.__runtime.read_scope(self.__world):
            self._validate()
            facts = {}
            for name in self.__runtime.player_facts():
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
                    raise ValueError('该国当前不能行动：已亡国，或不在行动阶段')
                if not isinstance(action,str):
                    code='unknown_action';raise ValueError('动作名必须是字符串')
                if action in BUILTINS:
                    data=self.query(action,arguments)
                    return {**base,'ok':True,'status':'effective','code':None,'message':'','data':data}
                if action not in self.__runtime.tools:
                    code = 'unknown_action'
                    raise ValueError(f'未知动作：{action}；动作名以当前工具表为准')
                code = 'unavailable_action'
                self.__runtime.require_tool(self.__world, self.actor, action)
                code = 'invalid_arguments'
                if arguments is not None and not isinstance(arguments, dict):
                    raise ValueError('参数必须是一个对象：{参数名: 值}')
                # ToolArgumentError distinguishes transport mistakes from
                # programming faults inside a module.
                code = 'domain_rejected'
                if not self.__runtime.tools[action].read_only and self.__on_write:self.__on_write()
                result = self.__runtime.call(self.__world,self.actor,action,arguments or {})
            except ValueError as exc:
                # ValueError 是过渡期的「业务拒绝」通道：模块用它说「钱不够」
                # 「这一步不允许」。KeyError 不再算这一档——缺一个键几乎总是
                # 引擎自己的 bug，把它说成「参数错」会替 bug 打掩护。
                from .runtime import ToolArgumentError
                if isinstance(exc,ToolArgumentError):code='invalid_arguments'
                return {**base, 'ok': False, 'status': 'rejected', 'code': code, 'message': str(exc), 'data': wire(getattr(exc,'details',None))}
            except Exception as exc:
                # 模块内部的 bug（OverflowError、AttributeError、TypeError…）只作废
                # 这一次动作。0.1 的分工是「模型动作不合法 → 回执里说清楚；锦上添花
                # 的失败 → 降级继续」，停机权只属于结算本身，不属于一次工具调用。
                return self._fault(base, exc)
            return self._result_receipt(base, result)

    def call_api(self, capability=None, method=None, arguments=None, action_label=None, **extra):
        """PlayerSession-level API entry; BoundPlayer adds services and audit."""
        if not isinstance(capability, str) or not isinstance(method, str):
            raise ValueError('API 需要 capability 与 method 字符串')
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError('参数必须是一个对象：{参数名: 值}')
        if extra:
            arguments = {**(arguments or {}), **extra}
        return self.api_call(capability, method, arguments, action=action_label)

    def api_call(self, capability, method, arguments=None, *, action=None):
        """Trusted API call by capability + declared method (no tool name).

        Read methods return detached data. Write methods return the same receipt
        shape as submit() so scripts and the rule AI keep one failure contract.
        """
        with self.__lock:
            label = action if isinstance(action, str) and action else (
                arguments.get('action') if isinstance(arguments, dict)
                and isinstance(arguments.get('action'), str) else method)
            base = {'api': API_VERSION, 'actor': self.__actor, 'action': label, 'period': self._period()}
            write = self.__runtime.api_is_write(capability, method)
            if arguments is not None and not isinstance(arguments, dict):
                raise ValueError('参数必须是一个对象：{参数名: 值}')
            if isinstance(arguments, dict) and 'actor' in arguments:
                raise ValueError('API 参数不能覆盖行动者')
            code = 'invalid_arguments'
            try:
                self._validate()
                if not write:
                    return wire(self.__runtime.api_call(self.__world, self.__actor, capability, method, arguments or {}))
                code = 'domain_rejected'
                if self.__on_write:
                    self.__on_write()
                result = self.__runtime.api_call(self.__world, self.__actor, capability, method, arguments or {})
            except ValueError as exc:
                if not write:
                    raise
                from .runtime import ToolArgumentError
                if isinstance(exc, ToolArgumentError):
                    code = 'invalid_arguments'
                return {**base, 'ok': False, 'status': 'rejected', 'code': code,
                        'message': str(exc), 'data': wire(getattr(exc, 'details', None))}
            except Exception as exc:
                if not write:
                    raise
                return self._fault(base, exc)
            return self._result_receipt(base, result)

    def _result_receipt(self, base, result):
        """两种入口共用已执行结果的编码与故障语义，避免重复执行。"""
        try:
            data = wire(result)
        except Exception as exc:
            # 序列化失败发生在动作已经生效之后，不能报成 rejected：
            # 否则调用者可能把同一笔买卖重做一次，再付一次钱。
            return self._fault(base, exc, applied=True)
        status = data.get('status', 'effective') if isinstance(data, dict) else 'effective'
        return {**base, 'ok': status not in ('failed', 'cancelled'), 'status': status,
                'code': 'action_failed' if status in ('failed', 'cancelled') else None,
                'message': data.get('message', '') if isinstance(data, dict) else '', 'data': data}

    def _fault(self, base, exc, applied=False):
        """模块内部异常 → 该动作作废、游戏继续；细节交给宿主的故障日志。"""
        detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if self.__on_fault:
            try:
                self.__on_fault(base['action'], exc, detail)
            except Exception:
                pass
        return {**base, 'ok': False, 'status': 'failed', 'code': 'internal_fault',
                'message': f'模块内部错误（{type(exc).__name__}）：'+('动作已执行，但结果无法编码；不要重复执行。' if applied else '本次执行中断，请先查询状态确认已生效部分。')+'游戏继续，详情已记故障日志。',
                'data': None}

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
        if not isinstance(topic,str):raise ValueError('topic 必须是字符串；省略 topic 可查看全部规则目录')
        with self.__lock:
            self._validate()
            # One assembly path: scope/visibility filtering, ManualOrder and
            # placeholder substitution live in Runtime.rule_sections().
            sections=self.__runtime.rule_sections(self.__world, player=True, actor=self.actor)
            selected=self.__runtime.select_rules(sections, topic)
            if not selected:
                return [{'title':'规则目录','body':'\n'.join(section.title+('：'+section.summary if section.summary else '') for section in sections)+'\n传 topic 查看对应主题；all 查看全部。'}]
            return [{'title':section.title,'body':section.body} for section in selected]
