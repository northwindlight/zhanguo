"""Deterministic callback strategies and a manually invoked read-only research slot."""
import ast
from copy import deepcopy
import inspect
import json

from engine.player import wire, API_VERSION
from engine.runtime import GameMod, RuleSection, Stage, ToolArgumentError
from engine.scripting import Machine, check, error_info, DEFAULT_LIMITS
from engine.state import unit_context


def slot_key(slot):
    key = str(slot)
    if key not in ('1', '2', '3', '4', 'research'):
        raise ValueError('slot 只能是 1～4 或 research')
    return key


# 沙盒只实现 Python 子集；这里把最常见的拒绝翻译成模型能直接照做的改法。
SANDBOX_HINTS = (
    ('JoinedStr', 'f-string 不可用：用 + 拼接或 % 格式化，例：print("价 " + str(row["price"]))'),
    ('Lambda', 'lambda 不可用：先 def 具名函数再作 key=，例：def chg(r): return r["change_pct"]；'
               'sorted(rows, key=chg, reverse=True)'),
    ('SetComp', '集合推导不可用：改用列表推导，成员判断用 in'),
    ('GeneratorExp', '生成器表达式不可用：改用列表推导 [...] 或 for 循环累计'),
    ('TryExcept', 'try/except 不可用：用 if 预判，例：if "key" in d: 或 d.get("key", 0)'),
    ('ClassDef', 'class 不可用：用 dict 存数据、def 写逻辑'),
    ('Import', 'import 不可用：可用内建 abs/all/any/bool/dict/enumerate/float/int/len/list/'
               'max/min/print/range/reversed/round/sorted/str/sum/tuple/zip'),
    ('Starred', '*解包不可用：逐个写出元素或用循环展开'),
    ('NamedExpr', '海象运算符不可用：先单独赋值再判断'),
    ('Attribute assignment', '不能给属性赋值：把对象放进 dict，改 dict 的键'),
)
_METHODS_LINE = ('可用方法：get/items/keys/values/append/extend/pop/copy/count/index/sort/'
                 'reverse/lower/upper/strip/split/join/startswith/endswith/replace')
_BUILTINS_LINE = ('可用内建：abs/all/any/bool/dict/enumerate/float/int/len/list/max/min/print/'
                  'range/reversed/round/sorted/str/sum/tuple/zip')


def with_hint(error):
    """把沙盒拒绝信息补上一句可操作的改法；错误字典结构保持不变。"""
    if not isinstance(error, dict):
        return error
    message = error.get('message') or ''
    for needle, hint in SANDBOX_HINTS:
        if needle in message:
            error['message'] = f'{message}。改法：{hint}'
            return error
    if message.startswith('Unsupported attribute:'):
        name = message.split(':', 1)[1].strip()
        extra = {'add': ' set 不可用：用 list 存成员，用 in 判断',
                 'format': ' .format 不可用：用 + 拼接或 % 格式化',
                 'update': ' 用 for 逐键赋值',
                 'setdefault': ' 用 if k not in d: d[k] = v'}.get(name, '')
        error['message'] = f'{message}。改法：{extra.strip()}；{_METHODS_LINE}' if extra \
            else f'{message}。{_METHODS_LINE}'
    elif message == 'Unsupported builtin':
        error['message'] = f'{message}。{_BUILTINS_LINE}'
    return error


def bounded_output(text, limit):
    return text.encode('utf-8')[:limit].decode('utf-8', errors='ignore')


