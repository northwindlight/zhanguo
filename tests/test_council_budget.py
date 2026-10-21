"""议会证据的投资预算闸：请求永不超 reviewer 窗口，降级只动本次请求的副本。"""
from copy import deepcopy
import json
import unittest

from clients.llm import assert_paired
from host import GameHost
from tests.test_parliament import CouncilBackend, reply, start_with_plan
from tests.test_player_clients import FakeBackend, call


class RecordingCouncilBackend(CouncilBackend):
    def __init__(self, decisions):
        super().__init__(decisions)
        self.calls = []

    def chat_turn(self, messages, tools, cfg, on_retry=None):
        self.calls.append((deepcopy(messages), deepcopy(tools)))
        return super().chat_turn(messages, tools, cfg, on_retry=on_retry)


class TestEvidenceFactsOnly(unittest.TestCase):
    def test_previews_and_reachability_are_filtered_but_the_map_is_kept(self):
        from mods.experimental.council import evidence_state, prune_changes
        state = {'facts': {'army': [], 'government': {},
                           'view': [{'position': [0, 0], 'owner': 'scenario:han'}],
                           'frontier': {'reachable': [[1, 1]]},
                           'econ': {'preview': {'military': {'units': []}}, 'stocks': [1]}}}
        out = evidence_state(state)
        self.assertNotIn('frontier', out['facts'])
        self.assertNotIn('preview', out['facts']['econ'])
        self.assertEqual(out['facts']['view'], state['facts']['view'])
        self.assertEqual(out['facts']['econ']['stocks'], [1])
        # 原状态不动：快照只裁剪议会证据的副本。
        self.assertIn('frontier', state['facts'])
        self.assertIn('preview', state['facts']['econ'])
        changes = [{'path': ['facts', 'frontier', 'reachable'], 'set': []},
                   {'path': ['facts', 'econ', 'preview', 'military'], 'set': {}},
                   {'path': ['facts', 'econ', 'stocks'], 'rows': {}},
                   {'path': ['facts', 'army'], 'rows': {}}]
        self.assertEqual([c['path'] for c in prune_changes(changes)],
                         [['facts', 'econ', 'stocks'], ['facts', 'army']])

    def test_after_action_diffs_are_dropped_but_turn_boundaries_keep_facts(self):
        from mods.experimental.council import prune_row
        row = {'kind': 'after.action', 'period': 3, 'changes': [{'path': ['facts', 'army'], 'rows': {}}],
               'events': {'items': [{'text': '事件'}]}}
        kept = {'kind': 'settlement', 'period': 3, 'changes': [{'path': ['facts', 'econ', 'stocks'], 'rows': {}}]}
        self.assertNotIn('changes', prune_row(row))
        self.assertEqual(prune_row(row)['events'], row['events'])
        self.assertIn('changes', prune_row(kept))
        self.assertIn('changes', row, '原行不被修改')


