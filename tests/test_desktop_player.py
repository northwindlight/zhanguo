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
