"""Generic engine contracts; no native world or production save is loaded."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
from types import SimpleNamespace
import unittest

from engine.runtime import GameMod, ModError, Runtime, Stage, Unit


class Definitions(GameMod):
    def __init__(self, definitions=None, stages=()):
        self.definitions = definitions or {}
        self.steps = stages
    def mixins(self):return ()
    def content_definitions(self):return self.definitions
    def stages(self):return self.steps


class LoadedHooks(GameMod):
    def mixins(self):return ()
    def stages(self):
        return (Stage('step',lambda w,c:w.trace.append('step'),hook='example-first'),)


class TestContent(unittest.TestCase):
    def test_domains_accumulate_in_namespaces_and_definitions_are_enumerable(self):
        runtime=Runtime([Unit('a',Definitions({'example:thing':{'field':1}})),
                         Unit('b',Definitions({'other:thing':{'field':2},'example:second':42}))])
        self.assertEqual(runtime.content['example']['thing']['field'],1)
        self.assertEqual(runtime.content['other']['thing']['field'],2)
        self.assertEqual(runtime.content.definition('example:second'),42)
        self.assertEqual({key for key,value in runtime.content.definitions()},
                         {'example:thing','other:thing','example:second'})
        with self.assertRaises(TypeError):runtime.content['example']['thing']['field']=999
        with self.assertRaises(TypeError):runtime.content['example']['new']=1
        runtime.register_content('a',{'example:new':3})
        self.assertEqual(runtime.content['example']['new'],3)

    def test_invalid_and_duplicate_registration_is_rejected_atomically(self):
        runtime=Runtime([Unit('a',Definitions({'example:thing':1}))])
        for definitions in ({'example:fresh':2,'example:thing':3},
                            {'example:fresh':2,'没有命名空间':3}):
            before=runtime.content.definitions()
            with self.assertRaises(ModError):runtime.register_content('a',definitions)
            self.assertEqual(runtime.content.definitions(),before)
        with self.assertRaises(ModError):runtime.register_content('missing',{'example:new':1})
        with self.assertRaises(ModError):Runtime([Unit('a',Definitions({'example:thing':1})),
                                                 Unit('b',Definitions({'example:thing':2}))])

    def test_registered_ids_are_in_save_environment_contract(self):
        runtime=Runtime([Unit('a',Definitions({'example:thing':1}))])
        manifest=deepcopy(runtime.dependency_manifest())
        runtime.register_content('a',{'example:second':2})
        with self.assertRaises(ModError):runtime.check_save_dependencies(manifest)

    def test_engine_does_not_interpret_or_copy_opaque_definitions(self):
        class Opaque:
            def __deepcopy__(self,memo):raise AssertionError('Engine must not interpret this definition')
        definition=Opaque()
        runtime=Runtime([Unit('a',Definitions({'example:opaque':definition}))])
        self.assertIs(runtime.content['example']['opaque'],definition)


class TestHooks(unittest.TestCase):
    def runtime(self, stages, hooks):
        return Runtime([Unit('example:steps',Definitions(stages=stages))], {'Hooks':hooks})

    def test_distribution_order_and_same_hook_dependencies_control_execution(self):
        hooks=[{'Id':'z-first','Name':'First'},{'Id':'unused'},{'Id':'a-last','Name':'Last'}]
        stages=(Stage('a.consumer',lambda w,c:w.trace.append('consumer'),hook='z-first',needs=('ready',)),
                Stage('z.provider',lambda w,c:w.trace.append('provider'),hook='z-first',provides=('ready',)),
                Stage('last',lambda w,c:w.trace.append('last'),hook='a-last',needs=('ready',)))
        runtime=self.runtime(stages,hooks)
        world=SimpleNamespace(phase='行动',trace=[])
        runtime.settle(world)
        self.assertEqual(world.trace,['provider','consumer','last'])
        self.assertEqual(runtime.hooks,('z-first','unused','a-last'))
        self.assertIn('First',runtime.settlement_table())
        self.assertIn('Last',runtime.settlement_table())

    def test_hook_count_and_names_have_no_engine_default(self):
        self.assertFalse(hasattr(Runtime,'DEFAULT_HOOKS'))
        hooks=[{'Id':f'custom.{index}'} for index in range(27)]
        runtime=self.runtime((),hooks)
        self.assertEqual(len(runtime.hooks),27)
        self.assertEqual(Runtime([]).hooks,())

    def test_invalid_duplicate_unknown_and_missing_hooks_are_rejected(self):
        for hooks in ([{'Id':'x'},{'Id':'x'}],[{'Id':'补给'}],{'x':'label'}):
            with self.assertRaises(ModError):self.runtime((),hooks)
        stage=Stage('x',lambda w,c:None,hook='missing')
        with self.assertRaises(ModError):self.runtime((stage,),[{'Id':'known'}])
        with self.assertRaises(ModError):self.runtime((stage,),[])
        with self.assertRaises(ModError):self.runtime((Stage('unplaced',lambda w,c:None),),[{'Id':'known'}])

    def test_missing_reversed_and_cyclic_dependencies_are_rejected(self):
        hooks=[{'Id':'first'},{'Id':'last'}]
        cases=(
            (Stage('a',lambda w,c:None,hook='first',needs=('missing',)),),
            (Stage('a',lambda w,c:None,hook='first',requires=('missing',)),),
            (Stage('a',lambda w,c:None,hook='first',after=('missing',)),),
            (Stage('a',lambda w,c:None,hook='first',before=('missing',)),),
            (Stage('a',lambda w,c:None,hook='first',needs=('late',)),
             Stage('b',lambda w,c:None,hook='last',provides=('late',))),
            (Stage('a',lambda w,c:None,hook='first',after=('b',)),
             Stage('b',lambda w,c:None,hook='last')),
            (Stage('a',lambda w,c:None,hook='first',needs=('b-ready',),provides=('a-ready',)),
             Stage('b',lambda w,c:None,hook='first',needs=('a-ready',),provides=('b-ready',))))
        for stages in cases:
            with self.subTest(stages=[s.name for s in stages]),self.assertRaises(ModError):self.runtime(stages,hooks)

    def test_top_level_hooks_load_from_distribution_mod_toml(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);edition=root/'edition';edition.mkdir()
            domain=edition/'domain';domain.mkdir()
            (root/'mods.toml').write_text('[Load]\nPackages=["edition"]\n')
            (edition/'mod.toml').write_text('[Distribution]\nId="example"\nMembers=["domain/mod.toml"]\n'
                                           '[[Hooks]]\nId="example-first"\nName="First"\n')
            (domain/'mod.toml').write_text('[Mod]\nId="example:hooks"\nVersion="1"\n'
                                          f'Entry="{__name__}:LoadedHooks"\n')
            runtime=Runtime.load(root/'mods.toml')
            self.assertEqual(runtime.hooks,('example-first',))
        native=tomllib.loads((Path(__file__).resolve().parents[1]/'mods/native/mod.toml').read_text())
        self.assertTrue(native['Hooks'])
        self.assertEqual(len({row['Id'] for row in native['Hooks']}),len(native['Hooks']))
        self.assertNotIn('Hooks',native['Distribution'])