class TestCouncilBudget(unittest.TestCase):
    actor = 'scenario:han'

    def cfg(self):
        # LLM 玩家的议长默认继承该国自己的模型与 ctx_window（顶层 reviewer 只兜底非 LLM 玩家）。
        return {'mods': 'mods.experimental.toml', 'map_size': 12, 'seed': 73,
                'parliament': {'reviewer': {'model': 'synthetic-council', 'api_key': 'review-secret'}},
                'nations': [{'name': '韩', 'id': self.actor, 'client': 'llm', 'model': 'synthetic-monarch',
                             'api_key': 'monarch-secret', 'ctx_window': 24000, 'ctx_fill': 0.5,
                             'max_tokens': 1024, 'ctx_compact': 'off'}]}

    def fill(self, service, rows=80):
        filler = '档案轨迹填充' * 40
        for i in range(rows):
            service.council['archive'].append({
                'kind': 'after.action', 'period': i,
                'changes': [{'path': ['facts', 'army'],
                             'rows': {'upsert': [{'id': j, 'note': filler} for j in range(6)]}}],
                'events': {'cursor': i, 'items': [{'id': i, 'period': i, 'text': filler}]}})

    def test_oversized_evidence_is_fitted_before_the_request(self):
        h = GameHost.new(self.cfg()); c = h.clients[self.actor]
        c._backend = FakeBackend([reply(call('p', 'plan', text='NEW-PLAN 修订国策'),
                                        call('e', 'end_turn', summary='完成修订'))])
        service = h.services[self.actor]['council']
        service._review_backend = RecordingCouncilBackend([True])
        start_with_plan(h, self.actor)
        self.fill(service)
        p = h.player(self.actor)
        self.assertTrue(p.submit('plan', {'text': 'NEW-PLAN 修订国策'})['ok'])
        limit = service._review_input_budget()
        self.assertEqual(limit, 12000)
        self.assertTrue(service._review_backend.calls)
        for messages, tools in service._review_backend.calls:
            self.assertLessEqual(service._request_tokens(messages, tools), limit)
            assert_paired(messages)
        evidence = service._review_backend.calls[0][0][1]['content']
        self.assertNotIn('"changes"', evidence)
        # 档案原文不动：降级只写进请求里的副本。
        self.assertTrue(all('changes' in row for row in service.council['archive']
                            if row.get('kind') == 'after.action' and 'changes' in row))

    def test_retained_rejected_evidence_is_degraded_before_the_new_request(self):
        h = GameHost.new(self.cfg()); c = h.clients[self.actor]
        c._backend = FakeBackend([reply(call('p', 'plan', text='NEW-PLAN 修订国策'),
                                        call('e', 'end_turn', summary='完成修订'))])
        service = h.services[self.actor]['council']
        service._review_backend = RecordingCouncilBackend([True])
        start_with_plan(h, self.actor)
        # 模拟「上一轮被打回、对话连同证据留下来」：旧证据里同样有大量 changes。
        from mods.experimental.council import encode
        filler = '旧审核证据' * 40
        old = {'previous_plan': {'approved': False, 'period': 90, 'text': 'OLD-PLAN'},
               'stage': 1, 'period': 91,
               'trajectory': [{'kind': 'after.action', 'period': i,
                               'changes': [{'path': ['facts', 'army'], 'set': [{'note': filler}]}],
                               'events': {'items': [{'text': filler}]}} for i in range(80)]}
        prefix = service.ui.text('experimental.review_first')
        service.council['dialogue'] = [
            {'role': 'system', 'content': '旧审核系统提示'},
            {'role': 'user', 'content': prefix + encode(old) + '\n旧地图'}]
        self.fill(service)
        p = h.player(self.actor)
        self.assertTrue(p.submit('plan', {'text': 'NEW-PLAN 修订国策'})['ok'])
        limit = service._review_input_budget()
        for messages, tools in service._review_backend.calls:
            self.assertLessEqual(service._request_tokens(messages, tools), limit)
            assert_paired(messages)
        retained = service._review_backend.calls[0][0][1]['content']
        self.assertNotIn('"changes"', retained)
        self.assertIn('旧地图', retained)

    def test_small_review_keeps_full_evidence(self):
        h = GameHost.new(self.cfg()); c = h.clients[self.actor]
        c._backend = FakeBackend([reply(call('p', 'plan', text='NEW-PLAN 修订国策'),
                                        call('e', 'end_turn', summary='完成修订'))])
        service = h.services[self.actor]['council']
        service._review_backend = RecordingCouncilBackend([True])
        start_with_plan(h, self.actor)
        service.council['archive'].append({'kind': 'after.action', 'period': 1,
                                           'changes': [{'path': ['facts', 'army'], 'set': []}],
                                           'events': {'cursor': 1, 'items': []}})
        p = h.player(self.actor)
        self.assertTrue(p.submit('plan', {'text': 'NEW-PLAN 修订国策'})['ok'])
        evidence = service._review_backend.calls[0][0][1]['content']
        self.assertIn('"changes"', evidence)
        self.assertNotIn('"omitted"', evidence)


if __name__ == '__main__':
    unittest.main()
