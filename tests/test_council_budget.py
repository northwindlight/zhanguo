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
