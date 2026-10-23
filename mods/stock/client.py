"""股市版模型客户端。

* :class:`StockView` 在原版视图后面补一段「本回合开局简报」：回合与日历、
  账户与财富、持仓、行情、上回合脚本的打印输出与报错。
  原版简报是战场口径，股市发行版用不到，所以这一段才是模型每回合真正读到的战情。
* :class:`StockClient` 把模型客户端接到股市发行版上。
* :class:`SimClient` 不需要模型与 API key 的离线客户端，跑一套简单均线策略，
  用来验证规则与结算，也方便本地试玩。
"""
from clients.llm import LLMClient
from clients.model_view import ModelView


class StockView(ModelView):
    """回合开局的股市简报。"""

    def state(self, observation, events):
        tail, image = super().state(observation, events)
        return tail + self.stock_briefing(observation), image

    def stock_briefing(self, observation):
        facts = observation.get('facts') or {}
        lines = ['', '【本回合开局简报】']

        view = facts.get('round') or {}
        if view:
            if view.get('trading_day'):
                lines.append(f'第 {view.get("round")} 回合，今日开市。')
            else:
                lines.append(f'第 {view.get("round")} 回合，今日休市（还有 {view.get("days_to_next_open")} 回合开市）：'
                             f'不能买卖，但可以改脚本、规划、借贷。')
            lines.append(f'当前窗口：{view.get("window_name")}，额度 {view.get("window_limit")} 分钟，'
                         f'已用 {view.get("window_used")}，本回合已过去 {view.get("elapsed_minutes")} 分钟。'
                         f'盘口时间片 {view.get("tick")}/{view.get("slices")}：'
                         f'每进一个行动者市场走一片，价格是分段走出来的，不是回合末一次跳完。'
                         + ('本回合的脚本已经跑过。' if view.get('opened') else '本回合还没跑过脚本。'))

        account = facts.get('portfolio') or {}
        if account:
            if not account.get('alive', True):
                lines.append(f'你已破产退市（第 {account.get("retired_round")} 回合），本局不再行动。')
            lines.append(f'现金 {account.get("cash")}，负债 {account.get("debt")}，'
                         f'持仓市值 {account.get("holdings_value")}，'
                         f'总财富 {account.get("wealth")}（{account.get("return_pct")}%）。'
                         f'授信上限 {account.get("credit_limit")}，追保线 {account.get("margin_limit")}。')
            positions = account.get('positions') or []
            if positions:
                lines.append('持仓：' + '；'.join(
                    f'{p["name"]}({p["symbol"]}) {p["quantity"]} 股，成本 {p["avg_cost"]}，'
                    f'现价 {p["price"]}，浮盈亏 {p["pnl"]}' for p in positions))
            else:
                lines.append('持仓：空仓。')
            liquidations = account.get('liquidations') or []
            if liquidations:
                last = liquidations[-1]
                lines.append(f'银行强制平仓：第 {last.get("round")} 回合卖出 {last.get("quantity")} 股 '
                             f'{last.get("symbol")}，回收 {last.get("amount")}。')
            curve = account.get('equity_curve') or []
            if curve:
                lines.append('净值曲线：' + '，'.join(
                    f'第 {point["round"]} 回合 {point["wealth"]}' for point in curve[-6:]))

        quotes = facts.get('market') or []
        if quotes:
            lines.append('行情（代码 名称 现价 涨跌 ma5 ma10 波动率）：')
            for row in quotes:
                lines.append(f'  {row["symbol"]} {row["name"]} {row["price"]} '
                             f'{row["change_pct"] * 100:+.2f}% ma5={row["ma5"]} ma10={row["ma10"]} '
                             f'vol={row["volatility"]}')

        status = facts.get('script_status') or {}
        result = status.get('result')
        if result:
            lines.append(f'上回合脚本：{result.get("status")}，步数 {result.get("steps")}，'
                         f'API 调用 {result.get("calls")}，动作 {result.get("actions")}。')
            error = result.get('error')
            if error:
                where = f'第 {error.get("line")} 行第 {error.get("column")} 列' if error.get('line') else ''
                lines.append(f'报错 {error.get("type")}：{where} {error.get("message")}')
                if error.get('source'):
                    lines.append(f'出错那行：{error.get("source")}')
            if result.get('output'):
                lines.append('打印输出：\n' + result['output'])
        else:
            lines.append('上回合还没跑过脚本。' if status.get('available') else '本回合已经用过脚本机会。')

        return '\n'.join(lines)


