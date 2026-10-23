"""股市版 Python 沙盒：回合末节点，所有玩家的脚本一起执行。

沿用原版 ``experimental:python`` 的沙盒本体：同一套受限 Python（无导入、无文件、
无网络、无随机、无动态执行）、同一套回执形状（打印输出、报错类型与行列、
步数与 API 调用次数）。这里改了两处：

1. **执行时机**：脚本不在行动阶段跑，而在回合末那个节点跑——所有玩家都结束行动
   之后，所有脚本同时开始执行（见 :class:`ConcurrentRunner`）。
2. **时钟**：换成本回合历（``stock:clock.period``）。

**为什么是时间片轮转而不是真并发**：Python 的 GIL 让沙盒没法真正并行，
所以这里一个接一个地跑。但它们看到的是**同一份市场快照**（同一时刻的收盘价），
下单一律进同一本挂单簿、回合末统一撮合——所以效果与真并发一致：
谁也影响不了谁的报价与决策。

脚本一旦跑起来就算「开过盘」，本回合剩下的动作转入回合末统一工作窗口。
"""
import inspect
import threading

from engine.player import wire
from engine.runtime import RuleSection, Stage, ToolArgumentError
from engine.scripting import Machine
from engine.state import unit_context
from mods.experimental.scripting import Programs, ScriptingMod
from mods.experimental.scripting_api import ScriptingAPI


class StockScriptingAPI(ScriptingAPI):
    """脚本 API：跑完顺手通知时钟「开过盘了」。"""

    def claim(self, actor, inputs=None):
        claim = super().claim(actor, inputs)
        self.world.runtime.object_call(self.world, 'stock:clock', 'mark_open', actor)
        return claim


