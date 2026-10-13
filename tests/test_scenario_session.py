"""Scenario administration through GameHost; synthetic configuration only."""
import tempfile
import unittest
from pathlib import Path
from host import GameHost
from scenarios.eight_nations import build,build_config,IDS,NATIONS,STARTS,CHARTER,main


class TestScenarioSession(unittest.TestCase):
    def test_initial_pacts_public_state_and_resume(self):
        host=build()
        self.assertEqual(host.world.territory.dimensions(),(24,20260921))
        self.assertEqual(len(host.world.diplomacy.to_save()['pacts']),7)
        self.assertEqual(host.scenario_text,CHARTER)
        for name in NATIONS:
            obs=host.player(IDS[name]).observe()
            self.assertEqual(len(obs['facts']['land']),5)
            self.assertTrue(host.player(IDS[name]).events()['items'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'scenario.json';host.save(path)
            restored=GameHost.load(path,host.cfg)
            self.assertEqual(host.to_save(),restored.to_save())
            host.cycle();restored.cycle()
            self.assertEqual(host.to_save(),restored.to_save())

    def test_config_and_nonoverwrite_guard(self):
        cfg=build_config({'nations':[{'client':'rule','id':'scenario:other','name':'x','polity':'huns'}]})
        self.assertEqual({n['id'] for n in cfg['nations']},set(IDS.values()))
        self.assertTrue(all(n['client']=='rule' and 'polity' not in n for n in cfg['nations']))
        self.assertEqual({n['name']:tuple(n['position']) for n in cfg['nations']},STARTS)
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'saved.json';out.write_text('keep')
            config=Path(directory)/'cfg.json'
            with self.assertRaises(SystemExit):main(['--out',str(out),'--config',str(config)])
            self.assertEqual(out.read_text(),'keep');self.assertFalse(config.exists())

    def test_pact_is_admin_only_and_validates(self):
        host=build();player=host.player(IDS['秦'])
        self.assertFalse(player.submit('seed_pact',{'kind':'guarantee','source':IDS['秦'],'target':IDS['周']})['ok'])
        before=host.world.diplomacy.to_save()
        with self.assertRaises(ValueError):host.world.diplomacy.seed_pact('guarantee',IDS['秦'],IDS['周'])
        self.assertEqual(before,host.world.diplomacy.to_save())
