"""模拟炒股发行版的验收。

覆盖：模型工具面（买卖只能走脚本）、交易日历、脚本下单与回合末结算、
回合开局简报里的打印输出与报错、借贷计息、银行强制平仓、破产退市、
窗口额度、存档往返与同种子可复现。
"""
import json
import shutil
import unittest
from copy import deepcopy
from pathlib import Path

from host import GameHost
from mods.stock.client import StockClient, StockView

ROOT = Path(__file__).resolve().parents[1]

BUY_SCRIPT = """order = api.call('stock:market', 'buy', symbol='shanhai', quantity=100)
print('已挂单：' + order['data']['symbol'] + ' x' + str(order['data']['quantity']))
"""

PRINT_SCRIPT = """print('今日观察：市场情绪偏暖')
quotes = api.call('stock:market', 'quotes')
print('股票数：' + str(len(quotes)))
"""

BROKEN_SCRIPT = """print('开始计算')
print(missing_variable)
"""


def config(*scripts, seed=11, actors=2, rounds=None):
    """rounds 给出「回合号 → 脚本」；不给就按顺序排在第 1、2、3…回合。"""
    table = dict(rounds) if rounds is not None else {index + 1: source for index, source in enumerate(scripts)}
    nations = []
    for index in range(actors):
        nation = {'id': f'stock:player{index}', 'name': f'量化{index}', 'client': 'replay'}
        if table:
            nation['scripts'] = table
        nations.append(nation)
    return {'mods': 'mods.stock.toml', 'seed': seed, 'nations': nations}


