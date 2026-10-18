"""API is API, tool is tool: trusted clients call capability methods, not tool names."""
import unittest
from unittest.mock import patch

from host import GameHost
from tests import test_world_domains as fixtures

Q, C = fixtures.Q, fixtures.C


class TestApiToolBoundary(unittest.TestCase):
    def world(self):
        harness = fixtures.TestWorldDomains()
        world = harness.world()
        harness.site(world, 'scenario:qc', Q, 1, 1)
        harness.site(world, 'scenario:cc', C, 6, 6)
        return world

    def test_read_api_returns_data_and_rejects_unknown_methods(self):
        world = self.world()
        player = world.runtime.player(world, Q)
        data = player.call_api('economy:queries', 'balance')
        self.assertIn('stocks', data)
        self.assertNotIn('ok', data)
        with self.assertRaisesRegex(ValueError, '未声明方法'):
            player.call_api('native:army', 'not_a_method')
        with self.assertRaisesRegex(ValueError, '未知 API 能力'):
            player.call_api('missing:capability', 'x')

    def test_write_api_returns_a_receipt_and_rejects_actor_override(self):
        world = self.world()
        player = world.runtime.player(world, Q)
        receipt = player.call_api('native:government', 'plan', {'text': '发展经济'}, action_label='plan')
        self.assertTrue(receipt['ok'], receipt)
        self.assertEqual(receipt['action'], 'plan')
        self.assertEqual(receipt['data']['text'], '发展经济')
        with self.assertRaisesRegex(ValueError, '不能覆盖行动者'):
            player.call_api('economy:queries', 'buy',
                            {'actor': C, 'resource': 'native:wood', 'quantity': 1})

    def test_bound_api_write_is_audited_under_its_semantic_action(self):
        host = GameHost.new({'map_size': 12, 'seed': 73,
                             'nations': [{'id': Q, 'name': '秦', 'client': 'rule'}]})
        host.player(Q).call_api('native:government', 'plan', {'text': '发展经济'}, action_label='plan')
        self.assertEqual(host.audit[-1]['action'], 'plan')
        self.assertTrue(host.audit[-1]['receipt']['ok'])

    def test_audit_records_the_issuing_model(self):
        host = GameHost.new({'map_size': 12, 'seed': 73,
                             'nations': [{'id': Q, 'name': '秦', 'client': 'llm', 'model': 'audit-model'}]})
        host.player(Q).submit('plan', {'text': '发展经济'})
        self.assertEqual(host.audit[-1]['model'], 'audit-model')
        rule = GameHost.new({'map_size': 12, 'seed': 73,
                             'nations': [{'id': Q, 'name': '秦', 'client': 'rule'}]})
        rule.player(Q).call_api('native:government', 'plan', {'text': '发展经济'}, action_label='plan')
        self.assertEqual(rule.audit[-1]['model'], 'rule')

    def test_rule_client_never_uses_the_tool_submit_path(self):
        host = GameHost.new({'map_size': 12, 'seed': 73,
                             'nations': [{'id': Q, 'name': '秦', 'client': 'rule'}]})
        with patch('engine.player.PlayerSession.submit', side_effect=AssertionError('tool submit called')):
            host.cycle()
        self.assertTrue(host.audit)
        self.assertTrue(all(row.get('receipt') is not None for row in host.audit))


if __name__ == '__main__':
    unittest.main()
