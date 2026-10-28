"""Persistent routes use public facts and the formal one-turn commander."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.player import wire
from engine.runtime import Runtime
from tests import test_world_domains as fixtures
Q,C,ROOT=fixtures.Q,fixtures.C,fixtures.ROOT


class TestMarchOrders(unittest.TestCase):
    def setUp(self):
        f=fixtures.TestWorldDomains();self.f=f;self.tmp=TemporaryDirectory();p=Path(self.tmp.name);(p/'map').mkdir()
        source=f.edition().replace((ROOT/'mods/native/map_generation/mod.toml').as_posix(),'map/mod.toml')
        (p/'mod.toml').write_text(source)
        (p/'map/mod.toml').write_text('[Mod]\nId="native:map-generation"\nVersion="fixed"\nEntry="tests.test_world_domains:FixedMap"\n')
        self.w=f.world(Runtime.load(p));f.site(self.w,'test:home',Q,1,1);f.site(self.w,'test:other',C,7,7)
        for x in range(7):
            for y in range(7):
                if not self.w.territory.cell(x,y).tile.id:f.site(self.w,f'test:cell-{x}-{y}',Q,x,y,hall=False)
        self.u=self.w.military.seed(Q,'native:infantry',1,1)[0]
        self.p=self.w.runtime.player(self.w,Q)
    def tearDown(self):self.tmp.cleanup()
    def nodes(self,*points,mode='move'):return [{'mode':mode,'position':list(p)} for p in points]
    def queue(self,*points,**kw):
        result=self.p.call_api('native:army','queue_orders',ids=[self.u.id],nodes=self.nodes(*points),**kw)
        self.assertTrue(result['ok'],result);return result['data']
    def orders(self):return self.p.call_api('native:army','orders')
    def cycle(self):self.f.cycle(self.w)
    def advance(self):return self.w.military.advance_march_orders(Q,self.p.observe())

    def test_cross_turn_path_and_append_save_reload_and_no_query_movement(self):
        self.queue((6,1));self.queue((6,6))
        self.assertNotEqual((self.w.units.by_id(self.u.id,Q).x,self.w.units.by_id(self.u.id,Q).y),(6,6))
        state=deepcopy(self.f.save(self.w));self.orders();self.orders();self.assertEqual(self.f.save(self.w),state)
        self.w=self.f.restore(self.w);self.p=self.w.runtime.player(self.w,Q)
        for _ in range(12):
            self.cycle();self.advance()
            if not self.orders():break
        actual=self.w.units.by_id(self.u.id,Q)
        self.assertEqual((actual.x,actual.y),(6,6));self.assertFalse(self.orders())

    def test_healing_latches_below_30_until_full_and_uses_normal_supply(self):
        unit=self.w.units.by_id(self.u.id,Q);unit.update(hp=29)
        self.queue((6,1));self.assertEqual((unit.x,unit.y),(1,1));self.assertTrue(self.orders()[0]['healing'])
        self.cycle();self.assertEqual(unit.hp,59);self.advance();self.assertEqual((unit.x,unit.y),(1,1))
        self.cycle();self.advance();self.assertEqual(unit.hp,89);self.assertEqual((unit.x,unit.y),(1,1))
        self.cycle();self.advance();self.assertEqual(unit.hp,100);self.assertNotEqual((unit.x,unit.y),(1,1))

    def test_heal_flag_can_disable_pause_and_units_have_independent_latches(self):
        unit=self.w.units.by_id(self.u.id,Q);unit.update(hp=29)
        other=self.w.military.seed(Q,'native:cavalry',1,2)[0]
        result=self.p.call_api('native:army','queue_orders',ids=[self.u.id,other.id],nodes=self.nodes((6,1)))
        self.assertTrue(result['ok'],result)
        self.assertEqual([r['aid'] for r in result['data']['started']],[other.id])
        self.p.call_api('native:army','queue_orders',ids=[self.u.id],nodes=[],auto_heal=False)
        self.assertNotEqual((unit.x,unit.y),(1,1))

    def test_healing_wait_explains_existing_supply_and_combat_blockers(self):
        unit=self.w.units.by_id(self.u.id,Q);unit.update(hp=29)
        self.queue((6,1));before=deepcopy(self.f.save(self.w))
        preview=self.w.military.supply_preview(Q)
        for status,text in [('engaged','同格仍在交战'),('supply_shortage','补给不足'),('equipment_shortage','装备不足')]:
            changed=deepcopy(preview);next(r for r in changed['units'] if r['aid']==self.u.id)['healing_status']=status
            with patch.object(self.w.military,'supply_preview',return_value=changed):
                self.assertIn(text,self.orders()[0]['reason'])
        self.assertEqual(self.f.save(self.w),before)

    def test_cancel_node_then_cancel_path_does_not_undo_actual_movement(self):
        self.queue((6,1));self.queue((6,4));self.queue((6,6))
        at=(self.w.units.by_id(self.u.id,Q).x,self.w.units.by_id(self.u.id,Q).y)
        self.p.call_api('native:army','cancel_orders',ids=[self.u.id],node=2)
        self.assertEqual([n['position'] for n in self.orders()[0]['nodes']],[[6,1],[6,6]])
        self.p.call_api('native:army','cancel_orders',ids=[self.u.id])
        self.assertFalse(self.orders());self.assertEqual((self.w.units.by_id(self.u.id,Q).x,self.w.units.by_id(self.u.id,Q).y),at)

    def test_manual_order_replaces_standing_route_only_when_executed(self):
        self.queue((6,1));self.cycle()
        unit=self.w.units.by_id(self.u.id,Q)
        self.p.call_api('native:army','move',ids=[self.u.id],x=unit.x,y=unit.y+1)
        self.assertFalse(self.orders())

    def test_multi_selection_invalid_node_edit_is_atomic(self):
        self.queue((6,1));other=self.w.military.seed(Q,'native:infantry',1,2)[0]
        before=deepcopy(self.w.military.to_save())
        self.assertFalse(self.p.call_api('native:army','cancel_orders',ids=[self.u.id,other.id],node=1)['ok'])
        self.assertEqual(self.w.military.to_save(),before)

    def test_invalid_route_does_not_change_any_unit_order(self):
        self.queue((6,1));before=deepcopy(self.w.military.to_save())
        for nodes in ([{'mode':'teleport','position':[2,2]}],[{'mode':'move','position':[100,1]}]):
            self.assertFalse(self.p.call_api('native:army','queue_orders',ids=[self.u.id],nodes=nodes)['ok'])
            self.assertEqual(self.w.military.to_save(),before)

    def test_malformed_saved_order_is_rejected_atomically(self):
        self.queue((6,1));before=deepcopy(self.w.military.to_save())
        for key,value in [('aid',9999),('owner',C),('last_period',99),('auto_heal','yes')]:
            bad=deepcopy(before);bad['march_orders'][0][key]=value
            with self.assertRaises(ValueError):self.w.military.restore(bad)
            self.assertEqual(self.w.military.to_save(),before)

    def test_dead_unit_and_disband_prune_orders(self):
        self.queue((6,1));self.cycle()
        self.p.call_api('native:army','disband',ids=[self.u.id]);self.w.military.cleanup_march_orders()
        self.assertFalse(self.w.military._march_orders)

    def test_newborn_waits_and_current_turn_cannot_move_twice(self):
        unit=self.w.units.by_id(self.u.id,Q);unit.update(born_turn=0)
        result=self.queue((6,1));self.assertFalse(result['started'])
        self.cycle();self.advance();at=(unit.x,unit.y)
        self.advance();self.assertEqual((unit.x,unit.y),at)

    def test_unknown_map_is_not_read_to_plan_or_export_route(self):
        # Reduce the actual legal vision, then forbid observer access. The
        # chosen destination is player input, never a source of hidden tiles.
        unit=self.w.units.by_id(self.u.id,Q);unit.update(born_turn=0)
        observation=self.p.observe();observation['facts']['view']=[t for t in observation['facts']['view'] if t['position'][0]<3]
        with patch.object(self.w.territory,'terrain',side_effect=AssertionError('hidden world terrain read')):
            self.w.military.enqueue_orders(Q,[self.u.id],self.nodes((6,1)),observation)
            row=self.w.military.march_orders(Q,observation)[0]['nodes'][0]
            self.assertTrue(row['uncertain']);self.assertEqual(row['points'],[])

    def test_attack_itinerary_moves_through_friendly_land_without_changing_direct_attack(self):
        result=self.p.call_api('native:army','queue_orders',ids=[self.u.id],nodes=self.nodes((6,1),mode='attack'))
        self.assertTrue(result['ok'],result);self.assertEqual(result['data']['started'][0]['mode'],'move')
        self.cycle();unit=self.w.units.by_id(self.u.id,Q)
        rejected=self.p.call_api('native:army','attack',ids=[self.u.id],x=unit.x,y=unit.y)
        self.assertFalse(rejected['ok']);self.assertTrue(self.orders())

    def test_multiple_attackers_do_not_stall_when_first_unit_captures_goal(self):
        target=(2,1);self.w.territory.transfer('test:cell-2-1',C)
        self.f.declare(self.w,Q,C)
        other=self.w.military.seed(Q,'native:infantry',1,2)[0]
        result=self.p.call_api('native:army','queue_orders',ids=[self.u.id,other.id],nodes=self.nodes(target,mode='attack'))
        self.assertTrue(result['ok'],result)
        self.assertEqual([r['mode'] for r in result['data']['started']],['attack','move'])
        self.assertFalse(self.orders())

    def test_war_goal_waits_when_there_is_no_effective_war(self):
        self.w.territory.transfer('test:cell-2-1',C)
        result=self.p.call_api('native:army','queue_orders',ids=[self.u.id],nodes=self.nodes((2,1),mode='attack'))
        self.assertTrue(result['ok'],result);self.assertFalse(result['data']['started'])
        self.assertIn('没有生效的战争',self.orders()[0]['reason'])

    def test_defended_target_is_one_battle_not_a_repeated_attack_each_turn(self):
        target=(2,1);self.w.territory.transfer('test:cell-2-1',C);self.f.declare(self.w,Q,C)
        self.w.military.seed(C,'native:infantry',*target)
        result=self.p.call_api('native:army','queue_orders',ids=[self.u.id],nodes=self.nodes(target,mode='attack'))
        self.assertTrue(result['ok'],result);self.assertTrue(result['data']['started'][0]['receipt']['engaged'])
        before=deepcopy(self.w.military.to_save());self.assertFalse(self.advance());self.assertEqual(self.w.military.to_save(),before)

    def test_real_session_boundary_resumes_saved_order_and_records_motion(self):
        from host import GameHost
        from tests.test_human import config,A
        from mods.native.human import HumanAPI
        cfg=config();h=GameHost.new(cfg);w=h.world;api=HumanAPI(h)
        for x in range(12):
            for y in range(12):
                if not w.territory.cell(x,y).tile.id:w.territory.create(f'test:corridor-{x}-{y}',A,x,y,terrain='native:plain')
        home=next(s for s in w.buildings.snapshot().sites if s.owner==A)
        unit=w.military.seed(A,'native:infantry',*home.position)[0]
        goal=max((s.position for s in w.buildings.snapshot().sites if s.owner==A),key=lambda p:max(abs(p[0]-unit.x),abs(p[1]-unit.y)))
        receipt=api.submit(A,'queue_orders',{'ids':[unit.id],'nodes':[{'mode':'move','position':list(goal)}]})
        self.assertTrue(receipt['ok'],receipt)
        h.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})])
        path=Path(self.tmp.name)/'standing.json';h.save(path);loaded=GameHost.load(path,cfg)
        self.assertEqual(h.to_save(),loaded.to_save())
        calls=[];loaded.cycle(on_human=lambda p:[HumanAPI(loaded).submit(A,'end_turn',{})],on_result=lambda *r:calls.append(r))
        actual=[r for r in calls if r[3].get('automatic')=='march_orders']
        self.assertTrue(actual);self.assertTrue(actual[0][3]['data']['position_changed'])

    def test_each_automatic_motion_has_its_own_intermediate_snapshot(self):
        other=self.w.military.seed(Q,'native:infantry',1,2)[0]
        self.p.call_api('native:army','queue_orders',ids=[self.u.id,other.id],nodes=self.nodes((6,1)))
        self.cycle();snapshots=[]
        self.w.military.advance_march_orders(Q,self.p.observe(),refresh=self.p.observe,
            on_start=lambda r:snapshots.append([(u.id,u.x,u.y) for u in self.w.units.of(Q)]))
        self.assertEqual(len(snapshots),2);self.assertNotEqual(snapshots[0],snapshots[1])

    def test_other_nations_standing_order_waits_for_its_turn(self):
        from host import GameHost
        from tests.test_human import config,A,B
        from mods.native.human import HumanAPI
        cfg=config();cfg['nations'].reverse()
        h=GameHost.new(cfg);api=HumanAPI(h);sites=[s for s in h.world.buildings.snapshot().sites if s.owner==B]
        origin=sites[0].position;goal=sites[1].position
        unit=h.world.military.seed(B,'native:infantry',*origin)[0];h.world.units.by_id(unit.id,B).update(born_turn=0)
        receipt=h.player(B).call_api('native:army','queue_orders',ids=[unit.id],nodes=[{'mode':'move','position':list(goal)}])
        self.assertTrue(receipt['ok'],receipt)
        h.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})]);calls=[]
        def human(p):
            actual=h.world.units.by_id(unit.id,B)
            self.assertEqual((actual.x,actual.y),origin)
            return [api.submit(A,'end_turn',{})]
        h.cycle(on_human=human,on_result=lambda *r:calls.append(r))
        movement=next(i for i,r in enumerate(calls) if r[0]==B and r[3].get('automatic')=='march_orders')
        ended=next(i for i,r in enumerate(calls) if r[0]==A and r[1]=='end_turn')
        self.assertGreater(movement,ended)


if __name__=='__main__':unittest.main()
