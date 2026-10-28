"""Regression cases for player previews versus the formal military validator."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from engine.player import PlayerSession
from mods.native.army.candidates import candidates, command_states
from mods.native.human import HumanAPI
from tests import test_world_domains as fixture
from tests.test_world_domains import Q, C, Z


class TestArmyNavigation(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TestWorldDomains();self.w=self.f.world()
        self.f.site(self.w,'scenario:navigation-q',Q,2,2)
        self.f.site(self.w,'scenario:navigation-c',C,3,2)
        self.f.site(self.w,'scenario:navigation-z',Z,4,2)
        self.player=PlayerSession(self.w.runtime,self.w,Q)
        self.api=HumanAPI(SimpleNamespace(runtime=self.w.runtime,world=self.w,
            clients={Q:SimpleNamespace(kind='human')},player=lambda actor:self.player,grid=lambda:(8,73)))

    def targets(self,ids,mode):return self.api.targets(Q,ids,mode)['targets']

    def test_neutral_attacker_does_not_block_attack_on_enemy_owned_land(self):
        self.f.declare(self.w,Q,C);self.f.declare(self.w,Z,C)
        a=self.w.military.seed(Q,'native:infantry',2,2)[0]
        third=self.w.military.seed(Z,'native:infantry',3,2)[0]
        self.w.units.by_id(third.id,Z).update(engaged=True)
        self.w.military.seed(C,'native:infantry',3,2)
        self.assertIn([3,2],[t['position'] for t in self.targets([a.id],'attack')])
        result=self.api.submit(Q,'attack',{'ids':[a.id],'x':3,'y':2})
        self.assertTrue(result['ok']);self.assertTrue(result['data']['engaged'])

    def test_neutral_only_engagement_on_wilderness_remains_blocked(self):
        point=(2,3);self.w.territory.create('scenario:navigation-wild',None,*point,terrain='native:plain')
        a=self.w.military.seed(Q,'native:infantry',2,2)[0]
        third=self.w.military.seed(Z,'native:infantry',*point)[0]
        self.w.units.by_id(third.id,Z).update(engaged=True)
        self.assertNotIn(list(point),[t['position'] for t in self.targets([a.id],'attack')])
        result=self.player.submit('military',{'action':'attack','ids':[a.id],'x':point[0],'y':point[1]})
        self.assertFalse(result['ok'])

    def test_in_place_attack_after_moving_is_enabled_by_rule_projection(self):
        point=(2,3);self.w.territory.create('scenario:navigation-wild',None,*point,terrain='native:plain')
        a=self.w.military.seed(Q,'native:infantry',2,2)[0]
        self.w.military.move_many(Q,[a.id],*point)
        state=command_states(self.player.observe(),8)[a.id]
        self.assertFalse(state['move']['available']);self.assertTrue(state['attack']['available'])
        self.assertIn(list(point),[t['position'] for t in self.targets([a.id],'attack')])
        result=self.api.submit(Q,'attack',{'ids':[a.id],'x':point[0],'y':point[1]})
        self.assertTrue(result['ok']);self.assertTrue(result['data']['occupied'])

    def test_own_land_under_attack_still_allows_incoming_movement(self):
        self.f.declare(self.w,Q,C)
        self.w.territory.create('scenario:navigation-own',Q,2,3,terrain='native:plain')
        a=self.w.military.seed(Q,'native:infantry',2,2)[0]
        enemy=self.w.military.seed(C,'native:infantry',2,3)[0]
        self.w.units.by_id(enemy.id,C).update(engaged=True)
        self.assertIn([2,3],[t['position'] for t in self.targets([a.id],'move')])
        self.assertTrue(self.api.submit(Q,'move',{'ids':[a.id],'x':2,'y':3})['ok'])

    def test_visible_candidates_match_formal_batch_for_all_modes_and_unit_states(self):
        self.f.declare(self.w,Q,C)
        a=self.w.military.seed(Q,'native:cavalry',2,2)[0]
        for moved,engaged,newborn in ((-1,False,None),(self.w.government.period(),False,None),(-1,True,None),(-1,False,self.w.government.period())):
            unit=self.w.units.by_id(a.id,Q);unit.update(moved_turn=moved,engaged=engaged,born_turn=newborn)
            obs=self.player.observe();visible={tuple(t['position']) for t in obs['facts']['view']}
            for mode in ('move','attack'):
                shown={tuple(t['position']) for t in candidates(obs,[a.id],mode,8)['targets'] if not t['uncertain']}
                for point in visible:
                    if point==(2,2) and mode=='move':continue
                    try:self.w.military._batch(Q,[a.id],*point,mode=='attack')
                    except ValueError:legal=False
                    else:legal=True
                    # Unknown intermediate cells can make a route an attempt.
                    if point in shown:self.assertTrue(legal,(mode,moved,engaged,newborn,point))
                    geometric=dict(self.w.military.reachable(Q,a.id,mode=='attack',visible_only=True))
                    if legal and point in geometric:self.assertIn(point,shown,(mode,point))

    def test_routes_use_shared_cost_and_corner_rules_without_hidden_facts(self):
        self.w.territory.transfer('scenario:navigation-c',Q)
        self.w.territory.transfer('scenario:navigation-z',Q)
        a=self.w.military.seed(Q,'native:cavalry',2,2)[0]
        for point in ((3,3),(4,3),(2,3),(1,2),(1,3)):
            cell=self.w.territory.cell(*point)
            self.w.territory._cells[point]=replace(cell,tile=replace(cell.tile,terrain='native:plain'))
        wall=(2,3);cell=self.w.territory.cell(*wall)
        self.w.territory._cells[wall]=replace(cell,tile=replace(cell.tile,terrain='native:high-mountain'))
        shown=self.targets([a.id],'move')
        detour=next(t for t in shown if t['position']==[3,3])
        self.assertEqual(detour['routes'][0]['points'],[[2,2],[3,2],[3,3]])
        for target in shown:
            if target['uncertain']:continue
            points=target['routes'][0]['points'];self.assertEqual(points[0],[2,2]);self.assertEqual(points[-1],target['position'])
            self.assertNotIn(list(wall),points)
            costs=self.w.units.definition('native:cavalry')['move_cost']
            distance=sum(max(costs[self.w.territory.cell(*a).tile.terrain],costs[self.w.territory.cell(*b).tile.terrain]) for a,b in zip(points,points[1:]))
            self.assertEqual(distance,dict(self.w.military.reachable(Q,a.id))[tuple(target['position'])])


if __name__=='__main__':unittest.main()
