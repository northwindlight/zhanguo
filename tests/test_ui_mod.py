"""UI mod assembly, public map safety, language keys and offline wire formats."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ctx
from engine.runtime import Runtime,GameMod
from host import GameHost,ROOT
from clients.llm import LLMClient,LLMState
from llm_provider import _public_messages,_anthropic_messages
from ui import load_ui
from mods.ui.maps import render_snapshot,side
from mods.ui.i18n import Translator
from tests.test_player_clients import FakeBackend


class AlternateUIMod(GameMod):
    def mixins(self):return ()
    def provides(self):return ('ui:presentation',)
    def presentations(self):return {'ui:create':self.create}
    @staticmethod
    def create(cfg):
        layer=load_ui({'ui_catalog':{'console.new':'alternate UI'}})
        layer.map_svg=lambda obs:'<svg>alternate renderer</svg>'
        return layer


class TestUIMod(unittest.TestCase):
    def host(self,experimental=False):
        cfg={'map_size':12,'seed':73,'vision':True,'nations':[{'name':'秦','id':'scenario:qin'}]}
        if experimental:cfg['mods']='mods.experimental.toml'
        return GameHost.new(cfg)

    def test_ui_is_independent_stateless_mod_and_both_distributions_load_it(self):
        runtime=Runtime.load(ROOT/'ui.toml')
        self.assertEqual(set(runtime.units),{'ui:standard'})
        self.assertEqual(runtime.units['ui:standard'].mod.state_keys(),())
        for path in ('mods.toml','mods.experimental.toml'):
            with self.subTest(path=path):
                game=Runtime.load(ROOT/path)
                self.assertIn('ui:standard',game.units)
                self.assertIn('ui:create',game.presentations)
                self.assertIn('ui:presentation',game.capabilities)

    def test_ui_mod_is_replaceable_without_changing_native_or_player_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mod.toml'
            path.write_text('[Mod]\nId="ui:alternate"\nVersion="1.0.0"\nEntry="tests.test_ui_mod:AlternateUIMod"\n[Capabilities]\nProvides=["ui:presentation"]\n')
            host=self.host();before=host.to_save();ui=load_ui({'ui_mods':directory})
            obs=host.player('scenario:qin').observe()
            self.assertEqual(ui.text('console.new'),'alternate UI')
            self.assertIn('alternate renderer',ui.map_svg(obs))
            self.assertEqual(host.to_save(),before)

    def test_experimental_monarch_and_reviewer_share_ui_map_and_hide_candidate_in_step_one(self):
        from tests.test_parliament import TestParliament,CouncilBackend,reply
        from tests.test_player_clients import call
        fixture=TestParliament();cfg=fixture.cfg();cfg['vision']=True
        host=GameHost.new(cfg);client=host.clients[fixture.actor]
        client._backend=FakeBackend([reply(call('p','plan',text='HIDDEN-CANDIDATE'),call('e','end_turn',summary='完成本期经营行动'))])
        host.services[fixture.actor]['council']._review_backend=CouncilBackend([True]);host.cycle()
        monarch=client._backend.requests[0][-1][ctx.MAP_KEY]['facts']
        first=host.services[fixture.actor]['council']._review_backend.requests[0]
        reviewer=first[-1][ctx.MAP_KEY]['facts']
        self.assertEqual(monarch,reviewer)
        self.assertNotIn('HIDDEN-CANDIDATE',json.dumps(first))
        self.assertIn('HIDDEN-CANDIDATE',json.dumps(host.services[fixture.actor]['council']._review_backend.requests[1]))

    def test_catalogs_complete_and_placeholders_checked_and_language_is_per_client(self):
        zh=Translator();en=Translator('en-US')
        self.assertEqual(set(zh.messages),set(en.messages))
        self.assertEqual(en('console.new'),'New game');self.assertEqual(zh('console.new'),'新开一局')
        with self.assertRaisesRegex(ValueError,'placeholders'):Translator(catalog={'console.sent':'Sent'})
        custom=Translator('custom',{'console.sent':'→ {target}'})
        self.assertEqual(custom('console.sent',target='Q'),'→ Q')

    def test_ui_translates_annotations_without_changing_action_contract(self):
        schemas=[{'type':'function','function':{'name':'custom','description':'old',
            'parameters':{'type':'object','properties':{'kind':{'type':'string','enum':['native:gold'],'description':'old'}}}}}]
        original=deepcopy(schemas)
        ui=load_ui({'ui_catalog':{'tools.custom.description':'new','tools.custom.parameters.properties.kind.description':'kind'}})
        result=ui.llm({**ui.cfg}).actions(schemas)
        self.assertEqual(result[0]['function']['description'],'new')
        self.assertEqual(result[0]['function']['parameters']['properties']['kind']['enum'],['native:gold'])
        self.assertEqual(schemas,original)

    def test_map_snapshot_is_detached_and_discards_units_outside_visible_tiles(self):
        host=self.host();obs=host.player('scenario:qin').observe();original=deepcopy(obs)
        obs['facts']['army'].append({'x':1000,'y':1000,'owner':'secret:owner','hp':100,'type':'secret:kind'})
        state=host.ui.map_snapshot(obs)
        self.assertNotIn('secret:owner',json.dumps(state))
        obs['facts']['view'][0]['owner']='changed'
        self.assertEqual(state['tiles'],original['facts']['view'])

    def test_png_is_deterministic_bounded_and_fog_pixels_are_dark(self):
        from PIL import Image
        observation={'actor':'Q','period':0,'facts':{'view':[
            {'position':[0,0],'terrain':'native:plain','owner':'Q','buildings':[],'resources':[]},
            {'position':[2,0],'terrain':'native:forest','owner':None,'buildings':[],'resources':[]}],
            'army':[],'diplomacy':{}}}
        ui=load_ui();state=ui.map_snapshot(observation);png=render_snapshot(state,max_px=400)
        self.assertEqual(png,render_snapshot(json.loads(json.dumps(state)),max_px=400))
        image=Image.open(io.BytesIO(png));self.assertLessEqual(max(image.size),400)
        self.assertEqual(image.getpixel((24+96+48,24+48)),(18,25,35))
        svg=ui.map_svg(observation);self.assertIn('>0</text>',svg);self.assertIn('>2</text>',svg)

    def test_army_sides_use_effective_alliances_and_wars(self):
        state={'actor':'Q','alliances':[{'members':['Q','A']}],
               'wars':[{'attackers':['Q','A'],'defenders':['E']}]}
        self.assertEqual([side(state,a) for a in ('Q','A','E','N')],['self','ally','enemy','neutral'])

    def test_openai_and_anthropic_receive_same_png_without_mutating_history(self):
        host=self.host();obs=host.player('scenario:qin').observe();view=host.ui.llm(host.cfg)
        tail,image=view.state(obs,[]);message={'role':'user','content':tail,ctx.MAP_KEY:image}
        original=deepcopy(message)
        openai=_public_messages([message],renderer=host.ui.expand_message)
        anthropic=_anthropic_messages([message],'fake',{'vision':True,'_ui_expand_message':host.ui.expand_message})
        url=openai[0]['content'][1]['image_url']['url']
        self.assertEqual(url.split(',',1)[1],anthropic[0]['content'][1]['source']['data'])
        self.assertEqual(message,original)
        self.assertNotIn(ctx.MAP_KEY,openai[0]);self.assertNotIn('base64',json.dumps(message))
        with patch('mods.ui.maps.render_snapshot',side_effect=ImportError('no Pillow')):
            text=_public_messages([message],renderer=host.ui.expand_message)
        self.assertIsInstance(text[0]['content'],str);self.assertIn('文本地图',text[0]['content'])

    def test_llm_map_survives_json_save_and_resumed_request_replay(self):
        host=self.host();player=host.player('scenario:qin');backend=FakeBackend()
        cfg={'vision':True,'language':'en-US','ctx_full_turns':8}
        client=LLMClient(cfg,backend=backend,ui=host.ui)
        client.play(player);stored=deepcopy(client.state.replay[0]['messages'])
        self.assertIn(ctx.MAP_KEY,stored[0])
        self.assertIn('Map coordinates start at 0',stored[0]['content'])
        self.assertNotIn('base64',json.dumps(client.to_save()))
        host.world.resolve_turn();host.world.phase='行动'
        resumed=LLMClient(cfg,state=LLMState.from_save(json.loads(json.dumps(client.state.to_save()))),backend=backend,ui=host.ui)
        resumed.play(player)
        requested=backend.requests[-1];start=next(i for i,m in enumerate(requested) if m==stored[0])
        self.assertEqual(requested[start:start+len(stored)],stored)

    def test_locale_changes_only_presentation_and_archive_labels(self):
        host=self.host();before=host.to_save();obs=host.player('scenario:qin').observe()
        en=load_ui({'language':'en-US'})
        self.assertIn('treasury',en.console.board(host.board_rows()))
        self.assertEqual(en.map_snapshot(obs),host.ui.map_snapshot(obs))
        archive=ctx.render_archive([{'turn':1,'text':'opaque original'}],[],2,2000,messages=en.text)
        self.assertIn('Turn 1: opaque original',archive)
        self.assertEqual(host.to_save(),before)

    def test_console_graphical_exports_use_ui_mod(self):
        from mp_run import command
        host=self.host()
        class Observer:
            ui=host.ui
            def line(self,text):pass
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ('png','svg'):
                path=Path(directory)/('map.'+suffix)
                self.assertTrue(command(host,f'map 秦 {path}',Observer()))
                self.assertTrue(path.read_bytes().startswith(b'\x89PNG' if suffix=='png' else b'<svg'))


if __name__=='__main__':unittest.main()
