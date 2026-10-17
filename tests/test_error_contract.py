"""异常契约：谁错、算谁头上、能不能停机。

0.2 原来用「异常类型」当契约（ValueError/KeyError=参数错、其余=崩），边界是偶然的：
模块内部一个 OverflowError 与「参数写错了」在类型上分不开。这里钉三件事：
① 参数错 → invalid_arguments；② 业务拒绝 → rejected；③ 模块内部 bug → internal_fault，
该动作作废、游戏继续、故障留痕；只有结算本身失败才允许停机。
"""
import unittest
from unittest.mock import patch

from mods.native.market.contracts import BufferPolicy, Listing
from mods.native.market_pricing.pricing import InventoryPricing
from mods.ui.maps import MAP_MAX_PX, MAP_MIN_PX, expand_message, render_snapshot
from tests import test_world_domains as fixtures
from ui import MAP_KEY

Q, C = fixtures.Q, fixtures.C
POLICY = BufferPolicy(elasticity=1.0, flow=0.25, refill=0.0, spread=0.10, low=1e-6, high=1e9)
LISTING = Listing('native:wood', 2.0, 1920, 1920)


def snapshot(tiles):
    return {'version': 'zhanguo.ui.map/1', 'actor': Q, 'period': 1, 'tiles': tiles,
            'armies': [], 'alliances': [], 'wars': []}


class TestToolContract(unittest.TestCase):
    def world(self):
        t = fixtures.TestWorldDomains(); w = t.world()
        t.site(w, 'scenario:qc', Q, 1, 1); t.site(w, 'scenario:cc', C, 6, 6)
        return w

    def test_unknown_arguments_are_rejected_not_left_to_typeerror(self):
        """schema 声明 additionalProperties=false，运行时就得真拒收——
        否则多余参数会一路渗进 **args 的 handler，变成未捕获的 TypeError。"""
        p = self.world().runtime.player(self.world(), Q)
        w = self.world(); p = w.runtime.player(w, Q)
        receipt = p.submit('offer_peace', {'target': C, 'kind': 'white', 'bogus': 1})
        self.assertFalse(receipt['ok']); self.assertEqual(receipt['code'], 'invalid_arguments')
        self.assertIn('bogus', receipt['message'])

    def test_handler_bugs_void_the_action_and_leave_a_trail(self):
        """OverflowError/KeyError/TypeError 都不是「参数错」，也不该停机。"""
        for fault in (OverflowError('math range error'), KeyError('missing'), TypeError('bad call')):
            w = self.world(); faults = []
            p = w.runtime.player(w, Q, on_fault=lambda action, exc, detail: faults.append((action, exc, detail)))
            with patch.object(w.runtime, 'call', side_effect=fault):
                receipt = p.submit('plan', {'text': '发展'})
            self.assertFalse(receipt['ok'], fault)
            self.assertEqual(receipt['code'], 'internal_fault', fault)
            self.assertNotEqual(receipt['code'], 'invalid_arguments', fault)
            self.assertIn(type(fault).__name__, receipt['message'], fault)
            self.assertEqual(faults[0][0], 'plan')
            self.assertIn('Traceback', faults[0][2])

    def test_inferred_schema_rejects_extra_end_turn_argument_before_writes(self):
        w=self.world();p=w.runtime.player(w,Q)
        self.assertTrue(p.submit('plan',{'text':'按实际库存发展生产'})['ok'])
        before=fixtures.TestWorldDomains().save(w)
        receipt=p.submit('end_turn',{'summary':'完成本期经营与部署','source':'unwanted'})
        self.assertEqual(receipt['code'],'invalid_arguments')
        self.assertIn('未知参数：source',receipt['message'])
        self.assertEqual(fixtures.TestWorldDomains().save(w),before)

    def test_serialization_failure_after_a_write_is_not_reported_as_rejected(self):
        """动作已经生效之后才炸的序列化失败，报成 rejected 会让模型重做一次。"""
        w = self.world(); p = w.runtime.player(w, Q)
        with patch('engine.player.wire', side_effect=TypeError('unsupported public value')):
            receipt = p.submit('plan', {'text': '发展'})
        self.assertFalse(receipt['ok']); self.assertEqual(receipt['code'], 'internal_fault')


class TestPriceCurveBounds(unittest.TestCase):
    def test_normal_orders_keep_the_published_schedule(self):
        """夹子只该拦住浮点装不下的量级，正常委托必须逐字不变。"""
        import math
        engine = InventoryPricing()
        for side in ('buy', 'sell'):
            sign = 1 if side == 'buy' else -1
            quantity = 50
            path = engine.walk(LISTING, 2.0, quantity, side, 0, 1920, POLICY)
            reference = 2.0 * sum(math.exp(sign * step / 1920) for step in range(quantity)) * (1 + sign * 0.05)
            self.assertAlmostEqual(path.exact_total, reference, delta=abs(reference) * 1e-9)

    def test_huge_orders_are_rejected_instead_of_overflowing(self):
        """指数滑点没有上界（这是口径），但 float 有：越界是业务拒绝，不是崩溃。"""
        engine = InventoryPricing()
        for quantity in (1_360_000, 2_000_000, 10**9):
            with self.assertRaises(ValueError) as caught:
                engine.walk(LISTING, 2.0, quantity, 'buy', 0, 1920, POLICY)
            self.assertNotIsInstance(caught.exception, OverflowError, quantity)
            self.assertIn('超出市场可定价范围', str(caught.exception))
        with self.assertRaises(ValueError):
            engine.walk(LISTING, 2.0, 1, 'buy', 10**9, 1920, POLICY)

    def test_level_stays_inside_the_declared_band(self):
        engine = InventoryPricing()
        for inventory in (0, 1, 1920, 10**9):
            price = engine.level(LISTING, inventory, 1920, POLICY).price
            self.assertGreaterEqual(price, POLICY.low); self.assertLessEqual(price, POLICY.high)


class TestNonModelPathsDegrade(unittest.TestCase):
    def test_map_size_is_clamped_not_fatal(self):
        tiles = [{'position': [1, 1], 'owner': Q, 'terrain': 'native:plain',
                  'buildings': [], 'resources': []}]
        for max_px in (10**9, -5, 0, True, 'big'):
            self.assertIsNotNone(render_snapshot(snapshot(tiles), max_px=max_px))
        self.assertEqual((MAP_MIN_PX, MAP_MAX_PX), (240, 1800))

    def test_render_failure_falls_back_to_the_text_map(self):
        """渲染是锦上添花：画不出来就退回文本地图，不该把整局带走。"""
        message = {'role': 'user', 'content': '地图如下',
                   MAP_KEY: {'facts': {'version': 'zhanguo.ui.map/1'}, 'max_px': 960, 'fallback': '（文本地图）'}}
        for broken in ({'version': 'wrong'}, {}, {'version': 'zhanguo.ui.map/1', 'tiles': [{'position': [0]}]}):
            payload = dict(message); payload[MAP_KEY] = {**message[MAP_KEY], 'facts': broken}
            result = expand_message(payload)
            self.assertIsInstance(result['content'], str); self.assertIn('（文本地图）', result['content'])


if __name__ == '__main__':
    unittest.main()
