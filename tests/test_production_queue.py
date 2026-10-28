"""Prepaid production must conserve resources, capacity and saved progress."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
from mods.native.human import HumanAPI
from tests.test_human import config,A,B


class TestConstructionQueue(unittest.TestCase):
    def setUp(self):
        self.cfg=config();self.h=GameHost.new(self.cfg);self.w=self.h.world
        self.site=next(s for s in self.w.buildings.snapshot().sites if s.owner==A)

    def stocks(self):return {s.id:s.amount for s in self.w.resources.snapshot(A)}
    def enqueue(self,kind='castle'):return self.w.buildings.enqueue(A,self.site.id,'native:'+kind)
    def cycle(self):self.h.cycle(on_human=lambda p:[HumanAPI(self.h).submit(A,'end_turn',{})])
    def reload(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'queued.json';self.h.save(path);loaded=GameHost.load(path,self.cfg)
        self.assertEqual(loaded.to_save(),self.h.to_save());return loaded

    def test_queue_prepays_once_and_survives_boundary_and_reload(self):
        before=self.stocks();first=self.enqueue();second=self.enqueue()
        self.assertEqual((first['status'],second['status']),('construction','queued'))
        self.assertEqual(before['native:gold']-self.stocks()['native:gold'],150)
        self.assertEqual(dict(self.w.buildings.reserved_costs(A)),{'native:gold':100,'native:wood':20})
        self.cycle();loaded=self.reload();before={s.id:s.amount for s in loaded.world.resources.snapshot(A)}
        receipts=loaded.world.buildings.advance_orders(A)
        self.assertEqual([r.id for r in receipts],[second['id']])
        self.assertEqual({s.id:s.amount for s in loaded.world.resources.snapshot(A)},before)
        self.assertFalse(loaded.world.buildings.reserved_costs(A))
        self.assertEqual(loaded.world.buildings.advance_orders(A),())

    def test_pending_full_refund_started_half_and_completed_zero(self):
        first=self.enqueue();second=self.enqueue();before=self.stocks()
        cancelled=self.w.buildings.cancel_order(A,second['id'])
        self.assertEqual(dict(cancelled.refunds),second['paid'])
        self.assertEqual(self.stocks()['native:gold']-before['native:gold'],100)
        self.assertEqual(dict(self.w.buildings.demolish(A,first['id']).refunds),{'native:gold':25,'native:wood':10})
        self.assertTrue(self.w.buildings.planning(A,self.site.id)['ordered'])
        pending=self.enqueue();self.cycle();self.w.buildings.advance_orders(A);self.cycle()
        self.assertEqual(self.w.buildings.demolish(A,pending['id']).refunds,())

    def test_queue_holds_per_site_and_shared_resource_limits(self):
        self.enqueue('watchtower')
        with self.assertRaises(ValueError):self.enqueue('watchtower')
        from dataclasses import replace
        self.w.buildings._sites[self.site.id]=replace(self.site,resources=(('native:farmland',1),))
        order=self.enqueue('farm')
        self.assertEqual(order['status'],'queued')
        with self.assertRaises(ValueError):self.enqueue('militia-camp')
        self.w.buildings.cancel_order(A,order['id'])
        self.assertEqual(self.enqueue('militia-camp')['status'],'queued')

    def test_loss_of_site_refunds_pending_without_transferring_order(self):
        self.enqueue();pending=self.enqueue();before=self.stocks()
        self.w.territory.transfer(self.site.id,B)
        self.assertEqual(self.stocks()['native:gold']-before['native:gold'],pending['paid']['native:gold'])
        self.assertFalse(self.w.buildings.reserved_costs(A));self.assertFalse(self.w.buildings.snapshot().orders)
        self.assertFalse(any(o['status']=='queued' for o in self.w.buildings.construction_orders(B)))

    def test_castle_dependencies_cannot_be_removed_from_the_middle(self):
        first=self.enqueue();second=self.enqueue();third=self.enqueue()
        with self.assertRaises(ValueError):self.w.buildings.cancel_order(A,second['id'])
        with self.assertRaises(ValueError):self.w.buildings.demolish(A,first['id'])
        self.w.buildings.cancel_order(A,third['id']);self.w.buildings.cancel_order(A,second['id'])
        self.assertEqual(dict(self.w.buildings.demolish(A,first['id']).refunds)['native:gold'],25)

    def test_reads_are_detached_and_malformed_locked_quote_is_rejected(self):
        self.enqueue();pending=self.enqueue();before=self.w.buildings.to_save()
        rows=self.w.buildings.construction_orders(A);rows[0]['paid']['native:gold']=9999
        self.assertEqual(self.w.buildings.to_save(),before)
        bad=deepcopy(before);bad['queued'][0]['costs']['native:gold']+=1
        with self.assertRaises(ValueError):self.w.buildings.restore(bad)
        self.assertEqual(self.w.buildings.to_save(),before)

    def test_public_queue_api_planning_and_automatic_action_boundary(self):
        api=HumanAPI(self.h);point=dict(zip(('x','y'),self.site.position))
        first=api.submit(A,'queue_build',{'kind':'castle',**point})
        second=api.submit(A,'queue_build',{'kind':'castle',**point})
        self.assertTrue(first['ok']);self.assertTrue(second['ok'])
        self.assertTrue(self.h.player(A).call_api('native:buildings','planning',**point,queued=True)['buildings'])
        self.cycle();self.assertEqual(len(self.w.buildings.reserved_costs(A)),2)
        callbacks=[]
        self.h.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})],on_result=lambda *r:callbacks.append(r))
        automatic=[r for r in callbacks if r[3].get('automatic')=='production_queue']
        self.assertEqual([(r[0],r[1]) for r in automatic],[(A,'build')])
        self.assertFalse(self.w.buildings.reserved_costs(A))
        self.assertEqual(len([b for b in self.w.buildings.instances(self.site.id) if b.kind=='native:castle']),2)

    def test_prepaid_assets_and_expense_recorded_only_on_start(self):
        self.enqueue();before=self.h.player(A).query('query',{'panel':'econ'})
        self.enqueue();after=self.h.player(A).query('query',{'panel':'econ'})
        self.assertEqual(before['gross_assets'],after['gross_assets'])
        self.assertGreater(after['prepaid']['known'],0)
        self.cycle()
        turn=self.w.economy.turns(A)[-1]
        actual=self.w.economy._value({'native:gold':50,'native:wood':20})
        self.assertEqual(turn.investment,actual)
        self.assertEqual(turn.inventory_assets,self.w.economy._value(
            {s.id:s.amount+dict(self.w.buildings.reserved_costs(A)).get(s.id,0) for s in self.w.resources.snapshot(A)}))

    def test_cancelled_prerequisite_blocks_paid_work_without_losing_it_on_reload(self):
        self.enqueue();prerequisite=self.enqueue('watchtower');barracks=self.enqueue('barracks')
        self.w.buildings.cancel_order(A,prerequisite['id']);self.cycle();loaded=self.reload()
        self.assertEqual(loaded.world.buildings.advance_orders(A),())
        order=next(o for o in loaded.world.buildings.construction_orders(A) if o['id']==barracks['id'])
        self.assertIn('建筑数量不足',order['reason'])
        self.assertEqual(order['refunds'],barracks['paid'])

    def test_locked_price_does_not_change_when_later_terrain_quote_changes(self):
        from dataclasses import replace
        self.enqueue();pending=self.enqueue();self.cycle();before=self.stocks()
        self.w.buildings._sites[self.site.id]=replace(self.w.buildings.site(self.site.id),penalty=100)
        receipt=self.w.buildings.advance_orders(A)[0]
        self.assertEqual(dict(receipt.costs),pending['paid']);self.assertEqual(self.stocks(),before)


class TestRecruitmentQueue(unittest.TestCase):
    def setUp(self):
        self.cfg=config();self.cfg['nations'][0]['resources']={'native:food':1000,'native:equipment':1000}
        self.h=GameHost.new(self.cfg);self.w=self.h.world;self.api=HumanAPI(self.h)
        self.site=next(s for s in self.w.buildings.snapshot().sites if s.owner==A)
        self.w.buildings.seed(self.site.id,'native:barracks')
        self.w.buildings.seed(self.site.id,'native:wood-power-plant')

    def stocks(self):return {s.id:s.amount for s in self.w.resources.snapshot(A)}
    def enqueue(self,count=3,kind='infantry'):return self.w.military.enqueue_recruit(A,self.site.id,'native:'+kind,count)
    def cycle(self):self.h.cycle(on_human=lambda p:[self.api.submit(A,'end_turn',{})])

    def test_partial_recruitment_cancels_only_unspawned_costs(self):
        before=self.stocks();order=self.enqueue();cost=self.w.military.recruit_costs(A,'native:infantry')
        self.assertEqual(order['remaining'],2)
        self.assertEqual(len(self.w.units.of(A)),1)
        self.assertEqual({k:before[k]-self.stocks()[k] for k in cost},{k:v*3 for k,v in cost.items()})
        refund=self.w.military.cancel_recruit_order(A,order['id'])
        self.assertEqual(refund['refunds'],{k:v*2 for k,v in cost.items()})
        self.assertEqual(len(self.w.units.of(A)),1)
        self.assertEqual({k:before[k]-self.stocks()[k] for k in cost},cost)

    def test_public_tool_human_state_and_cancellation_use_same_queue(self):
        point=dict(zip(('x','y'),self.site.position))
        receipt=self.api.submit(A,'queue_recruit',{'kind':'infantry','count':3,**point})
        self.assertTrue(receipt['ok']);identity=receipt['data']['id']
        rows=self.api.state(A)['production_queue']['recruitment']
        self.assertEqual((rows[0]['completed'],rows[0]['remaining']),(1,2))
        queried=self.h.player(A).query('query',{'panel':'recruitment_queue'})
        self.assertEqual(rows,queried)
        result=self.api.submit(A,'cancel_recruit_order',{'id':identity})
        self.assertTrue(result['ok']);self.assertEqual(result['data']['refunds'],rows[0]['reserved'])
        self.assertEqual(len(self.w.units.of(A)),1)

    def test_progress_and_save_load_then_automatic_recruit_no_double_debit(self):
        order=self.enqueue();self.cycle()
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'army.json';self.h.save(path);h=GameHost.load(path,self.cfg)
        self.assertEqual(self.h.to_save(),h.to_save())
        results=[]
        def play(p):
            self.assertEqual(h.world.military.recruit_orders(A)[0]['remaining'],1)
            self.assertEqual(len(h.world.units.of(A)),2)
            return [HumanAPI(h).submit(A,'end_turn',{})]
        h.cycle(on_human=play,on_result=lambda *r:results.append(r))
        automatic=[r for r in results if r[3].get('automatic')=='production_queue']
        self.assertEqual([r[2]['action'] for r in automatic],['recruit'])
        self.assertEqual(len(h.world.military._facts),0) # settled once

    def test_lost_facility_waits_and_remaining_costs_survive_restore(self):
        order=self.enqueue();self.cycle()
        building=next(b for b in self.w.buildings.instances(self.site.id) if b.kind=='native:barracks')
        self.w.buildings.demolish(A,building.id)
        before=deepcopy(self.w.military.to_save())
        self.assertEqual(self.w.military.advance_recruit_orders(A),())
        self.assertIn('兵营',self.w.military.recruit_orders(A)[0]['reason'])
        self.w.military.restore(before);self.assertEqual(self.w.military.to_save(),before)
        self.w.territory.transfer(self.site.id,B);self.w.military.cleanup_recruit_orders(A)
        self.assertFalse(self.w.military.reserved_costs(A))

    def test_reads_and_invalid_restore_are_atomic(self):
        self.enqueue();before=deepcopy(self.w.military.to_save())
        rows=self.w.military.recruit_orders(A);rows[0]['reserved']['native:food']=9999
        self.assertEqual(self.w.military.to_save(),before)
        bad=deepcopy(before);bad['recruit_queue'][0]['unit_costs']['native:food']+=1
        with self.assertRaises(ValueError):self.w.military.restore(bad)
        self.assertEqual(self.w.military.to_save(),before)

    def test_militia_queue_reserves_national_capacity(self):
        self.w.buildings.seed(self.site.id,'native:militia-camp')
        order=self.enqueue(2,'militia');self.assertEqual(order['remaining'],1)
        with self.assertRaises(ValueError):self.enqueue(1,'militia')
        with self.assertRaises(ValueError):self.w.military.recruit(A,self.site.id,'native:militia')
        self.w.military.cancel_recruit_order(A,order['id'])
        self.assertEqual(self.enqueue(1,'militia')['remaining'],1)

    def test_prepaid_work_is_fifo_and_not_spendable_or_double_charged(self):
        first=self.enqueue(2);second=self.enqueue(2,'cavalry')
        self.assertEqual([r['kind'] for r in self.w.military.recruit_orders(A)],['native:infantry','native:cavalry'])
        self.cycle();before=self.stocks()
        def play(p):
            self.assertEqual(self.stocks(),before)
            self.assertEqual(len(self.w.units.of(A)),2)
            self.assertEqual([u.kind for u in self.w.units.of(A)],['native:infantry']*2)
            return [self.api.submit(A,'end_turn',{})]
        self.h.cycle(on_human=play)
        self.assertEqual([r['kind'] for r in self.w.military.recruit_orders(A)],['native:cavalry'])

    def test_power_shortage_waits_with_full_prepayment_and_nation_loss_refunds(self):
        self.w.military._grid_short[A]=True
        before=self.stocks();order=self.enqueue(2)
        self.assertEqual(order['remaining'],2);self.assertFalse(self.w.units.of(A))
        self.assertIn('电网',self.w.military.recruit_orders(A)[0]['reason'])
        self.w.military.remove_nation(A)
        self.assertEqual(self.stocks(),before);self.assertFalse(self.w.military.reserved_costs(A))


if __name__=='__main__':unittest.main()
