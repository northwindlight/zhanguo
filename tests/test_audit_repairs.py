"""Behavioral closure of the implementation audit, using synthetic worlds only."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.deltas import difference,apply
from host import GameHost
from clients import context
from clients.llm import LLMClient
from mods.native.vision.mod import Vision
from tests.test_player_clients import FakeBackend,call
from tests import test_world_domains as domains
from tests.test_world_domains import Q,C,Z,Y,B


class AuditRepairs(unittest.TestCase):
    def world(self):
        f=domains.TestWorldDomains();w=f.world()
        for actor,pos in ((Q,(1,1)),(C,(2,1)),(Z,(6,6)),(Y,(6,1))):f.site(w,actor+'/capital',actor,*pos)
        return f,w

    def test_negative_treasury_zero_debits_and_free_diplomacy(self):
        f,w=self.world();f.found(w)
        w.resources.adjust_cash('native:gold',{Q:-w.resources.balance(Q,'native:gold')-50})
        before=w.resources.balance(Q,'native:gold')
        w.resources.exchange(Q,{'native:gold':0},{})
        f.call(w,Q,'bloc_rename',name='新盟');f.cycle(w)
        self.assertEqual(w.diplomacy.alliance(Q).name,'新盟')
        self.assertEqual(w.resources.balance(Q,'native:gold'),before)
        with self.assertRaises(ValueError):f.call(w,Q,'declare_war',target=C)

    def test_barracks_stack_and_recruit_affordable_partial_count(self):
        f,w=self.world();site=Q+'/capital'
        w.buildings.seed(site,'native:barracks',3)
        w.resources.consume(Q,'native:equipment',w.resources.balance(Q,'native:equipment')-11)
        recruits=f.call(w,Q,'recruit',site=site,count=99)
        self.assertEqual(len(recruits),2)
        self.assertEqual(w.resources.balance(Q,'native:equipment'),1)
        self.assertEqual(w.buildings.effects(site,'native:barracks')['recruit_cap'],3)

    def test_defender_cannot_disband_single_or_in_atomic_batch(self):
        f,w=self.world();f.declare(w)
        attacker=w.military.seed(Q,'native:infantry',1,1)[0]
        defender=w.military.seed(C,'native:infantry',2,1)[0]
        other=w.military.seed(C,'native:infantry',2,1)[0]
        f.call(w,Q,'attack',ids=[attacker.id],x=2,y=1)
        before=f.save(w)
        for args in ({'aid':defender.id},{'ids':[other.id,defender.id]}):
            with self.assertRaises(ValueError):f.call(w,C,'disband',**args)
        self.assertEqual(f.save(w),before)

    def test_coordinate_forms_keep_zero_base_and_unowned_names_private(self):
        f,w=self.world();p=w.runtime.player(w,Q)
        for place in ('1 1','1,1','(1,1)','[1,1]'):
            self.assertEqual(p.query('tile',{'place':place})['position'],[1,1])
        for row in p.query('view'):
            if row['owner'] is None:self.assertEqual(row['name'],'')
        name=w.territory.cell(0,0).tile.name
        with self.assertRaises(ValueError):w.territory.resolve(name)
        self.assertTrue(w.territory.cell(0,0).tile.name)

    def test_war_joins_existing_front_and_followers_cannot_make_separate_peace(self):
        f,w=self.world()
        f.call(w,Z,'propose_defense',target=Q);f.cycle(w)
        offer=w.diplomacy.snapshot(Q).offers[0]
        f.call(w,Q,'accept_proposal',offer=offer.id);f.cycle(w)
        f.declare(w)
        f.call(w,Z,'declare_war',target=C);f.cycle(w)
        wars=w.diplomacy.snapshot(Q).wars
        self.assertEqual(len(wars),1);self.assertEqual(wars[0].attackers,(Q,Z))
        with self.assertRaises(ValueError):f.call(w,Z,'offer_peace',target=C,kind='white')

    def test_defender_obligations_break_conflicting_pacts(self):
        f,w=self.world()
        w.diplomacy.seed_pact('guarantee',Z,C)
        w.diplomacy.seed_pact('defense',Z,Q)
        f.declare(w)
        self.assertTrue(w.diplomacy.war_between(Q,Z))
        self.assertFalse(any({p.a,p.b}=={w.diplomacy.entity(Q),w.diplomacy.entity(Z)} for p in w.diplomacy.snapshot(Q).pacts))

    def test_two_applicants_can_share_a_ballot_window(self):
        f,w=self.world();f.found(w)
        f.call(w,C,'bloc_join',bloc=B);f.call(w,Y,'bloc_join',bloc=B);f.cycle(w)
        votes=w.diplomacy.snapshot(Q).votes;self.assertEqual(len(votes),2)
        for vote in votes:
            f.call(w,Q,'vote',vote=vote.id,accept=False)
            f.call(w,Q,'vote',vote=vote.id,accept=True)
            f.call(w,Z,'vote',vote=vote.id,accept=None)
        f.cycle(w)
        self.assertEqual(set(w.diplomacy.alliance(Q).members),{Q,Z,C,Y})
        with self.assertRaises(ValueError):f.call(w,Q,'bloc_leave')

    def test_delivery_at_next_boundary_and_sender_death_does_not_recall_gifts(self):
        f,w=self.world();before=w.resources.balance(C,'native:food')
        sent=f.call(w,Q,'gift',target=C,resource='native:food',quantity=3)
        self.assertEqual(sent.arrive,1)
        # Sender dies while the courier is already in flight.
        w.intelligence.resolve();w.territory.release(Q);w.nations.extinguish(Q,'synthetic')
        w.intelligence.deliver()
        self.assertEqual(w.resources.balance(C,'native:food'),before+3)

    def test_messages_do_not_disclose_other_witnesses_and_spy_sees_construction(self):
        f,w=self.world();w.intelligence.emit('test','仅秦楚',parties=(Q,C))
        for row in w.runtime.player(w,Q).events()['items']:self.assertFalse(row.get('recipients'))
        w.buildings.seed(C+'/capital','native:barracks',completed=False)
        tiles=w.intelligence._map(C)
        tile=next(t for t in tiles if t['position']==[2,1])
        self.assertEqual(tile['construction']['native:barracks'],1)
        self.assertNotIn([6,6],[t['position'] for t in tiles])

    def test_row_delta_reconstructs_exact_state_and_is_smaller(self):
        before={'army':[{'owner':Q,'id':i,'hp':100,'x':1,'y':1} for i in range(220)]}
        after=deepcopy(before);after['army'][3]['hp']=45;after['army'].pop(10)
        changes=difference(before,after)
        self.assertEqual(apply(before,changes),after)
        self.assertLess(len(json.dumps(changes)),len(json.dumps(after))//10)

    def test_trade_receipt_reports_remaining_stock_and_no_batch_message_is_appended(self):
        h=GameHost.new({'map_size':12,'seed':73,'nations':[{'id':Q,'name':'秦','client':'rule'}]})
        p=h.player(Q)
        ui=h.ui.llm(h.cfg)
        receipt=p.submit('economy', {'action': 'buy', 'resource':'wood','quantity':2});self.assertTrue(receipt['ok'])
        stocks={s['id']:s['amount'] for s in p.observe()['facts']['econ']['stocks']}
        self.assertIn(f"余{stocks['native:wood']}",ui.receipt(receipt,{'action':'buy','resource':'wood','quantity':2}))
        # 批次之间不再追加上下文：模型靠自己的对话历史与回执。
        self.assertFalse(hasattr(ui,'refresh'))

    def test_visibility_scan_is_shared_within_observation_and_invalidates_after_actions(self):
        f,w=self.world();p=w.runtime.player(w,Q)
        original=Vision.positions;calls=[]
        def counted(view,actor):calls.append(actor);return original(view,actor)
        with patch.object(Vision,'positions',counted):p.observe()
        self.assertEqual(calls.count(Q),1)
        self.assertFalse(w.runtime.object_call(w,'native:vision','visible',Q,6,6))
        w.territory.transfer(Z+'/capital',Q)
        self.assertTrue(w.runtime.object_call(w,'native:vision','visible',Q,6,6))

    def test_state_payload_is_allowlisted_and_queries_recover_omitted_details(self):
        f,w=self.world();p=w.runtime.player(w,Q);obs=p.observe();obs['facts']['accidental_new_panel']='NEVER_AUTO_INJECT'
        ui=GameHost.new({'map_size':8,'nations':[{'id':Q,'name':'秦','client':'llm'}]}).ui.llm({})
        ui.rules(p.rules('all'));text,_=ui.state(obs,[])
        # 新增面板不会自动变成常驻载荷；规则正文也不进 system。
        self.assertNotIn('NEVER_AUTO_INJECT',text);self.assertNotIn('costs_by_level',ui.system())
        self.assertIn('【国力】',text)
        self.assertTrue(p.query('building_catalog'));self.assertTrue(p.query('land'))
        self.assertLess(context.est_tokens(text),12000)

    def test_settlement_is_readonly_ranked_by_recorded_consumption_and_rejects_old_format(self):
        from settlement import load_save,settle,main
        h=GameHost.new({'map_size':12,'seed':73,'nations':[{'id':Q,'name':'秦','client':'rule'},{'id':C,'name':'楚','client':'rule'}]})
        h.cycle()
        with TemporaryDirectory() as directory:
            save=Path(directory)/'synthetic.json';h.save(save);before=save.read_bytes()
            data,world=load_save(save);rows=settle(data,world)
            self.assertEqual([r['total'] for r in rows],sorted([r['total'] for r in rows],reverse=True))
            self.assertEqual(save.read_bytes(),before)
            old=Path(directory)/'old.json';old.write_text('{"version":1}')
            with self.assertRaises(ValueError):load_save(old)

    def test_long_accounting_window_preserves_consumption_and_resume(self):
        f,w=self.world();w.military.seed(Q,'native:infantry',1,1)
        total=0
        for period in range(65):
            result=f.cycle(w)
            total+=next(r.spending.known for r in result['economy_turns'] if r.owner==Q)
            self.assertAlmostEqual(sum(v.known for v in w.economy.spending(Q).values()),total)
            if period==39:w=f.restore(w)
        saved=w.economy.to_save()
        self.assertEqual(saved['cutoff'],40)
        self.assertTrue(all(r['period']>=40 for r in saved['turns']))
        self.assertLessEqual(len(w.economy.turns(Q)),29)
        self.assertEqual(len(w.economy.reports(Q)),6)
        other=f.restore(w);f.cycle(w);f.cycle(other)
        self.assertEqual(f.save(w),f.save(other))

    def test_saved_audit_is_bounded_and_archive_keeps_the_evicted_records(self):
        from engine.session import AUDIT_LIMIT
        cfg={'map_size':8,'seed':73,'nations':[{'id':Q,'name':'秦','client':'rule'}]}
        h=GameHost.new(cfg);p=h.player(Q)
        for _ in range(AUDIT_LIMIT+5):p.submit('government')
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.json';h.save(path)
            data=json.loads(path.read_text())
            self.assertEqual(len(data['host']['audit']),AUDIT_LIMIT)
            archives=list(path.with_suffix('.json.audit').glob('*/*.json'))
            self.assertEqual(len(archives),1)
            self.assertEqual(len(json.loads(archives[0].read_text())['records']),AUDIT_LIMIT+5)
            loaded=GameHost.load(path,cfg);loaded.player(Q).submit('government')
            self.assertEqual(loaded.audit[-1]['id'],AUDIT_LIMIT+6)

    def test_compression_uses_bounded_actual_prefix_and_no_action_tools(self):
        f,w=self.world();backend=FakeBackend()
        client=LLMClient({'ctx_full_turns':1,'ctx_window':16384,'max_tokens':1024},backend=backend)
        for _ in range(6):client.play(w.runtime.player(w,Q));f.cycle(w)
        prompt,tools=backend.compressions[0]
        self.assertLess(context.messages_tokens(prompt)+1500+context.SAFETY_TOKENS,16384)
        self.assertEqual(tools,[])
        self.assertNotIn('"messages":',prompt[-1]['content'])

    def test_oversized_compression_instruction_falls_back_without_provider_call(self):
        f,w=self.world();backend=FakeBackend()
        client=LLMClient({'ctx_window':4096,'ctx_full_turns':1,'ctx_compact_tokens':4096},backend=backend)
        client.state.replay=[{'turn':i,'messages':[{'role':'user','content':'合成回放'}]} for i in range(6)]
        client.state.summaries=[{'turn':i,'text':'合成小结'} for i in range(6)]
        client.state.long_memory='需要保留的原长期记忆'
        notifications=[]
        client._compact(w.runtime.player(w,Q),[],context.make_plan(client.cfg),[],notifications.append)
        self.assertFalse(backend.compressions)
        self.assertEqual(client.state.long_memory,'需要保留的原长期记忆')
        self.assertTrue(any('ValueError' in text for text in notifications))

    def test_vote_keeps_confirmed_next_period_contract_without_implicit_ballot(self):
        f,w=self.world();f.found(w)
        f.call(w,Q,'guarantee',target=C);f.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0]
        self.assertEqual(vote.deadline,w.government.period())
        self.assertEqual(vote.ballots,())
        f.call(w,Q,'vote',vote=vote.id,accept=True)
        f.call(w,Q,'vote',vote=vote.id,accept=None)
        self.assertEqual(len(w.diplomacy.snapshot(Q).votes),1)
        f.cycle(w)
        self.assertEqual(w.diplomacy.snapshot(Q).pacts,())

    def test_dissolved_alliance_cannot_execute_approved_vote_as_an_independent_nation(self):
        f,w=self.world();f.found(w)
        f.call(w,Q,'guarantee',target=C);f.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0]
        f.call(w,Z,'vote',vote=vote.id,accept=True)
        f.call(w,Q,'bloc_dissolve');f.cycle(w)
        self.assertIsNone(w.diplomacy.alliance(Q))
        self.assertEqual(w.diplomacy.snapshot(Q).pacts,())
        self.assertTrue(any('Alliance dissolved' in r.message for r in w.diplomacy.snapshot(Q).results))

    def test_snapshot_budget_includes_large_events_indexes_and_optional_fact_tables(self):
        f,w=self.world();p=w.runtime.player(w,Q);obs=p.observe()
        obs['facts']['army']*=600
        obs['facts']['intel']=[{'id':i,'source':C,'kind':'letter','sampled':0} for i in range(300)]
        ui=GameHost.new({'map_size':8,'nations':[{'id':Q,'name':'秦','client':'llm'}]}).ui.llm({'model_state_tokens':1024})
        events=[{'id':i,'text':'近讯'*300,'period':0,'kind':'test'} for i in range(50)]
        text,_=ui.state(obs,events)
        self.assertLessEqual(context.est_tokens(text),1024)
        self.assertIn('载荷预算',text)

    def test_step_limit_ends_through_normal_gate_and_preserves_replay(self):
        from clients.llm import assert_paired
        f,w=self.world()
        backend=FakeBackend([{'content':'本期仅提交战略','tool_calls':[call('p','plan',text='积蓄资源')]}])
        client=LLMClient({'max_steps':1},backend=backend)
        receipts=client.play(w.runtime.player(w,Q))
        self.assertTrue(receipts[-1]['ok']);self.assertEqual(receipts[-1]['action'],'end_turn')
        self.assertFalse(w.government.can_act(Q))
        self.assertTrue(client.state.summaries);assert_paired(client.state.replay[0]['messages'])

    def test_observer_letter_has_private_mailbox_identity_and_roundtrips(self):
        f,w=self.world();w.intelligence.send(Q,'请留意边疆')
        message=w.intelligence.messages(Q)[0]
        self.assertEqual(message.source,'observer');self.assertEqual(message.payload['sender'],'神秘人')
        self.assertEqual(w.runtime.player(w,Q).query('intel',{'kind':'observer-letter'})[0]['source'],'observer')
        self.assertEqual(w.intelligence.messages(C),())
        other=f.restore(w)
        self.assertEqual(other.intelligence.messages(Q),w.intelligence.messages(Q))

    def test_domain_history_survives_join_and_is_archived_after_successful_save(self):
        cfg={'map_size':16,'seed':73,'nations':[{'id':Q,'name':'秦','client':'llm'},{'id':C,'name':'楚','client':'llm'}]}
        h=GameHost.new(cfg)
        for actor in h.actors():h.clients[actor]=LLMClient({'ctx_compact':'off','ctx_full_turns':1},backend=FakeBackend())
        for _ in range(31):h.cycle()
        pending=len(h.world.economy.pending_archive());self.assertGreater(pending,0)
        h.add('新国')
        self.assertEqual(len(h.world.economy.pending_archive()),pending)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.json';h.save(path)
            archive=path.with_suffix('.json.audit')
            histories=[json.loads(p.read_text()) for p in archive.glob('*/*/*.json')]
            economic=next(r for r in histories if r['module']=='native:economy')
            self.assertEqual(len(economic['records']),pending)
            self.assertFalse(h.world.economy.pending_archive())
            resumed=GameHost.load(path,cfg)
            self.assertEqual(resumed.world.economy.spending(Q),h.world.economy.spending(Q))

    def test_finale_uses_no_tools_and_one_speaker_failure_keeps_other_speech(self):
        from settlement import run_chat
        cfg={'map_size':12,'nations':[{'id':Q,'name':'秦','client':'llm'},{'id':C,'name':'楚','client':'llm'}]}
        h=GameHost.new(cfg)
        from settlement import settle
        data=h.to_save();rows=settle(data,h.world);calls=[]
        class Speaker:
            def complete_text(self,messages,item,tools):
                calls.append((item['id'],tools))
                if item['id']==Q:raise RuntimeError('synthetic provider fault')
                return '已完成复盘',{}
        transcript=run_chat(data,rows,cfg,1,log=lambda _:None,backend_factory=lambda _:Speaker())
        self.assertTrue(any('发言失败' in text for text in transcript))
        self.assertTrue(any('已完成复盘' in text for text in transcript))
        self.assertTrue(all(tools==[] for _,tools in calls));self.assertEqual(data,h.to_save())

    def test_generated_manual_and_settlement_sequence_do_not_drift(self):
        from docs.generate_manual import render,DEFAULT_OUTPUT
        from engine.runtime import Runtime
        runtime=Runtime.load('mods.toml')
        self.assertEqual(DEFAULT_OUTPUT.read_text(),render(runtime))
        self.assertEqual(runtime.stage_order,[
            'native:diplomacy.tally','native:buildings.produce','native:diplomacy.resolve',
            'native:intelligence.dispatch','native:army.supply','native:army.battle',
            'native:army.withdraw','native:army.claims','native:succession.settle',
            'native:army.repatriate','native:market.settle','native:buildings.complete',
            'native:bank.settlement','native:economy.reports','native:intelligence.deliver',
            'native:intelligence.events','native:government.advance'])

    def test_fractional_spending_checkpoint_preserves_exact_historical_totals(self):
        from types import SimpleNamespace
        from mods.native.economy_reports.objects import Economy
        from mods.native.economy_reports.contracts import EconomicFact,Valuation
        def create():
            return Economy(10,'native:gold',
                lambda amounts:tuple(Valuation(k,n,1.1,n*1.1,'synthetic') for k,n in amounts.items()),
                lambda:(Q,),lambda owner:(SimpleNamespace(id='native:wood',amount=100),),lambda owner:{})
        def facts(period):
            return (EconomicFact(f'native:fraction/{period}',Q,period,
                'construction' if period%2 else 'military',(('native:wood',1),)),)
        original=create()
        for period in range(40):original.settle(period,facts(period))
        other=create();other.restore(json.loads(json.dumps(original.to_save())))
        self.assertEqual(other.to_save(),original.to_save())
        original.settle(40,facts(40));other.settle(40,facts(40))
        self.assertEqual(other.to_save(),original.to_save())

    def test_logistics_preview_matches_settlement_without_mutating_world(self):
        f,w=self.world();w.military.seed(Q,'native:infantry',1,1,3)
        w.resources.consume(Q,'native:supply',w.resources.balance(Q,'native:supply'))
        before=f.save(w);preview=w.runtime.player(w,Q).query('logistics')
        self.assertEqual(f.save(w),before)
        reports,_=w.military.supply()
        row=next(row for row in reports if row[0]==Q)
        self.assertEqual((preview['need'],preview['used'],preview['shortfall']),row[1:4])
        self.assertTrue(preview['starvation_damage_per_unit'])


if __name__=='__main__':unittest.main()
