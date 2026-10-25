"""Real offline v13 actions, presentation replay and boundary acknowledgement."""
from copy import deepcopy
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from host import GameHost
from scenarios.eight_nations import build
from mods.ui.desktop_activity import TimelineCapture,difference,key
from ui.desktop import Controller


def replay(scene,beats):
    scene=deepcopy(scene)
    for beat in beats:
        for collection,change in beat['delta'].items():
            values={key(collection,r):r for r in scene.get(collection,[])}
            for k in change['remove']:values.pop(k,None)
            for r in change['upsert']:values[key(collection,r)]=deepcopy(r)
            scene[collection]=list(values.values())
    return scene


class TestDesktopActivity(unittest.TestCase):
    def test_normal_opening_and_actor_banners_and_construction_lifecycle(self):
        h=build(seed=20261023);self.assertFalse(h.world.military.snapshot())
        self.assertTrue(all(c.version=='v13' for c in h.clients.values()))
        order=list(h.turn_order());c=TimelineCapture(h);initial=deepcopy(c.previous);h.cycle(on_result=c.capture);beats=c.finish()
        self.assertIsNone(c.warning)
        self.assertEqual([b['turn_title'] for b in beats if b['phase']=='actor'],
                         [h.roster[a]['name']+' · 第 1 回合' for a in order])
        self.assertTrue(any(b['phase']=='build' for b in beats));self.assertTrue(any(b['phase']=='complete' for b in beats))
        self.assertFalse(difference(replay(initial,beats),c.scene()))
        for beat in beats:
            if beat['phase']=='build':
                new=beat['delta']['buildings']['upsert'];self.assertTrue(all(b['remaining']==1 for b in new))
            if beat['phase']=='complete':self.assertTrue(all(b['remaining']==0 for b in beat['delta']['buildings']['upsert']))

    def test_observation_and_capture_do_not_change_rule_state_or_rng(self):
        h=build(seed=73)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'clone.json';h.save(path);plain=GameHost.load(path,h.cfg)
            c=TimelineCapture(h);before=h.to_save();c.scene();self.assertEqual(before,h.to_save())
            h.cycle(on_result=c.capture);c.finish();plain.cycle()
            self.assertEqual(h.to_save(),plain.to_save())

    def test_cycle_waits_for_exact_ack_and_pause_saves_complete_boundary(self):
        h=build(seed=73);rows=[]
        with tempfile.TemporaryDirectory() as d:
            def output(row):
                rows.append(row)
                if row['type']=='timeline' and row['stage']=='action':
                    control.commands.put({'command':'playback_ack','generation':row['generation'],'sequence':row['sequence']})
            control=Controller(h,Path(d)/'session.json',output,playback=True)
            control.persist();control.command({'command':'resume'});control.advance()
            self.assertEqual(h.clock(),1);self.assertEqual(control.saved_period,1)
            seq=control.waiting;self.assertIsNotNone(seq)
            with self.assertRaises(ValueError):control.advance()
            control.command({'command':'playback_ack','generation':'stale','sequence':seq})
            self.assertEqual(control.waiting,seq)
            control.command({'command':'pause'});self.assertFalse(control.running)
            self.assertEqual(GameHost.load(control.save,h.cfg).to_save(),h.to_save())
            control.command({'command':'playback_ack','generation':control.generation,'sequence':seq})
            self.assertIsNone(control.waiting);self.assertEqual(h.clock(),1)
            self.assertEqual([r['revision'] for r in rows],list(range(1,len(rows)+1)))

    def test_capture_fault_isolated_from_valid_rule_cycle(self):
        h=build(seed=73);c=TimelineCapture(h)
        with patch.object(c,'scene',side_effect=RuntimeError('synthetic renderer failure')):h.cycle(on_result=c.capture)
        self.assertEqual(h.clock(),1);self.assertTrue(c.warning);self.assertFalse(c.finish())
        h.to_save()

    def test_verified_routes_and_real_battle_order_with_synthetic_troops(self):
        h=build(seed=20261023)
        # Explicit test fixture, never a default desktop opening.
        for a in h.actors():h.subsidize(a,{},cavalry=2)
        c=TimelineCapture(h);initial=deepcopy(c.previous);h.cycle(on_result=c.capture);beats=c.finish()
        self.assertIsNone(c.warning);phases=Counter(b['phase'] for b in beats)
        self.assertGreater(phases['march'],0);self.assertGreater(phases['battle'],0);self.assertGreater(phases['maintenance'],0)
        self.assertEqual(phases['battle'],phases['casualties'])
        last_march=max(i for i,b in enumerate(beats) if b['phase']=='march')
        first_battle=min(i for i,b in enumerate(beats) if b['phase']=='battle')
        self.assertLess(last_march,first_battle)
        for i,b in enumerate(beats):
            if b['phase']=='march':
                self.assertEqual(beats[i-1]['phase'],'order')
                self.assertIn(beats[i+1]['phase'],('arrival','engage'))
                self.assertEqual(b['playback_scope'],'actor')
                turn_end=next(j for j in range(i+1,len(beats)) if beats[j]['phase']=='turn-end' and beats[j]['actor']==b['actor'])
                self.assertLess(i,turn_end)
                arrived=replay(initial,beats[:i+2])
                positions={key('armies',u):[u['x'],u['y']] for u in arrived['armies']}
                for motion in b['motions']:self.assertEqual(positions[motion['unit']],motion['points'][-1])
            if b['phase']=='battle':
                self.assertEqual(beats[i+1]['phase'],'casualties');self.assertEqual(beats[i+1]['source'],b['source'])
            for motion in b.get('motions',[]):
                for p,q in zip(motion['points'],motion['points'][1:]):
                    self.assertTrue(h.world.territory.terrain(*p).passable);self.assertTrue(h.world.territory.terrain(*q).passable)
                    self.assertTrue(h.world.military._step(tuple(p),tuple(q)))
        self.assertFalse(difference(replay(initial,beats),c.scene()))

    def test_production_pops_equal_actual_facility_outputs(self):
        h=build(seed=20261023);h.cycle()
        c=TimelineCapture(h);h.cycle(on_result=c.capture);beats=c.finish()
        pops=[p for b in beats if b['phase']=='production' for p in b['pops'] if p['amount']>0]
        expected=Counter()
        for valued in h.world.economy.facts(1):
            f=valued.fact
            if f.category=='production':
                for resource,amount in f.outputs:expected[(int(f.id.rsplit('/',1)[-1]),resource)]+=amount
        actual=Counter()
        for p in pops:actual[(p['building'],p['resource'])]+=p['amount']
        self.assertTrue(actual);self.assertEqual(actual,expected)

    def test_healing_and_resource_pops_use_real_maintenance_costs(self):
        h=build(seed=73);actor=h.actors()[0];tile=h.world.territory.owned(actor)[0]
        unit=h.world.military.seed(actor,'native:infantry',*tile.tile.position)[0]
        h.world.units.by_id(unit.id,actor).update(hp=40)
        c=TimelineCapture(h);h.world.resolve_turn();beats=c.finish()
        self.assertIsNone(c.warning)
        maintenance=[b for b in beats if b['phase']=='maintenance'];self.assertEqual(len(maintenance),1)
        pops={p['resource']:p['amount'] for p in maintenance[0]['pops']}
        self.assertEqual(pops['hp'],30);self.assertEqual(pops['native:equipment'],-1)
        self.assertLess(pops['native:supply'],0);self.assertEqual(h.world.units.by_id(unit.id,actor).hp,70)


if __name__=='__main__':unittest.main()
