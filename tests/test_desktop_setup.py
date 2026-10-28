"""Desktop setup uses formal configuration and explicit, lossless save copies."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from host import GameHost,ROOT
from mods.ui.desktop_setup import catalog,validate,inspect_save,upgrade_bundle
from mods.native.save_upgrade import prepare_upgrade
from tests.test_human import config


class TestDesktopSetup(unittest.TestCase):
    def fixture(self,edition):
        return json.loads(gzip.decompress((ROOT/'tests/fixtures'/f'v07-march-{edition}.json.gz').read_bytes()))

    def test_bundled_catalog_is_generated_from_real_loaded_distributions(self):
        stored=json.loads((ROOT/'mods/ui/desktop/distribution-catalog.json').read_text())
        self.assertEqual(stored,catalog())
        for edition in stored['editions']:
            r=GameHost.prepare(config(edition['entry'])).runtime
            self.assertEqual(edition['clients'],list(r.distribution['ClientFactories']))
            self.assertEqual(edition['version'],r.distribution['Version'])

    def test_new_game_preflight_never_constructs_a_session(self):
        cfg=config('mods.experimental.toml');cfg['experimental_features']={'council':False,'scripts':False,'combat_research':False}
        with patch.object(GameHost,'__init__',side_effect=AssertionError('session created during preflight')):
            info=validate(cfg)
        self.assertTrue(all(not item['enabled'] for item in info['features'].values()))

    def test_current_and_known_old_saves_are_distinguished_and_mixed_config_refused(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp);c=p/'config.json';s=p/'session.json'
            c.write_text(json.dumps(config()));s.write_text(json.dumps(GameHost.new(config()).to_save()))
            result=inspect_save(s,c);self.assertTrue(result['can_resume']);self.assertFalse(result['can_upgrade'])
            s.write_text(json.dumps(self.fixture('native')))
            result=inspect_save(s,c);self.assertFalse(result['can_resume']);self.assertTrue(result['can_upgrade'])
            c.write_text(json.dumps(config('mods.experimental.toml')))
            result=inspect_save(s,c);self.assertFalse(result['can_resume']);self.assertFalse(result['can_upgrade'])

    def test_copy_preserves_routes_scripts_prepaid_work_and_companion_history(self):
        for edition,mods in [('native','mods.toml'),('experimental','mods.experimental.toml')]:
            with self.subTest(edition=edition),TemporaryDirectory() as tmp:
                p=Path(tmp);source=p/'old.json';c=p/'config.json';data=self.fixture(edition)
                source.write_text(json.dumps(data,ensure_ascii=False));original=source.read_bytes();c.write_text(json.dumps(config(mods)))
                history=source.with_suffix('.json.audit');history.mkdir();(history/'segment.json').write_text('synthetic audit')
                result=upgrade_bundle(source,c,p/'copy');upgraded=json.loads(Path(result['session']).read_text())
                expected=deepcopy(data);expected['dependencies']=upgraded['dependencies']
                self.assertEqual(upgraded,expected);self.assertEqual(source.read_bytes(),original)
                self.assertTrue(upgraded['modules']['native:army']['military']['march_orders'])
                if edition=='experimental':self.assertTrue(upgraded['modules']['experimental:python'])
                else:self.assertTrue(upgraded['modules']['native:buildings']['queued'])
                self.assertEqual((p/'copy/session.json.audit/segment.json').read_text(),'synthetic audit')
                self.assertTrue(inspect_save(result['session'],result['config'])['can_resume'])
                with self.assertRaises(FileExistsError):upgrade_bundle(source,c,p/'copy')

    def test_changing_features_during_upgrade_cannot_drop_old_components(self):
        cfg=config('mods.experimental.toml');cfg['experimental_features']={'council':False,'scripts':False}
        with self.assertRaises(ValueError):prepare_upgrade(self.fixture('experimental'),cfg)
