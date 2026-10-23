"""Natural world limits and movement, using generated/synthetic maps only."""
from collections import Counter
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.runtime import Runtime
from host import GameHost
from mods.native.map_generation.algorithm import MapGen, _quotas
from mods.native.map_generation.mod import MapGenerator, MapCell
from mods.native.map_generation.parameters import TERRAIN_WEIGHTS, RESOURCES
from ruleai.v11plus import pathfind, targeting
from scenarios.eight_nations import build

ROOT=Path(__file__).resolve().parents[1]
BLOCKED={'native:high-mountain','native:ocean'}
Q,C='scenario:qin','scenario:chu'


def flood(start, points):
    seen={start};queue=[start]
    for x,y in queue:
        for p in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            if p in points and p not in seen:seen.add(p);queue.append(p)
    return seen


class TestBoundaryGeneration(unittest.TestCase):
    def test_sealed_rim_connected_land_coastal_seas_and_resource_quotas(self):
        for size in (8,12,24,60):
            for seed in (0,73,20260921,20261023):
                with self.subTest(size=size,seed=seed):
                    g=MapGen(seed,size);points={(x,y) for y in range(size) for x in range(size)}
                    rim={p for p in points if min(*p,size-1-p[0],size-1-p[1])==0}
                    self.assertTrue(all(g.terrain(*p) in BLOCKED for p in rim))
                    land={p for p in points if g.terrain(*p) not in BLOCKED}
                    self.assertTrue(land);self.assertEqual(flood(min(land),land),land)
                    counts=Counter(g.terrain(*p) for p in land)
                    self.assertEqual(counts,Counter(_quotas(TERRAIN_WEIGHTS,len(land))))
                    self.assertEqual({g.terrain(*p) for p in points if g.terrain(*p) in BLOCKED},BLOCKED)
                    for p in points-land:self.assertTrue(all(v==0 for v in g.resources(*p).values()))
                    water={p for p in points if g.terrain(*p)=='native:ocean'}
                    while water:
                        group=flood(min(water),water);water-=group
                        self.assertTrue(group & rim)

    def test_continent_shoulders_are_not_a_rectangular_one_cell_fence(self):
        for seed in (0,73,20260921,20261023):
            g=MapGen(seed,24)
            depths=[]
            for x in range(1,23):
                land=[y for y in range(24) if g.terrain(x,y) not in BLOCKED]
                if land:depths.append(min(land))
            self.assertGreaterEqual(len(set(depths)),3,seed)
            self.assertGreaterEqual(max(depths)-min(depths),3,seed)

    def test_cross_process_determinism_includes_boundaries_and_names(self):
        code="""import hashlib,json
from mods.native.map_generation.algorithm import MapGen
g=MapGen(20261023,24)
rows=[(g.terrain(x,y),g.resources(x,y),g.name(x,y)) for y in range(24) for x in range(24)]
print(hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest())
"""
        results=[subprocess.check_output([sys.executable,'-c',code],cwd=ROOT,
                 env={**os.environ,'PYTHONHASHSEED':salt},text=True).strip() for salt in ('1','777')]
        self.assertEqual(*results)

    def test_initial_and_late_arrivals_avoid_barriers_without_mutating_the_rim(self):
        for seed in (73,20260921,20261023):
            h=build(seed=seed)
            for actor in h.actors():
                self.assertEqual(len(h.world.territory.owned(actor)),5)
                self.assertTrue(all(h.world.territory.terrain(*t.tile.position).passable for t in h.world.territory.owned(actor)))
            for p in ((x,y) for x in range(24) for y in range(24) if min(x,y,23-x,23-y)==0):
                self.assertFalse(h.world.territory.terrain(*p).passable)
                self.assertIsNone(h.world.territory.owner(*p))
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/'synthetic.json';h.save(path)
                self.assertEqual(GameHost.load(path,h.cfg).to_save(),h.to_save())
        h=GameHost.new({'map_size':24,'seed':73,'nations':[
            {'id':Q,'name':'秦','client':'rule','position':[0,0]},
            {'id':C,'name':'楚','client':'rule','appearance':'manual'}]})
        h.add('楚')
        self.assertEqual(len(h.world.territory.owned(C)),5)
        self.assertTrue(all(h.world.territory.terrain(*t.tile.position).passable for t in h.world.territory.snapshot()))


def barrier_cell(_generator, seed, size, x, y, terrain=None):
    # A continuous north/south ridge with a single orthogonal pass at (3,4).
    kind=terrain or ('native:ocean' if min(x,y,size-1-x,size-1-y)==0 else
                     'native:high-mountain' if x==3 and y!=4 else 'native:plain')
    return MapCell((x,y),kind,tuple((r,0 if kind in BLOCKED else 3) for r in RESOURCES),f'测试{x}-{y}')