class StockClient(LLMClient):
    """模型客户端：换用股市视图，系统提示里带上发行版自己的规则。"""

    def __init__(self, cfg, state=None, backend=None, ui=None, record=None, services=None):
        super().__init__(cfg, state, backend, ui, record, services)
        self.ui = StockView(self.cfg, presentation=self.presentation)


STRATEGY = '''quotes = api.call('stock:market', 'quotes')
book = api.call('stock:portfolio', 'holdings')
calendar = api.call('stock:clock', 'snapshot')
if not calendar['trading_day']:
    print('休市日：本日不交易，继续打磨脚本。')
else:
    held = {}
    for row in book['positions']:
        held[row['symbol']] = row['quantity']
    cash = book['cash']
    picked = []
    for row in quotes:
        symbol = row['symbol']
        if not row['ma5']:
            continue
        if symbol in held:
            if row['price'] > row['ma5']:
                api.call('stock:market', 'sell', symbol=symbol, quantity=held[symbol])
        else:
            if row['price'] < row['ma5']:
                picked.append(symbol)
    budget = cash * 0.3
    for symbol in picked[:3]:
        for row in quotes:
            if row['symbol'] == symbol:
                qty = int(budget / row['price'])
                if qty > 0:
                    api.call('stock:market', 'buy', symbol=symbol, quantity=qty)
                break
    print('sim：候选买入 ' + str(len(picked[:3])) + ' 只。')
'''


class ReplayClient:
    """回放客户端：按配置里的脚本表逐回合提交指定脚本。

    用途是演示与验收：把每回合要跑的 Python 脚本写进玩家配置的 ``scripts``
    （键是回合号），客户端就会在对应回合保存并执行它，跑完结束回合。
    """

    kind = 'replay'
    version = '1'

    def __init__(self, cfg, state=None, backend=None, ui=None, record=None, services=None):
        self.cfg = dict(cfg or {})
        self.state = dict(state or {})
        self.scripts = {str(key): value for key, value in (self.cfg.get('scripts') or {}).items()}

    def play(self, player, *, emit=None, head=None, on_call=None, **kwargs):
        round_number = player.observe()['period']
        source = self.scripts.get(str(round_number))
        receipts = []
        if source is not None:
            receipts.append(player.submit('script', {'action': 'write', 'source': source}))
            receipts.append(player.submit('script', {'action': 'run'}))
        receipts.append(player.submit('end_turn', {'summary': '回放回合结束。'}))
        return receipts

    def to_save(self):
        return {'kind': self.kind, 'version': self.version, 'state': dict(self.state)}


class SimClient:
    """离线客户端：写一套均线策略、跑一次、结束回合。"""

    kind = 'sim'
    version = '1'

    def __init__(self, cfg, state=None, backend=None, ui=None, record=None, services=None):
        self.cfg = dict(cfg or {})
        self.state = dict(state or {'written': False})

    def play(self, player, *, emit=None, head=None, on_call=None, **kwargs):
        receipts = []
        if not self.state.get('written'):
            receipts.append(player.submit('script', {'action': 'write', 'source': STRATEGY}))
            self.state['written'] = True
        receipts.append(player.submit('script', {'action': 'run'}))
        receipts.append(player.submit('end_turn', {'summary': '按均线策略执行了一轮买卖。'}))
        return receipts

    def to_save(self):
        return {'kind': self.kind, 'version': self.version, 'state': dict(self.state)}
