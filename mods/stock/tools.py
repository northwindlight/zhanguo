"""股市发行版的模型工具面。

模型直接能用的只有这些：

============  ==========================================================
script        伞工具：write 保存源码 / patch 精确改一处 / check 免费查语法
              / status 查状态（脚本在回合末由引擎统一执行，模型侧不手动跑）
script_status 只读事实：上回合脚本的执行结果（打印输出与报错）
portfolio     只读事实：自己的持仓、现金、负债与财富
market        只读事实：行情快照（现价、均线、动量、波动率、交易成本）
round         只读事实：回合号、交易日历与两个窗口的额度
bank          伞工具：borrow 借钱 / repay 还钱
end_turn      结束本回合，提交一句小结
============  ==========================================================

**买卖下单没有工具。** 唯一的下单通道是写进 Python 脚本，由脚本
``api.call('stock:market', 'buy'|'sell', symbol=..., quantity=...)`` 发出；
借贷可以直接调，也可以写在脚本里自动做。
"""
from engine.runtime import Action, Tool
from mods.native.tools import commands as command_layer


class StockToolLayer:
    """发行版工具层：模型能看见的表、回执与抬头。"""

    def __init__(self, runtime, tools, actions, receipts, captions, commands):
        self.runtime = runtime
        self._tools = tuple(tools)
        self._actions = tuple(actions)
        self._receipts = dict(receipts)
        self._captions = dict(captions)
        self._commands = dict(commands)

    def tools(self):
        return self._tools

    def actions(self):
        return self._actions

    def catalogs(self):
        return {}

    def _canonical(self, action):
        tool = self.runtime.tools.get(action) if isinstance(action, str) else None
        return tool.name if tool is not None else action

    def _render_name(self, action, args):
        canonical = self._canonical(action)
        inner = (args or {}).get('action') if canonical in self._commands else None
        target = self._commands.get(canonical, {}).get(inner) if isinstance(inner, str) else None
        return self._canonical(target) if target else canonical

    def caption(self, action, args, actor, receipt, ctx):
        render = self._captions.get(self._render_name(action, args))
        if render is None:
            return None
        try:
            return render(args or {}, actor, receipt or {}, ctx)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            return None

    def receipt(self, action, args, receipt, actor, ctx):
        render = self._receipts.get(self._render_name(action, args))
        if render is None:
            return None
        try:
            return render(args or {}, receipt or {}, actor, ctx)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            return None

    def panel(self, panel, data, args, actor, ctx):
        return None


