"""Player renderer and choreography must use detached, authorized facts only."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from host import GameHost
from mods.native.human import HumanAPI
from mods.ui.desktop_player import frame, flow
from mods.ui.desktop_human import PlayerCapture
from ui.desktop import Controller
from tests.test_human import config, A, B


class TestDesktopPlayer(unittest.TestCase):
    def setUp(self):
        self.host=GameHost.new(config());self.api=HumanAPI(self.host)

    def render(self):return frame(self.host.ui,self.api.state(A))

    def test_human_march_uses_verified_intermediate_cells_instead_of_endpoint_line(self):
        w=self.host.world
        start=next(t.tile.position for t in w.territory.owned(A) if t.tile.terrain=='native:plain')
        unit=w.military.seed(A,'native:cavalry',*start)[0]
        target=next(t for t in self.api.targets(A,[unit.id],'move')['targets']
                    if not t['uncertain'] and len(t['routes'][0]['points'])>2)
        with TemporaryDirectory() as tmp:
            control=Controller(self.host,Path(tmp)/'s.json',lambda row:None,playback=True)
            capture=PlayerCapture(control)
            with patch.object(self.host,'observer_route',side_effect=AssertionError('observer route leak')):
                result=self.api.submit(A,'move',{'ids':[unit.id],'x':target['position'][0],'y':target['position'][1]})
                self.assertTrue(result['ok'])
                audit=self.host.audit[-1];capture.capture(A,audit['action'],audit['arguments'],result)
            self.assertIsNone(capture.warning)
            motion=capture.drain()[0]['motions'][0]
            self.assertEqual(motion['points'],target['routes'][0]['points']);self.assertFalse(motion['schematic'])

    def test_foreign_extinction_invalidates_old_color_and_preserves_historical_facts(self):
        w=self.host.world
        self.assertTrue(self.api.submit(A,'buy_map',{'target':B})['ok'])
        history=next(t for t in self.render()['tiles'] if t['visibility']=='memory' and t['owner']==B)
        point=next(tuple(t['position']) for t in self.api.state(A)['map']['tiles'] if t['visibility']=='live' and
                   t['current'].get('owner') is None and w.territory.terrain(*t['position']).claimable)
        w.territory.create_claim('scenario:extinction-live',B,*point)
        self.assertEqual(next(t for t in self.render()['tiles'] if t['position']==list(point))['owner'],B)
        sites={s.id:s for s in w.buildings.snapshot().sites}
        for building in w.buildings.snapshot().instances:
            if sites[building.site].owner==B and building.kind=='native:town-hall':w.buildings.demolish(B,building.id)
        w.resolve_turn();w.phase='行动'
        self.assertNotIn(B,self.host.actors())
        after=self.render()
        self.assertIsNone(next(t for t in after['tiles'] if t['position']==list(point))['owner'])
        old=next(t for t in after['tiles'] if t['position']==history['position'])
        self.assertIsNone(old['owner']);self.assertEqual(old['historical_owner'],B)
        self.assertEqual(old['ownership_status'],'outdated')
        self.assertEqual(old['knowledge'],history['knowledge'])
        self.assertEqual(old['terrain'],history['terrain'])
        self.assertEqual(old['sampled'],history['sampled'])
        self.assertEqual(old['building_labels'],history['building_labels'])
        self.assertFalse(any(b['owner']==B for b in old['building_instances']))

    def test_a_defeats_b_with_old_b_map_and_no_hidden_new_owner_is_revealed(self):
        for secret_owner in (None,'scenario:third'):
            with self.subTest(secret_owner=secret_owner):
                cfg=config();cfg['nations'].append({'id':'scenario:third','name':'赵','client':'rule'})
                host=GameHost.new(cfg);api=HumanAPI(host);w=host.world
                live={tuple(t['position']) for t in api.state(A)['map']['tiles'] if t['visibility']=='live'}
                sites={s.id:s for s in w.buildings.snapshot().sites}
                hall=next(b for b in w.buildings.snapshot().instances if b.kind=='native:town-hall' and sites[b.site].owner==B)
                capital=sites[hall.site].position
                point=next((x,y) for x in range(1,11) for y in range(1,11) if (x,y) not in live and
                    w.territory.owner(x,y) is None and w.territory.terrain(x,y).claimable and
                    max(abs(x-capital[0]),abs(y-capital[1]))>3)
                site='scenario:b-remote';w.territory.create(site,B,*point,terrain='native:plain')
                w.buildings.seed(site,'native:castle');w.intelligence.publish_maps()
                self.assertTrue(api.submit(A,'buy_map',{'target':B})['ok'])
                old=next(t for t in frame(host.ui,api.state(A))['tiles'] if t['position']==list(point))
                self.assertEqual(old['owner'],B);self.assertEqual(old['visibility'],'memory')
                self.assertTrue(api.submit(A,'declare_war',{'target':B,'confirm':True})['ok'])
                host.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})])
                unit=w.military.seed(A,'native:infantry',*capital)[0]
                if secret_owner:w.territory.transfer(site,secret_owner)
                def take_capital(player):
                    result=api.submit(A,'attack',{'ids':[unit.id],'x':capital[0],'y':capital[1]})
                    self.assertTrue(result['ok']);self.assertTrue(result['data']['occupied'])
                    return [result,api.submit(A,'end_turn',{})]
                host.cycle(on_human=take_capital)
                self.assertNotIn(B,host.actors());self.assertEqual(w.territory.owner(*point),secret_owner)
                with patch.object(host,'observer_snapshot',side_effect=AssertionError('observer leak')), \
                     patch.object(host,'observer_scene',side_effect=AssertionError('observer leak')):
                    result=frame(host.ui,api.state(A))
                cell=next(t for t in result['tiles'] if t['position']==list(point))
                self.assertEqual(cell['visibility'],'memory');self.assertIsNone(cell['owner'])
                self.assertEqual((cell['ownership_status'],cell['historical_owner']),('outdated',B))
                self.assertEqual(cell['knowledge'],old['knowledge'])
                self.assertEqual(cell['building_labels'],old['building_labels'])
                current=next(t for t in result['tiles'] if t['position']==list(capital))
                self.assertEqual((current['visibility'],current['owner']),('live',A))
                self.assertNotIn('ownership_status',current)
                with TemporaryDirectory() as tmp:
                    path=Path(tmp)/'old-b-map.json';host.save(path);loaded=GameHost.load(path,cfg)
                    restored=frame(loaded.ui,HumanAPI(loaded).state(A))
                self.assertEqual(next(t for t in restored['tiles'] if t['position']==list(point)),cell)

    def test_market_projection_keeps_public_reference_separate_from_inventory(self):
        state=self.api.state(A);reference=dict(state['facts']['market']['reference'])
        inventory=dict(state['facts']['market']['inventory'])
        for row in frame(self.host.ui,state)['market']:
            self.assertEqual(row['reference'],reference[row['id']])
            self.assertEqual(row['inventory'],inventory[row['id']])

    def test_battle_replay_removes_the_idle_guard_instead_of_duplicating_or_reviving_it(self):
        w=self.host.world
        point=next(tuple(t['position']) for t in self.api.state(A)['map']['tiles'] if t['visibility']=='live' and
                   t['current'].get('owner') is None and w.territory.terrain(*t['position']).claimable)
        w.military.seed(A,'native:infantry',*point,count=8)
        ids=[u.id for u in w.military.snapshot(A) if (u.x,u.y)==point]
        with TemporaryDirectory() as tmp:
            c=Controller(self.host,Path(tmp)/'s.json',lambda row:None,playback=True)
            capture=PlayerCapture(c)
            before=capture.previous
            guard=next(u for u in before['scene']['guards'] if (u['x'],u['y'])==point)
            def action(player):
                result=self.api.submit(A,'attack',{'ids':ids,'x':point[0],'y':point[1]})
                self.assertTrue(result['ok'])
                return [self.api.submit(A,'end_turn',{})]
            self.host.cycle(on_human=action)
            beats=capture.finish();self.assertIsNone(capture.warning)
            stages=[b for b in beats if b['phase'] in ('battle','casualties')]
            self.assertTrue(stages)
            for beat in stages:
                visual=beat['visual']['scene']
                self.assertFalse(any((u['x'],u['y'])==point for u in visual['guards']))
                rows=[u for u in visual['armies'] if (u['x'],u['y'])==point and u['owner']=='野人']
                self.assertLessEqual(len(rows),1)
                if rows:self.assertEqual(rows[0]['id'],guard['id'])
                expected=beat['combat']['before' if beat['phase']=='battle' else 'after']
                self.assertEqual([(u['id'],u['hp']) for u in rows],[(u['id'],u['hp']) for u in expected if u['owner']=='野人'])
                if beat['phase']=='casualties':self.assertFalse(rows,'dead guard reappeared after casualties')

    def test_projection_has_no_observer_or_foreign_account_and_is_detached(self):
        state=self.api.state(A);before=deepcopy(state)
        with patch.object(self.host,'observer_snapshot',side_effect=AssertionError('observer')), \
             patch.object(self.host,'observer_scene',side_effect=AssertionError('observer')):
            view=frame(self.host.ui,state)
        self.assertEqual(state,before)
        self.assertEqual(view['map_seed'],0)
        self.assertEqual([n['id'] for n in view['nations']],[A])
        self.assertEqual([n['id'] for n in view['scene']['accounts']],[A])
        for tile in view['tiles']:
            if tile['visibility']=='unknown':
                self.assertEqual(set(tile),{'position','visibility','knowledge'})
        view['scene']['accounts'][0]['stocks'].clear()
        self.assertEqual(state,before)

    def test_hidden_development_never_changes_a_purchased_map(self):
        self.assertTrue(self.api.submit(A,'buy_map',{'target':B})['ok'])
        before=self.render();tile=next(t for t in before['tiles'] if t['visibility']=='memory' and t['owner']==B)
        site=next(s for s in self.host.world.buildings.snapshot().sites if s.position==tuple(tile['position']))
        self.host.world.buildings.seed(site.id,'native:castle')
        self.host.world.intelligence._messages=[]
        after=self.render()
        self.assertEqual(next(t for t in after['tiles'] if t['position']==tile['position']),tile)
        self.assertEqual(before['scene']['buildings'],after['scene']['buildings'])
        self.assertFalse(tile['dynamic'])
        self.assertTrue(all(not b['level_known'] for b in tile['building_instances']))

    def test_partial_live_foreign_observation_retains_old_building_date(self):
        state=self.api.state(A);tile=next(t for t in state['map']['tiles'] if t['visibility']=='live')
        tile['current']['owner']=B
        tile['current']['buildings']=[['native:town-hall',1],['native:castle',1]]
        tile['fields']['building/native:equipment-factory']={'value':{'count':2,'construction':1},'sampled':12,'order':1,'source':'map','acquired':15}
        view=frame(self.host.ui,state)
        observed=next(t for t in view['tiles'] if t['position']==tile['position'])
        factories=[b for b in observed['building_instances'] if b['kind']=='native:equipment-factory']
        self.assertEqual(len(factories),3)
        self.assertTrue(all(b['sampled']==12 and not b['level_known'] for b in factories))
        self.assertTrue(any('第13期' in label for label in observed['building_labels']))
        defense=next(d for d in view['scene']['tile_defenses'] if [d['x'],d['y']]==tile['position'])
        self.assertFalse(defense['castle_known'])
        self.assertFalse(observed['dynamic'])

    def test_own_army_does_not_reveal_unknown_terrain(self):
        unknown=next(t for t in self.api.state(A)['map']['tiles'] if t['visibility']=='unknown' and self.host.world.territory.terrain(*t['position']).passable)
        self.host.world.military.seed(A,'native:infantry',*unknown['position'])
        view=self.render()
        tile=next(t for t in view['tiles'] if t['position']==unknown['position'])
        self.assertEqual(tile['visibility'],'unknown');self.assertNotIn('terrain',tile)
        self.assertTrue(any([u['x'],u['y']]==unknown['position'] for u in view['armies'] if u['owner']==A))

    def test_actual_flow_excludes_trade_and_uses_authorized_maintenance(self):
        self.api.submit(A,'buy',{'resource':'food','quantity':1})
        self.host.cycle(on_human=lambda p:self.api.submit(A,'end_turn',{}))
        state=self.api.state(A);f=flow(state)
        self.assertEqual(f['period'],0)
        self.assertEqual(f['production_in'],state['facts']['econ']['latest_flows']['outputs'])
        self.assertEqual(f['military_out'],[])
        self.assertNotIn('trade',f)

    def test_trade_playback_uses_real_stock_delta_and_renderer_schema(self):
        with TemporaryDirectory() as tmp:
            c=Controller(self.host,Path(tmp)/'s.json',lambda row:None,playback=True)
            capture=PlayerCapture(c)
            before={s['id']:s['amount'] for s in capture.previous['scene']['accounts'][0]['stocks']}
            result=self.api.submit(A,'buy',{'resource':'food','quantity':2})
            audit=self.host.audit[-1]
            capture.capture(A,audit['action'],audit['arguments'],result)
            beat=capture.drain()[0]
            self.assertEqual(beat['phase'],'trade');self.assertEqual(beat['trade']['goods'],[{'resource':'native:food','amount':2}])
            gold=next(s['amount'] for s in beat['visual']['scene']['accounts'][0]['stocks'] if s['id']=='native:gold')
            self.assertEqual(beat['trade']['gold'],gold-before['native:gold'])
            self.assertIsNone(capture.warning)

    def test_foreign_private_action_has_no_beat_or_camera_target(self):
        with TemporaryDirectory() as tmp:
            c=Controller(self.host,Path(tmp)/'s.json',lambda row:None,playback=True)
            capture=PlayerCapture(c)
            capture.capture(B,'send_letter',{'text':'secret'}, {'ok':True,'data':{'private':'secret'}})
            self.assertEqual(capture.drain(),[])
            self.assertIsNone(capture.warning)

    def test_settlement_and_delivered_letter_never_consult_observer(self):
        with TemporaryDirectory() as tmp:
            c=Controller(self.host,Path(tmp)/'s.json',lambda row:None,playback=True)
            capture=PlayerCapture(c)
            self.assertTrue(self.host.player(B).submit('intelligence',{'action':'send_letter','target':A,'text':'送达正文'})['ok'])
            self.host.cycle(on_human=lambda p:self.api.submit(A,'end_turn',{}))
            self.host.cycle(on_human=lambda p:self.api.submit(A,'end_turn',{}))
            with patch.object(self.host,'observer_scene',side_effect=AssertionError('observer')):
                beats=capture.finish()
            self.assertTrue(beats)
            self.assertTrue(all(len(b['visual']['nations'])==1 for b in beats))
            self.assertTrue(all(b.get('position') is None or any(t['position']==b['position'] and t['visibility']=='live' for t in b['visual']['tiles']) for b in beats))
            self.assertEqual(beats[-1]['phase'],'settlement')
            letters=[b for b in beats if b['phase']=='letter']
            self.assertTrue(letters)
            self.assertEqual(letters[0]['text'],'送达正文')
            self.assertTrue(letters[0]['letter']['schematic'])

    def test_visible_foreign_changes_do_not_name_private_call_metadata(self):
        with TemporaryDirectory() as tmp:
            rows=[];c=Controller(self.host,Path(tmp)/'s.json',rows.append,playback=True)
            c._capture=PlayerCapture(c)
            c.publish([],"action",actor=B,call=17)
            foreign=next(r for r in rows if r['type']=='timeline')
            self.assertIsNone(foreign['actor']);self.assertIsNone(foreign['call'])
            rows.clear();c.publish([],"action",actor=A,call=18)
            own=next(r for r in rows if r['type']=='timeline')
            self.assertEqual(own['actor'],A);self.assertEqual(own['call'],18)