class TestBarrierRules(unittest.TestCase):
    def setUp(self):
        p=patch.object(MapGenerator,'cell',barrier_cell);p.start();self.addCleanup(p.stop)
        self.w=Runtime.load(ROOT/'mods.toml').world_class()()
        self.w.territory.configure(8,73)
        for actor,name,x in ((Q,'秦',2),(C,'楚',5)):
            self.w.nations.create(actor,name,{'native:gold':10000,'native:food':100,'native:supply':1000,'native:equipment':100})
            self.w.territory.create(actor+'/capital',actor,x,2)
            self.w.buildings.seed(actor+'/capital','native:town-hall')

    def test_obstacles_have_no_guards_sites_or_claims_and_rejections_are_atomic(self):
        w=self.w;w.territory.create(Q+'/coast',Q,2,1)
        unit=w.military.seed(Q,'native:cavalry',2,2)[0]
        before=deepcopy(w.runtime.save_mod_state(w))
        for point in ((3,2),(1,0)):
            for action in ('move','attack'):
                with self.assertRaises(ValueError):w.call_tool(Q,action,{'aid':unit.id,'x':point[0],'y':point[1]})
            with self.assertRaises(ValueError):w.territory.create_claim('scenario:illegal',Q,*point)
            with self.assertRaises(ValueError):w.military.seed(Q,'native:infantry',*point)
        self.assertEqual(w.runtime.save_mod_state(w),before)
        self.assertTrue(all(w.territory.terrain(u.x,u.y).claimable for u in w.military.visible_units(Q)))
        for attack in (False,True):
            self.assertTrue(all(w.territory.terrain(*p).passable for p,_ in w.military.reachable(Q,unit.id,attack)))

    def test_cavalry_cannot_jump_ridge_but_can_travel_through_pass(self):
        w=self.w;unit=w.military.seed(Q,'native:cavalry',2,2)[0]
        self.assertNotIn((4,2),dict(w.military.reachable(Q,unit.id,True)))
        with self.assertRaises(ValueError):w.military.attack(Q,[unit.id],4,2)
        for point in ((2,4),(4,4),(4,2)):
            w.units.by_id(unit.id,Q).update(moved_turn=-1)
            w.military.move_many(Q,[unit.id],*point)
            actual=w.units.by_id(unit.id,Q)
            self.assertEqual((actual.x,actual.y),point)

    def test_diagonal_corner_cannot_be_squeezed_in_move_attack_or_retreat(self):
        w=self.w;unit=w.military.seed(Q,'native:infantry',3,4)[0]
        for attack in (False,True):
            self.assertNotIn((4,3),dict(w.military.reachable(Q,unit.id,attack)))
            self.assertIn((4,4),dict(w.military.reachable(Q,unit.id,attack)))
        w.units.by_id(unit.id,Q).update(engaged=True)
        with self.assertRaises(ValueError):w.military.retreat_many(Q,[unit.id],4,3)

    def test_peacetime_repatriation_routes_around_the_ridge(self):
        w=self.w;unit=w.military.seed(Q,'native:cavalry',5,2)[0]
        w.military.repatriate();actual=w.units.by_id(unit.id,Q)
        self.assertEqual((actual.x,actual.y),(4,4))
        self.assertTrue(w.territory.terrain(actual.x,actual.y).passable)

    def test_catalog_and_ai_treat_barriers_as_unclaimable(self):
        w=self.w;w.military.seed(Q,'native:cavalry',2,2)
        catalog=w.call_tool(Q,'terrain_catalog',{})
        blocked=[t for t in catalog if not t.passable]
        self.assertEqual({t.id for t in blocked},BLOCKED)
        self.assertTrue(all(not t.claimable and t.cavalry_cost is None for t in blocked))
        player=w.runtime.player(w,Q)
        obs=player.observe()
        self.assertNotIn((3,2),targeting.candidates(obs))
        visible=obs['facts']['view']
        knowledge={'view':visible,'allies':{Q},'unit_catalog':player.query('unit_catalog')}
        self.assertGreater(pathfind.distance(knowledge,'native:cavalry',(2,2),(4,2)),2)

    def test_restore_rejects_army_on_natural_barrier(self):
        w=self.w;w.military.seed(Q,'native:cavalry',2,2)
        state=deepcopy(w.runtime.save_mod_state(w))
        state['native:army']['units']['armies'][0]['x']=3
        other=w.runtime.world_class()()
        with self.assertRaises(ValueError):w.runtime.load_mod_state(other,state)
