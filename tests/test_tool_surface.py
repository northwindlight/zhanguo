"""Model-facing tool surface: command umbrellas, merged foreign panel, policy groups."""
import unittest

from mods.ui.console import ConsoleUI
from tests import test_world_domains as fixtures

Q, C = fixtures.Q, fixtures.C

BUILTINS = {'player_state', 'player_rules', 'player_events', 'memory_search'}
NATIVE = {'build', 'economy', 'end_turn', 'intelligence', 'military', 'plan', 'query', 'relations'}


class TestToolSurface(unittest.TestCase):
    def world(self):
        harness = fixtures.TestWorldDomains()
        world = harness.world()
        harness.site(world, 'scenario:qc', Q, 1, 1)
        harness.site(world, 'scenario:cc', C, 6, 6)
        return world

    def ui(self, world):
        ui = ConsoleUI({}, tool_layer=world.runtime.tool_layer)
        ui._names = {Q: '秦', C: '楚'}
        return ui

    def test_native_model_surface_is_eight_commands_plus_builtins(self):
        world = self.world()
        player = world.runtime.player(world, Q)
        self.assertEqual({s['function']['name'] for s in player.actions()}, BUILTINS | NATIVE)
        # 旧动作名仍可按名调用（脚本、规则 AI 与旧测试），只是不进模型工具表。
        self.assertNotIn('buy', {s['function']['name'] for s in player.actions()})
        self.assertTrue(player.submit('buy', {'resource': 'native:wood', 'quantity': 1})['ok'])
        self.assertTrue(player.submit('economy', {'action': 'buy', 'resource': 'native:wood', 'quantity': 1})['ok'])

    def test_umbrella_receipts_and_captions_use_the_inner_action_prose(self):
        world = self.world()
        player = world.runtime.player(world, Q)
        ui = self.ui(world)
        args = {'action': 'buy', 'resource': 'native:wood', 'quantity': 1}
        receipt = player.submit('economy', args)
        self.assertTrue(receipt['ok'])
        self.assertEqual(ui.call_caption('economy', args, Q, receipt), '购入 木头×1')
        self.assertIn('实付', ui.receipt_body('economy', args, receipt, Q))

    def test_foreign_panel_merges_nations_relations_and_diplomacy(self):
        world = self.world()
        player = world.runtime.player(world, Q)
        data = player.query('foreign')
        self.assertEqual(set(data), {'nation', 'nations', 'countries', 'diplomacy'})
        ui = self.ui(world)
        text = ui.receipt_body('query', {'panel': 'foreign'},
                               {'ok': True, 'data': data, 'message': '', 'action': 'foreign'}, Q)
        for expected in ('当前国土', '外交对象与条约', '【外交】', '【联盟】'):
            self.assertIn(expected, text)
        # 在世是常态，不逐行标「在世」；只有亡国才写。
        self.assertNotIn('在世', text)

    def test_group_policy_filters_the_action_enum_and_blocks_submission(self):
        world = self.world()
        world.runtime.activate_actor(world, Q, 'native:huns', site='scenario:qc')
        player = world.runtime.player(world, Q)
        schemas = {s['function']['name']: s['function']['parameters']['properties'] for s in player.actions()}
        relations = schemas['relations']['action']['enum']
        self.assertIn('declare_war', relations)
        for blocked in ('guarantee', 'propose_defense', 'bloc_found', 'bloc_join', 'vote'):
            self.assertNotIn(blocked, relations)
        self.assertEqual(schemas['intelligence']['action']['enum'], ['send_letter', 'spy'])
        self.assertFalse(player.submit('relations', {'action': 'guarantee', 'target': C})['ok'])
        self.assertTrue(player.submit('relations', {'action': 'declare_war', 'target': C})['ok'])
        # 组展开同时覆盖模块自己的能力键（accept.treaties 等）。
        self.assertFalse(world.runtime.permits(world, Q, 'accept.treaties'))
        self.assertFalse(world.runtime.permits(world, Q, 'communication.gift'))
        self.assertTrue(world.runtime.permits(world, Q, 'communication.letter'))


if __name__ == '__main__':
    unittest.main()
