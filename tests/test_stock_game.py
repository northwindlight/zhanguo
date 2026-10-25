"""Stock v2 acceptance: real matching, causal callbacks, one ledger and paid compute."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from host import GameHost
from mods.stock.client import StockClient, StockView
from mods.stock.market import MAKER, CLEARING

ROOT = Path(__file__).resolve().parents[1]
ACTOR = 'stock:player0'
BUY = "api.call('stock:market','buy',symbol='shanhai',quantity=10)\n"


def config(source=None, actors=1, seed=11):
    return dict(mods='mods.stock.toml', seed=seed, nations=[
        dict(id=f'stock:player{i}', name=f'量化{i}', client='replay',
             scripts={'1': source} if source else {}) for i in range(actors)])


class StockGameTest(unittest.TestCase):
    def host(self, source=None, actors=1, seed=11, ticks=200):
        h = GameHost.new(config(source, actors, seed))
        h.world.clock.session_ticks = ticks
        return h

    def submit(self, h, tool, **arguments):
        return h.player(ACTOR).submit(tool, arguments)

    def result(self, h, slot='1'):
        return h.world.scripts.status(ACTOR, slot=slot)['result']

    def open(self, h):
        h.runtime.object_call(h.world, 'stock:market', 'open_session')
        return h.world.market

    def buy_resource(self, h, kind):
        r = self.submit(h, 'compute', action='buy', kind=kind)
        self.assertTrue(r['ok'], r)
        return r['data']

    def write(self, h, source, slot='1'):
        r = self.submit(h, 'script', action='write', source=source, slot=slot)
        self.assertTrue(r['ok'], r)

    def research(self, h, source, inputs=None):
        if not h.world.compute.status(ACTOR)['research']:
            self.buy_resource(h, 'research')
        self.write(h, source, 'research')
        return self.submit(h, 'script', action='run', slot='research', inputs=inputs or {})

    def test_distribution_is_v2_and_has_real_backend(self):
        h = self.host()
        self.assertEqual(h.runtime.distribution['Version'], '2.1.0')
        m = self.open(h)
        from pams import Market
        self.assertIsInstance(m.backend.books['shanhai'], Market)

    def test_default_has_20000_ticks(self):
        h = GameHost.new(config())
        self.assertEqual(h.world.clock.slices(), 20000)

    def test_wrong_backend_version_fails_before_starting_clients(self):
        with patch('importlib.metadata.version', return_value='0.0'):
            with self.assertRaisesRegex(ValueError, 'pams==0.3.0'):
                GameHost.new(config())

    def test_model_tools_hide_direct_trades(self):
        h = self.host()
        names = {t['function']['name'] for t in h.player(ACTOR).actions()}
        self.assertTrue({'script', 'compute', 'compute_status', 'end_turn'} <= names)
        self.assertFalse({'buy', 'sell', 'short', 'cover', 'stock_trade'} & names)
        self.assertFalse(self.submit(h, 'stock_trade', symbol='shanhai', quantity=10)['ok'])

    def test_direct_pre_market_api_trade_rejected(self):
        h = self.host()
        self.assertFalse(h.player(ACTOR).call_api('stock:market', 'buy', symbol='shanhai', quantity=10)['ok'])

    def test_script_is_deferred_and_order_applies_next_tick(self):
        h = self.host(BUY)
        self.assertIsNone(self.result(h))
        self.assertFalse(h.world.market.pending_orders())
        h.cycle()
        fills = h.world.market.fills(ACTOR)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]['tick'], 1)
        self.assertEqual(h.world.portfolio.position_of(ACTOR)['shares']['shanhai'], 10)

    def test_shared_tick_has_equal_public_snapshots(self):
        source = "def on_tick():\n    print(api.call('stock:clock','snapshot')['tick'], api.call('stock:market','quotes')[0]['price'])\n"
        h = self.host(source, actors=2)
        h.cycle()
        self.assertEqual(self.result(h)['output'], h.world.scripts.status('stock:player1')['result']['output'])

    def test_sleep_schedules_callback_without_advancing_current_tick(self):
        source = "def on_tick():\n    a=api.call('stock:clock','snapshot')['tick']\n    api.call('stock:clock','sleep',ticks=50)\n    b=api.call('stock:clock','snapshot')['tick']\n    print(a,b)\n"
        h = self.host(source)
        h.cycle()
        self.assertEqual(self.result(h)['output'], '0 0\n50 50\n100 100\n150 150\n')
        self.assertLess(self.result(h)['steps'], 10000)

    def test_callback_persistent_private_state(self):
        source = "def on_tick():\n    state['count']=state.get('count',0)+1\n"
        h = self.host(source, actors=2)
        h.cycle(); h.cycle()
        self.assertEqual(h.world.scripts.rows[ACTOR]['1']['state']['count'], 4)
        self.assertEqual(h.world.scripts.rows['stock:player1']['1']['state']['count'], 4)

    def test_trade_feedback_is_visible_in_next_callback(self):
        source = "def on_tick():\n    c=api.call('stock:clock','snapshot')\n    if c['tick']==0:\n        api.call('stock:market','buy',symbol='shanhai',quantity=10)\n    else:\n        print(api.call('stock:portfolio','holdings')['positions'][0]['quantity'])\n"
        h = self.host(source); h.cycle()
        self.assertEqual(self.result(h)['output'], '10\n')

    def test_orders_change_book_and_partial_fills(self):
        h = self.host(); m = self.open(h)
        before = m.backend.depth('shanhai')['asks'][0][0]
        order = m.place(h.world, ACTOR, 'buy', 'shanhai', 150)
        m.advance(1)
        fills = m.fills(ACTOR)
        self.assertEqual(sum(f['quantity'] for f in fills), 150)
        self.assertEqual(len(fills), 2)
        self.assertGreater(m.backend.depth('shanhai')['asks'][0][0], before)
        # PAMS explicitly clears this batch at a uniform price.
        self.assertEqual(len({f['price'] for f in fills}), 1)
        self.assertEqual(m.orders[order['id']]['status'], 'filled')

    def test_reservation_prevents_cash_double_spend(self):
        h = self.host(); m = self.open(h)
        m.place(h.world, ACTOR, 'buy', 'shanhai', 1000)
        m.place(h.world, ACTOR, 'buy', 'shanhai', 1000)
        with self.assertRaisesRegex(ValueError, '现金'):
            m.place(h.world, ACTOR, 'buy', 'shanhai', 1000)
        self.assertLess(h.world.portfolio.holdings(ACTOR)['available_cash'], 20000)

    def test_cancel_before_delivery_does_not_trade(self):
        h = self.host(); m = self.open(h)
        o = m.place(h.world, ACTOR, 'buy', 'shanhai', 10)
        m.cancel(h.world, ACTOR, o['id']); m.advance(1)
        self.assertFalse(m.fills(ACTOR))
        self.assertEqual(m.orders[o['id']]['status'], 'cancelled')
        self.assertEqual(h.world.portfolio.holdings(ACTOR)['reserved_cash'], 0)

    def test_limit_day_order_expires_and_releases(self):
        h = self.host("api.call('stock:market','buy',symbol='shanhai',quantity=10,price=1)\n")
        h.cycle()
        self.assertFalse(h.world.market.fills(ACTOR))
        self.assertEqual(h.world.market.order_history(ACTOR)[0]['status'], 'expired')
        self.assertEqual(h.world.portfolio.holdings(ACTOR)['reserved_cash'], 0)

    def test_cannot_cancel_other_account_order(self):
        h = self.host(actors=2); m = self.open(h)
        o = m.place(h.world, ACTOR, 'buy', 'shanhai', 10, 1)
        with self.assertRaises(ValueError): m.cancel(h.world, 'stock:player1', o['id'])

    def test_sell_requires_available_inventory(self):
        h = self.host(); m = self.open(h)
        with self.assertRaisesRegex(ValueError, '持仓'):
            m.place(h.world, ACTOR, 'sell', 'shanhai', 1)

    def test_cash_and_shares_conserved_across_matching(self):
        h = self.host(BUY, actors=2)
        before = deepcopy(h.world.portfolio.accounts)
        h.cycle()
        portfolio = h.world.portfolio
        self.assertAlmostEqual(sum(a['cash'] for a in before.values()),
                               sum(a['cash'] for a in portfolio.accounts.values())+portfolio.fees_collected,
                               delta=.01)
        for symbol in h.world.market.stocks:
            self.assertEqual(sum(a['positions'].get(symbol, {}).get('quantity', 0) for a in before.values()),
                             sum(a['positions'].get(symbol, {}).get('quantity', 0) for a in portfolio.accounts.values()))

    def test_fills_cannot_be_booked_twice(self):
        h = self.host(BUY); h.cycle()
        before = deepcopy(h.world.portfolio.accounts)
        for fill in h.world.market.fills():
            h.world.portfolio.execute(fill, fill['remaining'])
        self.assertEqual(before, h.world.portfolio.accounts)

    def test_partial_sale_preserves_average_cost(self):
        h = self.host(); m = self.open(h)
        m.place(h.world, ACTOR, 'buy', 'shanhai', 100); m.advance(1)
        before = deepcopy(h.world.portfolio.accounts[ACTOR]['positions']['shanhai'])
        m.place(h.world, ACTOR, 'sell', 'shanhai', 50); m.advance(2)
        after = h.world.portfolio.accounts[ACTOR]['positions']['shanhai']
        self.assertEqual(after['quantity'], 50)
        self.assertLessEqual(abs(after['cost']-before['cost']/2), .01)  # cent allocation

    def test_short_borrow_cover_and_collateral(self):
        h = self.host(); m = self.open(h)
        m.place(h.world, ACTOR, 'short', 'shanhai', 100); m.advance(1)
        book = h.world.portfolio.holdings(ACTOR)
        self.assertEqual(book['positions'][0]['quantity'], -100)
        self.assertGreater(book['short_collateral'], 0)
        self.assertLess(book['available_cash'], 100000)
        m.place(h.world, ACTOR, 'cover', 'shanhai', 100); m.advance(2)
        self.assertFalse(h.world.portfolio.holdings(ACTOR)['positions'])
        self.assertEqual(h.world.portfolio.short_used('shanhai'), 0)

    def test_borrow_supply_is_finite_and_reserved(self):
        h = self.host(); m = self.open(h)
        with self.assertRaisesRegex(ValueError, '借券'):
            m.place(h.world, ACTOR, 'short', 'shanhai', 5001)

    def test_short_borrow_fee_charged_once(self):
        h = self.host("api.call('stock:market','short',symbol='shanhai',quantity=10)\n")
        h.cycle()
        book = h.world.portfolio.holdings(ACTOR)
        self.assertEqual(book['borrow_fees'], round(book['short_value']*.0002, 2))
        self.assertEqual(book['debt'], 0)

    def test_long_short_direction_must_be_explicit(self):
        h = self.host(); m = self.open(h)
        m.place(h.world, ACTOR, 'buy', 'shanhai', 10); m.advance(1)
        with self.assertRaisesRegex(ValueError, '相反'):
            m.place(h.world, ACTOR, 'short', 'shanhai', 10)

    def test_loan_does_not_create_wealth_and_interest_once(self):
        h = self.host()
        r = self.submit(h, 'bank', action='borrow', amount=10000)
        self.assertTrue(r['ok'])
        self.assertEqual(h.world.portfolio.holdings(ACTOR)['wealth'], 100000)
        h.cycle()
        book = h.world.portfolio.holdings(ACTOR)
        self.assertEqual(book['debt'], 10000)
        self.assertEqual(book['cash'], 109800)
        self.assertEqual(book['wealth'], 99800)
        self.assertEqual(book['interest_paid'], 200)

    def test_excess_loan_rejected_without_money_change(self):
        h = self.host()
        self.assertFalse(self.submit(h, 'bank', action='borrow', amount=1000000)['ok'])
        self.assertEqual(h.world.portfolio.accounts[ACTOR]['cash'], 100000)

    def test_liquidation_transfers_cash_and_does_not_create_wealth(self):
        h = self.host(); self.open(h)
        p = h.world.portfolio; a = p.accounts[ACTOR]
        a.update(cash=96000., debt=90000.)
        a['positions']['shanhai'] = dict(quantity=100, cost=4200.)
        before = p.holdings(ACTOR)['wealth']
        total = sum(x['cash'] for x in p.accounts.values())+p.fees_collected
        records = p.force_liquidate(h.world, 1, ACTOR, a)
        self.assertTrue(records)
        self.assertLessEqual(p.holdings(ACTOR)['wealth'], before)
        # Repayment destroys loan-created cash and reduces debt equally.
        self.assertAlmostEqual(total, sum(x['cash'] for x in p.accounts.values())+p.fees_collected+records[0]['repaid'])

    def test_bankruptcy_retires_actor_without_losing_history(self):
        h = self.host(actors=2)
        h.world.portfolio.accounts[ACTOR]['cash'] = -1
        h.cycle()
        self.assertNotIn(ACTOR, h.actors())
        self.assertFalse(h.world.portfolio.holdings(ACTOR)['alive'])

    def test_calendar_five_open_two_closed(self):
        h = self.host()
        for day in range(1, 9):
            self.assertEqual(h.world.clock.trading_day(), (day-1)%7 < 5)
            h.cycle()

    def test_holiday_cannot_trade(self):
        h = self.host()
        h.world.clock.round = 6
        h.clients[ACTOR].scripts['6'] = BUY
        h.cycle()
        self.assertIn('休市', self.result(h)['error']['message'])

    def test_visible_path_contains_no_future_and_news_are_causal(self):
        h = self.host(); m = self.open(h)
        self.assertEqual(len(m.quotes(ACTOR)[0]['path']), 1)
        self.assertFalse(m.quotes(ACTOR)[0]['news'])
        for tick in range(1, 68): m.advance(tick)
        q = m.quotes(ACTOR)[0]
        self.assertTrue(q['news'])
        self.assertTrue(all(n['tick'] <= 67 for n in q['news']))
        self.assertLess(len(q['path']), 21)
        self.assertNotIn('seed', q)
        self.assertNotIn('fundamental', q)

    def test_seed_and_full_results_survive_save_resume(self):
        source = "def on_tick():\n    state['count']=state.get('count',0)+1\n"
        h = self.host(source, seed=99); h.cycle()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'s.json'; h.save(path)
            loaded = GameHost.load(path, config(source, seed=99))
            loaded.world.clock.session_ticks = 200
            h.cycle(); loaded.cycle()
            self.assertEqual(h.runtime.save_mod_state(h.world), loaded.runtime.save_mod_state(loaded.world))

    def test_mid_session_save_forbidden(self):
        h = self.host(); self.open(h)
        with self.assertRaises(ValueError): h.to_save()

    def test_trade_requests_and_fills_are_audited(self):
        h = self.host(BUY); h.cycle()
        actions = [row['action'] for row in h.audit]
        self.assertIn('buy', actions); self.assertIn('stock_fill', actions)

    def test_default_compute_pool(self):
        h = self.host(); s = h.world.compute.status(ACTOR)
        self.assertEqual((s['steps_limit'], s['calls_limit'], s['slots']), (4000000, 4000, 1))
        self.assertFalse(s['research'])

    def test_buy_pool_debits_game_cash_and_increases_shared_quota(self):
        h = self.host(); s = self.buy_resource(h, 'pool')
        self.assertEqual(s['paid'], 100)
        self.assertEqual(s['steps_limit'], 8000000)
        self.assertEqual(h.world.portfolio.holdings(ACTOR)['wealth'], 99900)

    def test_buy_pool_cannot_reset_usage(self):
        h = self.host(); h.world.compute.consume(ACTOR, 1000, 2)
        s = self.buy_resource(h, 'pool')
        self.assertEqual((s['steps_used'], s['calls_used']), (1000, 2))

    def test_compute_capacity_maxima(self):
        h = self.host()
        for _ in range(3): self.buy_resource(h, 'pool'); self.buy_resource(h, 'slot')
        self.assertFalse(self.submit(h, 'compute', action='buy', kind='pool')['ok'])
        self.assertFalse(self.submit(h, 'compute', action='buy', kind='slot')['ok'])

    def test_locked_slots_cannot_be_written_or_run(self):
        h = self.host()
        self.assertFalse(self.submit(h, 'script', action='write', slot='2', source='print(1)')['ok'])
        self.assertFalse(self.submit(h, 'script', action='run', slot='research')['ok'])
        self.assertFalse(self.submit(h, 'script', action='run', slot='1')['ok'])

    def test_multiple_slots_share_pool_and_keep_private_state(self):
        h = self.host(); self.buy_resource(h, 'slot')
        self.write(h, "state['a']=1\nprint('one')", '1')
        self.write(h, "state['b']=2\nprint('two')", '2')
        h.cycle()
        self.assertEqual(h.world.scripts.rows[ACTOR]['1']['state'], {'a': 1})
        self.assertEqual(h.world.scripts.rows[ACTOR]['2']['state'], {'b': 2})
        self.assertEqual(h.world.compute.status(ACTOR)['steps_used'], 0)  # next day's reset
        total = sum(h.world.scripts.rows[ACTOR][s]['result']['steps'] for s in ('1','2'))
        self.assertGreater(total, 0)

    def test_research_runs_now_and_does_not_advance_market(self):
        h = self.host()
        r = self.research(h, "print(inputs['x'] * 7)\nprint(api.call('stock:clock','snapshot')['tick'])", {'x': 3})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['data']['output'], '21\n0\n')
        self.assertEqual(h.world.clock.tick, 0)
        self.assertEqual(h.world.market.state, h.world.market.PRE)
        self.assertGreater(h.world.compute.status(ACTOR)['steps_used'], 0)

    def test_research_can_repeat_but_uses_same_pool(self):
        h = self.host(); self.research(h, "state['runs']=state.get('runs',0)+1\nprint(state['runs'])")
        before = h.world.compute.status(ACTOR)['steps_used']
        r = self.submit(h, 'script', action='run', slot='research')
        self.assertTrue(r['ok']); self.assertEqual(r['data']['output'], '2\n')
        self.assertGreater(h.world.compute.status(ACTOR)['steps_used'], before)

    def test_research_write_apis_are_forbidden(self):
        for cap, method, args in [('stock:market','buy',"symbol='shanhai',quantity=1"),
                                  ('stock:portfolio','borrow','amount=1'),
                                  ('stock:compute','buy',"kind='pool'"),
                                  ('stock:clock','sleep','ticks=1')]:
            with self.subTest(method=method):
                h = self.host()
                r = self.research(h, f"api.call('{cap}','{method}',{args})")
                self.assertFalse(r['ok'], r)
                self.assertEqual(h.world.clock.tick, 0)
                self.assertFalse(h.world.market.pending_orders())
                self.assertEqual(h.world.portfolio.accounts[ACTOR]['debt'], 0)

    def test_budget_exhaustion_survives_rewrite_and_slot_changes(self):
        h = self.host(); self.buy_resource(h, 'research'); self.buy_resource(h, 'slot')
        h.world.compute.consume(ACTOR, 4000000, 0)
        self.write(h, 'print(1)', 'research')
        self.assertFalse(self.submit(h, 'script', action='run', slot='research')['ok'])
        self.write(h, 'print(2)', 'research')
        self.assertFalse(self.submit(h, 'script', action='run', slot='research')['ok'])
        self.assertEqual(h.world.compute.status(ACTOR)['steps_remaining'], 0)

    def test_failed_script_consumes_computation(self):
        h = self.host(); r = self.research(h, "print('before')\nprint(missing)")
        self.assertFalse(r['ok']); self.assertIn('before', r['data']['output'])
        self.assertEqual(r['data']['error']['type'], 'NameError')
        self.assertGreater(h.world.compute.status(ACTOR)['steps_used'], 0)

    def test_checks_are_free_and_do_not_run_code(self):
        h = self.host()
        r = self.submit(h, 'script', action='check', source="print(1/0)")
        self.assertTrue(r['data']['valid'])
        self.assertEqual(h.world.compute.status(ACTOR)['steps_used'], 0)

    def test_burst_limit_stops_non_yielding_strategy(self):
        h = self.host("def on_tick():\n    while True:\n        pass")
        h.cycle(); r = self.result(h)
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['steps'], 50000)
        self.assertIn('budget', r['error']['message'])

    def test_insufficient_cash_purchase_is_atomic(self):
        h = self.host(); h.world.portfolio.accounts[ACTOR]['cash'] = 1
        before = deepcopy(h.world.compute.accounts[ACTOR])
        self.assertFalse(self.submit(h, 'compute', action='buy', kind='research')['ok'])
        self.assertEqual(before, h.world.compute.accounts[ACTOR])
        self.assertEqual(h.world.portfolio.accounts[ACTOR]['cash'], 1)

    def test_failed_renewal_downgrades_but_keeps_unlock_and_sources(self):
        h = self.host(); self.buy_resource(h, 'research'); self.buy_resource(h, 'slot'); self.buy_resource(h, 'pool')
        self.write(h, 'print(1)', 'research')
        h.world.portfolio.accounts[ACTOR]['cash'] = 1
        h.cycle()
        s = h.world.compute.status(ACTOR)
        self.assertEqual((s['pool'], s['slots'], s['research']), (1, 1, False))
        self.assertTrue(s['research_unlocked'])
        self.assertEqual(h.world.scripts.rows[ACTOR]['research']['source'], 'print(1)')

    def test_resource_usage_and_unlock_survive_save(self):
        h = self.host(); self.buy_resource(h, 'research'); h.cycle()
        h.world.compute.consume(ACTOR, 1234, 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'s.json'; h.save(path)
            loaded = GameHost.load(path, config())
            self.assertEqual(h.world.compute.status(ACTOR), loaded.world.compute.status(ACTOR))

    def test_briefing_reports_compute_and_research_output(self):
        h = self.host(BUY); self.research(h, "print('研究结果')")
        text = StockView({'language':'zh-CN'}).stock_briefing(h.player(ACTOR).observe())
        self.assertIn('算力池', text)
        self.assertIn('研究槽', text)
        h.cycle()
        text = StockView({'language':'zh-CN'}).stock_briefing(h.player(ACTOR).observe())
        self.assertIn('总财富', text)

    def test_invalid_trade_and_resource_amounts_rejected(self):
        h = self.host(); m = self.open(h)
        for quantity in (0, -1, True, 1.5):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                m.place(h.world, ACTOR, 'buy', 'shanhai', quantity)
        for amount in (0, -1, True, float('nan'), float('inf')):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                h.world.portfolio.borrow(ACTOR, amount)

    def test_intelligence_charges_and_returns_requested_snapshot(self):
        h = self.host(actors=2)
        other = 'stock:player1'
        h.world.portfolio.borrow(other, 10000)
        account = h.world.portfolio.accounts[other]
        account['positions']['shanhai'] = dict(quantity=30, cost=1260)
        account['cash'] -= 1260
        r = self.submit(h, 'intel', action='steal', target=other)
        self.assertTrue(r['ok'], r)
        report = r['data']; snapshot = report['snapshot']
        self.assertEqual((report['round'], report['tick'], report['phase']), (1, 0, 'pre'))
        self.assertEqual(snapshot['cash'], 108740)
        self.assertEqual(snapshot['debt'], 10000)
        self.assertEqual(snapshot['positions'][0]['quantity'], 30)
        self.assertNotIn('avg_cost', snapshot['positions'][0])
        self.assertNotIn('scripts', snapshot)
        book = h.world.portfolio.holdings(ACTOR)
        self.assertEqual(book['wealth'], 99000)
        self.assertEqual(book['intelligence_expenses'], 1000)
        self.assertEqual(book['compute_expenses'], 0)

    def test_intelligence_is_a_frozen_private_report(self):
        h = self.host(actors=2)
        other = 'stock:player1'
        report = self.submit(h, 'intel', action='steal', target=other)['data']
        h.world.portfolio.borrow(other, 5000)
        mine = h.player(ACTOR).observe()['facts']['intel_status']['reports']
        self.assertEqual(mine[0], report)
        self.assertEqual(mine[0]['snapshot']['debt'], 0)
        self.assertFalse(h.player(other).observe()['facts']['intel_status']['reports'])

    def test_intelligence_cannot_target_self_background_or_unknown(self):
        h = self.host()
        for target in (ACTOR, MAKER, CLEARING, 'stock:unknown'):
            with self.subTest(target=target):
                self.assertFalse(self.submit(h, 'intel', action='steal', target=target)['ok'])
                self.assertEqual(h.world.portfolio.accounts[ACTOR]['cash'], 100000)

    def test_poor_spy_gets_no_report_or_debit(self):
        h = self.host(actors=2)
        h.world.portfolio.accounts[ACTOR]['cash'] = 1
        self.assertFalse(self.submit(h, 'intel', action='steal', target='stock:player1')['ok'])
        self.assertFalse(h.world.intelligence.status(ACTOR)['reports'])
        self.assertEqual(h.world.portfolio.accounts[ACTOR]['cash'], 1)

    def test_script_cannot_launch_spy_but_can_read_purchased_report(self):
        h = self.host(actors=2)
        self.submit(h, 'intel', action='steal', target='stock:player1')
        r = self.research(h, "print(api.call('stock:intelligence','status')['reports'][0]['snapshot']['cash'])")
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['data']['output'], '100000.0\n')
        self.write(h, "api.call('stock:intelligence','steal',target='stock:player1')", 'research')
        self.assertFalse(self.submit(h, 'script', action='run', slot='research')['ok'])
        self.assertEqual(len(h.world.intelligence.status(ACTOR)['reports']), 1)

    def test_private_account_cannot_be_read_by_forged_api_arguments(self):
        h = self.host(actors=2)
        r = self.research(h, "api.call('stock:portfolio','holdings',actor='stock:player1')")
        self.assertFalse(r['ok'])
        r = self.research(h, "api.call('stock:portfolio','intelligence_snapshot',target='stock:player1')")
        self.assertFalse(r['ok'])

    def test_intelligence_survives_save_and_resume(self):
        h = self.host(actors=2)
        self.submit(h, 'intel', action='steal', target='stock:player1')
        h.cycle()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'s.json'; h.save(path)
            loaded = GameHost.load(path, config(actors=2))
            self.assertEqual(h.world.intelligence.status(ACTOR), loaded.world.intelligence.status(ACTOR))

    def test_release_and_reenable_research_does_not_charge_unlock_twice(self):
        h = self.host()
        self.assertEqual(self.buy_resource(h, 'research')['paid'], 550)
        self.submit(h, 'compute', action='release', kind='research')
        self.assertEqual(self.buy_resource(h, 'research')['paid'], 0)
        self.assertEqual(h.world.compute.status(ACTOR)['fees_paid'], 550)

    def test_failed_state_is_not_committed_but_valid_previous_state_remains(self):
        h = self.host()
        self.research(h, "state['good']=1")
        r = self.research(h, "state['good']=2\nprint(missing)")
        self.assertFalse(r['ok'])
        self.assertEqual(h.world.scripts.rows[ACTOR]['research']['state'], {'good': 1})

    def test_rejected_trade_does_not_erase_earlier_successful_action(self):
        h = self.host(BUY + "api.call('stock:market','sell',symbol='shanhai',quantity=99999)\n")
        h.cycle()
        self.assertEqual(self.result(h)['status'], 'failed')
        self.assertEqual(h.world.portfolio.position_of(ACTOR)['shares']['shanhai'], 10)

    def test_declared_rules_and_model_briefing_are_current(self):
        h = self.host()
        text = json.dumps(h.player(ACTOR).rules('all'), ensure_ascii=False)
        self.assertIn('PAMS', text); self.assertIn('商业谍报', text)
        self.assertIn('research', text); self.assertIn('4000000', text)

    def test_llm_client_receives_new_tools_and_budget_facts(self):
        class Backend:
            def chat_turn(self, messages, tools, cfg, on_retry=None):
                self.messages, self.tools = messages, tools
                return {'content':'完成规划', 'tool_calls':[dict(id='1', type='function',
                    function=dict(name='end_turn', arguments=json.dumps({'summary':'等待交易'})))]}, {'hit':0,'miss':0}
            def complete_text(self, *args, **kwargs): return '等待交易。', {'hit':0,'miss':0}
        h = self.host(); backend = Backend()
        StockClient({'language':'zh-CN'}, backend=backend).play(h.player(ACTOR))
        names = {t['function']['name'] for t in backend.tools}
        self.assertTrue({'compute', 'intel', 'script'} <= names)
        self.assertIn('算力池', json.dumps(backend.messages, ensure_ascii=False))

    def test_borrow_supply_public_snapshot_equal_with_same_tick_reservations(self):
        source = "def on_tick():\n    c=api.call('stock:clock','snapshot')\n    print(api.call('stock:market','quotes')[0]['borrow_available'])\n    if c['tick']==0:\n        api.call('stock:market','short',symbol='shanhai',quantity=10)\n"
        h = self.host(source, actors=2); h.cycle()
        a = self.result(h)['output']
        b = h.world.scripts.status('stock:player1')['result']['output']
        self.assertEqual(a, b)
        self.assertTrue(a.startswith('5000\n'), a)

    def test_unfilled_extreme_quotes_cannot_pump_wealth(self):
        h = self.host(); m = self.open(h)
        m.place(h.world, ACTOR, 'buy', 'shanhai', 10); m.advance(1)
        before = h.world.portfolio.holdings(ACTOR)['wealth']
        m.cancel_actor(MAKER)
        m.place(h.world, ACTOR, 'sell', 'shanhai', 1, 1000000)
        m.place(h.world, ACTOR, 'buy', 'shanhai', 1, 1)
        m.advance(2)
        self.assertEqual(h.world.portfolio.holdings(ACTOR)['wealth'], before)

    def test_realized_pnl_includes_entry_and_exit_fees(self):
        h = self.host(); m = self.open(h)
        m.place(h.world, ACTOR, 'buy', 'shanhai', 10); m.advance(1)
        m.place(h.world, ACTOR, 'sell', 'shanhai', 10); m.advance(2)
        book = h.world.portfolio.holdings(ACTOR)
        self.assertAlmostEqual(book['realized_pnl'], book['cash']-100000, places=2)

    def test_illiquid_clearing_freezes_and_normal_cover_can_repair(self):
        h = self.host(); m = self.open(h)
        m.place(h.world, ACTOR, 'short', 'shanhai', 100); m.advance(1)
        p = h.world.portfolio
        p.accounts[ACTOR]['cash'] = 100*m.price_of('shanhai')+1000
        p.accounts[CLEARING]['positions']['shanhai']['quantity'] = 0
        p.risk_check(1)
        self.assertTrue(p.holdings(ACTOR)['risk_frozen'])
        with self.assertRaisesRegex(ValueError, '冻结'):
            m.place(h.world, ACTOR, 'buy', 'qingzhou', 1)
        m.place(h.world, ACTOR, 'cover', 'shanhai', 100); m.advance(2)
        self.assertFalse(p.holdings(ACTOR)['risk_frozen'])
        self.assertFalse(p.holdings(ACTOR)['positions'])

    def test_out_of_range_deferred_order_rejected_before_acceptance(self):
        h = self.host(); m = self.open(h)
        for value in (1e308, .001, True):
            with self.subTest(price=value), self.assertRaises(ValueError):
                m.place(h.world, ACTOR, 'buy', 'shanhai', 1, value)

    def test_system_id_cannot_be_used_as_player(self):
        cfg = config(); cfg['nations'][0]['id'] = MAKER
        with self.assertRaisesRegex(ValueError, 'reserved'):
            GameHost.new(cfg)

    def test_resumed_script_requests_and_fills_remain_audited(self):
        h = self.host(); h.cycle()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'s.json'; h.save(path)
            loaded = GameHost.load(path, config())
            loaded.world.clock.session_ticks = 200
            loaded.clients[ACTOR].scripts['2'] = BUY
            loaded.cycle()
            actions = [row['action'] for row in loaded.audit]
            self.assertIn('buy', actions)
            self.assertIn('stock_fill', actions)

    def test_check_hints_explain_sandbox_workarounds(self):
        h = self.host()
        for source, needle in (
            ('x = f"{1+1}"\n', '+ 拼接'),
            ('best = sorted([], key=lambda r: r)\n', 'key='),
            ('s = {i for i in range(3)}\n', '列表推导'),
            ('s = set()\ns.add(1)\n', 'list'),
        ):
            with self.subTest(needle=needle):
                r = self.submit(h, 'script', action='check', source=source)
                self.assertTrue(r['ok'])
                error = r['data'].get('error') or {}
                self.assertIn('改法', error.get('message', ''))
                self.assertIn(needle, error['message'])

    def test_execution_error_also_carries_hint(self):
        h = self.host()
        r = self.research(h, 'x = f"{api}"\n')
        self.assertFalse(r['ok'])
        self.assertIn('改法', r['data']['error']['message'])

    def test_advertised_key_pattern_actually_runs(self):
        source = ("def on_tick():\n"
                  "    rows = api.call('stock:market', 'quotes')\n"
                  "    def chg(r): return r['change_pct']\n"
                  "    top = sorted(rows, key=chg, reverse=True)[0]\n"
                  "    print('top ' + top['symbol'] + ' ' + str(top['price']))\n"
                  "    api.call('stock:clock', 'sleep', ticks=200)\n")
        h = self.host(source)
        h.cycle()
        self.assertEqual(self.result(h)['status'], 'effective')
        self.assertTrue(self.result(h)['output'].startswith('top '))

    def test_day_report_fact_and_briefing_show_last_session(self):
        h = self.host(BUY)
        h.cycle()
        report = h.player(ACTOR).observe()['facts']['day_report']
        self.assertEqual((report['orders'], report['fills'], report['bought_quantity']), (1, 1, 10))
        self.assertGreater(report['bought_amount'], 0)
        self.assertEqual(report['statuses'].get('filled'), 1)
        h.clients[ACTOR].scripts['2'] = BUY
        h.cycle()
        text = StockView({'language': 'zh-CN'}).stock_briefing(h.player(ACTOR).observe())
        self.assertIn('执行回执', text)
        self.assertIn('买入10股', text)
        self.assertIn('最新消息', text)
        # Morning after two sessions: the calendar must point at upcoming reports.
        quotes = {row['symbol']: row for row in h.world.market.quotes(ACTOR)}
        self.assertEqual(quotes['shanhai']['next_report_round'], 8)    # offset 1 reports again at seq 6
        self.assertEqual(quotes['qingzhou']['next_report_round'], 9)   # offset 2 already reported round 2
        self.assertEqual(quotes['yundu']['next_report_round'], 3)      # offset 3 reports today
        self.assertEqual(quotes['yundu']['report_tick'], round(200*.2))
        self.assertIsNone(quotes['shanhai']['report_tick'])

    def test_settlement_is_quiet_for_observer_but_full_in_audit(self):
        h = self.host(BUY)
        seen = []
        h.cycle(on_result=lambda actor, action, args, receipt: seen.append(action))
        self.assertNotIn('buy', seen)
        self.assertNotIn('stock_fill', seen)
        self.assertEqual(seen.count('settlement'), 1)
        actions = [row['action'] for row in h.audit]
        self.assertIn('buy', actions)
        self.assertIn('stock_fill', actions)
        row = next(r for r in h.audit if r['action'] == 'settlement')
        text = row['receipt']['data']['text']
        self.assertIn('槽1 effective', text)
        self.assertIn('委托1笔（成交）', text)
        self.assertIn('成交1笔，买10股/卖0股', text)
        self.assertIn('财富', text)

    def test_rest_day_settlement_digest_has_no_market_noise(self):
        h = self.host()
        h.world.clock.round = 6
        h.clients[ACTOR].scripts['6'] = "print('规划中')\n"
        seen = []
        h.cycle(on_result=lambda actor, action, args, receipt: seen.append(action))
        self.assertIn('settlement', seen)
        row = next(r for r in h.audit if r['action'] == 'settlement')
        text = row['receipt']['data']['text']
        self.assertIn('槽1 effective', text)
        self.assertIn('财富', text)
        self.assertNotIn('委托', text)

    def test_old_market_save_without_dynamics_is_rejected(self):
        h = self.host(); h.cycle()
        legacy = h.world.market.to_save()
        legacy.pop('dynamics')
        with self.assertRaisesRegex(ValueError, '动力学'):
            h.world.market.restore(legacy)

    def test_background_agents_split_the_float(self):
        h = self.host(); m = self.open(h)
        from mods.stock.market import BACKGROUND, FLOW, NOISE
        shares = h.world.portfolio.position_of
        total = sum(shares(a)['shares']['shanhai'] for a in BACKGROUND)
        self.assertEqual(total, m.float_shares)
        self.assertIn(NOISE, h.world.portfolio.accounts)
        self.assertTrue(all(h.world.portfolio.accounts[a]['background'] for a in BACKGROUND))

    def test_depth_and_calendar_vary_per_stock(self):
        h = self.host(); m = self.open(h)
        thin = m.backend.depth('yundu')['asks'][0][1]
        deep = m.backend.depth('hongfan')['asks'][0][1]
        self.assertEqual((thin, deep), (50, 140))
        quotes = {row['symbol']: row for row in m.quotes(ACTOR)}
        self.assertEqual(quotes['shanhai']['next_report_round'], 1)   # offset 1 on day 1
        self.assertEqual(quotes['qingzhou']['next_report_round'], 2)  # offset 2
        self.assertEqual(quotes['shanhai']['report_tick'], round(200*.2))
        self.assertIsNone(quotes['qingzhou']['report_tick'])

    def test_news_are_staggered_with_kinds_and_earnings_calendar(self):
        h = self.host(); m = self.open(h)
        for tick in range(1, 68): m.advance(tick)
        for row in m.quotes(ACTOR):
            self.assertTrue(row['news'], row['symbol'])
            self.assertTrue(all(n['tick'] <= 67 for n in row['news']))
        for tick in range(68, 201): m.advance(tick)
        rows = {row['symbol']: row for row in m.quotes(ACTOR)}
        for symbol, row in rows.items():
            kinds = {n['kind'] for n in row['news']}
            self.assertTrue(kinds <= {'market', 'wire', 'earnings'}, kinds)
            self.assertTrue(any(n['kind'] == 'wire' for n in row['news']))
            if symbol in ('shanhai', 'hongfan'):
                self.assertEqual(row['news'][0]['kind'], 'earnings')  # report day, tick 40
            else:
                self.assertEqual(row['news'][0]['tick'], 67)          # common market wire
        reporting = {s for s, row in rows.items()
                     if any(n['kind'] == 'earnings' for n in row['news'])}
        self.assertEqual(reporting, {'shanhai', 'hongfan'})

    def test_same_seed_replays_and_different_seed_diverges(self):
        cfg = config(seed=7)
        first = GameHost.new(cfg); first.world.clock.session_ticks = 200; first.cycle()
        second = GameHost.new(cfg); second.world.clock.session_ticks = 200; second.cycle()
        self.assertEqual(first.runtime.save_mod_state(first.world),
                         second.runtime.save_mod_state(second.world))
        other = GameHost.new(config(seed=8)); other.world.clock.session_ticks = 200; other.cycle()
        self.assertNotEqual(first.world.market.close, other.world.market.close)

    def test_fundamentals_move_and_persist_across_days(self):
        h = self.host(); m = self.open(h)
        start = dict(m.fundamental)
        for tick in range(1, 201): m.advance(tick)
        self.assertNotEqual(start, m.fundamental)
        end_of_day = dict(m.fundamental)
        m.settle(h.world, 1)
        self.assertEqual(m.fundamental, end_of_day)
        self.assertEqual(m.state, m.CLOSED)


if __name__ == '__main__': unittest.main()