def tool_call(ident, name, **arguments):
    return {'id': ident, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


class FakeBackend:
    """假模型：写脚本、跑脚本、结束回合。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.tools = []

    def chat_turn(self, messages, tools, cfg, on_retry=None):
        self.requests.append(deepcopy(messages))
        self.tools.append([tool['function']['name'] for tool in tools])
        if self.responses:
            message = self.responses.pop(0)
        else:
            message = {'content': '先看自己的持仓，再按均线策略下单。',
                       'tool_calls': [tool_call('1', 'script', action='write', source=BUY_SCRIPT),
                                      tool_call('2', 'script', action='run'),
                                      tool_call('3', 'end_turn', summary='按均线策略执行一轮买卖。')]}
        return message, {'hit': 10, 'miss': 2}

    def complete_text(self, messages, cfg, tools=None, **kwargs):
        return '按均线策略执行。', {'hit': 8, 'miss': 1}


class StockGameTest(unittest.TestCase):
    def host(self, *scripts, seed=11, actors=2, rounds=None):
        return GameHost.new(config(*scripts, seed=seed, actors=actors, rounds=rounds))

    def world(self, host):
        return host.world

    # ── 工具面：买卖只能走 Python ────────────────────────────────
    def test_model_surface_has_no_trade_tool(self):
        host = self.host()
        player = host.session.player('stock:player0')
        names = {schema['function']['name'] for schema in player.actions()}
        self.assertEqual(names & {'stock_trade'}, set(), '买卖封堵工具不该出现在模型工具表里')
        for forbidden in ('buy', 'sell', 'market_buy', 'market_sell', 'trade'):
            self.assertNotIn(forbidden, names)
        self.assertIn('script', names)
        self.assertIn('bank', names)

    def test_direct_trade_call_is_rejected(self):
        host = self.host()
        player = host.session.player('stock:player0')
        receipt = player.submit('stock_trade', {'symbol': 'shanhai', 'quantity': 10})
        self.assertFalse(receipt['ok'])
        self.assertIn('脚本', receipt['message'])

    def test_trade_methods_exist_for_scripts(self):
        host = self.host()
        player = host.session.player('stock:player0')
        receipt = player.submit('script', {'action': 'write', 'source': BUY_SCRIPT})
        self.assertTrue(receipt['ok'], receipt.get('message'))
        receipt = player.submit('script', {'action': 'run'})
        self.assertTrue(receipt['ok'], receipt.get('message'))

    # ── 交易日历 ──────────────────────────────────────────────────
    def test_five_trading_days_then_two_rest_days(self):
        host = self.host()
        expected = [True] * 5 + [False] * 2 + [True] * 5 + [False] * 2 + [True] * 2
        for index in range(1, len(expected) + 1):
            self.assertEqual(host.world.clock.period(), index)
            self.assertEqual(host.world.clock.trading_day(), expected[index - 1],
                             f'第 {index} 回合的交易日判断不对')
            host.cycle()

    def test_rest_day_rejects_trading(self):
        host = self.host(rounds={6: BUY_SCRIPT})
        for _ in range(5):
            host.cycle()
        self.assertFalse(host.world.clock.trading_day())
        host.cycle()
        status = host.world.scripts.status('stock:player0')
        self.assertIsNotNone(status['result'])
        self.assertEqual(status['result']['status'], 'failed')
        self.assertIn('休市', status['result']['error']['message'])

    # ── 下单与结算 ────────────────────────────────────────────────
    def test_script_order_settles_at_round_end(self):
        host = self.host(BUY_SCRIPT)
        before = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(before['positions'], [])
        host.cycle()
        after = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(len(after['positions']), 1)
        position = after['positions'][0]
        self.assertEqual(position['symbol'], 'shanhai')
        self.assertEqual(position['quantity'], 100)
        self.assertLess(after['cash'], before['cash'])
        fills = host.world.market.fills()
        mine = [fill for fill in fills if fill['actor'] == 'stock:player0']
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['side'], 'buy')
        self.assertEqual(mine[0]['quantity'], 100)

    def test_script_cannot_take_shares_it_does_not_own(self):
        host = self.host("api.call('stock:market', 'sell', symbol='shanhai', quantity=10)\n")
        host.cycle()
        status = host.world.scripts.status('stock:player0')
        self.assertEqual(status['result']['status'], 'failed')
        self.assertIn('持仓不足', status['result']['error']['message'])

    # ── 回合开局简报 ──────────────────────────────────────────────
    def test_briefing_shows_previous_output_and_error(self):
        host = self.host(PRINT_SCRIPT, BROKEN_SCRIPT)
        view = StockView({'language': 'zh-CN'})
        player = host.session.player('stock:player0')

        host.cycle()
        text = view.stock_briefing(player.observe())
        self.assertIn('市场情绪偏暖', text)
        self.assertIn('股票数', text)
        self.assertIn('第 2 回合', text)
        self.assertIn('总财富', text)

        host.cycle()
        observation = player.observe()
        self.assertEqual(observation['facts']['script_status']['result']['status'], 'failed')
        text = view.stock_briefing(observation)
        self.assertIn('NameError', text)
        self.assertIn('第 2 行', text)
        self.assertIn('print(missing_variable)', text)
        self.assertIn('开始计算', text)

    def test_briefing_reports_holdings_and_calendar(self):
        host = self.host(BUY_SCRIPT)
        host.cycle()
        player = host.session.player('stock:player0')
        text = StockView({'language': 'zh-CN'}).stock_briefing(player.observe())
        self.assertIn('山海智造', text)
        self.assertIn('今日开市', text)
        self.assertIn('授信上限', text)

    # ── 借贷、计息与强制平仓 ──────────────────────────────────────
    def test_borrow_adds_cash_and_debt(self):
        host = self.host()
        player = host.session.player('stock:player0')
        receipt = player.submit('bank', {'action': 'borrow', 'amount': 10000})
        self.assertTrue(receipt['ok'], receipt.get('message'))
        book = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(book['cash'], 110000.0)
        self.assertEqual(book['debt'], 10000.0)
        self.assertEqual(book['wealth'], 100000.0)

    def test_borrow_beyond_credit_limit_is_rejected(self):
        host = self.host()
        player = host.session.player('stock:player0')
        receipt = player.submit('bank', {'action': 'borrow', 'amount': 999999})
        self.assertFalse(receipt['ok'])
        self.assertIn('授信', receipt['message'])

    def test_interest_is_charged_each_round(self):
        host = self.host()
        player = host.session.player('stock:player0')
        player.submit('bank', {'action': 'borrow', 'amount': 10000})
        host.cycle()
        book = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(book['debt'], 10200.0)
        self.assertEqual(book['interest_paid'], 200.0)
        self.assertEqual(book['cash'], 109800.0)

    def test_bank_forces_liquidation_when_margin_is_broken(self):
        host = self.host(BUY_SCRIPT)
        host.cycle()
        account = host.world.portfolio.accounts['stock:player0']
        self.assertTrue(account['positions'])
        account['debt'] = 90000.0
        host.cycle()
        book = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(book['positions'], [], '追保线被击穿后银行应该把持仓砍掉')
        self.assertTrue(book['liquidations'], '应该有强平记录')
        record = book['liquidations'][-1]
        self.assertEqual(record['symbol'], 'shanhai')
        self.assertEqual(record['quantity'], 100)

    def test_negative_net_worth_retires_the_player(self):
        host = self.host()
        host.world.portfolio.accounts['stock:player0']['cash'] = -5000.0
        host.cycle()
        book = host.world.portfolio.holdings('stock:player0')
        self.assertFalse(book['alive'])
        self.assertNotIn('stock:player0', host.world.portfolio.active_actors())
        self.assertNotIn('stock:player0', host.world.nation_actors)
        self.assertIn('stock:player1', host.world.nation_actors)

    # ── 窗口额度 ──────────────────────────────────────────────────
    def test_script_run_switches_to_the_work_window(self):
        host = self.host(BUY_SCRIPT)
        self.assertEqual(host.world.clock.window_of('stock:player0'), 'market')
        host.cycle()
        self.assertEqual(host.world.clock.window_of('stock:player0'), 'market')

    def test_window_budget_is_enforced(self):
        host = self.host()
        clock = host.world.clock
        for _ in range(20):
            clock.charge('stock:player0')
        with self.assertRaises(ValueError) as caught:
            clock.charge('stock:player0')
        self.assertIn('开盘', str(caught.exception))

    def test_check_and_status_do_not_consume_budget(self):
        host = self.host()
        player = host.session.player('stock:player0')
        receipt = player.submit('script', {'action': 'check', 'source': 'print(1)\n'})
        self.assertTrue(receipt['ok'])
        self.assertTrue(receipt['data']['valid'])
        player.submit('script', {'action': 'status'})
        self.assertEqual(host.world.clock.used_of('stock:player0')['market'], 0)

    def test_check_reports_syntax_errors(self):
        host = self.host()
        player = host.session.player('stock:player0')
        receipt = player.submit('script', {'action': 'check', 'source': 'print(\n'})
        self.assertTrue(receipt['ok'])
        self.assertFalse(receipt['data']['valid'])
        self.assertIn('line', receipt['data']['error'])

    def test_script_run_costs_one_action_and_marks_the_market_open(self):
        host = self.host()
        player = host.session.player('stock:player0')
        player.submit('script', {'action': 'write', 'source': BUY_SCRIPT})
        self.assertEqual(host.world.clock.used_of('stock:player0')['market'], 1)
        player.submit('script', {'action': 'run'})
        counts = host.world.clock.used_of('stock:player0')
        self.assertEqual(counts['market'], 2)
        self.assertEqual(counts['work'], 0)
        self.assertEqual(host.world.clock.window_of('stock:player0'), 'work')
        player.submit('script', {'action': 'patch', 'old': 'quantity=100', 'new': 'quantity=50'})
        self.assertEqual(host.world.clock.used_of('stock:player0')['work'], 1)

    # ── 存档与可复现 ──────────────────────────────────────────────
    def test_save_and_restore_roundtrip(self):
        host = self.host(BUY_SCRIPT)
        host.cycle()
        path = ROOT / '.tmp-stock-save.json'
        try:
            host.save(str(path))
            restored = GameHost.load(str(path), config(BUY_SCRIPT))
            self.assertEqual(restored.world.portfolio.holdings('stock:player0'),
                             host.world.portfolio.holdings('stock:player0'))
            self.assertEqual(restored.world.clock.period(), host.world.clock.period())
        finally:
            path.unlink(missing_ok=True)
            shutil.rmtree(path.with_suffix(path.suffix + '.audit'), ignore_errors=True)

    def test_same_seed_replays_the_same_prices(self):
        first = self.host(seed=99)
        second = self.host(seed=99)
        for _ in range(4):
            first.cycle()
            second.cycle()
        prices_first = [row['price'] for row in first.world.market.quotes()]
        prices_second = [row['price'] for row in second.world.market.quotes()]
        self.assertEqual(prices_first, prices_second)

    def test_different_seed_moves_prices_differently(self):
        first = self.host(seed=99)
        second = self.host(seed=100)
        for _ in range(4):
            first.cycle()
            second.cycle()
        prices_first = [row['price'] for row in first.world.market.quotes()]
        prices_second = [row['price'] for row in second.world.market.quotes()]
        self.assertNotEqual(prices_first, prices_second)

    # ── 模型客户端：看得见简报，买卖只在脚本里 ────────────────────
    def test_llm_client_sees_the_briefing_and_has_no_trade_tool(self):
        host = self.host()
        backend = FakeBackend()
        client = StockClient({'language': 'zh-CN'}, backend=backend)
        client.play(host.session.player('stock:player0'))
        prompt = json.dumps(backend.requests[0], ensure_ascii=False)
        self.assertIn('本回合开局简报', prompt)
        self.assertIn('山海智造', prompt)
        self.assertIn('总财富', prompt)
        system = backend.requests[0][0]['content']
        self.assertIn('交易日', system)
        self.assertIn('强制平仓', system)
        names = set(backend.tools[0])
        self.assertNotIn('stock_trade', names)
        for forbidden in ('buy', 'sell', 'market_buy', 'market_sell'):
            self.assertNotIn(forbidden, names)
        self.assertIn('script', names)
        pending = host.world.market.pending_orders('stock:player0')
        self.assertEqual(len(pending), 1, '模型应当通过脚本挂出订单')
        self.assertEqual(pending[0]['symbol'], 'shanhai')
        self.assertEqual(pending[0]['quantity'], 100)
        book = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(book['positions'], [], '成交要等回合末统一撮合')

    # ── 规则与结算表 ──────────────────────────────────────────────
    def test_rules_mention_the_calendar_and_the_sandbox(self):
        host = self.host()
        manual = host.runtime.manual()
        self.assertIn('交易日', manual)
        self.assertIn('强制平仓', manual)
        self.assertIn('Python 量化沙盒', manual)


if __name__ == '__main__':
    unittest.main()
