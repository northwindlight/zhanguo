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
        if isinstance(render, str):
            return render
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
        def script_write(world, actor, source: str, slot: str = "1"):
            return runtime.player_api(world, 'sandbox:programs').write(actor, source, slot)

        def script_patch(world, actor, old: str, new: str, slot: str = "1"):
            return runtime.player_api(world, 'sandbox:programs').patch(actor, old, new, slot)

        def script_check(world, actor, source: str | None = None, slot: str = "1"):
            return runtime.player_api(world, 'sandbox:programs').check(actor, source, slot)

        def script_status(world, actor, include_source: bool = False, slot: str = "1"):
            return runtime.player_api(world, 'sandbox:programs').status(actor, include_source, slot)

        def script_run(world, actor, slot: str = "research", inputs: dict | None = None):
            return runtime.player_api(world, 'sandbox:programs').run(actor, slot, inputs)

        script_specs = (
            Action('script_write', script_write, owner='sandbox:programs', api=('write',),
                   description='保存本国 Python 量化脚本。沙盒是Python子集：支持 def/for/while/if、'
                              '列表与字典推导、切片、三元，以及"先def具名函数再作 sorted/min/max 的 key="；'
                              '不支持 f-string（用+拼接或%格式化）、lambda、set、try/class/import。'
                              '脚本里 api.call(capability, method, ...) 调行情与下单，inputs 取本回合参数，'
                              'print 打印结果；源码跨回合复用，改用 patch 精确替换一处。'
                              '可运行例：\\ndef on_tick():\\n'
                              '    rows = api.call("stock:market", "quotes")\\n'
                              '    def chg(r): return r["change_pct"]\\n'
                              '    top = sorted(rows, key=chg, reverse=True)[0]\\n'
                              '    print("最强 " + top["symbol"] + " " + str(top["price"]))\\n'
                              '    api.call("stock:clock", "sleep", ticks=200)'),
            Action('script_patch', script_patch, owner='sandbox:programs', api=('patch',),
                   description='精确替换已保存源码中的一处内容（old 必须在源码里恰好出现一次），不执行'),
            Action('script_check', script_check, owner='sandbox:programs', api=('check',), read_only=True,
                   description='免费检查 Python 语法与沙盒规则，不执行、不消耗窗口额度；'
                              '被拒写法会附一句改法提示，保存后先检查再结束回合'),
            Tool('script_status', script_status, owner='sandbox:programs', api=('status',), read_only=True, fact=True,
                 description='本国脚本状态：上回合执行结果（打印输出、报错类型与行列）、源码版本与预算；'
                            'include_source=true 查看源码'),
        )
        script_specs = (*script_specs, Action('script_run', script_run, owner='sandbox:programs', api=('run',),
            description='行动时手动运行已解锁的research研究槽，只读；inputs为JSON参数。不能下单或推进行情，共享账户每日算力。'))
        umbrella = command_layer.action_tool(
            runtime, 'script',
            '计算槽：write保存、patch修订、check免费语法检查（会附改法提示）、status查结果；slot为1～4或research。'
            '自动交易槽普通源码每日一次；定义def on_tick():则按sleep间隔回调，state是独立持久JSON字典。'
            '沙盒Python子集：无f-string/lambda/set/try/class/import；排序用 def 具名函数作 key=，字符串用+拼接。'
            'api.call("stock:clock","sleep",ticks=N)只安排下次回调，回调应返回，不写while等待。'
            'run仅手动运行已解锁的research槽，用于分析和回测，只读不下单。'
            '交易日全体结束后沿20000共同tick自动运行，委托最早下一tick生效，成交立即可见。',
            (('write', script_specs[0]), ('patch', script_specs[1]), ('check', script_specs[2]),
             ('status', script_specs[3]), ('run', script_specs[4])))
        tools.append(umbrella)
        tools.extend(spec for spec in script_specs if isinstance(spec, Tool) and not isinstance(spec, Action))
        actions.extend(spec for spec in script_specs if isinstance(spec, Action))
        commands['script'] = {'write': 'script_write', 'patch': 'script_patch',
                               'check': 'script_check', 'status': 'script_status', 'run': 'script_run'}
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

        def pool(world, actor):
            return runtime.player_api(world, 'stock:market').pool(actor)

        def day_report(world, actor):
            return runtime.player_api(world, 'stock:market').day_summary(actor)

        tools.append(Tool('pool', pool, owner='stock:market', api=('pool',),
                          read_only=True, fact=True,
                          description='动态股票池：当前交易名单、下一只新股（名称/行业/发行价/挂牌回合）'
                                     '与退市警示名单（股票及复核回合）。新股挂牌和退市摘牌都会先公开公告。'))

        tools.append(Tool('market', market, owner='stock:market', api=('quotes',),
                          covers=(('stock:market', 'history'), ('stock:market', 'catalog'), ('stock:market', 'pool'), ('stock:market', 'orders'), ('stock:market', 'fills')),
                          read_only=True, fact=True,
                          description='当前行情：每只股票的现价、昨收、涨跌、ma5/ma10/ma20 均线、动量、'
                                     '已实现波动率、盘口深度十档、可借券余额、退市复核日，以及每只股票'
                                     '已公布的消息（市场快讯/公司快讯/财报/公司事件，含现金流变化百分比'
                                     '与个性变化）和公开财报日程（next_report_round 与当日 report_tick）。'
                                     '股票池是动态的：api.call("stock:market","pool") 查询当前名单、'
                                     '下一只新股（名称/行业/发行价/挂牌回合）和退市警示名单。'))
        tools.append(Tool('day_report', day_report, owner='stock:market', api=('day_summary',),
                          read_only=True, fact=True,
                          description='上一交易日的执行回执：自己委托各状态笔数（成交/部分成交后到期/'
                                     '撤单）、成交笔数、买入卖出股数与金额、手续费。用来核对策略是否按预期执行。'))

        def stock_trade(world, actor, symbol=None, quantity=None):
            raise ValueError('买卖只能通过 Python 脚本下单：把下单写进脚本，用 '
                             'api.call("stock:market","buy"/"sell",symbol=...,quantity=...) 发出。')

        # 下单方法必须被某个工具覆盖才能进 [PlayerAPI]（脚本才调得到），
        # 但模型工具表里不能出现买卖工具：这里放一个不出现在工具表里的封堵工具，
        # 真被按名字调用就直接回执拒绝，并说明正确路径。
        tools.append(Tool('stock_trade', stock_trade, owner='stock:market', api=('buy',),
                          covers=(('stock:market', 'sell'), ('stock:market', 'short'), ('stock:market', 'cover'), ('stock:market', 'cancel')),
                          hidden=True,
                          description='买卖封堵：买卖只能通过 Python 脚本下单，不作为模型工具暴露。'))

    if 'stock:clock' in runtime.capabilities:
        def round_view(world, actor):
            return runtime.player_api(world, 'stock:clock').snapshot(actor)

        tools.append(Tool('round', round_view, owner='stock:clock', api=('snapshot',),
                          read_only=True, fact=True,
                          description='回合日历与窗口额度：第几个回合、今天开市还是休市、离下次开盘还有几回合、'
                                     '共同tick、交易时刻与盘前规划动作的已用额度。'))

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

    if 'stock:compute' in runtime.capabilities:
        def compute_status(world, actor):
            return runtime.player_api(world, 'stock:compute').status(actor)
        def compute_buy(world, actor, kind: str):
            return runtime.player_api(world, 'stock:compute').buy(actor, kind)
        def compute_release(world, actor, kind: str):
            return runtime.player_api(world, 'stock:compute').release(actor, kind)
        specs = (
            Action('compute_buy', compute_buy, owner='stock:compute', api=('buy',),
                description='购买游戏计算资源，kind=pool增购共享算力池、slot增购自动交易槽、research解锁行动内研究槽；立即扣游戏现金，后续每日续租。'),
            Action('compute_release', compute_release, owner='stock:compute', api=('release',),
                description='退租一档pool或slot，或停用research；当日不退款、不重置用量，源码保留。'))
        tools.append(Tool('compute_status', compute_status, owner='stock:compute', api=('status',),
                          read_only=True, fact=True,
                          description='本账户算力池、槽位、研究槽、每日共享总量/用量/剩余额度、购买价和续租费用。'))
        tools.append(command_layer.action_tool(runtime, 'compute',
            '用游戏现金租计算资源：action=buy/release，kind=pool/slot/research。所有槽共享每日预算；研究槽仅行动中手动只读运行。',
            (('buy', specs[0]), ('release', specs[1]))))
        actions.extend(specs)
        commands['compute'] = {'buy': 'compute_buy', 'release': 'compute_release'}

    if 'stock:intelligence' in runtime.capabilities:
        def intel_status(world, actor):
            return runtime.player_api(world, 'stock:intelligence').status(actor)
        def intel_steal(world, actor, target: str):
            return runtime.player_api(world, 'stock:intelligence').steal(actor, target)
        spec = Action('intel_steal', intel_steal, owner='stock:intelligence', api=('steal',),
                      description='支付游戏费用，取得其他玩家的现金、银行贷款及各股票持仓规模快照；target为公开玩家id。报告不会自动刷新。')
        tools.append(Tool('intel_status', intel_status, owner='stock:intelligence', api=('status',),
                          read_only=True, fact=True, description='公开玩家名单、商业谍报价格及自己已购买的报告，含获取时间。'))
        tools.append(command_layer.action_tool(runtime, 'intel',
                     '商业谍报：action=steal，target=其他玩家id；按公布价格付费获取现金、贷款、持仓规模快照。',
                     (('steal', spec),)))
        actions.append(spec)
        commands['intel'] = {'steal': 'intel_steal'}

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
