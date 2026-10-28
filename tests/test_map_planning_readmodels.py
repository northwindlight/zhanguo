"""Map filters and cost explanations use real owned-site construction facts."""
from dataclasses import replace
from copy import deepcopy
import unittest
from unittest.mock import patch

from host import GameHost
from mods.native.human import HumanAPI
from mods.ui.desktop_catalog import present
from tests.test_human import config,A,B


class TestMapPlanningReadModels(unittest.TestCase):
    def setUp(self):
        self.h=GameHost.new(config());self.w=self.h.world;self.api=HumanAPI(self.h)
        self.site=next(s for s in self.w.buildings.snapshot().sites if s.owner==A)

    def space(self):
        return next(r for r in self.h.player(A).call_api('native:buildings','sites')['capacities'] if r['site']==self.site.id)

    def test_shared_resource_space_reserves_actual_and_pending_and_refunds_release_it(self):
        self.w.buildings._sites[self.site.id]=replace(self.site,capacity=4,resources=(('native:farmland',2),('native:wood',1)))
        first=self.w.buildings.enqueue(A,self.site.id,'native:farm')
        second=self.w.buildings.enqueue(A,self.site.id,'native:militia-camp')
        space=self.space();farmland=next(r for r in space['resources'] if r['resource']=='native:farmland')
        self.assertEqual((space['occupied'],space['free'],space['construction'],space['pending']),(3,1,1,1))
        self.assertEqual((farmland['used'],farmland['free']),(2,0));self.assertEqual(space['order_remaining'],0)
        planning=self.w.buildings.planning(A,self.site.id,queued=True)
        self.assertFalse(next(r for r in planning['buildings'] if r['kind']=='native:farm')['available'])
        self.w.buildings.cancel_order(A,second['id']);space=self.space()
        self.assertEqual((space['free'],space['pending']),(2,0))
        self.assertEqual(next(r['free'] for r in space['resources'] if r['resource']=='native:farmland'),1)
        self.w.buildings.demolish(A,first['id'])
        self.assertEqual(self.space()['free'],3);self.assertEqual(self.space()['order_remaining'],0)

    def test_read_models_are_detached_and_only_owned_sites_are_returned(self):
        before=deepcopy(self.h.to_save())
        with patch.object(self.h,'observer_snapshot',side_effect=AssertionError('observer leak')):
            state=self.api.state(A);data=self.api.site_options(A,*self.site.position)
            cards=present(self.h.ui,data,state)
        own={s['id'] for s in state['facts']['building_sites']['sites']}
        self.assertEqual({r['site'] for r in state['facts']['building_sites']['capacities']},own)
        self.assertEqual(cards['terrain']['id'],next(c['current']['terrain'] for c in state['map']['tiles'] if c['position']==list(self.site.position)))
        foreign=next(s for s in self.w.buildings.snapshot().sites if s.owner==B)
        with self.assertRaises(ValueError):self.w.buildings.site_capacity(A,foreign.id)
        state['facts']['building_sites']['capacities'][0]['free']=-999
        self.assertEqual(before,self.h.to_save())

    def test_price_steps_use_actual_rounding_and_match_the_paid_cost(self):
        from mods.native.buildings.types import Farm
        self.w.buildings._sites[self.site.id]=replace(self.site,penalty=15)
        self.w.buildings.seed(self.site.id,'native:academy')
        before=self.h.to_save()
        row=next(r for r in self.w.buildings.planning(A,self.site.id,policy_pct=123)['buildings'] if r['kind']=='native:farm')
        steps=row['cost_breakdown'];price=row['price']
        self.assertEqual([s['id'] for s in steps],['base','terrain','discount','policy'])
        self.assertEqual(steps[1]['after'],Farm.construction_cost(1,penalty=15).get('native:gold',0))
        self.assertEqual(steps[2]['after'],Farm.construction_cost(1,penalty=15,discount=price['discount']).get('native:gold',0))
        self.assertEqual(sum(s['change'] for s in steps),price['costs']['native:gold'])
        self.assertEqual(before,self.h.to_save())
        receipt=self.w.buildings.build(A,self.site.id,'native:farm',policy_pct=123)
        self.assertEqual(dict(receipt.costs),price['costs'])

    def test_new_read_models_do_not_change_saved_state_or_dependencies(self):
        data=deepcopy(self.h.to_save());self.space();self.api.site_options(A,*self.site.position)
        self.assertEqual(self.h.to_save(),data)
        other=GameHost(GameHost.prepare(config()));other.session.restore(data)
        self.assertEqual(other.to_save(),data)
