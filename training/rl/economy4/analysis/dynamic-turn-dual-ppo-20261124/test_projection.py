"""Validate legacy occupancy projection against real main queued construction."""
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0,'/home/northwind/zhanguo')
from host import GameHost
from tests.test_human import config,A,B
from legacy_projection import project_buildings


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.h=GameHost.new(config());self.w=self.h.world
        self.site=next(s for s in self.w.buildings.snapshot().sites if s.owner==A)

    def stocks(self):return {r.id:r.amount for r in self.w.resources.snapshot(A)}
    def view(self):return project_buildings(self.h.player(A).call_api('native:buildings','sites',{}))[self.site.id]
    def enqueue(self,kind='native:castle'):
        result=self.h.player(A).call_api('native:buildings','enqueue',dict(site=self.site.id,kind=kind))
        # Writing API returns an ordinary receipt wrapper.
        return result.get('data',result)

    def test_submit_is_immediately_occupied_but_not_completed(self):
        before=self.view();self.enqueue();after=self.view()
        self.assertEqual(after['occupied'],before['occupied']+1)
        self.assertEqual(after['free'],before['free']-1)
        self.assertEqual(after['pending'].get('native:castle'),1)
        self.assertEqual(after['completed'].get('native:castle',0),0)
        self.assertEqual(len(self.w.buildings.instances(self.site.id)),1)

    def test_prepaid_stock_is_not_charged_again_by_observation(self):
        before=self.stocks();order=self.enqueue();paid=self.stocks()
        for key,value in order['paid'].items():self.assertEqual(before[key]-paid[key],value)
        self.view();self.view()
        self.assertEqual(paid,self.stocks())

    def test_queued_power_does_not_appear_as_completed_power(self):
        self.enqueue('native:wood-power-plant');view=self.view()
        self.assertEqual(view['pending']['native:wood-power-plant'],1)
        self.assertEqual(view['completed'].get('native:wood-power-plant',0),0)

    def test_starting_queue_does_not_double_count_occupancy(self):
        self.enqueue();before=self.view();self.w.buildings.advance_orders(A);after=self.view()
        self.assertEqual(before,after)
        self.assertEqual(len(self.w.buildings.instances(self.site.id)),2)

    def test_cancel_restores_space_and_main_full_refund(self):
        before=self.view();stocks=self.stocks();order=self.enqueue()
        self.w.buildings.cancel_order(A,order['id'])
        self.assertEqual(self.view(),before);self.assertEqual(self.stocks(),stocks)

    def test_own_only_and_detached(self):
        foreign=next(s for s in self.w.buildings.snapshot().sites if s.owner==B)
        self.w.buildings.enqueue(B,foreign.id,'native:castle')
        rows=project_buildings(self.h.player(A).call_api('native:buildings','sites',{}))
        self.assertNotIn(foreign.id,rows)
        rows[self.site.id]['completed']['native:town-hall']=999
        self.assertEqual(self.view()['completed']['native:town-hall'],1)


if __name__=='__main__':
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ProjectionTests))
    Path(__file__).with_name('projection-proof.json').write_text(json.dumps(dict(tests=result.testsRun,
        passed=result.wasSuccessful(),main_semantics='Submission prepays/reserves; start deferred; original execution and refunds retained'),indent=2))
    raise SystemExit(not result.wasSuccessful())
