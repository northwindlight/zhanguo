"""Real API and settlement tests for tributaries and ordinary subjects; offline."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
from engine.runtime import Runtime
from mods.native.diplomacy.vassalage import Vassal
from tests import test_building_objects as building_tests

Q,C,Z,W='scenario:qin','scenario:chu','scenario:zhao','scenario:zhou'


class VassalageTests(unittest.TestCase):
    def host(self,scenario=False,experimental=False,runtime=None):
        cfg={'map_size':12,'seed':73,'mods':'mods.experimental.toml' if experimental else 'mods.toml',
             'nations':[{'id':actor,'name':name,'client':'rule','position':pos}
                 for actor,name,pos in ((Q,'秦',[2,2]),(C,'楚',[8,2]),(Z,'赵',[2,8]),(W,'周',[8,8]))]}
        if scenario:cfg['scenario']='eight_nations'
        h=GameHost.new(cfg,runtime=runtime)
        for actor,client in h.clients.items():
            client.play=lambda player,**kwargs:[]
            self.assertTrue(h.player(actor).submit('plan',{'text':'依据实际生产经营国家'})['ok'])
        return h

    def act(self,h,actor,action,ok=True,**args):
        r=h.player(actor).submit('relations',{'action':action,**args})
        self.assertEqual(r['ok'],ok,r)
        return r

    def subject(self,h,lord=Q,subject=C):
        self.act(h,lord,'propose_vassalage',target=subject);h.cycle()
        offer=h.player(subject).call_api('native:diplomacy','snapshot')['offers'][-1]['id']
        self.act(h,subject,'accept_proposal',offer=offer);h.cycle()
        return h.world.diplomacy.vassal(subject)

    def bloc(self,h,chief=W,members=(C,Z)):
        self.act(h,chief,'bloc_found',bloc='scenario:union',name='合纵',tos=list(members));h.cycle()
        offer=h.world.diplomacy.snapshot(chief).offers[0]
        for actor in members:self.act(h,actor,'accept_proposal',offer=offer.id)
        h.cycle()

    def test_normal_vassal_is_same_entity_with_no_diplomacy(self):
        for experimental in (False,True):
            with self.subTest(experimental=experimental):
                h=self.host(experimental=experimental);s=self.subject(h)
                self.assertEqual((s.kind,s.tribute_percent),('ordinary',15))
                self.assertEqual(h.world.diplomacy.entity(C),'nation/'+Q)
                self.assertEqual(set(h.world.diplomacy.members('nation/'+Q)),{Q,C})
                self.assertEqual(h.world.diplomacy.representative('nation/'+Q),Q)
                for action,args in (('declare_war',{'target':Z}),('bloc_leave',{}),('propose_vassalage',{'target':Z}),
                                    ('offer_peace',{'target':Z,'kind':'white'})):
                    self.act(h,C,action,ok=False,**args)
                self.act(h,Z,'propose_vassalage',target=C,ok=False)
                self.assertFalse(h.player(C).submit('intelligence',{'action':'send_letter','target':Z,'text':'自行谈判'})['ok'])
                self.assertTrue(h.player(C).submit('intelligence',{'action':'send_letter','target':Q,'text':'请宗主安排外交'})['ok'])
                countries=h.player(C).call_api('native:intelligence','countries')
                self.assertEqual(next(n for n in countries if n['id']==Q)['relation'],'ally')

    def test_subject_follows_lord_into_war_and_lord_releases_only_in_peace(self):
        h=self.host();self.subject(h)
        self.act(h,Q,'declare_war',target=Z);h.cycle()
        self.assertTrue(h.world.diplomacy.war_between(C,Z))
        self.act(h,Q,'release_vassal',target=C,ok=False)
        self.act(h,Q,'offer_peace',target=Z,kind='white');h.cycle()
        offer=next(o for o in h.world.diplomacy.snapshot(Z).offers if o.action=='peace')
        self.act(h,Z,'accept_peace',offer=offer.id);h.cycle()
        self.act(h,Q,'release_vassal',target=C);h.cycle()
        self.assertIsNone(h.world.diplomacy.vassal(C));self.assertEqual(h.world.diplomacy.entity(C),'nation/'+C)

    def test_independence_is_real_war_and_does_not_end_existing_front(self):
        h=self.host();self.subject(h)
        self.act(h,Q,'declare_war',target=Z);h.cycle()
        self.act(h,C,'independence_war');h.cycle()
        self.assertIsNone(h.world.diplomacy.vassal(C))
        self.assertTrue(h.world.diplomacy.war_between(C,Q))
        self.assertTrue(h.world.diplomacy.war_between(Q,Z))
        self.assertFalse(h.world.diplomacy.war_between(C,Z))
        self.assertTrue(h.world.diplomacy.diplomatic_authority(C))
        self.roundtrip(h)

    def test_peace_can_impose_or_offer_vassalage(self):
        for kind in ('submit','subjugate'):
            with self.subTest(kind=kind):
                h=self.host();self.act(h,Q,'declare_war',target=C);h.cycle()
                self.act(h,Q,'offer_peace',target=C,kind=kind);h.cycle()
                offer=next(o for o in h.world.diplomacy.snapshot(C).offers if o.action=='peace')
                self.act(h,C,'accept_peace',offer=offer.id);h.cycle()
                lord,subject=(C,Q) if kind=='submit' else (Q,C)
                self.assertEqual(h.world.diplomacy.vassal(subject).overlord,lord)
                self.assertFalse(h.world.diplomacy.war_between(Q,C));self.roundtrip(h)

    def test_zhou_allies_automatically_are_ten_percent_tributaries_only_in_selected_scenario(self):
        for scenario in (False,True):
            with self.subTest(scenario=scenario):
                h=self.host(scenario);self.bloc(h)
                relations=h.world.diplomacy.snapshot(W).vassals
                self.assertEqual(len(relations),2 if scenario else 0)
                for s in relations:
                    self.assertEqual((s.overlord,s.kind,s.tribute_percent),(W,'tributary',10))
                    self.assertTrue(h.world.diplomacy.diplomatic_authority(s.subject))
                if scenario:self.assertEqual(h.world.diplomacy.alliance(W).name,'周天下')
                self.roundtrip(h)

    def test_submitting_to_zhou_automatically_creates_then_extends_zhou_tianxia(self):
        h=self.host(True);s=self.subject(h,W,C)
        self.assertEqual((s.kind,s.tribute_percent),('tributary',10))
        self.assertEqual(h.world.diplomacy.alliance(W).name,'周天下')
        # Existing peers retain a real vote before the chief issues another invitation.
        self.act(h,W,'propose_vassalage',target=Z);h.cycle()
        vote=h.world.diplomacy.snapshot(W).votes[0]
        self.act(h,W,'vote',vote=vote.id,accept=True);self.act(h,C,'vote',vote=vote.id,accept=True);h.cycle()
        offer=next(o for o in h.world.diplomacy.snapshot(Z).offers if o.action=='vassal')
        self.act(h,Z,'accept_proposal',offer=offer.id);h.cycle()
        self.assertEqual(h.world.diplomacy.vassal(Z).tribute_percent,10)
        self.assertEqual(set(h.world.diplomacy.alliance(W).members),{W,C,Z});self.roundtrip(h)

    def test_tributary_can_vote_and_leave_but_zhou_cannot_transfer_chief(self):
        h=self.host(True);self.bloc(h)
        self.act(h,W,'declare_war',target=Q);h.cycle()
        vote=h.world.diplomacy.snapshot(C).votes[0]
        self.act(h,C,'vote',vote=vote.id,accept=False);self.act(h,Z,'vote',vote=vote.id,accept=False);h.cycle()
        self.assertFalse(h.world.diplomacy.war_between(W,Q))
        self.act(h,C,'bloc_leave');h.cycle()
        self.assertIsNone(h.world.diplomacy.vassal(C))
        self.act(h,W,'bloc_transfer',target=Z,ok=False)
        self.act(h,W,'bloc_dissolve');h.cycle()
        self.assertFalse(h.world.diplomacy.snapshot(Z).vassals);self.roundtrip(h)

    def test_zhou_cannot_submit_or_be_subjugated_in_any_diplomatic_direction(self):
        h=self.host(True)
        self.act(h,W,'submit_vassalage',target=Q,ok=False)
        self.act(h,Q,'propose_vassalage',target=W,ok=False)
        self.act(h,Q,'declare_war',target=W);h.cycle()
        self.act(h,W,'offer_peace',target=Q,kind='submit',ok=False)
        self.act(h,Q,'offer_peace',target=W,kind='subjugate',ok=False)
        # Normal peace remains available; the protection is only against subjection.
        self.act(h,Q,'offer_peace',target=W,kind='white')

    def test_zhou_death_keeps_alliance_and_succeeds_chief_without_tribute(self):
        h=self.host(True);self.bloc(h,chief=W,members=(Q,C))
        old=h.world.diplomacy.alliance(W)
        for site in h.world.territory.owned(W):h.world.territory.transfer(site.tile.id,None)
        h.cycle()
        bloc=h.world.diplomacy.alliance(Q)
        self.assertEqual(bloc.id,old.id);self.assertEqual(set(bloc.members),{Q,C})
        self.assertEqual(bloc.chief,Q)
        self.assertFalse(h.world.diplomacy.snapshot(Q).vassals)
        self.assertFalse(h.world.diplomacy.production_shares())
        self.act(h,Q,'bloc_transfer',target=C);h.cycle()
        self.assertEqual(h.world.diplomacy.alliance(C).chief,C)
        self.assertFalse(h.world.diplomacy.production_shares());self.roundtrip(h)

    def test_death_of_overlord_releases_ordinary_subjects(self):
        h=self.host();self.subject(h)
        for site in h.world.territory.owned(Q):h.world.territory.transfer(site.tile.id,None)
        h.cycle();self.assertIsNone(h.world.diplomacy.vassal(C));self.assertTrue(h.world.diplomacy.diplomatic_authority(C))
        self.roundtrip(h)

    def test_lord_becoming_an_ordinary_vassal_releases_old_subjects_at_settlement(self):
        h=self.host();self.subject(h,Q,C)
        self.act(h,Z,'propose_vassalage',target=Q);h.cycle()
        offer=next(o for o in h.world.diplomacy.snapshot(Q).offers if o.action=='vassal')
        self.act(h,Q,'accept_proposal',offer=offer.id)
        self.assertEqual(h.world.diplomacy.vassal(C).overlord,Q)
        self.assertIsNone(h.world.diplomacy.vassal(Q))
        h.cycle()
        self.assertEqual(h.world.diplomacy.vassal(Q).overlord,Z)
        self.assertIsNone(h.world.diplomacy.vassal(C))
        self.assertEqual(h.world.diplomacy.entity(C),'nation/'+C)
        self.assertTrue(h.world.diplomacy.diplomatic_authority(C))
        self.assertFalse(h.world.diplomacy.allied(Q,C));self.roundtrip(h)

    def test_actual_production_tax_rounds_down_and_does_not_tax_stock(self):
        for scenario,lord,rate in ((False,Q,15),(True,W,10)):
            with self.subTest(rate=rate):
                h=self.host(scenario);self.subject(h,lord,C)
                site=next(s for s in h.world.buildings.snapshot().sites if s.owner==C and dict(s.resources).get('native:farmland',0)>0)
                h.world.buildings.seed(site.id,'native:farm')
                core=next(i.site for i in h.world.buildings.snapshot().instances if i.kind=='native:town-hall' and next(t for t in h.world.buildings.snapshot().sites if t.id==i.site).owner==C)
                h.world.buildings.seed(core,'native:castle',5)
                h.world.buildings.seed(core,'native:wood-power-plant')
                before={a:h.world.resources.balance(a,'native:gold') for a in (C,lord)}
                h.cycle()
                self.assertEqual(h.world.resources.balance(C,'native:gold')-before[C],11)
                self.assertEqual(h.world.resources.balance(lord,'native:gold')-before[lord],1)
                reports={a:h.world.economy.turns(a)[-1] for a in (C,lord)}
                self.assertGreater(reports[C].tribute_paid.known,0)
                self.assertEqual(reports[C].tribute_paid,reports[lord].tribute_received)
                self.assertEqual(reports[lord].production.known,0)
                self.assertEqual(reports[C].spending.known,0)
                self.roundtrip(h)

    def test_corrupt_vassal_save_rejected_atomically(self):
        h=self.host();self.subject(h);before=h.world.diplomacy.to_save()
        for mutate in (lambda s:s['vassals'][0].update(tribute_percent=0),
                       lambda s:s['vassals'].append({'subject':Q,'overlord':C,'kind':'ordinary','tribute_percent':15})):
            data=deepcopy(before);mutate(data)
            with self.assertRaises(ValueError):h.world.diplomacy.restore(data)
            self.assertEqual(h.world.diplomacy.to_save(),before)

    def test_tribute_is_withheld_before_input_consumption_and_recipient_cannot_use_it_this_period(self):
        w=building_tests.TestBuildings().world(accounts={'Qin':{},'Chu':{}})
        w.buildings.seed('example:qin','native:farm',10)
        w.buildings.seed('example:qin','native:supply-factory',10)
        w.buildings.seed('example:chu','native:supply-factory')
        w.resources.credit('Qin','native:food',10)
        w.resources.credit('Qin','native:ore',10)
        w.resources.credit('Chu','native:food',1.9)
        w.resources.credit('Chu','native:ore',1)
        report=w.buildings.produce(external_energy={'Qin':10,'Chu':1},
            production_shares={'Qin':{'recipient':'Chu','percent':15}})
        factories=[r for r in report.records if r.kind=='native:supply-factory']
        self.assertEqual(sum(r.status=='running' for r in factories),9)
        self.assertEqual(sum(r.status=='missing-input' for r in factories),2)
        self.assertEqual(w.resources.balance('Qin','native:food'),1)
        self.assertAlmostEqual(w.resources.balance('Chu','native:food'),2.9)
        self.assertEqual(dict((key,amount) for _,_,key,amount in report.transfers),{'native:food':1,'native:supply':2})

    def test_all_storable_production_is_taxed_once_and_unproduced_inventory_is_not_taxed(self):
        w=building_tests.TestBuildings().world()
        for index,kind in enumerate(('native:farm','native:lumber-camp','native:mine','native:oil-well','native:gold-mine',
                     'native:equipment-factory','native:supply-factory')):
            site=f'example:production-{index}'
            w.buildings.register_site(site,'Qin',(index+2,0),resources={'native:farmland':20,'native:wood':20,'native:ore':20,'native:oil':20,'native:gold':20})
            w.buildings.seed(site,kind,10)
        before={a:{s.id:s.amount for s in w.resources.snapshot(a)} for a in ('Qin','Chu')}
        report=w.buildings.produce(external_energy={'Qin':20},production_shares={'Qin':{'recipient':'Chu','percent':10}})
        outputs=dict(report.outputs)
        self.assertEqual(set(outputs),{'native:food','native:wood','native:ore','native:oil','native:gold','native:equipment','native:supply'})
        for source,target,key,amount in report.transfers:
            self.assertEqual((source,target),('Qin','Chu'))
            self.assertAlmostEqual(amount,outputs[key]//10)
            self.assertAlmostEqual(w.resources.balance('Chu',key)-before['Chu'][key],amount)

    def test_tribute_rounds_down_on_national_total_not_each_building(self):
        w=building_tests.TestBuildings().world()
        w.buildings.seed('example:qin','native:mine',20)
        w.buildings.register_site('example:industry','Qin',(2,0))
        w.buildings.seed('example:industry','native:equipment-factory',17)
        w.resources.consume('Qin','native:ore',100)
        report=w.buildings.produce(external_energy={'Qin':17},
            production_shares={'Qin':{'recipient':'Chu','percent':15}})
        self.assertEqual(sum(r.status=='running' and r.kind=='native:equipment-factory' for r in report.records),17)
        self.assertEqual(w.resources.balance('Qin','native:ore'),0)
        self.assertEqual(w.resources.balance('Chu','native:ore'),3)
        self.assertEqual(w.resources.balance('Qin','native:equipment'),29)

    def test_tax_reporting_preview_roi_and_chinese_ui_are_consistent(self):
        h=self.host();self.subject(h)
        site=next(s for s in h.world.buildings.snapshot().sites if s.owner==C and dict(s.resources).get('native:farmland',0)>0)
        h.world.buildings.seed(site.id,'native:farm')
        core=next(i.site for i in h.world.buildings.snapshot().instances if i.kind=='native:town-hall' and next(t for t in h.world.buildings.snapshot().sites if t.id==i.site).owner==C)
        h.world.buildings.seed(core,'native:castle',5)
        h.world.buildings.seed(core,'native:wood-power-plant');h.cycle()
        economy=h.player(C).query('econ')
        self.assertEqual(economy['preview']['tribute']['percent'],15)
        estimate=h.player(C).call_api('economy:queries','estimate',kind='native:farm')
        self.assertTrue(any('上贡15%' in line for line in estimate['assumptions']))
        self.assertAlmostEqual(estimate['outputs']['known'],estimate['outputs_sell']['known'])
        snapshot=h.observer_snapshot()
        self.assertAlmostEqual(snapshot['resource_flows']['owners'][C]['native:food'],1)
        text=h.ui.console.overview(snapshot)
        self.assertIn('普通附庸',text);self.assertIn('上贡15%',text)
        event=next(e for e in h.records()['events'] if e['kind']=='tribute')
        rendered=h.ui.console.event(event,snapshot['names'])
        self.assertIn('黄金',rendered);self.assertNotIn('native:gold',rendered)

    def test_scenario_binding_is_per_world_even_when_runtime_is_shared(self):
        runtime=Runtime.load(Path(__file__).resolve().parents[1]/'mods.toml')
        active=self.host(True,runtime=runtime);plain=self.host(False,runtime=runtime)
        self.bloc(active);self.bloc(plain)
        self.assertEqual(len(active.world.diplomacy.snapshot(W).vassals),2)
        self.assertFalse(plain.world.diplomacy.snapshot(W).vassals)
        self.assertIsNone(plain.world.diplomacy.to_save()['subject_alliance'])

    def test_subject_offer_silence_rejects_without_mutating_diplomacy(self):
        h=self.host();self.act(h,Q,'propose_vassalage',target=C);h.cycle()
        r=h.player(C).submit('end_turn',{'summary':'拒绝称臣，保留自主外交能力'})
        self.assertTrue(r['ok'],r);h.cycle()
        self.assertIsNone(h.world.diplomacy.vassal(C))
        self.assertFalse(h.world.diplomacy.snapshot(C).offers)

    def test_simultaneous_submission_charges_zhou_once_only_at_settlement(self):
        h=self.host(True);before=h.world.resources.balance(W,'native:gold')
        for actor in (C,Z):self.act(h,actor,'submit_vassalage',target=W)
        self.assertIsNone(h.world.diplomacy.alliance(W))
        self.assertIsNone(h.world.diplomacy.vassal(C))
        self.assertEqual(h.world.resources.balance(W,'native:gold'),before)
        h.cycle()
        bloc=h.world.diplomacy.alliance(W)
        self.assertEqual((bloc.name,bloc.chief),('周天下',W))
        self.assertEqual(set(bloc.members),{W,C,Z})
        self.assertEqual(h.world.resources.balance(W,'native:gold'),before-100)
        self.assertEqual(h.world.economy.turns(W)[-1].spending.known,100)
        # A later application is a normal admission vote; no second founding fee.
        self.act(h,Q,'submit_vassalage',target=W);h.cycle()
        vote=h.world.diplomacy.snapshot(W).votes[0]
        for actor in bloc.members:self.act(h,actor,'vote',vote=vote.id,accept=True)
        h.cycle()
        self.assertEqual(h.world.resources.balance(W,'native:gold'),before-100)
        self.assertEqual(h.world.diplomacy.vassal(Q).tribute_percent,10)
        self.roundtrip(h)

    def test_submission_does_not_bankrupt_unfunded_zhou(self):
        h=self.host(True)
        h.world.resources.consume(W,'native:gold',1350)
        self.act(h,C,'submit_vassalage',target=W)
        h.world.resources.consume(W,'native:gold',100)
        h.cycle()
        self.assertIsNone(h.world.diplomacy.alliance(W))
        self.assertIsNone(h.world.diplomacy.vassal(C))
        self.assertEqual(h.world.resources.balance(W,'native:gold'),50)

    def test_war_settles_after_submission_and_targets_current_defending_entity(self):
        h=self.host(True)
        self.act(h,Q,'declare_war',target=W)
        self.act(h,C,'submit_vassalage',target=W)
        self.assertFalse(h.world.diplomacy.war_between(Q,W))
        self.assertIsNone(h.world.diplomacy.vassal(C))
        h.cycle()
        self.assertTrue(h.world.diplomacy.war_between(Q,W))
        self.assertTrue(h.world.diplomacy.war_between(Q,C))
        results=[r for r in h.world.diplomacy.snapshot(Q).results if r.status=='effective']
        self.assertEqual(results[-1].action,'declare_war')
        self.roundtrip(h)

    def test_active_submission_to_ordinary_lord_requires_acceptance(self):
        h=self.host();self.act(h,C,'submit_vassalage',target=Q)
        self.assertIsNone(h.world.diplomacy.vassal(C));h.cycle()
        self.assertIsNone(h.world.diplomacy.vassal(C))
        offer=next(o for o in h.world.diplomacy.snapshot(Q).offers if o.action=='submission')
        self.act(h,Q,'accept_proposal',offer=offer.id);h.cycle()
        self.assertEqual(h.world.diplomacy.vassal(C).tribute_percent,15)

    def test_lord_joins_alliance_with_subjects_but_only_lord_votes(self):
        h=self.host();self.subject(h,Q,C);self.bloc(h,chief=Z,members=(Q,))
        bloc=h.world.diplomacy.alliance(Q)
        self.assertEqual(set(bloc.members),{Z,Q})
        self.assertEqual(h.world.diplomacy.entity(C),'bloc/'+bloc.id)
        self.assertEqual(set(h.world.diplomacy.members('bloc/'+bloc.id)),{Z,Q,C})
        self.act(h,Q,'declare_war',target=W);h.cycle()
        vote=h.world.diplomacy.snapshot(Q).votes[0]
        self.assertEqual(set(vote.voters),{Z,Q})
        self.assertFalse(h.world.diplomacy.snapshot(C).votes)
        self.act(h,C,'vote',vote=vote.id,accept=True,ok=False)
        for actor in (Z,Q):self.act(h,actor,'vote',vote=vote.id,accept=True)
        h.cycle()
        self.assertTrue(h.world.diplomacy.war_between(C,W));self.roundtrip(h)
        self.act(h,C,'independence_war');h.cycle()
        self.assertEqual(h.world.diplomacy.entity(C),'nation/'+C)
        self.assertTrue(h.world.diplomacy.war_between(C,Q))
        self.assertTrue(h.world.diplomacy.war_between(C,Z))
        self.assertFalse(h.world.diplomacy.war_between(C,W));self.roundtrip(h)

    def test_lord_joins_zhou_transferring_subjects_to_zhou_as_equal_tributaries(self):
        h=self.host(True);self.subject(h,Q,C);self.bloc(h,chief=W,members=(Q,))
        self.assertEqual(h.world.diplomacy.vassal(Q).kind,'tributary')
        self.assertEqual(h.world.diplomacy.vassal(C).overlord,W)
        self.assertEqual(h.world.diplomacy.production_shares()[Q]['percent'],10)
        self.assertEqual(h.world.diplomacy.production_shares()[C],{'recipient':W,'percent':10})
        self.assertTrue(h.world.diplomacy.diplomatic_authority(Q))
        self.assertTrue(h.world.diplomacy.diplomatic_authority(C))
        self.assertEqual(set(h.world.diplomacy.alliance(W).members),{W,Q,C});self.roundtrip(h)
        self.act(h,Q,'propose_vassalage',target=Z);h.cycle()
        vote=h.world.diplomacy.snapshot(Q).votes[0]
        for actor in (W,Q,C):self.act(h,actor,'vote',vote=vote.id,accept=True)
        h.cycle();offer=next(o for o in h.world.diplomacy.snapshot(Z).offers if o.action=='vassal')
        self.act(h,Z,'accept_proposal',offer=offer.id);h.cycle()
        self.assertEqual(h.world.diplomacy.vassal(Z).overlord,W)
        self.assertEqual(h.world.diplomacy.vassal(Z).tribute_percent,10)
        self.assertTrue(all(s.overlord==W for s in h.world.diplomacy.snapshot(W).vassals));self.roundtrip(h)

    def test_vassal_invitation_by_allied_lord_preserves_lord_identity_after_vote(self):
        h=self.host();self.bloc(h,chief=Z,members=(Q,))
        self.act(h,Q,'propose_vassalage',target=C);h.cycle()
        vote=h.world.diplomacy.snapshot(Q).votes[0]
        for actor in (Z,Q):self.act(h,actor,'vote',vote=vote.id,accept=True)
        h.cycle();offer=next(o for o in h.world.diplomacy.snapshot(C).offers if o.action=='vassal')
        self.assertEqual(offer.actor,Q)
        self.act(h,C,'accept_proposal',offer=offer.id);h.cycle()
        self.assertEqual(h.world.diplomacy.vassal(C).overlord,Q)
        self.assertEqual(h.world.diplomacy.entity(C),h.world.diplomacy.entity(Z));self.roundtrip(h)

    def test_alliance_peace_submission_does_not_enslave_other_allies(self):
        for scenario in (False,True):
            with self.subTest(scenario=scenario):
                h=self.host(scenario);self.bloc(h,chief=Q,members=(Z,))
                self.act(h,Q,'declare_war',target=W);h.cycle()
                vote=h.world.diplomacy.snapshot(Q).votes[0]
                for actor in (Q,Z):self.act(h,actor,'vote',vote=vote.id,accept=True)
                h.cycle()
                self.act(h,Q,'offer_peace',target=W,kind='submit');h.cycle()
                vote=h.world.diplomacy.snapshot(Q).votes[0]
                for actor in (Q,Z):self.act(h,actor,'vote',vote=vote.id,accept=True)
                h.cycle()
                offer=next(o for o in h.world.diplomacy.snapshot(W).offers if o.action=='peace')
                self.act(h,W,'accept_peace',offer=offer.id)
                self.assertIsNone(h.world.diplomacy.vassal(Q))
                before=h.world.resources.balance(W,'native:gold');h.cycle()
                self.assertEqual(h.world.diplomacy.vassal(Q).overlord,W)
                self.assertEqual(h.world.diplomacy.vassal(Q).kind,'tributary' if scenario else 'ordinary')
                self.assertIsNone(h.world.diplomacy.vassal(Z))
                self.assertFalse(h.world.diplomacy.allied(Z,Q))
                self.assertFalse(h.world.diplomacy.war_between(Q,W))
                self.assertEqual(h.world.resources.balance(W,'native:gold'),before-(100 if scenario else 0))
                self.roundtrip(h)

    def roundtrip(self,h):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'save.json';h.save(path)
            loaded=GameHost.load(path,h.cfg)
            self.assertEqual(loaded.to_save(),h.to_save())


if __name__=='__main__':unittest.main()
