"""Standing orders run through real markets and the single-player host."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import test_market_objects as market_fixtures
from tests.test_human import config,A,B
from host import GameHost
from mods.native.human import HumanAPI


class TestAutoTrading(unittest.TestCase):
    def world(self,**kwargs):return market_fixtures.TestMarket().world(**kwargs)

    def test_settings_are_private_readonly_and_invalid_settings_are_atomic(self):
        w=self.world();m=w.market
        rule=m.set_auto_trade('Qin','native:food',buy_below=11000,sell_above=12000,buy_limit=100)
        self.assertTrue(rule['enabled']);before=w.runtime.save_mod_state(w)
        self.assertFalse(m.auto_trades('Chu')['rules'])
        detached=m.auto_trades('Qin');detached['rules'][0]['buy_limit']=999
        self.assertEqual(w.runtime.save_mod_state(w),before)
        for args in ({'buy_below':100,'sell_above':99},{'buy_limit':0},{'sell_limit':True},
                     {'buy_below':-1},{'enabled':1},{'reserve_gold':-1},{'max_buy_price':float('nan')}):
            with self.subTest(args=args),self.assertRaises(ValueError):m.set_auto_trade('Qin','native:food',**args)
            self.assertEqual(w.runtime.save_mod_state(w),before)

    def test_buy_target_and_per_turn_cap_use_real_quotes_and_run_once(self):
        w=self.world();m=w.market
        m.set_auto_trade('Qin','native:food',buy_below=10080,buy_limit=50)
        expected=m.quote('native:food',50,'buy','Qin');cash=w.resources.balance('Qin','native:gold')
        result=m.run_auto_trades('Qin');self.assertEqual(result['orders'][0]['quantity'],50)
        self.assertEqual(w.resources.balance('Qin','native:food'),10050)
        self.assertEqual(w.resources.balance('Qin','native:gold'),cash-expected.gold)
        before=w.runtime.save_mod_state(w)
        self.assertTrue(m.run_auto_trades('Qin')['already_executed']);self.assertEqual(w.runtime.save_mod_state(w),before)
        m.settle();m.run_auto_trades('Qin');self.assertEqual(w.resources.balance('Qin','native:food'),10080)

    def test_sell_excess_first_can_fund_a_shortage(self):
        w=self.world();m=w.market;w.resources.consume('Qin','native:gold',100000)
        m.set_auto_trade('Qin','native:wood',buy_below=10,buy_limit=10)
        m.set_auto_trade('Qin','native:food',sell_above=9900,sell_limit=100)
        result=m.run_auto_trades('Qin')['orders']
        self.assertEqual([r['side'] for r in result],['sell','buy'])
        self.assertEqual([r['quantity'] for r in result],[100,10])
        self.assertEqual(w.resources.balance('Qin','native:food'),9900)
        self.assertEqual(w.resources.balance('Qin','native:wood'),10)

    def test_buy_respects_available_cash_reserve_and_inventory_with_partial_fill(self):
        w=self.world();m=w.market;w.resources.consume('Qin','native:gold',99980)
        m.set_auto_trade('Qin','native:food',buy_below=10200,buy_limit=200,reserve_gold=10)
        result=m.run_auto_trades('Qin')['orders'][0];self.assertEqual(result['status'],'partial')
        self.assertGreater(result['quantity'],0);self.assertGreaterEqual(w.resources.balance('Qin','native:gold'),10)
        self.assertGreater(m.quote('native:food',1,'buy','Qin').gold,w.resources.balance('Qin','native:gold')-10)
        w=self.world();m=w.market;m.buy('Chu','native:wood',1915)
        m.set_auto_trade('Qin','native:wood',buy_below=50)
        result=m.run_auto_trades('Qin')['orders'][0]
        self.assertEqual(result['quantity'],5);self.assertEqual(result['status'],'partial')
        self.assertEqual(m.inventory['native:wood'],0)

    def test_average_price_limit_includes_spread_depth_and_prior_own_volume(self):
        w=self.world();m=w.market
        m.buy('Qin','native:food',40)
        limit=m.quote('native:food',10,'buy','Qin').exact_total/10
        m.set_auto_trade('Qin','native:food',buy_below=11000,buy_limit=50,max_buy_price=limit+1e-12)
        order=m.run_auto_trades('Qin')['orders'][0]
        self.assertEqual(order['quantity'],10)
        w=self.world();m=w.market
        limit=m.quote('native:food',10,'sell','Qin').exact_total/10
        m.set_auto_trade('Qin','native:food',sell_above=9000,sell_limit=50,min_sell_price=limit-1e-12)
        self.assertEqual(m.run_auto_trades('Qin')['orders'][0]['quantity'],10)

    def test_empty_market_price_block_paused_and_removed_rules_cannot_trade(self):
        w=self.world();m=w.market;m.buy('Chu','native:wood',1920)
        m.set_auto_trade('Qin','native:wood',buy_below=50)
        m.set_auto_trade('Qin','native:food',sell_above=0,min_sell_price=1000)
        result=m.run_auto_trades('Qin')['orders']
        self.assertTrue(all(r['quantity']==0 and r['status']=='blocked' for r in result))
        before=w.resources.snapshot('Qin');m.settle()
        m.set_auto_trade('Qin','native:food',sell_above=0,enabled=False)
        m.remove_auto_trade('Qin','native:wood');self.assertFalse(m.run_auto_trades('Qin')['orders'])
        self.assertEqual(w.resources.snapshot('Qin'),before)

    def test_roundtrip_preserves_rules_last_results_and_once_per_turn_marker(self):
        w=self.world();m=w.market;m.set_auto_trade('Qin','native:food',sell_above=9900)
        m.run_auto_trades('Qin');saved=w.runtime.save_mod_state(w)
        clone=self.world();clone.runtime.load_mod_state(clone,deepcopy(saved))
        self.assertEqual(clone.market.auto_trades('Qin'),m.auto_trades('Qin'))
        self.assertTrue(clone.market.run_auto_trades('Qin')['already_executed'])
        m.settle();clone.market.settle()
        self.assertEqual(m.run_auto_trades('Qin'),clone.market.run_auto_trades('Qin'))
        self.assertEqual(w.runtime.save_mod_state(w),clone.runtime.save_mod_state(clone))

    def test_replaces_rule_atomically_and_does_not_immediately_trade(self):
        w=self.world();m=w.market;before=w.resources.snapshot('Qin')
        m.set_auto_trade('Qin','native:food',sell_above=0)
        m.set_auto_trade('Qin','native:food',buy_below=10000)
        self.assertIsNone(m.auto_trades('Qin')['rules'][0]['sell_above'])
        self.assertEqual(w.resources.snapshot('Qin'),before)
        self.assertFalse(m.run_auto_trades('Qin')['orders'])

    def test_fixed_pricing_provider_is_used_for_automatic_orders(self):
        w=self.world(fixed=True);m=w.market
        m.set_auto_trade('Qin','native:food',sell_above=9900,sell_limit=100,min_sell_price=1.5)
        order=m.run_auto_trades('Qin')['orders'][0]
        self.assertEqual((order['quantity'],order['gold']),(100,150))

    def test_host_human_and_ai_api_run_on_successful_end_turn_without_stage_changes(self):
        for mods in ('mods.toml','mods.experimental.toml'):
            with self.subTest(mods=mods):
                h=GameHost.new(config(mods));api=HumanAPI(h);stages=h.runtime.stage_order[:]
                self.assertEqual(len(stages),18)
                result=api.submit(A,'auto_trade',{'resource':'food','buy_below':30,'buy_limit':10})
                self.assertTrue(result['ok'],result)
                self.assertEqual(h.world.resources.balance(A,'native:food'),20)
                other=h.player(B)
                self.assertTrue(other.call_api('economy:queries','auto_trade',resource='wood',sell_above=190,sell_limit=10)['ok'])
                self.assertNotEqual(other.query('query',{'panel':'market'})['automatic']['rules'],h.player(A).query('query',{'panel':'market'})['automatic']['rules'])
                receipt=api.submit(A,'end_turn',{})
                self.assertEqual(receipt['data']['auto_trades']['orders'][0]['quantity'],10)
                self.assertEqual(h.world.resources.balance(A,'native:food'),30)
                with self.assertRaises(ValueError):api.submit(A,'auto_trade',{'resource':'food','enabled':False})
                self.assertEqual(h.runtime.stage_order,stages)

    def test_manual_quote_buttons_are_exact_readonly_and_own_stock_limited(self):
        h=GameHost.new(config());api=HumanAPI(h);before=h.to_save()
        options=api.market_options(A,'native:food')
        self.assertEqual(before,h.to_save());self.assertEqual(len(options['quotes']),10)
        buy=next(q for q in options['quotes'] if q['side']=='buy' and q['quantity']==10)
        sell=next(q for q in options['quotes'] if q['side']=='sell' and q['quantity']==50)
        self.assertTrue(buy['available']);self.assertFalse(sell['available'])
        result=api.submit(A,'buy',{'resource':'food','quantity':10})
        self.assertEqual(result['data']['gold'],buy['quote']['gold'])
        with self.assertRaises(ValueError):api.market_options(A,'native:gold')

    def test_fractional_refund_stock_can_be_replenished_with_whole_trade_units(self):
        w=self.world();m=w.market;w.resources.credit('Qin','native:wood',9.5)
        m.set_auto_trade('Qin','native:wood',buy_below=10,sell_above=10)
        self.assertEqual(m.run_auto_trades('Qin')['orders'][0]['quantity'],1)
        self.assertEqual(w.resources.balance('Qin','native:wood'),10.5)
        m.settle();self.assertFalse(m.run_auto_trades('Qin')['orders'])

    def test_model_command_and_bound_api_both_execute_and_session_roundtrips(self):
        cfg=config();cfg['nations'][1]['max_actions']=1
        h=GameHost.new(cfg);api=HumanAPI(h);other=h.player(B)
        self.assertTrue(other.submit('economy',{'action':'auto_trade','resource':'wood','sell_above':0,'sell_limit':1})['ok'])
        def human(player):
            return [api.submit(A,'auto_trade',{'resource':'food','buy_below':50,'buy_limit':5}),api.submit(A,'end_turn',{})]
        h.cycle(on_human=human)
        last=h.world.market.auto_trades(B)['last']
        self.assertEqual(last['period'],0)
        self.assertTrue(any(o['side']=='sell' and o['quantity']>0 for o in last['orders']))
        self.assertTrue(any('auto_trades' in (row.get('receipt') or {}).get('data',{}) for row in h.audit if row['actor']==B and row['action']=='end_turn'))
        with TemporaryDirectory() as directory:
            path=Path(directory)/'game.json';h.save(path);clone=GameHost.load(path,h.cfg)
            self.assertEqual(clone.world.market.to_save(),h.world.market.to_save())
            self.assertEqual(clone.runtime.stage_order,h.runtime.stage_order)


if __name__=='__main__':unittest.main()