class SlotPrograms:
    def __init__(self, world, limits):
        self.world, self.limits = world, dict(limits)
        self.rows = {}
        self.recorder = None

    def bind_state(self, world, key): self.world = world; return self
    def _actor(self, actor):
        if actor not in self.world.nation_actors:
            raise ValueError('账户当前不能使用计算槽')
    def _row(self, actor, slot='1'):
        self._actor(actor)
        slot = slot_key(slot)
        return self.rows.get(actor, {}).get(slot, dict(source='', revision=0,
                    last_period=None, result=None, state={}))
    def _source(self, source):
        if not isinstance(source, str) or len(source.encode('utf-8')) > self.limits['source']:
            raise ValueError('脚本必须是字符串，且不能超过源码字节上限')
        return source
    def write(self, actor, source, slot='1'):
        slot = slot_key(slot)
        if not self.world.compute.allowed(actor, slot):
            raise ValueError('该计算槽尚未启用，请先购买')
        row = dict(self._row(actor, slot))
        row.update(source=self._source(source), revision=row['revision']+1)
        self.rows.setdefault(actor, {})[slot] = row
        return dict(slot=slot, revision=row['revision'], bytes=len(source.encode('utf-8')))
    def patch(self, actor, old, new, slot='1'):
        row = self._row(actor, slot)
        if not isinstance(old, str) or not old or not isinstance(new, str) or row['source'].count(old) != 1:
            raise ValueError('old 必须恰好出现一次，new 必须是字符串')
        return self.write(actor, row['source'].replace(old, new, 1), slot)
    def check(self, actor, source=None, slot='1'):
        row = self._row(actor, slot)
        verdict = check(row['source'] if source is None else source, self.limits)
        if verdict.get('error'):
            verdict['error'] = with_hint(verdict['error'])
        return verdict
    def status(self, actor, include_source=False, slot='1'):
        slot = slot_key(slot)
        row = self._row(actor, slot)
        pool = self.world.compute.status(actor)
        result = {k: deepcopy(row[k]) for k in ('revision', 'last_period', 'result')}
        result.update(slot=slot, enabled=self.world.compute.allowed(actor, slot),
                      available=self.world.compute.allowed(actor, slot) and pool['steps_remaining'] > 0,
                      limits=dict(self.limits, steps=pool['steps_remaining'], calls=pool['calls_remaining']))
        if include_source: result['source'] = row['source']
        if slot == '1':
            result['other_slots'] = [dict(slot=s, revision=r['revision'], result=deepcopy(r['result']),
                                         enabled=self.world.compute.allowed(actor, s))
                                     for s, r in sorted(self.rows.get(actor, {}).items()) if s != '1']
        return result
    def claim(self, actor, slot='1'):
        slot = slot_key(slot)
        row = self._row(actor, slot)
        if not self.world.compute.allowed(actor, slot) or not row['revision']:
            return None
        period = self.world.clock.period()
        if slot != 'research' and row['last_period'] == period:
            return None
        row = deepcopy(row)
        row.update(last_period=period, result=None)
        self.rows.setdefault(actor, {})[slot] = row
        return dict(actor=actor, slot=slot, source=row['source'], revision=row['revision'],
                    period=period, wake=0, state=deepcopy(row['state']))
    def finish(self, actor, slot, result, state=None):
        row = self.rows[actor][slot]
        row['result'] = wire(result)
        if state is not None: row['state'] = deepcopy(state)
    def to_save(self): return deepcopy(self.rows)
    def restore(self, data):
        if not isinstance(data, dict): raise ValueError('无效的计算槽存档')
        for actor, slots in data.items():
            if not isinstance(actor, str) or not isinstance(slots, dict):
                raise ValueError('无效的计算槽身份')
            for slot, row in slots.items():
                slot_key(slot)
                if set(row) != {'source', 'revision', 'last_period', 'result', 'state'}:
                    raise ValueError('无效的计算槽字段')
                self._source(row['source'])
                if type(row['revision']) is not int or row['revision'] < 1:
                    raise ValueError('无效的源码版本')
                if row['last_period'] is not None and (type(row['last_period']) is not int
                        or not 1 <= row['last_period'] <= self.world.clock.period()):
                    raise ValueError('无效的脚本执行期')
                if type(row['state']) is not dict or len(json.dumps(row['state']).encode()) > 65536:
                    raise ValueError('无效的脚本持久状态')
                r = row['result']
                if r is not None:
                    if set(r) != {'status', 'message', 'output', 'steps', 'calls', 'error', 'actions',
                                  'failed_action', 'revision', 'period'}:
                        raise ValueError('无效的脚本执行结果')
                    for key, maximum in (('steps', 16000000), ('calls', 16000), ('actions', 16000)):
                        if type(r.get(key)) is not int or not 0 <= r[key] <= maximum:
                            raise ValueError('无效的脚本执行计数')
                    if len(r.get('output', '').encode()) > self.limits['output']:
                        raise ValueError('脚本输出超过上限')
                    if (r['period'] != row['last_period'] or type(r['revision']) is not int
                            or not 1 <= r['revision'] <= row['revision']
                            or r['status'] != ('failed' if r['error'] else 'effective')
                            or r['actions'] > r['calls']):
                        raise ValueError('脚本结果身份或状态不一致')
                    if r['error'] is not None:
                        if (not isinstance(r['error'], dict)
                                or set(r['error']) != {'type', 'message', 'line', 'column', 'source'}
                                or not all(isinstance(r['error'][k], str) for k in ('type','message','source'))):
                            raise ValueError('无效的脚本错误')
        self.rows = wire(data)


