"""Distribution-mounted programs and diplomacy consequences, entirely offline."""
from copy import deepcopy
from clients.model_view import model_view_for
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import io
import json

from engine.runtime import ModError
from engine.runtime import Runtime, Unit
from engine.scenario_mod import ScenarioMod
from host import GameHost,SaveFormatError
from scenarios.eight_nations import build,build_config,IDS,NATIONS,CHARTER
from tests import test_session_api as counter
from tests import test_world_domains as domains
from tests.test_world_domains import Q,C,Z,Y,B


class CounterVictory:
    """A different game, different world, and arbitrary award/event structure."""
    text='甲乙计数局：第二期结算后发放自定义奖项。'
    def __init__(self,world,trigger_victory):self.world=world;self.trigger=trigger_victory
    def __call__(self):
        if self.world.counter.period==2:
            self.trigger({'festival':'计数庆典','awards':{'counter:left':{'stars':7}},
                          'totals':dict(self.world.counter.rows)})


class CounterScenarioMod(ScenarioMod):
    text='轻量计数剧本：第二期触发奖项。'

    def on_settlement(self,world,trigger_victory):
        period=self.runtime.object_call(world,'example:counter','clock')
        if period==2:trigger_victory({'type':'counter.hook','period':period})


class VictoryHooks(TestCase):
    def host(self,names=NATIONS,experimental=False):
        cfg=build_config({'nations':[{'client':'rule'}]})
        if experimental:cfg['mods']='mods.experimental.toml'
        h=build(nations=names,config=cfg)
        # Keep real client save contracts while disabling all autonomous moves.
        for client in h.clients.values():client.play=lambda player,**kwargs:[]
        return h

    def submit(self,h,actor,action,**args):
        umbrella={'guarantee':'relations','cancel_guarantee':'relations','propose_defense':'relations',
                  'break_defense':'relations','accept_proposal':'relations','reject_proposal':'relations',
                  'bloc_found':'relations','bloc_join':'relations','bloc_leave':'relations',
                  'bloc_dissolve':'relations','bloc_transfer':'relations','bloc_rename':'relations',
                  'declare_war':'relations','offer_peace':'relations','accept_peace':'relations',
                  'reject_peace':'relations','vote':'relations'}.get(action)
        receipt=(h.player(actor).submit(umbrella,{'action':action,**args}) if umbrella
                 else h.player(actor).submit(action,args))
        self.assertTrue(receipt['ok'],receipt)
        return receipt

    def invite(self,h,members=None,chief='周',name='周天下'):
        members=tuple(members or h.actors());leader=IDS[chief]
        self.submit(h,leader,'bloc_found',bloc='scenario:mandate',name=name,tos=[a for a in members if a!=leader])
        h.cycle();self.assertIsNone(h.victory_event)
        offer=h.world.diplomacy.snapshot(leader).offers[0]
        return offer

    def accept(self,h,offer):
        for actor in offer.parties:
            if actor!=offer.actor:self.submit(h,actor,'accept_proposal',offer=offer.id)

    def test_eight_member_victory_after_settlement_and_terminal_resume(self):
        for experimental in (False,True):
            with self.subTest(experimental=experimental):
                h=self.host(experimental=experimental);offer=self.invite(h)
                self.assertEqual(len(h.world.diplomacy.to_save()['pacts']),7)
                self.accept(h,offer)
                self.assertIsNone(h.victory_event);self.assertFalse(h.finished())
                h.cycle();event=h.victory_event
                self.assertEqual(event['type'],'eight_nations.zhou_tianxia')
                self.assertEqual(event['period'],1)
                self.assertEqual(event['winners'],[IDS['周']])
                self.assertEqual(set(event['preserved']),set(h.actors())-{IDS['周']})
                self.assertEqual(event['secondary_winners'],[])
                from settlement import settle as rank,outcome_text
                rows={row['actor']:row for row in rank(h.to_save(),h.world)}
                self.assertEqual(rows[IDS['周']]['outcome'],'winner')
                for actor in set(h.actors())-{IDS['周']}:
                    self.assertEqual(rows[actor]['outcome'],'preserved')
                    self.assertEqual(outcome_text(rows[actor]),'臣属保全（未胜未败）')
                self.assertEqual(len([r for r in rows.values() if r['outcome']=='winner']),1)
                self.assertEqual(h.world.diplomacy.to_save()['pacts'],[])
                self.assertTrue(h.finished())
                public=[e for e in h.records()['events'] if e['kind']=='victory']
                self.assertEqual(len(public),1);self.assertEqual(public[0]['details'],event)
                player=h.player(IDS['秦']);before=h.runtime.save_mod_state(h.world)
                self.assertFalse(player.action_state()['can_act'])
                self.assertFalse(self.submit_rejected(player,'bloc_leave')['ok'])
                self.assertTrue(player.query('foreign'))
                self.assertTrue(player.submit('memory_search',{'query':'周天下'})['ok'])
                self.assertEqual(player.query('player_state')['victory_event'],event)
                self.assertEqual(h.runtime.save_mod_state(h.world),before)
                with self.assertRaisesRegex(ValueError,'ended'):h.cycle()
                with TemporaryDirectory() as folder:
                    path=Path(folder)/'game.json';h.save(path)
                    restored=GameHost.load(path,h.cfg)
                    self.assertTrue(restored.finished());self.assertEqual(restored.to_save(),h.to_save())
                    with patch('mp_run.sys.stdin',io.StringIO('')),patch('select.select',return_value=([],[],[])),patch('sys.stdout',new=io.StringIO()) as out:
                        cfg=Path(folder)/'cfg.json'
                        cfg.write_text(json.dumps({**h.cfg,'save':str(path),'journal':str(Path(folder)/'journal.md')}), encoding='utf-8')
                        from mp_run import run
                        self.assertEqual(run(['--config',str(cfg),'--turns','10']),0)
                    self.assertIn('胜利事件',out.getvalue())
                    self.assertEqual(GameHost.load(path,h.cfg).clock(),2)
                    from settlement import main as settle
                    report=Path(folder)/'report.md';before=path.read_bytes()
                    with patch('sys.stdout',new=io.StringIO()):
                        self.assertEqual(settle(['--save',str(path),'--no-chat','--out',str(report)]),0)
                    self.assertIn('臣属保全（未胜未败）',report.read_text(encoding='utf-8'))
                    self.assertNotIn('共同胜利',report.read_text(encoding='utf-8'))
                    self.assertNotIn('次要胜利',report.read_text(encoding='utf-8'))
                    self.assertEqual(path.read_bytes(),before)

    def submit_rejected(self,player,action):
        umbrella={'buy':'economy','sell':'economy','recruit':'military','build':None}.get(action)
        receipt=(player.submit(umbrella,{'action':action}) if umbrella
                 else player.submit(action))
        self.assertEqual(receipt['code'],'unavailable_action');return receipt

    def test_five_includes_zhou_but_four_does_not_win(self):
        for names,expected in ((NATIONS[:4]+('周',),True),(NATIONS[:3]+('周',),False)):
            h=self.host(names);offer=self.invite(h);self.accept(h,offer);h.cycle()
            self.assertEqual(h.victory_event is not None,expected)

    def test_missing_nation_or_wrong_chief_do_not_win(self):
        for chief,name,omit in (('周','周天下','秦'),('秦','周天下',None)):
            with self.subTest(chief=chief,name=name,omit=omit):
                h=self.host();members=[a for a in h.actors() if a!=IDS.get(omit)]
                offer=self.invite(h,members,chief=chief,name=name);self.accept(h,offer);h.cycle()
                self.assertIsNone(h.victory_event);self.assertFalse(h.finished())

    def test_rejected_invitation_and_pending_acceptance_never_win(self):
        h=self.host();offer=self.invite(h)
        self.submit(h,IDS['秦'],'reject_proposal',offer=offer.id);h.cycle()
        self.assertIsNone(h.victory_event);self.assertIsNone(h.world.diplomacy.alliance(IDS['周']))
        self.assertEqual(len(h.world.diplomacy.to_save()['pacts']),7)

    def test_partial_acceptance_resume_and_last_acceptance_wins(self):
        h=self.host();offer=self.invite(h);last=IDS['秦']
        for actor in offer.parties:
            if actor not in (offer.actor,last):self.submit(h,actor,'accept_proposal',offer=offer.id)
        h.cycle();self.assertIsNone(h.victory_event)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'pending.json';h.save(path);h=GameHost.load(path,h.cfg)
        for client in h.clients.values():client.play=lambda player,**kwargs:[]
        self.submit(h,last,'accept_proposal',offer=offer.id);h.cycle()
        self.assertEqual(h.victory_event['winners'],[IDS['周']])

    def test_chief_transfer_can_complete_actual_conditions(self):
        for chief,name,action,args in (
            ('秦','周天下','bloc_transfer',{'target':IDS['周']}),
        ):
            h=self.host();offer=self.invite(h,chief=chief,name=name);self.accept(h,offer);h.cycle()
            self.assertIsNone(h.victory_event)
            self.submit(h,IDS[chief],action,**args);h.cycle()
            self.assertEqual(h.victory_event['winners'],[IDS['周']])

    def test_zhou_leading_any_named_alliance_automatically_makes_tributaries(self):
        h=self.host();offer=self.invite(h,name='合纵');self.accept(h,offer);h.cycle()
        self.assertEqual(h.world.diplomacy.alliance(IDS['周']).name,'周天下')
        self.assertEqual(len(h.world.diplomacy.snapshot(IDS['周']).vassals),7)
        self.assertEqual(h.victory_event['winners'],[IDS['周']])

    def test_extinction_is_resolved_before_five_nation_threshold(self):
        names=NATIONS[:5]+('周',);h=self.host(names);outside=IDS[names[0]]
        offer=self.invite(h,[a for a in h.actors() if a!=outside]);self.accept(h,offer);h.cycle()
        self.assertIsNone(h.victory_event)
        for site in h.world.territory.owned(outside):h.world.territory.transfer(site.tile.id,None)
        h.cycle()
        self.assertEqual(len(h.actors()),5);self.assertEqual(h.victory_event['winners'],[IDS['周']]);self.assertEqual(len(h.victory_event['preserved']),4);self.assertNotIn(outside,h.victory_event['preserved']);self.assertEqual(h.victory_event['secondary_winners'],[])

        h=self.host(NATIONS[:4]+('周',));offer=self.invite(h);self.accept(h,offer)
        doomed=IDS['秦']
        for site in h.world.territory.owned(doomed):h.world.territory.transfer(site.tile.id,None)
        h.cycle()
        self.assertEqual(len(h.actors()),4);self.assertIsNone(h.victory_event)

    def test_conquest_is_a_distinct_terminal_event(self):
        h=self.host(('秦','周'));doomed=IDS['周']
        for site in h.world.territory.owned(doomed):h.world.territory.transfer(site.tile.id,None)
        h.cycle()
        self.assertEqual(h.victory_event['type'],'eight_nations.conquest')
        self.assertEqual(h.victory_event['winners'],[IDS['秦']])
        self.assertEqual(h.victory_event['secondary_winners'],[])

    def test_preservation_does_not_change_consumption_ranking_or_grant_dead_nations_protection(self):
        h=self.host();doomed=IDS['秦']
        for site in h.world.territory.owned(doomed):h.world.territory.transfer(site.tile.id,None)
        h.cycle()
        offer=self.invite(h);self.accept(h,offer);h.cycle()
        event=h.victory_event
        self.assertEqual(event['winners'],[IDS['周']]);self.assertNotIn(doomed,event['preserved'])
        from settlement import settle,scoreboard_text,outcome_text
        rows=settle(h.to_save(),h.world)
        self.assertEqual([r['total'] for r in rows],sorted((r['total'] for r in rows),reverse=True))
        row=next(r for r in rows if r['actor']==doomed)
        self.assertIsNone(row['outcome']);self.assertEqual(outcome_text(row),'已亡国')
        self.assertIn('臣属保全（未胜未败）',scoreboard_text(rows))

    def test_admission_and_other_member_exit_same_cycle_does_not_win(self):
        h=self.host();outsider=IDS['秦'];members=[a for a in h.actors() if a!=outsider]
        offer=self.invite(h,members);self.accept(h,offer);h.cycle()
        self.submit(h,outsider,'bloc_join',bloc='scenario:mandate');h.cycle()
        vote=h.world.diplomacy.snapshot(IDS['周']).votes[0]
        self.submit(h,IDS['周'],'vote',vote=vote.id,accept=True)
        # All commands must be validated at submission; a current member can
        # leave while the last applicant's admission is awaiting tally.
        self.submit(h,IDS['魏'],'bloc_leave');h.cycle()
        self.assertIsNone(h.victory_event)
        self.assertIsNone(h.world.diplomacy.alliance(IDS['魏']))
        self.assertIsNotNone(h.world.diplomacy.alliance(outsider))

    def test_counter_program_proves_hook_has_no_native_victory_policy(self):
        _,cfg,runtime=counter.TestDistributionAPI().host()
        runtime.distribution['Scenarios']={'festival':{'Entry':__name__+':CounterVictory'}}
        cfg['scenario']='festival';h=GameHost.new(cfg,runtime=runtime)
        h.cycle();self.assertIsNone(h.victory_event)
        h.cycle();self.assertTrue(h.finished())
        self.assertEqual(h.victory_event,{'festival':'计数庆典','awards':{'counter:left':{'stars':7}},
                                         'totals':{'counter:left':2,'counter:right':2}})
        self.assertFalse(h.player('counter:left').submit('increment')['ok'])
        self.assertEqual(h.player('counter:left').query('count'),2)
        event={'another':['event']};returned=h.trigger_victory(event)
        event['another'].append('changed');returned['awards'].clear()
        self.assertEqual(h.victory_event['awards'],{'counter:left':{'stars':7}})
        with TemporaryDirectory() as folder:
            path=Path(folder)/'counter.json';h.save(path)
            loaded=GameHost.load(path,cfg,runtime=runtime)
            self.assertEqual(loaded.to_save(),h.to_save())
            with self.assertRaises(SaveFormatError):GameHost.load(path,{k:v for k,v in cfg.items() if k!='scenario'},runtime=runtime)

    def test_lightweight_scenario_mod_has_no_native_dependency_or_persistent_instance_state(self):
        _,cfg,base=counter.TestDistributionAPI().host()
        mod=CounterScenarioMod()
        mod.configure({'Mod':{'Id':'scenario:counter'},'Capabilities':{'Needs':['example:counter']},
                       'Interfaces':{'example:counter':['clock']}},Path(__file__))
        distribution=deepcopy(base.distribution)
        distribution['Scenarios']={'festival':{'Mod':'scenario:counter'}}
        runtime=Runtime([*base.units.values(),Unit('scenario:counter',mod,version='1')],distribution)
        cfg['scenario']='festival';a=GameHost.new(cfg,runtime=runtime);b=GameHost.new(cfg,runtime=runtime)
        a.cycle();self.assertIsNone(a.victory_event)
        a.cycle();self.assertEqual(a.victory_event,{'type':'counter.hook','period':2})
        self.assertIsNone(b.victory_event);self.assertEqual(b.clock(),0)
        self.assertEqual(a.to_save()['modules']['scenario:counter'],{})
        self.assertNotIn('native:diplomacy',runtime.units)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'counter.json';a.save(path)
            loaded=GameHost.load(path,cfg,runtime=runtime)
            self.assertEqual(loaded.to_save(),a.to_save())
            with self.assertRaises(SaveFormatError):GameHost.load(path,{k:v for k,v in cfg.items() if k!='scenario'},runtime=runtime)

    def test_unmounted_scenarios_and_invalid_events_are_rejected(self):
        _,cfg,runtime=counter.TestDistributionAPI().host()
        with self.assertRaises(ModError):GameHost.new({**cfg,'scenario':'eight_nations'},runtime=runtime)
        h=GameHost.new(cfg,runtime=runtime)
        for event in ({},None,[],{'x':float('nan')},{'x':object()}):
            with self.assertRaises((TypeError,ValueError)):h.trigger_victory(event)
            self.assertIsNone(h.victory_event)
        h.trigger_victory({'custom':'ok'})
        malformed=h.to_save();malformed['host']['victory_event']=[]
        with self.assertRaises(SaveFormatError):GameHost.new(cfg,runtime=runtime).restore(malformed)

    def test_program_rebinds_when_world_is_replaced(self):
        _,cfg,runtime=counter.TestDistributionAPI().host()
        runtime.distribution['Scenarios']={'festival':{'Entry':__name__+':CounterVictory'}}
        h=GameHost.new({**cfg,'scenario':'festival'},runtime=runtime);h.cycle()
        candidate=runtime.world_class()();runtime.load_mod_state(candidate,runtime.save_mod_state(h.world))
        h.world=candidate;h.cycle()
        self.assertEqual(h.victory_event['totals'],{'counter:left':2,'counter:right':2})

    def test_rules_reach_monarch_after_opening_and_council_from_start(self):
        cfg=build_config({'mods':'mods.experimental.toml','nations':[{'client':'llm','model':'fake'}]})
        h=build(config=cfg);actor=IDS['秦'];client=h.clients[actor]
        ui=model_view_for(h.ui, client.cfg);obs=h.player(actor).observe();obs['period']=25
        ui.state(obs,[])
        self.assertIn(CHARTER,ui.system())
        self.assertIn(CHARTER,h.services[actor]['council'].cfg['extra_summary'])
        schemas={s['function']['name']:s['function'] for s in ui.actions(h.player(actor).actions())}
        actions=schemas['relations']['parameters']['properties']['action']['description']
        self.assertIn('原个人保障',actions)
        self.assertIn('任一成员交战',actions)
        for topic in ('入盟','退盟','宣战','求和','保障'):
            self.assertNotEqual(h.player(actor).rules(topic)[0]['title'],'规则目录')
        self.assertEqual(len(h.player(actor).rules('外交')),3)