def build(runtime):
    tools, actions, receipts, captions = [], [], {}, {}
    commands = {}

    # ── Python 脚本伞：写、改、查、跑 ────────────────────────────────
    if 'sandbox:programs' in runtime.capabilities:
        def script_write(world, actor, source: str):
            return runtime.player_api(world, 'sandbox:programs').write(actor, source)

        def script_patch(world, actor, old: str, new: str):
            return runtime.player_api(world, 'sandbox:programs').patch(actor, old, new)

        def script_check(world, actor, source: str | None = None):
            return runtime.player_api(world, 'sandbox:programs').check(actor, source)

        def script_status(world, actor, include_source: bool = False):
            return runtime.player_api(world, 'sandbox:programs').status(actor, include_source)

        script_specs = (
            Action('script_write', script_write, owner='sandbox:programs', api=('write',),
                   description='保存本国 Python 量化脚本。脚本在沙盒里跑，可用 '
                              'api.call(capability, method, ...) 调行情与下单、api.observe() 看自己的持仓、'
                              'inputs 取本回合参数、print 打印结果。源码跨回合复用，改用 patch 精确替换一处。'
                              '例：q=api.call("stock:market","quotes"); s=[r for r in q if r["ma5"] and r["ma5"]>r["price"]];'
                              'print(s)'),
            Action('script_patch', script_patch, owner='sandbox:programs', api=('patch',),
                   description='精确替换已保存源码中的一处内容（old 必须在源码里恰好出现一次），不执行'),
            Action('script_check', script_check, owner='sandbox:programs', api=('check',), read_only=True,
                   description='免费检查 Python 语法与安全规则，不执行、不消耗窗口额度；保存后先检查再跑'),
            Tool('script_status', script_status, owner='sandbox:programs', api=('status',), read_only=True, fact=True,
                 description='本国脚本状态：上回合执行结果（打印输出、报错类型与行列）、源码版本与预算；'
                            'include_source=true 查看源码'),
        )
        umbrella = command_layer.action_tool(
            runtime, 'script',
            '本国量化脚本：action=write 保存源码、patch 精确改一处、check 免费查语法、status 查状态。'
            '脚本不在你的回合里跑：等所有玩家都结束行动，脚本在回合末那个节点一起执行，'
            '看到同一时刻的同一份行情，下单进同一本挂单簿、回合末统一撮合；'
            '执行结果（打印输出与报错）在下一回合开局给你。'
            '买卖只能通过脚本下单：api.call("stock:market","buy",symbol=...,quantity=...)；'
            '行情用 api.call("stock:market","quotes")，历史用 api.call("stock:market","history",symbol=...,limit=...)。',
            (('write', script_specs[0]), ('patch', script_specs[1]), ('check', script_specs[2]),
             ('status', script_specs[3])))
        tools.append(umbrella)
        tools.extend(spec for spec in script_specs if isinstance(spec, Tool) and not isinstance(spec, Action))
        actions.extend(spec for spec in script_specs if isinstance(spec, Action))
        commands['script'] = {'write': 'script_write', 'patch': 'script_patch',
                               'check': 'script_check', 'status': 'script_status'}
        receipts.update({
            'script_write': _write_receipt,
            'script_patch': _patch_receipt,
            'script_check': _check_receipt,
            'script_status': _status_receipt,
        })
        captions.update({name: (lambda args, actor, receipt, ctx, name=name: _CAPTIONS[name])
                         for name in ('script_write', 'script_patch', 'script_check',
                                      'script_status')})

    # ── 只读事实 ────────────────────────────────────────────────────
    if 'stock:portfolio' in runtime.capabilities:
        def portfolio(world, actor):
            return runtime.player_api(world, 'stock:portfolio').holdings(actor)

        tools.append(Tool('portfolio', portfolio, owner='stock:portfolio',
                          api=('holdings', 'equity_curve'),
                          read_only=True, fact=True,
                          description='自己的账户：现金、负债、每只持仓的数量与成本、当前市值与浮盈亏、'
                                     '总财富与收益率、授信额度、追保线、已付利息、最近的净值曲线与强平记录。'))

    if 'stock:market' in runtime.capabilities:
        def market(world, actor):
            return runtime.player_api(world, 'stock:market').quotes(actor)

        tools.append(Tool('market', market, owner='stock:market', api=('quotes',),
                          covers=(('stock:market', 'history'), ('stock:market', 'catalog')),
                          read_only=True, fact=True,
                          description='当前行情：每只股票的现价、昨收、涨跌、ma5/ma10/ma20 均线、动量、'
                                     '已实现波动率，以及流通盘与冲击成本、手续费参数。'))

        def stock_trade(world, actor, symbol=None, quantity=None):
            raise ValueError('买卖只能通过 Python 脚本下单：把下单写进脚本，用 '
                             'api.call("stock:market","buy"/"sell",symbol=...,quantity=...) 发出。')

        # 下单方法必须被某个工具覆盖才能进 [PlayerAPI]（脚本才调得到），
        # 但模型工具表里不能出现买卖工具：这里放一个不出现在工具表里的封堵工具，
        # 真被按名字调用就直接回执拒绝，并说明正确路径。
        tools.append(Tool('stock_trade', stock_trade, owner='stock:market', api=('buy',),
                          covers=(('stock:market', 'sell'), ('stock:market', 'cancel')),
                          hidden=True,
                          description='买卖封堵：买卖只能通过 Python 脚本下单，不作为模型工具暴露。'))

    if 'stock:clock' in runtime.capabilities:
        def round_view(world, actor):
            return runtime.player_api(world, 'stock:clock').snapshot(actor)

        tools.append(Tool('round', round_view, owner='stock:clock', api=('snapshot',),
                          read_only=True, fact=True,
                          description='回合日历与窗口额度：第几个回合、今天开市还是休市、离下次开盘还有几回合、'
                                     '当前处在哪个窗口、额度用掉多少、已经过去多少分钟。'))

    # ── 银行：借贷 ──────────────────────────────────────────────────
    if 'stock:portfolio' in runtime.capabilities:
        def bank_borrow(world, actor, amount: float):
            return runtime.player_api(world, 'stock:portfolio').borrow(actor, amount)

        def bank_repay(world, actor, amount: float):
            return runtime.player_api(world, 'stock:portfolio').repay(actor, amount)

        bank_specs = (
            Action('bank_borrow', bank_borrow, owner='stock:portfolio', api=('borrow',),
                   description='向银行借钱：现金进账，负债上升，每回合末按利率计息。'
                              '受授信额度限制（基础授信 + 追保比例 × 持仓市值），超过会被拒。'
                              '负债超过追保线会被银行强制平仓。'),
            Action('bank_repay', bank_repay, owner='stock:portfolio', api=('repay',),
                   description='还给银行钱：先扣现金再减负债，不能超过现金或负债余额。'),
        )
        umbrella = command_layer.action_tool(
            runtime, 'bank',
            '银行：action=borrow 借钱、repay 还钱。借钱要算利息，也会放大强制平仓的风险。',
            (('borrow', bank_specs[0]), ('repay', bank_specs[1])))
        tools.append(umbrella)
        actions.extend(bank_specs)
        commands['bank'] = {'borrow': 'bank_borrow', 'repay': 'bank_repay'}
        receipts.update({'bank_borrow': _borrow_receipt, 'bank_repay': _repay_receipt})
        captions.update({'bank_borrow': '银行放款', 'bank_repay': '银行还款'})

    # ── 时钟休眠：只给脚本用 ────────────────────────────────────────
    if 'stock:clock' in runtime.capabilities:
        def clock_sleep(world, actor, ticks: int = 1):
            raise ValueError('休眠是脚本里的事：让脚本调 '
                             'api.call("stock:clock","sleep",ticks=1) 等市场往前走几片。')

        tools.append(Tool('clock_sleep', clock_sleep, owner='stock:clock', api=('sleep',),
                          hidden=True,
                          description='时钟休眠封堵：按时间片等待只能写在脚本里。'))

    # ── 结束回合 ────────────────────────────────────────────────────
    if 'stock:clock' in runtime.capabilities:
        def end_turn(world, actor, summary: str = ''):
            return runtime.player_api(world, 'stock:clock').end_turn(actor, summary)

        tools.append(Tool('end_turn', end_turn, owner='stock:clock', api=('end_turn',),
                          description='结束本回合行动，提交一句小结（这回合做了什么、打算做什么）。'
                                     '结算在所有玩家都结束以后统一进行。'))
        receipts['end_turn'] = _end_turn_receipt
        captions['end_turn'] = '结束回合'

    return StockToolLayer(runtime, tools, actions, receipts, captions, commands)


