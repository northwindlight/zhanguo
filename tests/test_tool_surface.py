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
        # 动作不在工具表里：模型只能经伞工具，脚本/规则 AI 走 API。
        self.assertNotIn('buy', {s['function']['name'] for s in player.actions()})
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

    def test_panels_are_derived_from_read_only_tools(self):
        world = self.world()
        runtime = world.runtime
        expected = {name for name, tool in runtime.tools.items()
                    if name == tool.name and tool.panel}
        self.assertEqual(set(runtime.panel_map), expected)
        self.assertTrue(expected)
        # 模块不再登记第二份面板处理器；Runtime.panel 与按名调用同一只读工具等价。
        from engine.player import wire
        self.assertFalse(any(hasattr(unit.mod, 'panels') for unit in runtime.units.values()))
        self.assertEqual(wire(runtime.panel(world, Q, 'land')), wire(runtime.call(world, Q, 'land', {})))
        from engine.runtime import ModError
        with self.assertRaises(ModError):
            runtime.panel(world, Q, 'buy')

    def test_observation_merges_the_four_foreign_facts(self):
        world = self.world()
        facts = world.runtime.player(world, Q).observe()['facts']
        self.assertFalse(set(facts) & {'nation', 'nations', 'countries', 'diplomacy'})
        self.assertEqual(set(facts['foreign']), {'nation', 'nations', 'countries', 'diplomacy'})
        # 按名查询仍然可用：合并的是事实面，不是只读工具。
        player = world.runtime.player(world, Q)
        self.assertTrue(player.query('nations'))
        self.assertTrue(player.query('diplomacy'))

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

    def test_umbrella_branches_carry_per_action_required_fields(self):
        world = self.world()
        player = world.runtime.player(world, Q)
        schemas = {s['function']['name']: s['function']['parameters'] for s in player.actions()}
        military = schemas['military']
        self.assertEqual(military['required'], ['action'])
        branches = {b['properties']['action']['enum'][0]: b for b in military['oneOf']}
        self.assertEqual(set(branches), {'recruit', 'move', 'attack', 'retreat', 'disband'})
        self.assertEqual(branches['disband']['required'], ['action'])
        self.assertIn({'required': ['aid']}, branches['disband']['anyOf'])
        # 地点写法约束随动作分支带出：recruit 分支仍要求 site/place/x,y 三选一。
        self.assertTrue(any('site' in branch.get('required', ()) for branch in branches['recruit']['oneOf']))
        relations = {b['properties']['action']['enum'][0]: b for b in schemas['relations']['oneOf']}
        self.assertEqual(relations['guarantee']['required'], ['action', 'target'])
        self.assertEqual(relations['offer_peace']['required'], ['action', 'target', 'kind'])
        self.assertEqual(relations['vote']['required'], ['action', 'vote', 'accept'])

    def test_umbrella_english_catalog_replaces_the_chinese_source_text(self):
        world = self.world()
        player = world.runtime.player(world, Q)
        from mods.ui.llm import LLMUI
        ui = LLMUI({'language': 'en-US'}, tool_layer=world.runtime.tool_layer)
        schemas = {s['function']['name']: s['function'] for s in ui.actions(player.actions())}
        for name in ('military', 'economy', 'relations', 'intelligence'):
            text = schemas[name]['description'] + schemas[name]['parameters']['properties']['action']['description']
            self.assertFalse(any('一' <= char <= '鿿' for char in text), name)
            self.assertIn('action', text)

    def test_group_policy_filters_the_action_enum_and_blocks_submission(self):
        world = self.world()
        world.runtime.activate_actor(world, Q, 'native:huns', site='scenario:qc')
        player = world.runtime.player(world, Q)
        schemas = {s['function']['name']: s['function']['parameters']['properties'] for s in player.actions()}
        relations = schemas['relations']['action']['enum']
        self.assertIn('declare_war', relations)
        for blocked in ('guarantee', 'propose_defense', 'bloc_found', 'bloc_join', 'vote'):
            self.assertNotIn(blocked, relations)
        # 分支随枚举一起收窄，否则 schema 会告诉模型一个它不能选的动作是合法的。
        full = {s['function']['name']: s['function']['parameters'] for s in player.actions()}
        branches = {b['properties']['action']['enum'][0] for b in full['relations']['oneOf']}
        self.assertEqual(branches, set(relations))
        self.assertEqual(schemas['intelligence']['action']['enum'], ['send_letter', 'spy'])
        self.assertFalse(player.submit('relations', {'action': 'guarantee', 'target': C})['ok'])
        self.assertTrue(player.submit('relations', {'action': 'declare_war', 'target': C})['ok'])
        # 组展开同时覆盖模块自己的能力键（accept.treaties 等）。
        self.assertFalse(world.runtime.permits(world, Q, 'accept.treaties'))
        self.assertFalse(world.runtime.permits(world, Q, 'communication.gift'))
        self.assertTrue(world.runtime.permits(world, Q, 'communication.letter'))


if __name__ == '__main__':
    unittest.main()