class ConcurrentRunner:
    """Single-threaded callbacks against one public time frontier.

    A plain script runs once per day. A script defining on_tick() is called at
    most once per wake and shares JSON state across wakes and trading days.
    """
    def __init__(self, module, world):
        self.module, self.world = module, world

    def _object(self, capability, method, *args):
        return self.module.runtime.object_call(self.world, capability, method, *args)

    def _api_call(self, actor, capability, method, arguments, research=False):
        runtime, world = self.module.runtime, self.world
        allowed = {'stock:market', 'stock:portfolio', 'stock:clock', 'stock:compute', 'stock:intelligence'}
        if (not isinstance(capability, str) or not isinstance(method, str)
                or capability not in allowed or method.startswith('_') or 'actor' in arguments):
            raise ValueError('脚本只能调用绑定身份的股票 API，不能嵌套控制脚本')
        unit_id = runtime.capabilities.get(capability)
        if unit_id is None: raise ValueError('未声明该能力')
        unit = runtime.units[unit_id]
        if not runtime.in_scope(world, actor, unit.mod): raise ValueError('API 超出账户作用域')
        declaration = unit.mod.declaration['PlayerAPI']
        if method not in declaration['Methods']: raise ValueError('未声明该方法')
        write = method not in declaration.get('ReadOnly', ())
        if method == 'end_turn' or capability in ('stock:compute', 'stock:intelligence') and write:
            raise ValueError('脚本不能结束回合、购买资源或发动谍报，请用模型工具')
        if research and write:
            raise ValueError('研究槽只读：不能下单、借贷或推进行情')
        base = dict(api=API_VERSION, actor=actor, action=method, period=world.clock.period())
        try:
            with unit_context(unit_id):
                api = runtime.player_api(world, capability)
                try: bound = inspect.signature(getattr(api, method)).bind(actor, **arguments)
                except TypeError as exc: raise ToolArgumentError(str(exc)) from exc
                result = getattr(api, method)(*bound.args, **bound.kwargs)
            if not write: return wire(result)
            receipt = dict(base, ok=True, status='effective', code=None, message='', data=wire(result))
        except ValueError as exc:
            if not write: raise
            receipt = dict(base, ok=False, status='rejected', code='domain_rejected', message=str(exc), data=None)
        if world.scripts.recorder:
            world.scripts.recorder(actor, method, dict(capability=capability, **arguments), receipt)
        return receipt

    def execute(self, task, research=False, inputs=None):
        actor, slot = task['actor'], task['slot']
        pool = self._object('stock:compute', 'status', actor)
        params = self.module.declaration['Parameters']
        cap = params['ResearchSteps'] if research else params['BurstSteps']
        limits = dict(self.module.limits, steps=min(cap, pool['steps_remaining']),
                      calls=min(params['ResearchCalls'] if research else params['BurstCalls'], pool['calls_remaining']))
        if limits['steps'] < 1:
            return self._exhausted(task)
        source = task['source']
        callback = False
        try:
            tree = ast.parse(source)
            callback = not research and any(isinstance(n, ast.FunctionDef) and n.name == 'on_tick' for n in tree.body)
        except SyntaxError:
            pass  # Machine produces the original syntax diagnostic.
        if callback: source += '\non_tick()\n'
        if callback:
            limits['source'] += len('\non_tick()\n')
            limits['nodes'] += 4
        next_wake = self.world.clock.tick + params['WakeInterval']

        def invoke(name, args, kwargs):
            nonlocal next_wake
            if name == 'call':
                if len(args) > 2:
                    raise ValueError('API 方法参数必须使用关键字传递')
                capability = args[0] if args else kwargs.get('capability')
                method = args[1] if len(args) > 1 else kwargs.get('method')
                arguments = {k: v for k, v in kwargs.items() if k not in ('capability', 'method')}
                if capability == 'stock:clock' and method == 'sleep':
                    if research or not callback:
                        raise ValueError('sleep 只能在 on_tick 回调中安排下一次唤醒')
                    ticks = arguments.get('ticks', params['WakeInterval'])
                    if type(ticks) is not int or ticks < 1:
                        raise ValueError('sleep ticks 必须为正整数')
                    next_wake = min(self.world.clock.slices(), self.world.clock.tick+ticks)
                    return dict(tick=self.world.clock.tick, wake_tick=next_wake)
                return self._api_call(actor, capability, method, arguments, research)
            if name == 'observe':
                return {key: self.module.runtime.call(self.world, actor, key, {})
                        for key in self.module.runtime.player_facts()}
            if name == 'rules':
                sections = self.module.runtime.rule_sections(self.world, player=True, actor=actor)
                selected = self.module.runtime.select_rules(sections, args[0] if args else '')
                return [dict(title=s.title, body=s.body) for s in selected]
            if name == 'events': return dict(cursor=len(self.world.market.news), items=deepcopy(self.world.market.news))
            if name == 'action_state': return dict(active=True, can_act=research, ended=not research)
            raise ValueError('未知的沙盒宿主方法')

        machine = Machine(source, invoke, limits)
        machine.env[0].update(inputs=wire(inputs or {}), state=deepcopy(task['state']))
        result = machine.run()
        if result['error']:
            result['error'] = with_hint(result['error'])
        try:
            state = wire(machine.env[0].get('state'))
            if type(state) is not dict or len(json.dumps(state, ensure_ascii=False).encode()) > 65536:
                raise ValueError('state 必须是至多64KB的JSON字典')
        except (TypeError, ValueError) as exc:
            result['error'] = error_info(ValueError(str(exc)), source, machine.line, machine.column)
            state = None
        else:
            if result['error']: state = None
        spent = min(result['steps'], limits['steps'])
        self._object('stock:compute', 'consume', actor, spent, result['calls'])
        receipts = result.pop('receipts')
        result.update(status='failed' if result['error'] else 'effective',
                      steps=spent, message=result['error']['message'] if result['error'] else '',
                      actions=len(receipts), failed_action=next((r for r in reversed(receipts) if not r['ok']), None),
                      revision=task['revision'], period=task['period'])
        previous = self.world.scripts.rows[actor][slot]['result']
        if previous is not None and previous['period'] == task['period'] and not research:
            for field in ('steps', 'calls', 'actions'): result[field] += previous[field]
            result['output'] = bounded_output(previous['output']+result['output'], self.module.limits['output'])
        self.world.scripts.finish(actor, slot, result, state)
        if state is not None: task['state'] = state
        task['wake'] = next_wake if callback and not result['error'] else None
        return result

    def _exhausted(self, task):
        result = dict(status='failed', message='共享算力池已用满', output='', steps=0, calls=0,
                      error=dict(type='SandboxLimitError', message='共享算力池已用满', line=None, column=None, source=''),
                      actions=0, failed_action=None, revision=task['revision'], period=task['period'])
        previous = self.world.scripts.rows[task['actor']][task['slot']]['result']
        if previous is not None and previous['period'] == task['period']:
            for key in ('steps', 'calls', 'actions', 'output'): result[key] = previous[key]
        self.world.scripts.finish(task['actor'], task['slot'], result)
        task['wake'] = None
        return result

    def run_all(self):
        world = self.world
        tasks = []
        for actor in sorted(world.nation_actors):
            for index in range(1, world.compute.status(actor)['slots']+1):
                task = world.scripts.claim(actor, str(index))
                if task: tasks.append(task)
        if not world.clock.trading_day():
            for task in tasks:
                self.execute(task)
            return
        self._object('stock:market', 'open_session')
        for tick in range(world.clock.slices()+1):
            if tick: self._object('stock:market', 'advance', tick)
            if tick == world.clock.slices(): break
            due = [task for task in tasks if task['wake'] == tick and task['actor'] in world.nation_actors]
            if due: self._object('stock:market', 'freeze_public')
            for task in due: self.execute(task)


