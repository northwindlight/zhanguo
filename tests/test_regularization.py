"""全部用合成世界和临时存档，验证公共入口的正规化时序。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
import mp
import mp_ai


def world():
    w = mp.World(size=24, seed=3, nations=['秦', '楚', '齐', '燕'])
    w.begin_turn()
    return w


def initial_bloc(w):
    # 只预置测试局，不走玩家公开命令；公共队列另有完整往返测试。
    w._apply_propose_bloc('秦', '北盟', ['楚', '齐'])
    pid = w.proposals[-1]['id']
    w._apply_accept_pact('楚', pid)
    w._apply_accept_pact('齐', pid)


class TestDiplomacyQueue(unittest.TestCase):
    def test_war_is_not_active_on_submission(self):
        w = world()
        before = w.res('秦', '黄金')
        ok, receipt = w.declare_war('秦', '燕')
        self.assertTrue(ok, receipt)
        self.assertIn('待生效', receipt)
        self.assertFalse(w.war_between('秦', '燕'))
        self.assertEqual(w.res('秦', '黄金'), before-10)
        self.assertEqual(w.player_state('秦')['current']['resources_reserved'], {'黄金': 10})
        w.resolve_turn()
        self.assertTrue(w.war_between('秦', '燕'))
        self.assertEqual(w.command_results[-1]['status'], '已生效')
        self.assertEqual(w.diplomacy_queue, [])

    def test_aliases_and_direct_engine_use_one_queue(self):
        w = world()
        mp_ai.execute(w, '秦', '宣战', {'to': '燕'})
        before = w.res('秦', '黄金')
        w.declare_war('秦', '燕')
        self.assertEqual(w.res('秦', '黄金'), before)
        self.assertEqual(len(w.diplomacy_queue), 1)
        self.assertFalse(w.wars)

    def test_gift_reserves_amount_and_fee_together(self):
        w = world()
        w.nations['秦'].res['黄金'] = 15
        self.assertFalse(w.gift('秦', '燕', '黄金', 10)[0])
        self.assertEqual(w.res('秦', '黄金'), 15)
        w.nations['秦'].res['黄金'] = 25
        self.assertTrue(w.gift('秦', '燕', '黄金', 10)[0])
        self.assertEqual(w.res('秦', '黄金'), 5)
        target = w.res('燕', '黄金')
        w.resolve_turn()
        self.assertEqual(w.res('燕', '黄金'), target)  # 本轮电网不足，不产生市政厅收入
        w.begin_turn()
        self.assertEqual(w.res('燕', '黄金'), target+10)
        self.assertEqual(w.command_results[-1]['delivery']['status'], '已送达')

    def test_failed_execution_refunds_and_has_result(self):
        w = world()
        initial_bloc(w)
        before = w.res('楚', '黄金')
        self.assertTrue(w.bloc_leave('楚')[0])
        w._apply_declare_war('燕', '秦')
        w.resolve_turn()
        self.assertEqual(w.command_results[-1]['status'], '失败')
        self.assertEqual(w.res('楚', '黄金'), before)
        self.assertIsNotNone(w.bloc_of('楚'))

    def test_peace_precedes_battle(self):
        w = world()
        w._apply_declare_war('秦', '燕')
        w._apply_offer_peace('秦', '燕', 'white')
        oid = w.peace_offers[-1]['id']
        self.assertTrue(w.accept_peace('燕', oid)[0])
        seen = []
        original = w._resolve_battles
        def battles():
            seen.append(w.war_between('秦', '燕'))
            return original()
        w._resolve_battles = battles
        w.resolve_turn()
        self.assertEqual(seen, [False])

    def test_alliance_found_is_queued_through_acceptance(self):
        w = world()
        self.assertTrue(w.propose_bloc('秦', '北盟', ['楚', '齐'])[0])
        self.assertFalse(w.proposals)
        w.resolve_turn(); w.begin_turn()
        oid = w.proposals[-1]['id']
        self.assertTrue(w.accept_pact('楚', oid)[0])
        self.assertTrue(w.accept_pact('齐', oid)[0])
        self.assertIsNone(w.bloc_of('秦'))
        w.resolve_turn()
        self.assertEqual(w.bloc_of('秦')['members'], ['秦', '楚', '齐'])

    def test_spy_and_letter_are_not_sent_until_diplomacy(self):
        w = world()
        self.assertTrue(w.spy('秦', '燕')[0])
        self.assertTrue(w.send_mail('秦', '楚', '你好')[0])
        self.assertFalse(w.spy_pending)
        self.assertFalse(w.mail_pending)
        gold = w.res('秦', '黄金')
        w.resolve_turn()
        self.assertEqual(w.res('秦', '黄金'), gold)
        self.assertEqual(w.spy_pending[-1]['arrive'], w.turn+mp.SPY_TURNS)
        w.begin_turn()
        self.assertEqual(w.mailbox['楚'][-1]['text'], '你好')

    def test_huns_cannot_bypass_tool_restrictions(self):
        w = world(); w.polity['秦'] = 'huns'
        self.assertFalse(w.gift('秦', '燕', '木头', 1)[0])
        self.assertIn('匈奴', mp_ai.execute(w, '秦', '馈赠', {'to':'燕','good':'木头','qty':1}))
        self.assertFalse(w.diplomacy_queue)


class TestVoting(unittest.TestCase):
    def test_next_turn_only_no_automatic_yes_and_no_veto(self):
        w = world(); initial_bloc(w)
        w.declare_war('秦', '燕'); w.resolve_turn()
        v = w.votes[-1]
        self.assertEqual(v['votes'], {})
        self.assertFalse(w.cast_vote('秦', v['id'], True)[0])
        w.begin_turn()
        for name, choice in [('楚', True), ('齐', True), ('秦', False)]:
            self.assertTrue(w.cast_vote(name, v['id'], choice)[0])
            self.assertTrue(w.votes)
            self.assertFalse(w.wars)
        w.resolve_turn()
        self.assertFalse(w.votes)
        self.assertTrue(w.war_between('楚', '燕'))

    def test_unvoted_abstains_at_tally_only(self):
        w = world(); initial_bloc(w)
        w.bloc_join('燕', '北盟'); w.resolve_turn(); w.begin_turn()
        v = w.votes[-1]
        w.cast_vote('楚', v['id'], True)
        self.assertIsNone(w.bloc_of('燕'))
        w.resolve_turn()
        self.assertEqual(w.bloc_of('燕')['name'], '北盟')
        news = w.events_for('燕', 50)
        self.assertTrue(any('通过' in s for s in news))
        self.assertFalse(any('赞成1/反对' in s for s in w.events_for('齐', 50)))

    def test_change_vote_and_silence_fails(self):
        w = world(); initial_bloc(w)
        w.bloc_join('燕', '北盟'); w.resolve_turn(); w.begin_turn()
        v = w.votes[-1]
        w.cast_vote('楚', v['id'], True)
        w.cast_vote('楚', v['id'], None)
        w.resolve_turn()
        self.assertIsNone(w.bloc_of('燕'))
        self.assertFalse(w.votes)


class TestSaveAndFacts(unittest.TestCase):
    def test_pending_commands_resume_once(self):
        w = world(); w.declare_war('秦', '燕')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'synthetic.json'; w.save(path)
            r = mp.World.load(path)
            self.assertEqual(w.diplomacy_queue, r.diplomacy_queue)
            r.resolve_turn(); r.save(path)
            again = mp.World.load(path)
            again.begin_turn(); again.resolve_turn()
            self.assertEqual(len(again.command_results), 1)
            self.assertEqual(len(again.wars), 1)

    def test_dependency_change_and_missing_metadata_rejected(self):
        w = world()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'synthetic.json'; w.save(path)
            original = json.loads(path.read_text())
            for change in ('version', 'requires', 'missing'):
                data = copy.deepcopy(original)
                if change == 'missing': data.pop('mod_dependencies')
                elif change == 'requires': data['mod_dependencies']['mods'][0]['requires'] = ['other']
                else: data['mod_dependencies']['mods'][0]['version'] = 'different'
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(mp.SaveFormatError, '拒绝加载'):
                    mp.World.load(path)

    def test_current_facts_do_not_use_old_player_memory(self):
        w = world()
        w.plans['秦'] = {'turn': 1, 'text': '燕已经断粮，这是我的猜测'}
        s = w.player_state('秦')
        self.assertNotIn('燕已经断粮', str(s['current']))
        self.assertEqual(s['player_plan']['source'], '玩家计划与假设')
        self.assertEqual(s['changes'], w.player_state('秦', since_event=0)['changes'])
        self.assertEqual(w.player_state('秦', since_event=s['event_cursor'])['changes'], [])

    def test_targets_obey_visibility(self):
        w = world()
        expected = {(x+1,y+1) for (x,y), tile in w.tiles.items()
                    if tile['owner'] != '秦' and tile['buildings'].get('市政厅') and w.visible_to('秦',x,y)}
        actual = {(t['x'],t['y']) for t in w.player_state('秦')['current']['visible_townhalls']}
        self.assertEqual(actual, expected)

    def test_truce_inclusive_boundary(self):
        w = world(); w.set_truce('秦','楚',3)
        w.turn=3; self.assertEqual(w.active_truce('秦','楚'),3)
        w.turn=4; self.assertIsNone(w.active_truce('秦','楚'))


class TestPendingIntegrity(unittest.TestCase):
    def test_rename_keeps_pending_vote_and_command_references(self):
        w = world()
        initial_bloc(w)
        w.bloc_join('燕', '北盟')
        w.resolve_turn(); w.begin_turn()
        vote = w.votes[-1]
        w.cast_vote('楚', vote['id'], True)
        w.cast_vote('齐', vote['id'], True)
        w.bloc_rename('秦', '新盟')
        w.send_mail('楚', '齐', '改名后的信')
        w.resolve_turn()
        self.assertEqual(w.bloc_of('燕')['name'], '新盟')
        self.assertEqual(w.command_results[-1]['status'], '已生效')
        w.begin_turn()
        self.assertEqual(w.mailbox['齐'][-1]['text'], '改名后的信')

    def test_duplicate_pending_ids_are_rejected(self):
        w = world()
        w.send_mail('秦', '楚', '第一封信')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'synthetic.json'
            w.save(p)
            data = json.loads(p.read_text())
            data['diplomacy_queue'].append(copy.deepcopy(data['diplomacy_queue'][0]))
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(mp.SaveFormatError, '请求编号重复'):
                mp.World.load(p)

    def test_blind_military_failure_reports_consumed_allowance(self):
        w = world()
        own = w.own_tiles('秦')[0]
        w.cheat('秦', 骑=1)
        army = w.nation_armies('秦')[0]
        army['moved_turn'] = -1
        army['born_turn'] = -1
        target = next(xy for xy in w.tiles if w.tiles[xy]['owner'] == '楚')
        from unittest.mock import patch
        with patch.object(w, 'visible_to', return_value=False):
            ok, msg = w.move('秦', army['id'], *target)
        self.assertFalse(ok)
        self.assertIn('位置未改变', msg)
        self.assertIn('本次额度已消耗', msg)
