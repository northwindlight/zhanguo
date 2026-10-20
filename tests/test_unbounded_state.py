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
    def test_action_prose_stores_the_rendered_receipt_without_json(self):
        """记忆存「我做了什么」，存模型看到的那句回执，不存 JSON 副本。"""
        big = {'status': 'effective', 'message': '占领成功', 'data': {'view': [[x, y, 'native:plain', '某地'] for x in range(400) for y in range(4)]}}
        calls = [{'function': {'name': 'attack', 'arguments': '{"ids":[1]}'}}]
        rendered = '步兵#1 已进攻 (5, 6)，进入交战'
        text = memory.action_text({'content': '进攻'}, calls, [big], [rendered])
        self.assertIn('attack', text); self.assertIn(rendered, text)
        self.assertNotIn('"receipt"', text); self.assertNotIn('"data"', text)
        self.assertLess(len(text), 300)
        # 没有 UI 时退回状态/消息，不把观察数据抄进记忆。
        fallback = memory.action_text({'content': ''}, calls, [big])
        self.assertIn('占领成功', fallback)
        self.assertNotIn('"view"', fallback)
        self.assertLess(len(fallback), 300)

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

    def test_legacy_json_history_is_rendered_before_search(self):
        legacy = json.dumps({'action': 'query', 'arguments': '{"panel":"foreign"}',
                             'receipt': {'status': 'effective', 'message': '',
                                         'data': json.dumps({'nation': {'name': '秦'}}, ensure_ascii=False)}},
                            ensure_ascii=False)
        history = [{'period': 9, 'text': legacy}]
        calls = []
        def render(action, arguments, receipt):
            calls.append((action, arguments, receipt))
            return '本国与全部对外关系：秦'
        result = memory.search(history, 'foreign', render=render)[0]
        self.assertEqual(calls[0][0], 'query')
        self.assertEqual(calls[0][1], {'panel': 'foreign'})
        self.assertEqual(calls[0][2]['data'], {'nation': {'name': '秦'}})
        self.assertNotIn('"action"', result['context'])
        self.assertNotIn('"receipt"', result['context'])
        self.assertIn('本国与全部对外关系：秦', result['context'])


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
