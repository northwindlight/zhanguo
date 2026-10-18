"""国家域验收：仅合成世界与临时 JSON，不使用真实对局存档。"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.runtime import GameMod, Runtime, ModError
from engine.state import unit_context

ROOT = Path(__file__).resolve().parents[1]


class NationReader(GameMod):
    def mixins(self):return ()
    def needs(self):return ('native:nation-inspection',)


class TestNations(unittest.TestCase):
    def world(self, official=False):
        runtime = Runtime.load(ROOT / ('mods.toml' if official else 'mods/nations_acceptance'))
        return runtime.world_class()()

    def populate(self, world):
        world.nations.create('scenario:qin', '秦', {'native:gold':1000, 'native:wood':100})
        world.nations.create('scenario:chu', '楚', {'native:gold':100})
        world.buildings.register_site('scenario:capital', 'scenario:qin', (0, 0),
                                     resources={'native:gold':10, 'native:farmland':10})
        world.buildings.seed('scenario:capital', 'native:gold-mine')
        return world

    def save(self, world):
        return json.loads(json.dumps(world.runtime.save_mod_state(world), allow_nan=False))

    def test_identity_account_and_sites_share_id_without_copying_state(self):
        world = self.populate(self.world())
        nation = world.nations.get('scenario:qin')
        self.assertEqual((nation.id, nation.name, nation.account), ('scenario:qin','秦','scenario:qin'))
        self.assertEqual(world.nation_actors, ('scenario:qin','scenario:chu'))
        self.assertEqual(world.resources.owners(), world.nation_actors)
        self.assertEqual(world.call_tool('scenario:qin','foreign',{})['nation'].sites[0].owner, nation.id)
        self.assertEqual([n.name for n in world.call_tool(nation.id,'foreign',{})['nations']], ['秦','楚'])
        world.buildings.transfer_site('scenario:capital', 'scenario:chu')
        self.assertEqual(world.nations.sites(nation.id), ())
        self.assertEqual(world.nations.sites('scenario:chu')[0].id, 'scenario:capital')
        self.assertNotIn('sites', world.nations.to_save()['nations'][0])

    def test_rename_does_not_change_account_site_or_action_identity(self):
        world = self.populate(self.world())
        world.nations.rename('scenario:qin', '秦国')
        self.assertEqual(world.nations.get('scenario:qin').name, '秦国')
        receipt = world.call_tool('scenario:qin','build',{'site':'scenario:capital','kind':'native:farm'})
        self.assertEqual(receipt.owner, 'scenario:qin')
        self.assertEqual(world.resources.balance('scenario:qin','native:gold'), 980)
        self.assertEqual(world.resources.balance('scenario:chu','native:gold'), 100)
        with self.assertRaises(ValueError):world.call_tool('秦国','foreign',{})

    def test_invalid_creation_and_account_collision_leave_both_domains_unchanged(self):
        world = self.populate(self.world())
        world.resources.open('scenario:treasury')
        cases = [('scenario:qin','另一国',{}), ('秦','秦',{}), ('scenario:new','  ',{}),
                 ('scenario:new','新国',{'native:missing':1}),
                 ('scenario:new','新国',{'native:food':-1}),
                 ('scenario:treasury','国库',{})]
        for args in cases:
            with self.subTest(args=args):
                before = self.save(world)
                with self.assertRaises(ValueError):world.nations.create(*args)
                self.assertEqual(self.save(world), before)

    def test_duplicate_display_names_do_not_collide(self):
        world = self.world()
        world.nations.create('scenario:a','周')
        world.nations.create('other:b','周')
        self.assertEqual(world.nation_actors, ('scenario:a','other:b'))

    def test_resource_accounts_do_not_automatically_become_nations(self):
        world = self.populate(self.world(official=True))
        world.resources.open('scenario:treasury',{'native:gold':100})
        before = self.save(world)
        for tool, args in [('foreign',{}), ('econ',{}), ('buy',{'resource':'native:wood','quantity':1}),
                           ('borrow',{})]:
            with self.assertRaises(ValueError):world.call_tool('scenario:treasury',tool,args)
        self.assertEqual(self.save(world), before)
        self.assertNotIn('scenario:treasury', world.nation_actors)

    def test_extinction_requires_site_disposal_preserves_account_and_reserves_id(self):
        world = self.populate(self.world())
        before = self.save(world)
        with self.assertRaises(ValueError):world.nations.extinguish('scenario:qin','国祚已失')
        self.assertEqual(self.save(world), before)
        world.buildings.transfer_site('scenario:capital','scenario:chu')
        nation = world.nations.extinguish('scenario:qin','国祚已失')
        self.assertFalse(nation.alive)
        self.assertEqual(nation.extinction_reason, '国祚已失')
        self.assertEqual(world.nation_actors, ('scenario:chu',))
        self.assertEqual(world.nations.ids(alive_only=False), ('scenario:qin','scenario:chu'))
        self.assertEqual(world.resources.balance(nation.account,'native:gold'), 1000)
        self.assertEqual(len(world.call_tool('scenario:chu','foreign',{})['nations']), 2)
        before = self.save(world)
        for operation in (lambda:world.nations.create(nation.id,'新秦'),
                          lambda:world.nations.rename(nation.id,'新秦'),
                          lambda:world.nations.extinguish(nation.id,'再次灭亡'),
                          lambda:world.call_tool(nation.id,'foreign',{})):
            with self.assertRaises(ValueError):operation()
            self.assertEqual(self.save(world), before)

    def test_invalid_lifecycle_mutations_and_immutable_records(self):
        world = self.populate(self.world())
        for operation in (lambda:world.nations.rename('scenario:qin',''),
                          lambda:world.nations.extinguish('scenario:chu',None),
                          lambda:world.nations.extinguish('scenario:chu','  ')):
            before = self.save(world)
            with self.assertRaises(ValueError):operation()
            self.assertEqual(self.save(world), before)
        with self.assertRaises(FrozenInstanceError):world.nations.get('scenario:qin').name = '改写'
        self.assertEqual(world.nations.get('scenario:qin').name, '秦')

    def test_foreign_modules_need_declared_interfaces_and_cannot_write_directly(self):
        world = self.populate(self.world(official=True))
        before = self.save(world)
        with unit_context('native:economy-queries'):
            with self.assertRaises(ModError):world.nations.rename('scenario:qin','改写')
            with self.assertRaises(ModError):world.nations.restore({'nations':[]})
            with self.assertRaises(ModError):world.runtime.object_call(world,'native:nations','create','scenario:x','X')
        self.assertEqual(self.save(world), before)
        inspection = world.runtime.units['native:nations'].mod.objects(world)['native:nation-inspection']
        self.assertFalse(hasattr(inspection, 'create'))
        self.assertFalse(hasattr(inspection, 'extinguish'))

    def test_corrupt_records_and_missing_account_are_rejected_before_registry_commit(self):
        world = self.populate(self.world())
        original = world.nations.to_save()
        cases = [{'nations':{}}, {'nations':original['nations'] * 2}]
        for changes in ({'id':'秦'}, {'name':''}, {'alive':1}, {'extinction_reason':'非法'},
                        {'alive':False}, {'id':'scenario:missing'}, {'extra':1}):
            bad = deepcopy(original)
            bad['nations'][0].update(changes)
            cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises((ValueError,KeyError)):world.nations.restore(bad)
                self.assertEqual(world.nations.to_save(), original)

    def test_declared_consumer_can_read_without_lifecycle_write_access(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            source = (ROOT/'mods/nations_acceptance/mod.toml').read_text()
            source = source.replace('../native/', str(ROOT/'mods/native') + '/')
            source = source.replace('Members = [', 'Members = ["reader/mod.toml", ', 1)
            (path/'mod.toml').write_text(source)
            (path/'reader').mkdir()
            (path/'reader/mod.toml').write_text(
                '[Mod]\nId="example:reader"\nVersion="1"\n'
                'Entry="tests.test_nation_objects:NationReader"\nRequires=["native:nations"]\n'
                '[Interfaces]\n"native:nation-inspection"=["snapshot"]\n')
            world = self.populate(Runtime.load(path).world_class()())
            with unit_context('example:reader'):
                records = world.runtime.object_call(world,'native:nation-inspection','snapshot')
                self.assertEqual(records[0].name, '秦')
                with self.assertRaises(ModError):
                    world.runtime.object_call(world,'native:nations','extinguish','scenario:chu','非法')
                with self.assertRaises(ModError):
                    world.runtime.object_call(world,'native:nation-inspection','ids')

    def test_load_rejects_extinct_owner_sites(self):
        world = self.populate(self.world())
        state = self.save(world)
        state['native:nations']['nations'][0].update(alive=False, extinction_reason='国祚已失')
        restored = self.world()
        with self.assertRaises(ValueError):restored.runtime.load_mod_state(restored,state)
        self.assertEqual(restored.nations.snapshot(), ())

    def test_dependency_checked_json_roundtrip_preserves_extinction_and_resumes_economy(self):
        world = self.populate(self.world(official=True))
        world.nations.extinguish('scenario:chu','剧本裁决')
        world.call_tool('scenario:qin','build',{'site':'scenario:capital','kind':'native:farm'})
        bundle = {'dependencies':world.runtime.dependency_manifest(), 'state':self.save(world)}
        with TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic-nations.json'
            path.write_text(json.dumps(bundle))
            loaded = json.loads(path.read_text())
            restored = self.world(official=True)
            restored.runtime.check_save_dependencies(loaded['dependencies'])
            restored.runtime.load_mod_state(restored,loaded['state'])
        self.assertEqual(self.save(restored), self.save(world))
        for _ in range(10):
            world.resolve_turn()
            restored.resolve_turn()
        self.assertEqual(self.save(restored), self.save(world))
        self.assertEqual(restored.nation_actors, ('scenario:qin',))
        self.assertEqual(restored.resources.balance('scenario:qin','native:food'), 9)
        self.assertEqual(restored.nations.sites('scenario:qin')[0].owner,'scenario:qin')

    def test_queries_remain_read_only_after_settlement(self):
        world = self.populate(self.world())
        world.resolve_turn()
        before = self.save(world)
        self.assertEqual(world.call_tool('scenario:qin','foreign',{})['nation'].nation.name, '秦')
        self.assertEqual(len(world.call_tool('scenario:qin','foreign',{})['nations']), 2)
        self.assertEqual(self.save(world), before)


if __name__ == '__main__':unittest.main()
