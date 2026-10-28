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
    def test_extinction_boundary_replay_releases_all_remaining_land(self):
        h=GameHost.new({'map_size':12,'seed':73,'nations':[
            {'id':'scenario:qin','name':'秦','client':'rule'},
            {'id':'scenario:chu','name':'楚','client':'rule'}]})
        c=TimelineCapture(h);initial=deepcopy(c.previous);owner='scenario:chu'
        sites={s.id:s for s in h.world.buildings.snapshot().sites}
        for b in h.world.buildings.snapshot().instances:
            if sites[b.site].owner==owner and b.kind=='native:town-hall':h.world.buildings.demolish(owner,b.id)
        h.world.resolve_turn();h.world.phase='行动'
        self.assertNotIn(owner,h.actors())
        beats=c.finish();self.assertIsNone(c.warning)
        actual=replay(initial,beats)
        self.assertFalse(any(t['owner']==owner for t in actual['ownership']))
        self.assertFalse(difference(actual,c.scene()))

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
        self.assertGreater(phases['march'],0);self.assertGreater(phases['battle'],0);self.assertEqual(phases['production'],8)
        last_production=max(i for i,b in enumerate(beats) if b['phase']=='production')
        first_supply=min(i for i,b in enumerate(beats) if b['phase']=='maintenance')
        last_supply=max(i for i,b in enumerate(beats) if b['phase']=='maintenance')
        self.assertLess(last_production,first_supply)
        self.assertLess(last_supply,min(i for i,b in enumerate(beats) if b['phase']=='battle'))
        for phase in ('production','maintenance'):
            countries=[b['actor'] for b in beats if b['phase']==phase]
            self.assertEqual(len(countries),len(set(countries)))
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

    def test_national_production_ledgers_equal_actual_facility_inputs_and_outputs(self):
        h=build(seed=20261023);h.cycle()
        c=TimelineCapture(h);h.cycle(on_result=c.capture);beats=c.finish()
        expected_in=Counter();expected_out=Counter()
        for valued in h.world.economy.facts(1):
            f=valued.fact
            if f.category=='production':
                for resource,amount in f.outputs:expected_in[(f.owner,resource)]+=amount
                for resource,amount in f.inputs:expected_out[(f.owner,resource)]+=amount
        actual_in=Counter();actual_out=Counter()
        ledgers=[b for b in beats if b['phase']=='production']
        self.assertEqual(len(ledgers),8);self.assertEqual(len({b['actor'] for b in ledgers}),8)
        self.assertFalse(any(b.get('pops') for b in beats))
        self.assertTrue(all('ledger' in b for b in beats if b['phase'] in ('production','maintenance')))
        for b in ledgers:
            for r,amount in b['ledger']['production_in']:actual_in[(b['actor'],r)]+=amount
            for r,amount in b['ledger']['production_out']:actual_out[(b['actor'],r)]+=amount
            self.assertEqual(b['playback_scope'],'settlement')
            hall=next(v for v in c.previous['buildings'] if v['id']==b['anchor_building'])
            self.assertEqual(b['position'],[hall['x'],hall['y']]);self.assertEqual(hall['owner'],b['actor'])
        self.assertTrue(actual_in);self.assertEqual(actual_in,expected_in);self.assertEqual(actual_out,expected_out)

    def test_healing_and_resource_ledgers_use_real_maintenance_costs(self):
        h=build(seed=73);actor=h.actors()[0];tile=h.world.territory.owned(actor)[0]
        unit=h.world.military.seed(actor,'native:infantry',*tile.tile.position)[0]
        h.world.units.by_id(unit.id,actor).update(hp=40)
        c=TimelineCapture(h);h.world.resolve_turn();beats=c.finish()
        self.assertIsNone(c.warning)
        ledger=next(b['ledger'] for b in beats if b['phase']=='maintenance' and b['actor']==actor)
        costs=dict(ledger['military_out'])
        self.assertEqual(ledger['healed_hp'],30);self.assertEqual(costs['native:equipment'],1)
        self.assertGreater(costs['native:supply'],0);self.assertEqual(h.world.units.by_id(unit.id,actor).hp,70)

    def test_distant_garrisons_share_one_summary_and_starvation_is_actual(self):
        h=build(seed=73);actor=h.actors()[0];land=h.world.territory.owned(actor)
        for tile in land[:3]:h.world.military.seed(actor,'native:infantry',*tile.tile.position)
        supply=next(s.amount for s in h.world.resources.snapshot(actor) if s.id=='native:supply')
        h.world.resources.exchange(actor,{'native:supply':supply},{})
        c=TimelineCapture(h);initial=deepcopy(c.previous);h.world.resolve_turn();beats=c.finish()
        self.assertIsNone(c.warning)
        summaries=[b for b in beats if b['phase']=='maintenance' and b['actor']==actor]
        self.assertEqual(len(summaries),1)
        ledger=summaries[0]['ledger'];self.assertEqual(ledger['units'],3)
        self.assertGreater(ledger['supply_need'],ledger['supply_used']);self.assertEqual(ledger['healed_hp'],0)
        before=sum(u['hp'] for u in initial['armies'] if u['owner']==actor)
        after=sum(u['hp'] for u in c.scene()['armies'] if u['owner']==actor)
        self.assertEqual(ledger['starvation_hp'],before-after)
        self.assertFalse(difference(replay(initial,beats),c.scene()))

    def test_single_nation_balances_replay_actual_flows_and_keep_banking_at_boundary(self):
        h=build(seed=20261023);h.cycle()
        c=TimelineCapture(h);initial=deepcopy(c.previous);h.world.resolve_turn();beats=c.finish()
        self.assertIsNone(c.warning)
        cli=h.observer_snapshot()['resource_flows'];before={a['id']:a for a in initial['accounts']}
        accumulated={}
        for index,beat in enumerate(beats):
            if 'ledger' not in beat:continue
            owner=beat['actor'];ledger=beat['ledger'];self.assertEqual(ledger['period'],cli['period'])
            changes=accumulated.setdefault(owner,Counter());changes.update(dict(ledger['net']))
            shown=replay(initial,beats[:index+1]);account=next(a for a in shown['accounts'] if a['id']==owner)
            prior={s['id']:s['amount'] for s in before[owner]['stocks']}
            for stock in account['stocks']:self.assertEqual(stock['amount'],prior[stock['id']]+changes.get(stock['id'],0))
            shown_flow=next(row for row in shown['resource_flows'] if row['id']==owner)
            self.assertEqual(shown_flow['period'],cli['period']-1)
        for owner,changes in accumulated.items():self.assertEqual({r:a for r,a in changes.items() if a},cli['owners'].get(owner,{}))
        self.assertFalse(difference(replay(initial,beats),c.scene()))

    def test_national_camera_falls_back_when_no_completed_townhall(self):
        h=build(seed=73);c=TimelineCapture(h);actor=h.actors()[0]
        for building in c.previous['buildings']:
            if building['owner']==actor:building['remaining']=1
        position=c.home(actor)
        self.assertIn(position,[[r['x'],r['y']] for r in c.previous['ownership'] if r['owner']==actor])


if __name__=='__main__':unittest.main()
