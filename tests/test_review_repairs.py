"""API/tool 审查缺口的行为回归；只用合成世界与模拟后端。"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from clients import memory
from engine.runtime import GameMod, ModError, Runtime, Tool, Unit
from mods.native.economy_queries.api import EconomyAPI
from mods.native.tools import query
from tests import test_api_tool_boundary as boundary
from tests import test_diplomacy_finish as diplomacy
from tests import test_parliament as parliament

Q, C = diplomacy.Q, diplomacy.C


class TestReviewRepairs(unittest.TestCase):
    def test_expired_unchanged_plan_is_reviewed_and_can_finish_on_both_paths(self):
        for api in (False, True):
            with self.subTest(api=api):
                h, client = parliament.TestParliament().host([True], [], revision=False)
                client.play = lambda player, **kwargs: []
                p = h.player(parliament.TestParliament.actor)
                text = '持续发展农业工业，按实际结算调整'
                self.assertTrue(p.submit('plan', {'text': text})['ok'])
                old = p.observe()['facts']['government']['plan']
                for _ in range(old['valid_until'] + 1):h.cycle()
                result = (p.call_api('native:government', 'plan', text=text) if api
                          else p.submit('plan', {'text': text}))
                self.assertTrue(result['ok'], result)
                self.assertEqual(len(h.services[p.actor]['council']._review_backend.requests), 2)
                current = p.observe()['facts']['government']['plan']
                self.assertEqual(current['period'], h.clock())
                self.assertTrue(p.submit('end_turn', {'summary': '完成本回合经营并通过审核'})['ok'])

    def test_api_finish_closes_all_offer_types_in_both_distributions(self):
        fixture = diplomacy.TestDiplomacyFinish()
        for experimental in (False, True):
            for kind in ('defense', 'found', 'peace'):
                with self.subTest(experimental=experimental, kind=kind):
                    h = fixture.host(experimental); offer = fixture.offer(h, kind)
                    result = h.player(C).call_api('native:government', 'end_turn', summary='完成本期实际经营')
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['data']['auto_rejected']['offers'], [offer])
                    self.assertFalse(h.player(C).call_api('native:diplomacy', 'snapshot')['offers'])

    def test_echoing_effective_plan_after_rejection_does_not_review_again(self):
        h, client = parliament.TestParliament().host([False], [], revision=True)
        client.play = lambda player, **kwargs: []
        p = h.player(parliament.TestParliament.actor)
        old = p.observe()['facts']['government']['plan']['text']
        self.assertFalse(p.submit('plan', {'text': '本回合修订的新国策'})['ok'])
        backend = h.services[p.actor]['council']._review_backend
        count = len(backend.requests)
        result = p.submit('plan', {'text': old})
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(backend.requests), count)
        self.assertFalse(p.submit('end_turn', {'summary': '仍须解释本回合否决'})['ok'])

    def test_script_end_turn_uses_the_same_diplomatic_finish(self):
        fixture = diplomacy.TestDiplomacyFinish(); h = fixture.host(True)
        offer = fixture.offer(h); p = h.player(C)
        source = 'api.call("native:government", "end_turn", summary="已完成本回合经营")'
        self.assertTrue(p.submit('script', {'action': 'write', 'source': source})['ok'])
        result = p.submit('script', {'action': 'run'})
        self.assertTrue(result['ok'], result)
        self.assertFalse(p.call_api('native:diplomacy', 'snapshot')['offers'])
        ends = [r for r in h.audit if r['actor'] == C and r['action'] == 'end_turn']
        self.assertEqual(ends[-1]['receipt']['data']['auto_rejected']['offers'], [offer])

    def test_memory_can_recover_sent_promises_after_replay_is_gone(self):
        h = diplomacy.TestDiplomacyFinish().host(); p = h.player(Q)
        text = '我承诺下回合赠送五十木料以换取共同防御'
        args = {'action': 'send_letter', 'target': C, 'text': text}
        receipt = p.submit('intelligence', args)
        ui = h.ui.llm({}); ui._remember(p.observe())
        rendered = ui.receipt(receipt, args)
        self.assertNotIn(text, rendered)  # 成功回执本来只报告字数与费用。
        calls = [{'function': {'name': 'intelligence', 'arguments': json.dumps(args, ensure_ascii=False)}}]
        history = []
        memory.record(history, 0, memory.action_text({'content': ''}, calls, [receipt], [rendered]))
        restored = json.loads(json.dumps(history))
        matches = memory.search(restored, '五十木料')
        self.assertIn(text, matches[0]['context'])
        self.assertNotIn('"arguments"', matches[0]['context'])

    def test_short_content_keys_work_for_api_trade_build_and_queries(self):
        w = boundary.TestApiToolBoundary().world(); p = w.runtime.player(w, Q)
        self.assertTrue(p.call_api('economy:queries', 'buy', resource='wood', quantity=1)['ok'])
        self.assertTrue(p.call_api('economy:queries', 'sell', resource='wood', quantity=1)['ok'])
        self.assertEqual(p.call_api('economy:queries', 'estimate', kind='farm')['kind'], 'native:farm')
        self.assertTrue(p.call_api('native:buildings', 'build', kind='farm', site='scenario:qc')['ok'])
        self.assertTrue(p.call_api('native:territory', 'land', terrain='plain'))
        w.runtime.content.register('test:conflict', {'test:wood': object()})
        before = w.resources.balance(Q, 'native:wood')
        result = p.call_api('economy:queries', 'buy', resource='wood', quantity=1)
        self.assertFalse(result['ok']); self.assertEqual(result['code'], 'invalid_arguments')
        self.assertIn('键值冲突', result['message'])
        self.assertEqual(w.resources.balance(Q, 'native:wood'), before)

    def test_serialization_fault_preserves_effect_and_audit_on_both_paths(self):
        original = EconomyAPI.buy
        def broken(api, *args, **kwargs):
            original(api, *args, **kwargs)
            return object()
        for api in (False, True):
            h = diplomacy.TestDiplomacyFinish().host(); p = h.player(Q)
            before = h.world.resources.balance(Q, 'native:wood'); count = len(h.audit)
            with patch.object(EconomyAPI, 'buy', broken):
                receipt = (p.call_api('economy:queries', 'buy', resource='wood', quantity=1) if api
                           else p.submit('economy', {'action': 'buy', 'resource': 'wood', 'quantity': 1}))
            self.assertEqual(receipt['code'], 'internal_fault')
            self.assertIn('不要重复执行', receipt['message'])
            self.assertEqual(h.world.resources.balance(Q, 'native:wood'), before + 1)
            self.assertEqual(len(h.audit), count + 1); self.assertEqual(len(h.faults), 1)

    def test_api_service_failure_is_audited_and_not_silently_swallowed(self):
        h = diplomacy.TestDiplomacyFinish().host(); count = len(h.audit)
        with patch.object(h.session, 'api_call', side_effect=RuntimeError('synthetic service fault')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic service fault'):
                h.player(Q).call_api('economy:queries', 'buy', resource='wood', quantity=1)
        self.assertEqual(len(h.audit), count + 1)
        self.assertIn('synthetic service fault', str(h.audit[-1]))

    def test_disabled_single_action_is_hidden_and_blocked_on_both_paths(self):
        w = boundary.TestApiToolBoundary().world()
        w.runtime.activate_actor(w, Q, 'native:huns', site='scenario:qc')
        policy = w.runtime.actor_policy(w, Q)
        policy.declaration['Policy']['Disable'] = ['tool.buy']
        p = w.runtime.player(w, Q)
        schema = next(s for s in p.actions() if s['function']['name'] == 'economy')
        self.assertNotIn('buy', schema['function']['parameters']['properties']['action']['enum'])
        self.assertFalse(p.submit('economy', {'action': 'buy', 'resource': 'wood', 'quantity': 1})['ok'])
        self.assertFalse(p.call_api('economy:queries', 'buy', resource='wood', quantity=1)['ok'])

    def test_foreign_obeys_the_same_module_interfaces_as_direct_api(self):
        w = boundary.TestApiToolBoundary().world(); p = w.runtime.player(w, Q)
        mod = w.runtime.units[w.runtime.capabilities['native:intelligence']].mod
        mod.declaration['Interfaces']['native:vision'] = []
        for call in (lambda: p.call_api('native:intelligence', 'countries'), lambda: p.query('foreign')):
            with self.assertRaisesRegex(ModError, '未声明对象接口调用'):call()

    def test_api_read_contract_does_not_depend_on_tool_table(self):
        w = boundary.TestApiToolBoundary().world(); r = w.runtime
        r.tools.clear(); r.actions.clear(); r.api_readonly.clear()
        self.assertFalse(r.api_is_write('economy:queries', 'balance'))
        self.assertTrue(r.api_is_write('economy:queries', 'buy'))
        self.assertIn('stocks', r.player(w, Q).call_api('economy:queries', 'balance'))

    def test_tool_cannot_reclassify_a_read_api_as_write(self):
        r = Runtime.load('mods.toml')
        units = list(r.units.values())
        army = next(u.mod for u in units if u.name == 'native:army')
        army.declaration['PlayerAPI']['ReadOnly'].remove('view')
        with self.assertRaisesRegex(ModError, '只读工具.*写 API'):
            Runtime(units, r.distribution)

    def test_api_continuation_cannot_repeat_a_purchase(self):
        h = diplomacy.TestDiplomacyFinish().host(); p = h.player(Q)
        before = h.world.resources.balance(Q, 'native:wood')
        def twice(player, capability, method, arguments, execute):
            execute()
            return execute()
        with patch.object(h.session, 'api_call', twice):
            with self.assertRaisesRegex(RuntimeError, 'only once'):
                p.call_api('economy:queries', 'buy', resource='wood', quantity=1)
        self.assertEqual(h.world.resources.balance(Q, 'native:wood'), before+1)
        self.assertIn('only once', str(h.audit[-1]))

    def test_query_derives_new_parameters_and_required_constraints(self):
        w = boundary.TestApiToolBoundary().world(); r = w.runtime
        tool = Tool('sample', lambda world, actor, weather: weather, owner='native:query',
                    parameters={'type': 'object', 'properties': {'weather': {'type': 'string', 'enum': ['rain']}},
                                'required': ['weather'], 'additionalProperties': False},
                    read_only=True, panel=True, description='合成天气')
        r._register_tool(tool, r.units[r.capabilities['native:query']].mod)
        dispatcher = query.build(r, [tool]); params = dispatcher.schema()['function']['parameters']
        self.assertEqual(params['properties']['weather']['enum'], ['rain'])
        self.assertEqual(params['oneOf'][0]['required'], ['panel', 'weather'])
        self.assertEqual(dispatcher.call(w, Q, {'panel': 'sample', 'weather': 'rain'}), 'rain')

    def test_fixed_declarations_come_from_toml_and_drift_is_rejected(self):
        class Declared(GameMod):
            declaration = {'State': {'Owns': []}, 'Capabilities': {'Needs': [], 'Provides': ['test:cap']}}
            def mixins(self):return ()
        r = Runtime([Unit('test:mod', Declared())])
        self.assertIn('test:cap', r.capabilities)
        class Drifted(Declared):
            def needs(self):return ('test:missing',)
        with self.assertRaisesRegex(ModError, '声明与实现漂移'):
            Runtime([Unit('test:mod', Drifted())])

    def test_ui_uses_domain_previews_and_preview_does_not_mutate_world(self):
        h = diplomacy.TestDiplomacyFinish().host(); w = h.world
        site = next(s for s in w.buildings.snapshot().sites if s.owner == Q)
        w.buildings.seed(site.id, 'native:wood-power-plant')
        w.buildings.seed(site.id, 'native:equipment-factory')
        unit = w.units.spawn(Q, 'native:infantry', *site.position); unit.take_damage(40)
        before = w.runtime.save_mod_state(w)
        obs = h.player(Q).observe(); preview = obs['facts']['econ']['preview']
        self.assertEqual(before, w.runtime.save_mod_state(w))
        self.assertEqual(preview['military']['healing_equipment'], 1)
        cues = h.ui.llm({}).dashboard(obs)
        self.assertEqual(cues['energy_capacity'], preview['energy'])
        self.assertEqual(cues['planned_regular_inputs'], preview['inputs'])
        # 即使呈现目录缺失，公开预演仍是唯一事实来源，UI 不重新实施规则。
        slim = deepcopy(obs)
        for key in ('unit_catalog', 'building_catalog', 'army'):slim['facts'].pop(key, None)
        self.assertEqual(h.ui.llm({}).dashboard(slim)['planned_regular_inputs'], preview['inputs'])
        equipment = w.resources.balance(Q, 'native:equipment')
        w.military.supply()
        self.assertEqual(equipment-w.resources.balance(Q, 'native:equipment'), preview['military']['healing_equipment'])
        self.assertEqual(w.units.by_id(unit.id, Q).hp, 90)


if __name__ == '__main__':unittest.main()
