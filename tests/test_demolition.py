"""Shared demolition rules, accounting, ownership and save/load consistency."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests import test_building_objects as fixtures
from tests.test_human import config,A,B
from host import GameHost
from mods.native.human import HumanAPI


class TestDemolition(unittest.TestCase):
    def world(self):return fixtures.TestBuildings().world()

    def test_cancel_refunds_actual_discounted_materials_exactly_half_and_keeps_quota(self):
        w=self.world();b=w.buildings
        b.seed('example:qin','native:academy')
        paid=b.build('Qin','example:qin','native:farm',policy_pct=80)
        before={r.id:r.amount for r in w.resources.snapshot('Qin')}
        quote=b.demolition_quote('Qin',paid.id)
        result=b.demolish('Qin',paid.id)
        self.assertFalse(result.completed)
        self.assertEqual(dict(result.refunds),{key:value/2 for key,value in paid.costs})
        self.assertEqual(dict(result.refunds),quote['refunds'])
        for key,value in result.refunds:self.assertEqual(w.resources.balance('Qin',key),before[key]+value)
        self.assertEqual(dict(result.refunds)['native:wood'],7.5)
        with self.assertRaises(ValueError):b.build('Qin','example:qin','native:farm')
        self.assertFalse(any(i.id==paid.id for i in b.instances()))
        before=w.runtime.save_mod_state(w)
        with self.assertRaises(ValueError):b.demolish('Qin',paid.id)
        self.assertEqual(w.runtime.save_mod_state(w),before)
        report=w.resolve_turn()['building_production']
        loss=next(r for r in report.construction if r.id==paid.id)
        self.assertEqual(dict(loss.costs),dict(result.refunds))
        self.assertEqual(dict(report.outputs),{})

    def test_completed_demolition_refunds_zero_and_stops_production(self):
        w=self.world();b=w.buildings
        paid=b.build('Qin','example:qin','native:farm');w.resolve_turn()
        stocks=w.resources.snapshot('Qin')
        self.assertTrue(b.demolish('Qin',paid.id).completed)
        self.assertEqual(stocks,w.resources.snapshot('Qin'))
        self.assertEqual(b.replacement_costs('Qin'),())
        report=w.resolve_turn()['building_production']
        self.assertFalse(report.records);self.assertFalse(report.outputs)

    def test_forbidden_invalid_and_non_top_castle_demolitions_are_atomic(self):
        w=self.world();b=w.buildings
        items=b.seed('example:qin','native:castle',3)
        before=w.runtime.save_mod_state(w)
        for owner,identity in [('Chu',items[-1].id),('Qin',items[0].id),('Qin',True),('Qin',0),('Qin',999)]:
            with self.subTest(owner=owner,id=identity),self.assertRaises(ValueError):b.demolish(owner,identity)
            self.assertEqual(w.runtime.save_mod_state(w),before)
        self.assertFalse(b.demolition_quote('Qin',items[0].id)['available'])
        b.demolish('Qin',items[-1].id)
        self.assertEqual([x.level for x in b.instances()], [1,2])
        saved=b.to_save();b.restore(deepcopy(saved));self.assertEqual(b.to_save(),saved)
        next_level=b.build('Qin','example:qin','native:castle')
        self.assertEqual(b.instances()[-1].level,3)
        self.assertEqual(dict(next_level.costs)['native:gold'],200)

    def test_refund_rejection_preserves_building_receipt_and_counters(self):
        w=self.world();b=w.buildings
        paid=b.build('Qin','example:qin','native:farm');before=w.runtime.save_mod_state(w)
        with patch.object(b,'_resources',side_effect=ValueError('synthetic credit rejected')):
            with self.assertRaises(ValueError):b.demolish('Qin',paid.id)
        self.assertEqual(w.runtime.save_mod_state(w),before)

    def test_cancelled_costs_survive_save_load_without_phantom_completion(self):
        w=self.world();b=w.buildings
        paid=b.build('Qin','example:qin','native:farm');b.demolish('Qin',paid.id)
        saved=w.runtime.save_mod_state(w);clone=self.world();clone.runtime.load_mod_state(clone,deepcopy(saved))
        self.assertEqual(clone.runtime.save_mod_state(clone),saved)
        a=w.resolve_turn();z=clone.resolve_turn()
        self.assertEqual(a['building_production'],z['building_production'])
        self.assertEqual(a['building_completions'],())
        self.assertFalse(b.to_save()['cancelled'])

    def test_unpaid_seed_construction_cannot_mint_refunds(self):
        w=self.world();b=w.buildings
        item,=b.seed('example:qin','native:farm',completed=False)
        before=w.resources.snapshot('Qin')
        self.assertEqual(b.demolish('Qin',item.id).refunds,())
        self.assertEqual(w.resources.snapshot('Qin'),before)

    def test_human_and_model_tools_share_api_and_owner_gate(self):
        h=GameHost.new(config());api=HumanAPI(h)
        site=next(s for s in h.world.buildings.snapshot().sites if s.owner==A)
        action=api.submit(A,'build',{'kind':'castle','x':site.position[0],'y':site.position[1]})
        identity=action['data']['id'];before=h.world.resources.snapshot(A)
        self.assertFalse(h.player(B).submit('demolish',{'id':identity})['ok'])
        self.assertEqual(h.world.resources.snapshot(A),before)
        result=api.submit(A,'demolish',{'id':identity})
        self.assertTrue(result['ok']);self.assertEqual(dict(result['data']['refunds']),{'native:gold':25,'native:wood':10})
        self.assertNotIn(identity,{r['id'] for r in api.site_options(A,*site.position)['demolitions']})
        self.assertIn('demolish',{s['function']['name'] for s in h.player(B).actions()})

    def test_cancellation_accounts_only_lost_materials_as_investment(self):
        h=GameHost.new(config());api=HumanAPI(h)
        site=next(s for s in h.world.buildings.snapshot().sites if s.owner==A)
        paid=api.submit(A,'build',{'kind':'castle','x':site.position[0],'y':site.position[1]})
        api.submit(A,'demolish',{'id':paid['data']['id']})
        h.cycle(on_human=lambda p:api.submit(A,'end_turn',{}))
        facts=h.world.economy.to_save()['events']
        event=next(r for r in facts if r['fact']['id']==f'native:building-construction/{paid["data"]["id"]}')
        self.assertEqual(dict(event['fact']['inputs']),{'native:gold':25,'native:wood':10})


if __name__=='__main__':unittest.main()
