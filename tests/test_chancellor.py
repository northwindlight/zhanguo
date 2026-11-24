"""丞相原型：离线脚本化模型，核对简报、奏折校验、准奏执行与方针记录。"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from host import GameHost
from mods.native.human import HumanAPI
from mods.ui import desktop_chancellor
from tests.test_human import A, Transport, config

PROFILE = {'provider': 'openai', 'base_url': 'http://offline.invalid', 'api_key': 'unused', 'model': 'offline'}


def first_buildable(host):
    """本国第一块可开工地块上第一项可建建筑，用来写一份真实可执行的脚本奏折。"""
    api = HumanAPI(host)
    for site in api.state(A)['facts']['building_sites']['sites']:
        x, y = site['position']
        options = api.site_options(A, x=x, y=y)
        for building in options['buildings']:
            if building.get('available'):
                return x, y, building['kind']
    raise AssertionError('测试局没有可建项')


class TestChancellor(unittest.TestCase):
    def run_turn(self, memorials, directory):
        host = GameHost.new(config())
        x, y, kind = first_buildable(host)
        script = Path(directory) / 'script.json'
        script.write_text(json.dumps([m(x, y, kind) for m in memorials], ensure_ascii=False), encoding='utf-8')
        desktop_chancellor.ScriptedBackend._cursor.clear()
        return host, script, (x, y, kind)

    def test_memorial_is_validated_and_approved_calls_use_the_human_channel(self):
        def memorial(x, y, kind):
            return {'summary': '粮少金多，先建产业。', 'proposals': [
                {'title': '建一座', 'reason': '有空位', 'calls': [{'operation': 'build', 'arguments': {'x': x, 'y': y, 'kind': kind}}]},
                {'title': '越权退朝', 'reason': '', 'calls': [{'operation': 'end_turn', 'arguments': {}}]},
                {'title': '调兵无编号', 'reason': '', 'calls': [{'operation': 'move', 'arguments': {'x': 0, 'y': 0}}]},
                {'title': '缺坐标', 'reason': '', 'calls': [{'operation': 'build', 'arguments': {'kind': kind}}]},
                {'title': '卖空气', 'reason': '', 'calls': [{'operation': 'sell', 'arguments': {'resource': 'native:food', 'quantity': -3}}]},
                {'title': '定方针', 'reason': '君主说过', 'calls': [], 'directive': '金币低于 300 不再扩军'},
            ]}
        with TemporaryDirectory() as d:
            host, script, (x, y, kind) = self.run_turn([memorial], d)
            with patch.dict(os.environ, {'ZHANGUO_CHANCELLOR_SCRIPT': str(script), 'ZHANGUO_CHANCELLOR_SCRIPT_DELAY': '0'}):
                t = Transport(host, d)
                try:
                    t.put(command='chancellor_config', profile=PROFILE)
                    t.wait(lambda r: r['type'] == 'chancellor_state' and r['enabled'])
                    turn = t.turn()
                    memo = t.wait(lambda r: r['type'] == 'chancellor_memorial')
                    self.assertTrue(memo['ok'], memo)
                    self.assertEqual(memo['summary'], '粮少金多，先建产业。')
                    titles = [p['title'] for p in memo['proposals']]
                    self.assertEqual(titles, ['建一座', '定方针'])
                    self.assertEqual(len(memo['dropped']), 4)
                    self.assertTrue(any('越权退朝' in d and '不在可奏请范围' in d for d in memo['dropped']))
                    self.assertTrue(any('调兵无编号' in d and 'ids' in d for d in memo['dropped']))
                    # 准奏：界面按原有真人通道提交奏折里的操作，回执是正式领域回执。
                    call = memo['proposals'][0]['calls'][0]
                    result = t.submit(turn, 'approve-1', call['operation'], call['arguments'])
                    self.assertTrue(result['ok'] and result['data']['ok'], result)
                    # 准驳反馈与方针写入存档旁记录，不进世界状态。
                    t.put(command='chancellor_directive', op='add', text=memo['proposals'][1]['directive'])
                    state = t.wait(lambda r: r['type'] == 'chancellor_state' and r['directives'])
                    self.assertEqual(state['directives'], ['金币低于 300 不再扩军'])
                    mark = len(t.rows)
                    t.put(command='chancellor_feedback', period=turn['period'], approved=['建一座'], rejected=[], failed=[])
                    t.put(command='snapshot');t.wait(lambda r: r['type'] == 'snapshot', after=mark)
                    saved = json.loads((Path(d) / desktop_chancellor.STATE_FILE).read_text(encoding='utf-8'))
                    self.assertEqual(saved['directives'], ['金币低于 300 不再扩军'])
                    self.assertEqual(saved['log'][-1]['approved'], ['建一座'])
                    self.assertNotIn('chancellor', json.dumps(host.cfg, ensure_ascii=False))
                    self.assertNotIn('unused', json.dumps(host.cfg, ensure_ascii=False))
                finally:
                    t.close()

    def test_briefing_uses_player_facts_and_edict_reaches_the_model(self):
        seen = []

        class Recorder(desktop_chancellor.ScriptedBackend):
            def chat_turn(self, messages, tools, cfg, on_retry=None, on_delta=None):
                seen.append(messages)
                return super().chat_turn(messages, tools, cfg)

        def memorial(x, y, kind):
            return {'summary': '遵旨。', 'reply': '臣明白，只卖不买。', 'proposals': []}
        with TemporaryDirectory() as d:
            host, script, (x, y, kind) = self.run_turn([memorial, memorial], d)
            with patch.dict(os.environ, {'ZHANGUO_CHANCELLOR_SCRIPT': str(script), 'ZHANGUO_CHANCELLOR_SCRIPT_DELAY': '0'}), \
                    patch.object(desktop_chancellor, 'ScriptedBackend', Recorder):
                t = Transport(host, d)
                try:
                    t.put(command='chancellor_config', profile=PROFILE)
                    t.turn()
                    t.wait(lambda r: r['type'] == 'chancellor_memorial')
                    after = len(t.rows)
                    t.put(command='chancellor_request', kind='edict', text='这回合只卖不买')
                    memo = t.wait(lambda r: r['type'] == 'chancellor_memorial', after=after)
                    self.assertEqual(memo['reply'], '臣明白，只卖不买。')
                    briefing = seen[-1][1]['content']
                    self.assertIn('【规则书】', seen[-1][0]['content'])
                    self.assertIn('【军队】', briefing)
                    self.assertIn('【列国】', briefing)
                    self.assertIn('【本国】', briefing)
                    self.assertIn(f'({x},{y})', briefing)
                    self.assertIn('本期可开工', briefing)
                    self.assertIn('【君主诏书】这回合只卖不买', briefing)
                    self.assertNotIn('楚国库存', briefing)
                finally:
                    t.close()

    def test_personal_rule_stops_automatic_memorials(self):
        def memorial(x, y, kind):
            return {'summary': '无事。', 'proposals': []}
        with TemporaryDirectory() as d:
            host, script, _ = self.run_turn([memorial], d)
            with patch.dict(os.environ, {'ZHANGUO_CHANCELLOR_SCRIPT': str(script), 'ZHANGUO_CHANCELLOR_SCRIPT_DELAY': '0'}):
                t = Transport(host, d)
                try:
                    t.put(command='chancellor_config', profile=PROFILE)
                    t.put(command='chancellor_mode', personal=True)
                    t.wait(lambda r: r['type'] == 'chancellor_state' and r.get('personal'))
                    mark = len(t.rows)
                    t.turn()
                    t.put(command='snapshot');t.wait(lambda r: r['type'] == 'snapshot', after=mark + 1)
                    self.assertFalse(any(r['type'] in ('chancellor_status', 'chancellor_memorial') for r in t.rows[mark:]))
                    t.put(command='chancellor_request', kind='memorial')
                    self.assertTrue(t.wait(lambda r: r['type'] == 'chancellor_memorial', after=mark)['ok'])
                finally:
                    t.close()

    def test_failures_are_reported_without_blocking_the_turn(self):
        with TemporaryDirectory() as d:
            host = GameHost.new(config())
            t = Transport(host, d)
            try:
                t.put(command='chancellor_request', kind='memorial')
                error = t.wait(lambda r: r['type'] == 'error')
                self.assertIn('未设丞相', error['message'])
                t.put(command='chancellor_config', profile={'model': 'x'})
                error = t.wait(lambda r: r['type'] == 'error' and '丞相模型档案' in r['message'])
                self.assertIn('base_url', error['message'])
            finally:
                t.close()


if __name__ == '__main__':
    unittest.main()
