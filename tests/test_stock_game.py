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
            message = {'content': '先看自己的持仓，再按均线策略准备这回合的脚本。',
                       'tool_calls': [tool_call('1', 'script', action='write', source=BUY_SCRIPT),
                                      tool_call('2', 'end_turn', summary='脚本写好了，等回合末统一执行。')]}
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
        player.submit('end_turn', {'summary': '脚本写好了。'})
        host.cycle()
        status = host.world.scripts.status('stock:player0')
        self.assertEqual(status['result']['status'], 'effective', status['result'])
        self.assertIn('已挂单', status['result']['output'])

    def test_script_is_not_executed_inside_the_turn(self):
        """脚本不在行动阶段跑：回合末那个节点才执行。"""
        host = self.host()
        player = host.session.player('stock:player0')
        player.submit('script', {'action': 'write', 'source': BUY_SCRIPT})
        self.assertIsNone(host.world.scripts.status('stock:player0')['result'],
                          '回合内脚本还不该有执行结果')
        self.assertEqual(host.world.market.pending_orders('stock:player0'), [])

    def test_threaded_concurrency_gives_the_same_result(self):
        """时间片轮转与真线程给出相同的对局结果（同一份行情、同一本挂单簿）。"""
        def run(mode):
            host = self.host(rounds={1: BUY_SCRIPT}, actors=3, seed=99)
            host.runtime.units['stock:python'].mod.concurrency = mode
            host.cycle()
            return [host.world.portfolio.holdings(actor) for actor in host.actors()]

        sliced = run('timeslice')
        threaded = run('threads')
        self.assertEqual(sliced, threaded)

    def test_scripts_run_together_at_the_round_end(self):
        """所有玩家都结束行动后，脚本在回合末一起执行、下单统一撮合。"""
        host = self.host(rounds={1: BUY_SCRIPT}, actors=2)
        host.cycle()
        for actor in ('stock:player0', 'stock:player1'):
            status = host.world.scripts.status(actor)
            self.assertIsNotNone(status['result'], f'{actor} 的脚本应当在回合末执行过')
            self.assertEqual(status['result']['status'], 'effective')
        fills = host.world.market.fills()
        self.assertEqual(len(fills), 2, '两个玩家的订单应当在同一次撮合里成交')
        self.assertEqual({fill['symbol'] for fill in fills}, {'shanhai'})

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

    # ── 盘中价格：不是瞬间跳的 ────────────────────────────────────
    def _spy_prices(self, host, symbol='shanhai'):
        """记录每个行动者进场时看到的价格与时间片。"""
        market = host.world.market
        seen = []
        ticks = []
        original = host.session.player

        def spy(actor):
            player = original(actor)
            seen.append(market.price_of(symbol))
            ticks.append(host.world.clock.tick_index())
            return player

        host.session.player = spy
        return seen, ticks

    def test_price_moves_during_the_session(self):
        """开盘窗口 20 分钟里行情连续变动：路径不是一根直线。"""
        host = self.host(actors=3)
        market = host.world.market
        opening = market.opening['shanhai']
        host.cycle()
        path = market.last_path['shanhai']
        self.assertGreaterEqual(len(path), 5, '路径至少要有几个采样点')
        self.assertGreater(len(set(path)), 1, '窗口里的价格应当连续变动')
        self.assertEqual(path[0], opening, '路径起点是开盘价')
        self.assertEqual(path[-1], market.close['shanhai'], '路径终点是收盘价')

    def test_scripts_trade_at_the_open_not_the_close(self):
        """脚本在开盘执行：成交价按开盘价，不是收盘价（收盘价此刻还没人知道）。"""
        host = self.host(rounds={1: BUY_SCRIPT}, actors=1)
        opening = host.world.market.opening['shanhai']
        host.cycle()
        market = host.world.market
        fills = market.fills()
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(fills[0]['price'] / opening, 1.0, places=2,
                               msg='成交价应当贴着开盘价（只差冲击成本）')
        self.assertNotAlmostEqual(fills[0]['price'], market.close['shanhai'], places=2)

    def test_accounts_are_valued_at_the_close(self):
        host = self.host(rounds={1: BUY_SCRIPT}, actors=1)
        host.cycle()
        market = host.world.market
        book = host.world.portfolio.holdings('stock:player0')
        position = book['positions'][0]
        self.assertEqual(position['price'], market.close['shanhai'],
                         '闭市后账户按收盘价估值')

    def test_pre_market_players_only_see_yesterdays_close(self):
        """盘前：今天还没开盘，玩家只看得到昨收。"""
        host = self.host(actors=3)
        seen, ticks = self._spy_prices(host)
        close = host.world.market.close['shanhai']
        host.cycle()
        self.assertEqual(len(seen), 3)
        for price in seen:
            self.assertEqual(price, close, '盘前不能看到今天的价格')
        self.assertEqual(ticks, [0, 0, 0], '盘前行情没有时间片可走')

    def test_order_flow_pushes_the_next_open(self):
        host = self.host(rounds={1: "api.call('stock:market','buy',symbol='shanhai',quantity=1000)\n"})
        host.cycle()
        market = host.world.market
        self.assertGreater(market.opening['shanhai'], market.close['shanhai'],
                           '净买入应当把下一回合的开盘价推高')

    def test_round_move_is_spread_over_the_round(self):
        host = self.host(actors=3)
        market = host.world.market
        opening = market.opening['shanhai']
        seen, ticks = self._spy_prices(host)
        host.cycle()
        close = market.close['shanhai']
        total = abs(close / opening - 1)
        first_slice = abs(seen[0] / opening - 1)
        self.assertGreater(total, 0)
        self.assertLess(first_slice, total,
                        '第一片应当只是整段走势的一部分，价格是逐步走出来的')

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

    def test_all_scripts_see_the_same_market_snapshot(self):
        """并发模拟：先跑的脚本不能影响后跑的脚本看到的行情。"""
        host = self.host(rounds={1: BUY_SCRIPT}, actors=2)
        seen = []

        original = host.session.player

        def spy(actor):
            player = original(actor)
            seen.append(actor)
            return player

        host.session.player = spy
        host.cycle()
        self.assertEqual(len(seen), 2)
        # 两个玩家拿到的成交价必须一致（同一时刻的同一份行情）
        fills = host.world.market.fills()
        prices = {fill['price'] for fill in fills}
        self.assertEqual(len(prices), 1, '并发执行时所有玩家应当按同一价格成交')

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

    def test_interest_is_charged_once(self):
        """利息是成本：只从现金里付一次，负债本金不膨胀。"""
        host = self.host()
        player = host.session.player('stock:player0')
        player.submit('bank', {'action': 'borrow', 'amount': 10000})
        host.cycle()
        book = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(book['debt'], 10000.0, '负债本金不该被利息顶起来')
        self.assertEqual(book['interest_paid'], 200.0)
        self.assertEqual(book['cash'], 109800.0)
        self.assertEqual(book['wealth'], 99800.0, '净值只该降一次利息')

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
        self.assertEqual(record['quantity'], 200, '两个玩家各持 100 股，强平一起砍')

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

    def test_writing_a_script_costs_one_action(self):
        host = self.host()
        player = host.session.player('stock:player0')
        player.submit('script', {'action': 'write', 'source': BUY_SCRIPT})
        self.assertEqual(host.world.clock.used_of('stock:player0')['market'], 1)
        self.assertEqual(host.world.clock.window_of('stock:player0'), 'market')

    def test_round_end_work_window_is_reported(self):
        host = self.host(BUY_SCRIPT)
        host.cycle()
        view = host.world.clock.view('stock:player0')
        self.assertEqual(view['window'], 'market')
        self.assertEqual(view['work_window_minutes'], 20)
        self.assertGreaterEqual(view['work_elapsed_minutes'], 0.0)

    def test_work_window_is_enforced(self):
        host = self.host()
        clock = host.world.clock
        self.assertFalse(clock.work_expired())
        clock.work_started_at = clock.started_at - 21 * 60
        self.assertTrue(clock.work_expired())

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
        book = host.world.portfolio.holdings('stock:player0')
        self.assertEqual(book['positions'], [], '回合内还没成交：脚本在回合末才执行')
        host.cycle()
        fills = host.world.market.fills()
        mine = [fill for fill in fills if fill['actor'] == 'stock:player0']
        self.assertEqual(len(mine), 1, '模型应当通过脚本挂出订单并成交')
        self.assertEqual(mine[0]['symbol'], 'shanhai')
        self.assertEqual(mine[0]['quantity'], 100)

    # ── 休眠与循环脚本 ────────────────────────────────────────────
    def test_script_can_sleep_by_ticks(self):
        """脚本按 tick 休眠：睡完看到的是后面的盘口价。"""
        source = ("""
before = api.call('stock:market', 'quotes')
api.call('stock:clock', 'sleep', ticks=10000)
after = api.call('stock:market', 'quotes')
print(str(before[0]['price']) + ' ' + str(after[0]['price']))
""")
        host = self.host(rounds={1: source}, actors=1)
        opening = host.world.market.opening['shanhai']
        host.cycle()
        result = host.world.scripts.status('stock:player0')['result']
        self.assertEqual(result['status'], 'effective', result.get('error'))
        before, after = (float(value) for value in result['output'].split())
        self.assertAlmostEqual(before, opening, places=2, msg='开睡前是开盘价')
        self.assertNotAlmostEqual(after, before, places=2, msg='睡过之后盘口价应当变了')

    def test_sleep_costs_compute_by_the_conversion(self):
        """换算式：每 tick 值 沙盒步数预算 / 20000 = 40 步。"""
        source = ("""
api.call('stock:clock', 'sleep', ticks=1000)
""")
        host = self.host(rounds={1: source}, actors=1)
        host.cycle()
        result = host.world.scripts.status('stock:player0')['result']
        self.assertEqual(result['status'], 'effective', result.get('error'))
        self.assertGreaterEqual(result['steps'], 1000 * 40)

    def test_loop_script_runs_to_the_close(self):
        """循环脚本：一路睡到收盘，中途还能下单。"""
        source = ("""
cal = api.call('stock:clock', 'snapshot')
quotes = api.call('stock:market', 'quotes')
bought = 0
while cal['tick'] < cal['slices']:
    row = quotes[0]
    if row['ma5'] and row['price'] < row['ma5'] and bought == 0:
        api.call('stock:market', 'buy', symbol=row['symbol'], quantity=200)
        bought = 1
    api.call('stock:clock', 'sleep', ticks=2500)
    cal = api.call('stock:clock', 'snapshot')
    quotes = api.call('stock:market', 'quotes')
print('tick=' + str(cal['tick']) + ' ' + cal['market_time'])
""")
        host = self.host(rounds={1: source}, actors=1)
        host.cycle()
        result = host.world.scripts.status('stock:player0')['result']
        self.assertEqual(result['status'], 'effective', result.get('error'))
        self.assertIn('tick=20000', result['output'])
        self.assertIn('17:30', result['output'], '20000 个 tick 走完就是 8 小时后的收盘')
        self.assertTrue(host.world.market.fills(), '循环途中应当下过单')

    # ── 记账恒等式与资金边界 ──────────────────────────────────────
    def test_equity_identity_holds_after_every_round(self):
        """净值 = 现金 + 持仓市值 − 负债，每回合都成立。"""
        buy = "api.call('stock:market','buy',symbol='shanhai',quantity=200)\n"
        host = self.host(rounds={1: buy}, actors=1)
        player = host.session.player('stock:player0')
        player.submit('bank', {'action': 'borrow', 'amount': 5000})
        for _ in range(4):
            host.cycle()
            book = host.world.portfolio.holdings('stock:player0')
            self.assertAlmostEqual(book['wealth'],
                                   book['cash'] + book['holdings_value'] - book['debt'],
                                   places=2, msg='净值恒等式被破坏了')

    def test_selling_realizes_pnl_consistently(self):
        buy = "api.call('stock:market','buy',symbol='shanhai',quantity=100)\n"
        sell = "api.call('stock:market','sell',symbol='shanhai',quantity=40)\n"
        host = self.host(rounds={1: buy, 2: sell}, actors=1)
        host.cycle()
        host.cycle()
        book = host.world.portfolio.holdings('stock:player0')
        position = book['positions'][0]
        self.assertEqual(position['quantity'], 60)
        self.assertLess(position['avg_cost'], 60,
                        '卖一半后均价应当基本不变（原来会翻倍）')
        fill = [f for f in host.world.market.fills() if f['side'] == 'sell'][0]
        expected = fill['amount'] - fill['fee'] - position['avg_cost'] * 40
        self.assertAlmostEqual(book['realized_pnl'], expected, places=1)

    def test_forced_liquidation_does_not_create_money(self):
        buy = "api.call('stock:market','buy',symbol='shanhai',quantity=100)\n"
        host = self.host(rounds={1: buy}, actors=1)
        player = host.session.player('stock:player0')
        player.submit('bank', {'action': 'borrow', 'amount': 20000})
        host.cycle()
        before = host.world.portfolio.holdings('stock:player0')
        host.cycle()
        after = host.world.portfolio.holdings('stock:player0')
        self.assertLessEqual(after['wealth'], before['wealth'] + 0.01,
                             '强平不该凭空生出钱来')
        self.assertTrue(after['liquidations'])

    def test_cancelled_order_does_not_fill(self):
        source = ("""
order = api.call('stock:market', 'buy', symbol='shanhai', quantity=100)
api.call('stock:market', 'cancel', order_id=order['data']['id'])
""")
        host = self.host(rounds={1: source}, actors=1)
        host.cycle()
        self.assertEqual(host.world.market.fills(), [], '撤掉的单不该成交')
        self.assertEqual(host.world.portfolio.holdings('stock:player0')['positions'], [])

    def test_future_path_is_not_leaked(self):
        source = """
quotes = api.call('stock:market', 'quotes')
print(str(quotes[0]['path'][-1]))
"""
        host = self.host(rounds={1: source}, actors=1)
        host.cycle()
        result = host.world.scripts.status('stock:player0')['result']
        self.assertEqual(result['status'], 'effective', result.get('error'))
        leaked = float(result['output'].strip())
        close = host.world.market.close['shanhai']
        self.assertNotAlmostEqual(leaked, close, places=2,
                                   msg='公开路径不能提前泄露当天收盘价')

    def test_save_and_load_continue_identically(self):
        host = self.host(seed=99, actors=1)
        for _ in range(3):
            host.cycle()
        path = ROOT / '.tmp-stock-continuity.json'
        try:
            host.save(str(path))
            restored = GameHost.load(str(path), {
                'mods': 'mods.stock.toml', 'seed': 99,
                'nations': [{'id': 'stock:player0', 'name': '量化0', 'client': 'replay'}]})
            for _ in range(3):
                host.cycle()
                restored.cycle()
            self.assertAlmostEqual(host.world.market.close['shanhai'],
                                   restored.world.market.close['shanhai'], places=2,
                                   msg='读档后行情应当与连续运行一致')
        finally:
            path.unlink(missing_ok=True)
            shutil.rmtree(path.with_suffix(path.suffix + '.audit'), ignore_errors=True)

    # ── 沙盒预算 ──────────────────────────────────────────────────
    def test_sandbox_budget_is_raised(self):
        host = self.host()
        limits = host.world.scripts.limits
        self.assertEqual(limits['steps'], 800000)
        self.assertEqual(limits['calls'], 2000)

    def test_script_can_spend_the_raised_budget(self):
        host = self.host()
        player = host.session.player('stock:player0')
        receipt = player.submit('script', {'action': 'write', 'source': BUY_SCRIPT})
        self.assertTrue(receipt['ok'], receipt.get('message'))
        host.cycle()
        result = host.world.scripts.status('stock:player0')['result']
        self.assertEqual(result['status'], 'effective')
        self.assertLessEqual(result['calls'], 2000)

    # ── 规则与结算表 ──────────────────────────────────────────────
    def test_rules_mention_the_calendar_and_the_sandbox(self):
        host = self.host()
        manual = host.runtime.manual()
        self.assertIn('交易日', manual)
        self.assertIn('强制平仓', manual)
        self.assertIn('Python 量化沙盒', manual)


if __name__ == '__main__':
    unittest.main()
