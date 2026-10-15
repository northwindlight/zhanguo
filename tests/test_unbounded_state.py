"""只增不减的状态与「每回合预算」：记忆、休战、成交记录。

长局（300 回合）下这些东西原本会线性膨胀：存档越来越大、每期都要为整局历史
再付一次扫描成本，而且过期休战还会被当成有效和约报给模型。
"""
import json
import unittest
from pathlib import Path

from clients import memory
from tests import test_market_objects as market
from tests import test_world_domains as fixtures

Q, C = fixtures.Q, fixtures.C
ROOT = Path(__file__).resolve().parents[1]


class TestMemoryBudget(unittest.TestCase):
    def test_action_prose_keeps_the_receipt_but_not_the_whole_observation(self):
        """记忆存「我做了什么」，不存观察数据的第二份副本。"""
        big = {'status': 'effective', 'message': '占领成功', 'data': {'view': [[x, y, 'native:plain', '某地'] for x in range(400) for y in range(4)]}}
        calls = [{'function': {'name': 'attack', 'arguments': '{"ids":[1]}'}}]
        text = memory.action_text({'content': '进攻'}, calls, [big])
        self.assertIn('attack', text); self.assertIn('占领成功', text)
        self.assertLess(len(text), memory.DATA_LIMIT + 300)
        self.assertIn('截断', text)
        self.assertLess(len(memory.action_text({'content': ''}, calls, [big])),
                        len(json.dumps(big, ensure_ascii=False)))

    def test_history_is_capped_but_keeps_the_recent_periods(self):
        history = []
        for period in range(memory.HISTORY_LIMIT + 40):
            memory.record(history, period, f'第{period}回合做了什么')
        self.assertEqual(len(history), memory.HISTORY_LIMIT)
        self.assertEqual(history[-1]['period'], memory.HISTORY_LIMIT + 39)
        self.assertEqual(history[0]['period'], 40)

    def test_search_still_reads_the_surviving_history(self):
        history = []
        memory.record(history, 3, '在 (5,6) 建了一座兵营')
        self.assertEqual(memory.search(history, '兵营')[0]['period'], 3)


class TestTruceExpiry(unittest.TestCase):
    def test_expired_truce_disappears_from_the_panel_and_the_state(self):
        t = fixtures.TestWorldDomains(); w = t.world()
        t.site(w, 'scenario:qc', Q, 1, 1); t.site(w, 'scenario:cc', C, 2, 1)
        t.declare(w)
        t.call(w, Q, 'offer_peace', target=C, kind='white', truce=3); t.cycle(w)
        offer = w.diplomacy.snapshot(C).offers[0]
        t.call(w, C, 'accept_peace', offer=offer.id); t.cycle(w)
        self.assertTrue(w.diplomacy.active_truce(Q, C))
        self.assertTrue([row for row in w.diplomacy.snapshot(Q).truces])
        for _ in range(4): t.cycle(w)
        self.assertFalse(w.diplomacy.active_truce(Q, C))
        self.assertEqual(w.diplomacy.snapshot(Q).truces, ())
        self.assertEqual(w.diplomacy._truces, {})


class TestMarketTradeScan(unittest.TestCase):
    def world(self):
        return market.TestMarket().world()

    def test_period_trades_are_the_tail_and_match_a_full_scan(self):
        w = self.world()
        for period in range(4):
            for resource in ('native:food', 'native:wood'):
                w.market.buy('Qin', resource, 5)
            w.market.settle()
        stale = w.market.snapshot().trades[0]
        w.market.buy('Chu', 'native:food', 7)
        tail = w.market._period_trades()
        self.assertTrue(all(row.period == w.market.period for row in tail))
        self.assertEqual(tail, [row for row in w.market.snapshot().trades if row.period == w.market.period])
        self.assertNotIn(stale, tail)
        self.assertEqual(w.market.churn('Qin', 'native:food').quantity, 0)


if __name__ == '__main__':
    unittest.main()