class AllianceConsequences(TestCase):
    def test_leaving_does_not_neutralize_former_alliance_defense(self):
        f=domains.TestWorldDomains();w=f.world();f.found(w,(Q,Z,Y))
        f.call(w,Z,'bloc_leave');f.cycle(w)
        f.declare(w,Z,Q)
        self.assertTrue(w.diplomacy.war_between(Z,Q));self.assertTrue(w.diplomacy.war_between(Z,Y))
        before=f.save(w)
        with self.assertRaises(ValueError):f.call(w,Y,'bloc_leave')
        with self.assertRaises(ValueError):f.call(w,Q,'bloc_dissolve')
        with self.assertRaises(ValueError):f.call(w,Y,'offer_peace',target=Z,kind='white')
        self.assertEqual(f.save(w),before)

    def test_join_clears_incoming_and_outgoing_pacts_without_restore_on_exit(self):
        f=domains.TestWorldDomains();w=f.world()
        w.diplomacy.seed_pact('guarantee',C,Z);w.diplomacy.seed_pact('guarantee',Z,Y)
        w.diplomacy._truce('nation/'+Z,'nation/'+C,20)
        f.found(w,(Q,Z,Y))
        self.assertEqual(w.diplomacy.to_save()['pacts'],[])
        self.assertFalse(w.diplomacy.active_truce(Z,C))
        f.call(w,Z,'bloc_leave');f.cycle(w)
        self.assertEqual(w.diplomacy.to_save()['pacts'],[])
        self.assertTrue(w.diplomacy.active_truce(Z,C))
        self.assertIn(('nation/'+C,'nation/'+Z,20),w.diplomacy.snapshot(Z).truces)
