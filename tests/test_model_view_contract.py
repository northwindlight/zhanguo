"""模型面向契约：发行版与模型客户端之间的通用约定。

这一组守住三件事：
  1. 系统提示按**实际工具表**生成，不写死任何发行版的面板名；
  2. 发行版没有的概念（国策、市场、视野）不会被简报硬凑出来；
  3. minigame 这种「开发模式」发行版能被真实模型客户端驱动。
"""
import json
import unittest

from host import GameHost
from clients.model_view import model_view_for
from presentation.facts import war_sides

ROOT = 'mods.minigame.toml'
NATIVE = 'mods.toml'


def call(ident, name, **arguments):
    return {'id': ident, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


class FakeBackend:
    """离线模型后端：按剧本回话，并记录发给模型的工具表。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []

    def chat_turn(self, messages, tools, cfg, on_retry=None):
        self.requests.append({'messages': messages, 'tools': tools})
        message = self.responses.pop(0) if self.responses else {
            'content': '先建设再扩张',
            'tool_calls': [call('b', 'build', x=3, y=3, kind='mini:barracks'),
                           call('e', 'end_turn', summary='本期建了兵营')]}
        return message, {'hit': 10, 'miss': 2}

    def complete_text(self, messages, cfg, tools=None, **kwargs):
        return '保持现状。', {'hit': 8, 'miss': 1}


class TestModelFacingContract(unittest.TestCase):

    def native(self):
        return GameHost.new({'mods': NATIVE, 'map_size': 10, 'seed': 1,
                             'nations': [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}]})

    def mini(self, client='llm'):
        return GameHost.new({'mods': ROOT, 'map_size': 12, 'seed': 3,
                             'nations': [{'id': 'scenario:qin', 'name': '秦', 'client': client,
                                          'position': [3, 3]},
                                         {'id': 'scenario:chu', 'name': '楚', 'client': client,
                                          'position': [8, 8]}]})

    def test_system_prompt_tool_vocabulary_comes_from_the_actual_tool_table(self):
        host = self.native()
        player = host.player('scenario:qin')
        ui = model_view_for(host.ui, host.cfg)
        ui.actions(player.actions())
        text = ui.system()
        # 面板清单来自 query 工具自己的 panel 枚举，不是手抄的名单。
        schema = next(s for s in player.actions() if s['function']['name'] == 'query')
        panels = schema['function']['parameters']['properties']['panel']['enum']
        self.assertIn('query(panel=', text)
        for panel in panels:
            self.assertIn(panel, text)
        # 带 action 参数的工具按名字列出；不重复抄 schema 里已有的枚举。
        for name in ('military', 'economy', 'relations', 'intelligence'):
            self.assertIn(name, text)

    def test_system_prompt_of_a_non_native_distribution_carries_no_native_vocabulary(self):
        host = self.mini()
        player = host.player('scenario:qin')
        ui = model_view_for(host.ui, host.cfg)
        schemas = ui.actions(player.actions())
        text = ui.system()
        for foreign in ('query(panel=', '国策', 'market', 'panel=foreign'):
            self.assertNotIn(foreign, text)
        # 发行版自己的工具名在工具表里；提示只说明读法。
        names = {schema['function']['name'] for schema in schemas}
        self.assertTrue({'build', 'end_turn', 'player_rules'} <= names)
        self.assertIn('player_rules(topic)', text)

    def test_briefing_only_shows_sections_the_distribution_actually_has(self):
        host = self.mini()
        player = host.player('scenario:qin')
        ui = model_view_for(host.ui, host.cfg)
        ui.actions(player.actions())
        text, _ = ui.state(player.observe(), [])
        # minigame 有资源、地图和军队，没有国策。
        self.assertIn('【国土/视野】', text)
        self.assertIn('【军队】', text)
        self.assertIn('【国力】', text)
        self.assertNotIn('【国策规划】', text)

    def test_native_keeps_its_own_system_prompt_from_its_tool_layer_catalog(self):
        host = self.native()
        player = host.player('scenario:qin')
        ui = model_view_for(host.ui, host.cfg)
        ui.actions(player.actions())
        text = ui.system()
        # native 的专属说明来自它自己的语言包，不靠宿主硬编码。
        self.assertIn('你是战国的一国玩家', text)
        self.assertIn('query(panel=map)', text)

    def test_minigame_is_driven_by_the_model_client_end_to_end(self):
        host = self.mini()
        player = host.player('scenario:qin')
        backend = FakeBackend()
        host.clients['scenario:qin']._backend = backend
        receipts = host.clients['scenario:qin'].play(player)
        self.assertTrue(receipts)
        actions = [receipt['action'] for receipt in receipts]
        self.assertIn('build', actions)
        self.assertIn('end_turn', actions)
        self.assertTrue(all(receipt['ok'] for receipt in receipts))
        # 回合小结必须回到模型手里（客户端按 data.text 取）。
        ended = next(receipt for receipt in receipts if receipt['action'] == 'end_turn')
        self.assertEqual(ended['data']['text'], '本期建了兵营')

    def test_minigame_exposes_the_facts_a_model_needs(self):
        host = self.mini(client='mini')
        facts = host.player('scenario:qin').observe()['facts']
        for name in ('stock', 'view', 'catalog', 'land', 'army', 'nations', 'diplomacy'):
            self.assertIn(name, facts)
        stocks = {row['id']: row['amount'] for row in facts['stock']}
        self.assertEqual(set(stocks), {'mini:gold', 'mini:food', 'mini:wood'})
        self.assertTrue(any(row['owner'] == 'scenario:qin' for row in facts['view']))
        self.assertTrue(facts['catalog']['buildings'])

    def test_war_facts_of_every_shape_are_read_without_crashing(self):
        self.assertEqual(war_sides({'attackers': ['a'], 'defenders': ['b']}), (['a'], ['b']))
        self.assertEqual(war_sides({'parties': ['a', 'b']}), (['a'], ['b']))
        self.assertEqual(war_sides(['a', 'b']), (['a'], ['b']))
        self.assertEqual(war_sides('乱写'), ([], []))

    def test_llm_client_accepts_the_host_factory_contract(self):
        from clients.llm import LLMClient, LLMState
        state = LLMState()
        client = LLMClient({'client': 'llm'}, record={'kind': 'llm', 'version': LLMClient.version,
                                                       'state': state.to_save()},
                           services={'ui': None})
        self.assertEqual(client.kind, 'llm')
        self.assertEqual(client.state.to_save(), state.to_save())


if __name__ == '__main__':
    unittest.main()
