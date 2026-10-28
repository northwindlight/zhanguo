"""Known historical fixtures, with a strict normal loader and explicit new copy."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.session import SaveFormatError
from engine.runtime import ModError
from host import GameHost
from mods.native.human import HumanAPI
from mods.native.save_upgrade import prepare_upgrade,upgrade_copy
from tests.test_human import config,A


class TestKnownSaveUpgrade(unittest.TestCase):
    def legacy(self,edition):
        return json.loads(gzip.decompress((Path(__file__).parent/'fixtures'/f'v063-{edition}.json.gz').read_bytes()))

    def test_both_editions_upgrade_without_changing_old_game_state(self):
        for edition,mods in (('native','mods.toml'),('experimental','mods.experimental.toml')):
            with self.subTest(edition=edition):
                old=self.legacy(edition);cfg=config(mods);upgraded=prepare_upgrade(old,cfg)
                expected=deepcopy(old)
                expected['dependencies']=upgraded['dependencies']
                expected['modules']['native:buildings']['queued']=[]
                expected['modules']['native:army']['military'].update(recruit_queue=[],next_recruit_order=1)
                self.assertEqual(upgraded,expected)
                host=GameHost(GameHost.prepare(cfg));host.session.restore(upgraded)
                api=HumanAPI(host);site=api.state(A)['facts']['building_sites']['sites'][0]
                self.assertTrue(api.submit(A,'queue_build',{'kind':'castle','x':site['position'][0],'y':site['position'][1]})['ok'])
                host.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})])
                self.assertEqual(host.clock(),1)

    def test_normal_loading_stays_strict_and_upgrade_rejects_unknown_or_mixed_edition(self):
        old=self.legacy('native');host=GameHost(GameHost.prepare(config()))
        with self.assertRaises(ModError):host.session.restore(old)
        unknown=deepcopy(old);unknown['dependencies']['distribution']['Version']='unknown'
        with self.assertRaises(SaveFormatError):prepare_upgrade(unknown,config())
        with self.assertRaises(SaveFormatError):prepare_upgrade(old,config('mods.experimental.toml'))
        invalid=deepcopy(old);invalid['modules']['native:buildings']['next_id']=-1
        with self.assertRaises(SaveFormatError):prepare_upgrade(invalid,config())
        unexpected=deepcopy(old);unexpected['modules']['native:buildings']['queued']=[{'paid':'must not discard'}]
        with self.assertRaises(SaveFormatError):prepare_upgrade(unexpected,config())

    def test_new_file_and_history_copy_never_overwrite_the_original(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'old.json';source.write_text(json.dumps(self.legacy('native')))
            original=source.read_bytes();history=source.with_suffix('.json.audit');history.mkdir()
            (history/'test-segment.json').write_text('isolated history')
            target=Path(tmp)/'new.json';upgrade_copy(source,target,config())
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual((target.with_suffix('.json.audit')/'test-segment.json').read_text(),'isolated history')
            GameHost.load(target,config())
            with self.assertRaises(FileExistsError):upgrade_copy(source,target,config())
            with self.assertRaises(FileExistsError):upgrade_copy(source,source,config())
