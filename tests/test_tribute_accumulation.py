"""Real production, persistence and AI presentation of carried tribute fractions."""
from copy import deepcopy
from clients.model_view import model_view_for
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from host import GameHost
from tests import test_building_objects as buildings
from tests import test_vassalage as vassalage


class TributeAccumulation(TestCase):
    def world(self,count=1):
        w=buildings.TestBuildings().world(accounts={'Qin':{},'Chu':{},'Zhao':{}})
        w.buildings.seed('example:qin','native:farm',count)
        return w

    def produce(self,w,recipient='Chu',percent=10):
        return w.buildings.produce(production_shares={'Qin':{'recipient':recipient,'percent':percent}})

    def test_small_output_accumulates_at_both_tribute_rates_without_float_drift(self):
        for rate in (10,15):
            with self.subTest(rate=rate):
                w=self.world()
                for turn in range(1,21):
                    self.produce(w,percent=rate)
                    self.assertEqual(w.resources.balance('Chu','native:food'),turn*rate//100)
                    self.assertEqual(w.resources.balance('Qin','native:food'),turn-turn*rate//100)
                    w.buildings.complete_construction()
                self.assertFalse(w.buildings.tribute_balances('Qin','Chu'))

    def test_nine_output_pays_nothing_then_one_and_keeps_eight_tenths(self):
        w=self.world(9)
        first=self.produce(w);self.assertFalse(first.transfers)
        self.assertEqual(w.buildings.tribute_balances('Qin','Chu'),{'native:food':0.9})
        w.buildings.complete_construction();second=self.produce(w)
        self.assertEqual(second.transfers,(('Qin','Chu','native:food',1),))
        self.assertEqual(w.buildings.tribute_balances('Qin','Chu'),{'native:food':0.8})
        self.assertEqual(w.resources.balance('Qin','native:food'),17)

    def test_recipient_resource_and_inactive_periods_do_not_mix_balances(self):
        w=self.world()
        for recipient in ('Chu','Zhao'):
            for _ in range(5):self.produce(w,recipient);w.buildings.complete_construction()
        w.buildings.produce();w.buildings.complete_construction()
        self.assertEqual(w.buildings.tribute_balances('Qin','Chu'),{'native:food':0.5})
        self.assertEqual(w.buildings.tribute_balances('Qin','Zhao'),{'native:food':0.5})
        w.buildings.seed('example:qin','native:mine')
        self.produce(w)
        self.assertEqual(w.buildings.tribute_balances('Qin','Chu'),{'native:food':0.6,'native:ore':0.1})
        self.assertEqual(w.buildings.tribute_balances('Qin','Zhao'),{'native:food':0.5})
        self.assertEqual(w.resources.balance('Chu','native:food'),0)

    def test_two_subjects_do_not_pool_fractions_and_tribute_is_not_taxed_again(self):
        w=self.world();w.buildings.seed('example:chu','native:farm')
        shares={actor:{'recipient':'Zhao','percent':10} for actor in ('Qin','Chu')}
        for _ in range(5):w.buildings.produce(production_shares=shares);w.buildings.complete_construction()
        self.assertEqual(w.resources.balance('Zhao','native:food'),0)
        for _ in range(5):w.buildings.produce(production_shares=shares);w.buildings.complete_construction()
        self.assertEqual(w.resources.balance('Zhao','native:food'),2)

    def test_mid_production_save_does_not_accrue_twice_and_resume_keeps_remainder(self):
        w=self.world()
        for _ in range(8):self.produce(w);w.buildings.complete_construction()
        self.produce(w)
        saved=json.loads(json.dumps(w.runtime.save_mod_state(w)))
        restored=self.world();restored.runtime.load_mod_state(restored,saved)
        with self.assertRaises(ValueError):self.produce(restored)
        for world in (w,restored):
            world.buildings.complete_construction();self.produce(world)
        self.assertEqual(w.runtime.save_mod_state(w),restored.runtime.save_mod_state(restored))
        self.assertEqual(restored.resources.balance('Chu','native:food'),1)
        self.assertFalse(restored.buildings.tribute_balances('Qin','Chu'))

    def test_failed_exchange_does_not_advance_fractional_ledger(self):
        w=self.world(9);self.produce(w);w.buildings.complete_construction()
        before=w.runtime.save_mod_state(w);original=w.buildings._resources
        def fail(method,*args):
            if method=='exchange_batch':raise ValueError('simulated exchange rejection')
            return original(method,*args)
        with patch.object(w.buildings,'_resources',fail),self.assertRaises(ValueError):self.produce(w)
        self.assertEqual(w.runtime.save_mod_state(w),before)
        self.produce(w)
        self.assertEqual(w.resources.balance('Chu','native:food'),1)
        self.assertEqual(w.buildings.tribute_balances('Qin','Chu'),{'native:food':0.8})

    def test_corrupt_fractional_ledgers_are_rejected_atomically(self):
        w=self.world();self.produce(w);before=w.buildings.to_save()
        for value in ('NaN','Infinity','-0.1','1','0',0.1,None,'not-a-number'):
            with self.subTest(value=value):
                bad=deepcopy(before);bad['tribute_remainders'][0]['remainder']=value
                with self.assertRaises(ValueError):w.buildings.restore(bad)
                self.assertEqual(w.buildings.to_save(),before)
        bad=deepcopy(before);bad['tribute_remainders']*=2
        with self.assertRaises(ValueError):w.buildings.restore(bad)
        self.assertEqual(w.buildings.to_save(),before)

    def test_ai_resource_query_and_turn_briefing_show_payable_and_receivable_fractions(self):
        f=vassalage.VassalageTests();C,W=vassalage.C,vassalage.W
        for experimental in (False,True):
            with self.subTest(experimental=experimental),TemporaryDirectory() as folder:
                h=f.host(True,experimental=experimental);f.subject(h,W,C)
                site=next(s for s in h.world.buildings.snapshot().sites
                          if s.owner==C and dict(s.resources).get('native:farmland',0)>0)
                h.world.buildings.seed(site.id,'native:farm')
                for _ in range(9):h.cycle()
                before=h.runtime.save_mod_state(h.world)
                names={a:r['name'] for a,r in h.roster.items()}
                for language in ('zh-CN','en-US'):
                    for actor in (C,W):
                        player=h.player(actor);obs=player.observe()
                        result=player.submit('query',{'panel':'econ'})
                        self.assertTrue(result['ok'],result)
                        preview=result['data']['preview']
                        if actor==C:
                            row=preview['tribute']
                            self.assertEqual(row['carried_remainders']['native:food'],0.9)
                            self.assertEqual(row['full_load_outgoing']['native:food'],1)
                            self.assertEqual(row['retained_outputs']['native:food'],0)
                            self.assertEqual(row['full_load_remainders']['native:food'],0)
                        else:
                            row=preview['tribute_receivables'][0]
                            self.assertEqual(row['subject'],C)
                            self.assertEqual(row['remainders']['native:food'],0.9)
                            self.assertNotIn('outputs',row)
                        ui=model_view_for(h.ui, {'language':language});ui.state(obs,[])
                        receipt=ui.receipts.receipt_body('econ',{},result,actor)
                        briefing=ui.state(obs,[])[0]
                        for text in (receipt,briefing):
                            self.assertIn('0.9',text)
                            self.assertNotIn('production_tribute',text)
                            self.assertIn(('累计余额' if actor==C else '尚未入库') if language=='zh-CN'
                                          else ('carried remainder' if actor==C else 'not inventory'),text)
                self.assertEqual(h.runtime.save_mod_state(h.world),before)
                path=Path(folder)/'game.json';h.save(path);loaded=GameHost.load(path,h.cfg)
                self.assertEqual(loaded.to_save(),h.to_save())
                for client in loaded.clients.values():client.play=lambda player,**kwargs:[]
                loaded.cycle()
                self.assertEqual(loaded.world.resources.balance(W,'native:food'),21)
                self.assertNotIn('native:food',loaded.world.buildings.tribute_balances(C,W))