# ── 回执渲染 ────────────────────────────────────────────────────────
_CAPTIONS = {
    'script_write': '保存脚本',
    'script_patch': '修订脚本',
    'script_check': '脚本检查',
    'script_status': '脚本状态',
}


def _money(value):
    if isinstance(value, (int, float)):
        return f'{value:.2f}'
    return str(value)


def _write_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    return f'脚本已保存（第 {data.get("revision", "?")} 版，{data.get("bytes", "?")} 字节）。'


def _patch_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    return f'脚本已修订（第 {data.get("revision", "?")} 版）。'


def _check_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    if data.get('valid'):
        return '语法检查通过。'
    error = data.get('error') or {}
    where = f'第 {error.get("line")} 行第 {error.get("column")} 列：' if error.get('line') else ''
    return f'语法有问题：{where}{error.get("message")}'


def _status_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    result = data.get('result')
    if not result:
        return '脚本还没跑过：脚本在回合末由引擎统一执行，结果下回合开局给你。'
    lines = [f'上回合脚本：{result.get("status")}，步数 {result.get("steps")}，'
             f'API 调用 {result.get("calls")}，动作 {result.get("actions")}。']
    error = result.get('error')
    if error:
        where = f'第 {error.get("line")} 行第 {error.get("column")} 列：' if error.get('line') else ''
        lines.append(f'报错 {error.get("type")}：{where}{error.get("message")}')
    if result.get('output'):
        lines.append('打印输出：\n' + result['output'])
    return '\n'.join(lines)


def _borrow_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    return (f'借到 {_money(data.get("borrowed", 0))}：现金 {_money(data.get("cash", 0))}，'
            f'负债 {_money(data.get("debt", 0))}，授信上限 {_money(data.get("credit_limit", 0))}。')


def _repay_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    return (f'还款 {_money(data.get("repaid", 0))}：现金 {_money(data.get("cash", 0))}，'
            f'负债 {_money(data.get("debt", 0))}。')


def _end_turn_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    return f'第 {data.get("round", "?")} 回合行动结束。{data.get("summary", "")}'