class ConcurrentRunner:
    """回合末节点：所有玩家的脚本轮流执行，模拟真并发。

    轮流只是因为 GIL 跑不出真并行；关键是它们看到同一份市场快照，
    且下单一律进同一本挂单簿。
    """

    def __init__(self, module, world):
        self.module = module
        self.world = world
        self.concurrency = getattr(module, 'concurrency', 'timeslice')

    def run_all(self):
        """按名册顺序把每个行动者的脚本跑一遍；没有脚本或本回合已用过的跳过。

        回合末统一工作有时限：用满了就停，剩下的人这回合不执行。
        """
        runtime = self.module.runtime
        runtime.object_call(self.world, 'stock:market', 'open_session')
        actors = tuple(getattr(self.world, 'nation_actors', ()) or ())
        if self.concurrency == 'threads':
            return self._run_threads(actors)
        results = {}
        for actor in actors:
            if runtime.object_call(self.world, 'stock:clock', 'work_expired'):
                break
            result = self.run_one(actor)
            if result is not None:
                results[actor] = result
        return results

    def _run_threads(self, actors):
        """真并发：每个脚本一个线程，GIL 自己把字节码切碎。

        线程之间共享的只有挂单簿（取号与入簿有锁），行情快照在批次开始前就定住了，
        所以谁也影响不了谁，看到的是同一份市场状态。
        """
        results = {}

        def worker(actor):
            result = self.run_one(actor)
            if result is not None:
                results[actor] = result

        threads = [threading.Thread(target=worker, args=(actor,), daemon=True) for actor in actors]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def run_one(self, actor):
        world = self.world
        runtime = self.module.runtime
        try:
            claim = runtime.object_call(world, 'sandbox:programs', 'claim', actor)
        except Exception:
            return None
        holder = {}
        machine = Machine(claim['source'], self._invoker(actor, holder), claim['limits'])
        holder['machine'] = machine
        machine.env[0]['inputs'] = {}
        result = machine.run()
        actions = result.pop('receipts')
        result.update(message=result['error']['message'] if result['error'] else '',
                      actions=len(actions),
                      failed_action=next((r for r in reversed(actions) if not r['ok']), None),
                      revision=claim['revision'], period=claim['period'])
        runtime.object_call(world, 'sandbox:programs', 'finish', actor, result)
        runtime.object_call(world, 'stock:clock', 'mark_open', actor)
        return result

    # ── 沙盒的宿主接口 ──────────────────────────────────────────
    def _advance(self, actor, holder):
        """计算量与时间片的换算：脚本干了多少活，就走过多少时间。

        steps_per_tick = 沙盒步数预算 / 本回合 tick 数；
        默认 800000 步 / 20000 tick = 每 tick 40 步。
        """
        machine = holder.get('machine')
        if machine is None:
            return
        runtime = self.module.runtime
        ticks = runtime.object_call(self.world, 'stock:clock', 'slices')
        budget = max(1, machine.limits.get('steps', 1))
        tick = int(machine.steps * ticks / budget)
        runtime.object_call(self.world, 'stock:clock', 'set_progress', actor, tick)

    def _sleep_cost(self, holder, ticks):
        """计算量与时间片的换算式：每 tick 值多少步。

        steps_per_tick = 沙盒步数预算 / 本回合 tick 数；
        默认 800000 步 / 20000 tick = 每 tick 40 步。
        换句话说：沙盒的计算额度就是这 20 分钟的时间额度，
        想睡满整个窗口，别的活就得省着干。
        """
        machine = holder.get('machine')
        if machine is None:
            return 0
        budget = max(1, machine.limits.get('steps', 1))
        ticks_total = max(1, self.module.runtime.object_call(self.world, 'stock:clock', 'slices'))
        wanted = max(1, int(ticks)) * (budget // ticks_total)
        remaining = max(0, budget - machine.steps)
        # 留一点余量给循环脚本的收尾调用（最后一次读行情、打印），别让它在最后一步死掉。
        reserve = max(1, budget // 100)
        return min(wanted, max(0, remaining - reserve))

    def _invoker(self, actor, holder):
        def invoke(name, args, kwargs):
            self._advance(actor, holder)
            if name == 'call':
                capability = args[0] if args else kwargs.get('capability')
                method = args[1] if len(args) > 1 else kwargs.get('method')
                arguments = {key: value for key, value in kwargs.items()
                             if key not in ('capability', 'method')}
                if capability == 'stock:clock' and method == 'sleep':
                    machine = holder.get('machine')
                    if machine is not None:
                        machine.charge(self._sleep_cost(holder, arguments.get('ticks', 1)))
                return wire(self._api_call(actor, capability, method, arguments))
            if name == 'observe':
                return wire(self._observe(actor))
            if name == 'rules':
                return wire(self._rules(actor, *(args or ())))
            if name == 'events':
                return wire({'cursor': 0, 'items': []})
            if name == 'action_state':
                return wire({'active': True, 'can_act': False, 'ended': True})
            raise ValueError(f'脚本不能调用宿主接口：{name}')
        return invoke

    def _api_call(self, actor, capability, method, arguments):
        """回合末的 API 调用：走模块声明的接口，但不受行动阶段与行动门限制。

        此刻所有玩家都已经结束行动，行动门本来就会拒绝写入；
        这里直接调模块接口，保留能力/方法声明与参数绑定校验。
        """
        world = self.world
        runtime = self.module.runtime
        unit_id = runtime.capabilities.get(capability)
        if unit_id is None:
            raise ValueError(f'未知 API 能力：{capability}')
        unit = runtime.units[unit_id]
        declaration = unit.mod.declaration.get('PlayerAPI', {})
        if method not in declaration.get('Methods', ()):
            raise ValueError(f'{capability} 未声明方法：{method}')
        if method.startswith('_'):
            raise ValueError('API 方法不能以下划线开头')
        arguments = dict(arguments or {})
        if 'actor' in arguments:
            raise ValueError('API 参数不能覆盖行动者')
        with unit_context(unit_id):
            api = runtime.player_api(world, capability)
            handler = getattr(api, method)
            try:
                bound = inspect.signature(handler).bind(actor, **arguments)
            except (ValueError, TypeError) as exc:
                raise ToolArgumentError(str(exc)) from exc
            result = handler(*bound.args, **bound.kwargs)
        if method in declaration.get('ReadOnly', ()):
            return result
        return {'ok': True, 'status': 'effective', 'code': None, 'message': '',
                'data': wire(result)}

    def _observe(self, actor):
        runtime = self.module.runtime
        facts = {}
        for name in runtime.player_facts():
            facts[name] = runtime.call(self.world, actor, name, {})
        return facts

    def _rules(self, actor, topic=''):
        runtime = self.module.runtime
        sections = runtime.rule_sections(self.world, player=True, actor=actor)
        selected = runtime.select_rules(sections, topic)
        if not selected:
            return [{'title': '规则目录',
                     'body': '\n'.join(section.title + ('：' + section.summary if section.summary else '')
                                       for section in sections) + '\n传 topic 查看对应主题；all 查看全部。'}]
        return [{'title': section.title, 'body': section.body, 'system': section.system}
                for section in selected]

    def _action_state(self):
        return {'active': True, 'can_act': False, 'ended': True}


class StockPythonMod(ScriptingMod):
    def initialize_world(self, world):
        world.scripts = Programs(
            self.limits,
            lambda actor: actor in getattr(world, self.runtime.distribution.get('ActorCollection', 'actors')),
            lambda: self.runtime.object_call(world, 'stock:clock', 'period'))

    def player_api(self, world):
        return StockScriptingAPI(world)

    def before_tool(self, world, actor, name, arguments):
        """写脚本算一次窗口额度；只读检查不记。"""
        if name != 'script':
            return
        action = arguments.get('action') if isinstance(arguments, dict) else None
        if action in ('check', 'status'):
            return
        self.runtime.object_call(world, 'stock:clock', 'charge', actor)

    def configure(self, declaration, path):
        super().configure(declaration, path)
        mode = str(declaration.get('Parameters', {}).get('Concurrency', 'timeslice')).strip().lower()
        if mode not in ('timeslice', 'threads'):
            raise ValueError('Concurrency 只能是 timeslice 或 threads')
        self.concurrency = mode

    def stages(self):
        return (Stage('stock.python.run', self._run_scripts, hook='market.settlement'),)

    def _run_scripts(self, world, changes):
        """开盘：所有玩家的脚本一起执行（时间片轮转模拟真并发）。"""
        runner = ConcurrentRunner(self, world)
        changes.update(scripts=runner.run_all())

    def rule_sections(self, world=None):
        limits = self.limits
        return (RuleSection(
            'Python 量化沙盒',
            '买卖只能靠脚本下单：脚本里 api.call("stock:market","buy",symbol=...,quantity=...) 买入、'
            'api.call("stock:market","sell",symbol=...,quantity=...) 卖出；'
            'api.call("stock:market","quotes") 取当前行情，'
            'api.call("stock:market","history",symbol=...,limit=...) 取历史收盘价，'
            'api.call("stock:portfolio","holdings") 取自己的持仓与财富，'
            'api.call("stock:portfolio","borrow",amount=...) 借钱。'
            'api.call 被拒绝会终止脚本并把回执原样带回来，脚本自己要用 if 判断。'
            'print 的内容会出现在下回合开局简报里。'
            '脚本不在你的回合里跑：等所有玩家都结束行动，市场开盘，脚本一起执行，'
            '下单进同一本挂单簿、闭市统一撮合。'
            '脚本是循环脚本：每轮读行情与持仓、判断要不要下单，'
            '然后 api.call("stock:clock","sleep",ticks=N) 等市场往前走 N 个时间片，'
            '循环到 tick 到达收盘（slices）就收工。'
            f'开盘窗口 20 分钟切成 {limits["steps"] and ""}20000 个时间片（1 tick = 1.44 秒交易时间，'
            '09:30 开盘、17:30 收盘）。'
            '换算式：每 tick 值 沙盒步数预算 / 20000 步 = 40 步——'
            '也就是说计算额度就是这 20 分钟的时间额度：想睡满整个窗口，别的活就得省着干。'
            'api.call("stock:clock","snapshot") 拿得到 tick、slices 与 market_time（当前交易时刻）。'
            '（实现上按时间片轮转或线程并发都一样：GIL 之下两者给出相同的对局结果。）'
            f'总预算 {limits["steps"]} 计算单位、{limits["calls"]} 次 API 调用；'
            '禁止导入、安装库、文件、网络、反射、随机与动态执行，没有 try/except。',
            ('脚本', '沙盒', 'python', 'sandbox', '量化', '下单', '并发'),
            '脚本怎么写、怎么在脚本里下单、什么时候执行'),)
