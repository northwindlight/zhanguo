"""Scenario startup is identical through the CLI and builder, without model calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from host import GameHost
from mp_run import make_world
from presentation.console import ConsoleUI
from scenarios.eight_nations import build, build_config, IDS, NATIONS


class ScenarioInitialization(TestCase):
    def config(self, experimental=False):
        cfg=build_config({'nations':[{'client':'rule'}]})
        if experimental:cfg['mods']='mods.experimental.toml'
        return cfg

    def quiet(self,h):
        for client in h.clients.values():client.play=lambda player,**kwargs:[]
        return h

    def pacts(self,h):
        return {(p['kind'],p['a'],p['b']) for p in h.world.diplomacy.to_save()['pacts']}

    def expected(self):
        return {('guarantee','nation/'+IDS[n],'nation/'+IDS['周']) for n in NATIONS if n!='周'}

    def test_cli_and_builder_seed_exactly_seven_in_both_distributions(self):
        for experimental in (False,True):
            with self.subTest(experimental=experimental),TemporaryDirectory() as folder:
                cfg=self.config(experimental);path=Path(folder)/'session.json'
                h,created=make_world(cfg,True,path)
                self.assertTrue(created);self.assertEqual(self.pacts(h),self.expected())
                self.assertEqual(len(h.world.diplomacy.to_save()['pacts']),7)
                restored,created=make_world(cfg,False,path)
                self.assertFalse(created);self.assertEqual(restored.to_save(),h.to_save())
                built=build(config=cfg)
                self.assertEqual(self.pacts(built),self.expected())
                self.assertEqual(len(built.world.diplomacy.to_save()['pacts']),7)

    def test_cancelled_guarantee_stays_cancelled_after_restore_and_world_rebind(self):
        for experimental in (False,True):
            with self.subTest(experimental=experimental),TemporaryDirectory() as folder:
                h=self.quiet(GameHost.new(self.config(experimental)))
                result=h.player(IDS['秦']).submit('relations',{'action':'cancel_guarantee','target':IDS['周']})
                self.assertTrue(result['ok'],result);h.cycle()
                expected=self.expected()-{('guarantee','nation/'+IDS['秦'],'nation/'+IDS['周'])}
                self.assertEqual(self.pacts(h),expected)
                path=Path(folder)/'session.json';h.save(path)
                restored=GameHost.load(path,h.cfg)
                self.assertEqual(self.pacts(restored),expected)
                candidate=h.runtime.world_class()()
                h.runtime.load_mod_state(candidate,h.runtime.save_mod_state(h.world));h.world=candidate
                self.assertEqual(self.pacts(h),expected)

    def test_only_selected_scenario_with_present_zhou_adds_guarantees(self):
        cfg=self.config();cfg.pop('scenario')
        self.assertFalse(self.pacts(GameHost.new(cfg)))
        cfg=self.config();cfg['nations']=[n for n in cfg['nations'] if n['name']!='周']
        self.assertFalse(self.pacts(GameHost.new(cfg)))

    def test_war_obligations_follow_protector_to_protected_direction(self):
        for target in ('燕','周'):
            with self.subTest(target=target):
                h=self.quiet(GameHost.new(self.config()))
                result=h.player(IDS['赵']).submit('relations',{'action':'declare_war','target':IDS[target]})
                self.assertTrue(result['ok'],result);h.cycle()
                war=h.world.diplomacy.snapshot(IDS['赵']).wars[0]
                self.assertEqual(set(war.attackers),{IDS['赵']})
                expected={IDS['燕']} if target=='燕' else set(IDS.values())-{IDS['赵']}
                self.assertEqual(set(war.defenders),expected)

    def test_foreign_receipt_identifies_both_roles_in_both_languages(self):
        h=GameHost.new(self.config());names={a:r['name'] for a,r in h.roster.items()}
        for language,expected,reverse in (
            ('zh-CN','保障人：赵；受保护方：周','保障人：周；受保护方：赵'),
            ('en-US','Guarantor: 赵; protected entity: 周','Guarantor: 周; protected entity: 赵'),
        ):
            for actor in (IDS['赵'],IDS['周']):
                with self.subTest(language=language,actor=actor):
                    player=h.player(actor);ui=ConsoleUI({'language':language},tool_layer=h.ui.console.tool_layer)
                    ui.remember(player.observe())
                    result=player.submit('query',{'panel':'foreign'})
                    self.assertTrue(result['ok'],result)
                    text=ui.action(h.clock(),actor,'query',{'panel':'foreign'},result,names)
                    self.assertIn(expected,text);self.assertNotIn(reverse,text)