class StockScriptingAPI:
    def __init__(self, world): self.world = world
    def write(self, actor, source, slot='1'): return self.world.scripts.write(actor, source, slot)
    def patch(self, actor, old, new, slot='1'): return self.world.scripts.patch(actor, old, new, slot)
    def check(self, actor, source=None, slot='1'): return self.world.scripts.check(actor, source, slot)
    def status(self, actor, include_source=False, slot='1'): return self.world.scripts.status(actor, include_source, slot)
    def run(self, actor, slot='research', inputs=None):
        if slot_key(slot) != 'research': raise ValueError('行动时只能手动运行 research 槽')
        if not self.world.clock.can_act(actor): raise ValueError('已经结束行动')
        if inputs is not None and type(inputs) is not dict: raise ValueError('inputs 必须是JSON对象')
        if len(json.dumps(wire(inputs or {}), ensure_ascii=False).encode()) > 65536:
            raise ValueError('研究参数最多64KB')
        task = self.world.scripts.claim(actor, 'research')
        if task is None: raise ValueError('研究槽尚未启用或没有保存源码')
        module = self.world.runtime.units['stock:python'].mod
        return ConcurrentRunner(module, self.world).execute(task, research=True, inputs=inputs)


class StockPythonMod(GameMod):
    def mixins(self): return ()
    def configure(self, declaration, path):
        super().configure(declaration, path)
        self.limits = dict(declaration['Limits'])
        if (set(self.limits) != set(DEFAULT_LIMITS)
                or any(type(v) is not int or v < 1 for v in self.limits.values())
                or self.limits['depth'] > 64 or self.limits['source'] > 65536
                or self.limits['nodes'] > 8192 or self.limits['integer_bits'] > 1024):
            raise ValueError('无效的股票沙盒结构上限')
        for value in declaration['Parameters'].values():
            if type(value) is not int or value < 1: raise ValueError('计算调度参数须为正整数')
    def initialize_world(self, world): world.scripts = SlotPrograms(world, self.limits)
    def objects(self, world): return {'sandbox:programs': world.scripts}
    def player_api(self, world): return StockScriptingAPI(world)
    def save_state(self, world): return world.scripts.to_save()
    def load_state(self, world, state): world.scripts.restore(state)
    def before_tool(self, world, actor, name, arguments):
        if name == 'script' and arguments.get('action') not in ('check', 'status'):
            self.runtime.object_call(world, 'stock:clock', 'charge', actor)
    def stages(self):
        return (Stage('stock.python.run', lambda w, c: ConcurrentRunner(self, w).run_all(), hook='market.settlement'),)
    def rule_sections(self, world=None):
        return (RuleSection('Python 策略与研究槽',
            'script(action=write/patch/check/status,slot="1"..."4"或"research")管理各槽源码；'
            '普通源码每天自动执行一次；定义无参数 def on_tick(): 会每100tick重新回调，可用'
            'api.call("stock:clock","sleep",ticks=N)设置下一次回调间隔，不暂停、不推进行情、不按等待扣步数。'
            '回调必须返回，不能用while+sleep等待；每回调最多50000计算单位、200次API。'
            'state是每槽独立、跨回调和跨日保存的64KB JSON字典；成功回调提交state，失败不撤销已受理动作。'
            'api.call("stock:market","buy"/"sell"/"short"/"cover",symbol=...,quantity=...,price=可选限价)下单，'
            'quotes/history/orders/fills读取已知行情和本账户回报。每个tick所有回调读同一公共时间截面，订单下一tick生效。'
            '研究槽解锁后可在行动时 script(action=run,slot="research",inputs={...}) 运行，只读，不下单、不推进行情。'
            '研究单次最多100万步、1000次API，所有槽共享账户每日预算；改源码和重跑不重置额度。'
            'print、错误行与各槽用量会出现在状态和下回合简报。'
            '沙盒是Python子集：支持 def/for/while/if、列表与字典推导、切片、三元表达式、'
            '具名函数作 sorted/min/max 的 key=；不支持 f-string（用+拼接或%格式化）、lambda、'
            'set、try/class/import/全局语句；方法仅限 get/items/keys/values/append/extend/pop/'
            'copy/count/index/sort/reverse/字符串处理，内建仅限 abs/all/any/bool/dict/enumerate/'
            'float/int/len/list/max/min/print/range/reversed/round/sorted/str/sum/tuple/zip。'
            '被拒的写法会在报错信息里附一句改法；script(action=check) 免费预检。',
            ('脚本', 'Python', '研究槽', 'on_tick', 'state'), '自动交易回调、持久状态及手动研究', system=True),)
