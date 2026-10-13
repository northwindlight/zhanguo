"""可扩展引擎的契约：依赖、自动工具、阶段和说明内容。"""
import unittest
from types import SimpleNamespace
from engine.runtime import GameMod, ModError, RuleSection, Runtime, Stage, Tool, Unit


def add(world, actor, quantity: int, enabled: bool = True):
    """增加本 Mod 的计数。"""
    if enabled:
        world.count += quantity
    return world.count


class CounterMod(GameMod):
    def mixins(self):
        return ()

    def tools(self):
        return (Tool('add', add, ('增加',)),)

    def stages(self):
        return (Stage('counter', lambda w, c: c.update(value=w.count), title='计数'),)

    def rule_sections(self, world=None):
        return (RuleSection('计数规则', '本 Mod 计数规则。\n{{SETTLEMENT_STAGES}}', ('计数',)),)


class TestRuntime(unittest.TestCase):
    def test_abstract_mod(self):
        with self.assertRaises(TypeError):
            GameMod()

    def test_missing_dependency(self):
        with self.assertRaisesRegex(ModError, '缺少依赖'):
            Runtime([Unit('counter', CounterMod(), requires=('missing',))])

    def test_requires_does_not_imply_order(self):
        empty = type('Empty', (GameMod,), {'mixins': lambda s: ()})
        r = Runtime([Unit('a', empty(), requires=('b',)), Unit('b', empty())])
        self.assertEqual(r.order, ['a', 'b'])

    def test_after_before_and_cycle(self):
        empty = type('Empty', (GameMod,), {'mixins': lambda s: ()})
        r = Runtime([Unit('a', empty(), after=('b',)), Unit('b', empty())])
        self.assertEqual(r.order, ['b', 'a'])
        with self.assertRaisesRegex(ModError, '存在环'):
            Runtime([Unit('a', empty(), after=('b',)), Unit('b', empty(), after=('a',))])

    def test_alias_and_automatic_conversion(self):
        r = Runtime([Unit('counter', CounterMod())])
        w = SimpleNamespace(count=0)
        self.assertEqual(r.call(w, 'owner', '增加', '{"quantity":"4","enabled":"yes"}'), 4)
        self.assertEqual(r.call(w, 'owner', 'add', {'quantity': 10, 'enabled': 'false'}), 4)
        with self.assertRaises(ValueError):
            r.call(w, 'owner', 'add', {'quantity': 1.5})
        self.assertEqual(w.count, 4)
        schema = r.schemas()[0]['function']['parameters']
        self.assertEqual(schema['properties']['quantity']['type'], 'integer')
        self.assertEqual(schema['required'], ['quantity'])

    def test_no_actor_override(self):
        r = Runtime([Unit('counter', CounterMod())])
        with self.assertRaises(ValueError):
            r.call(SimpleNamespace(count=0), 'me', 'add', {'actor': 'enemy', 'quantity': 1})

    def test_conflicting_tools_rejected(self):
        with self.assertRaisesRegex(ModError, '冲突'):
            Runtime([Unit('a', CounterMod()), Unit('b', CounterMod())])

    def test_non_native_mod_runs_without_native_rules(self):
        r = Runtime([Unit('counter', CounterMod())])
        w = SimpleNamespace(count=8, phase='行动')
        self.assertEqual(r.settle(w), {'value': 8})
        self.assertEqual(w.phase, '已结算')
        self.assertIn('计数规则', r.rules(w, '计数'))
        self.assertIn('| 1 | 计数 |', r.manual())

    def test_dependency_identity_rejects_version_and_declaration_changes(self):
        r = Runtime([Unit('counter', CounterMod(), version='1')])
        r.check_save_dependencies(r.dependency_manifest())
        other = Runtime([Unit('counter', CounterMod(), version='2')])
        with self.assertRaisesRegex(ModError, '拒绝加载'):
            other.check_save_dependencies(r.dependency_manifest())

    def test_extra_stage_is_scheduled_and_documented(self):
        class Extra(GameMod):
            def mixins(self): return ()
            def stages(self):
                return (Stage('extra', lambda w, c: c.update(extra=True), requires=('counter',),
                              before=('counter',), title='扩展'),)
            def rule_sections(self, world=None): return (RuleSection('扩展规则', '扩展正文', ('扩展',)),)
        r = Runtime([Unit('counter', CounterMod()), Unit('extra', Extra(), requires=('counter',))])
        self.assertEqual(r.stage_order, ['extra', 'counter'])
        self.assertIn('扩展规则', r.manual())
        self.assertIn('扩展正文', r.rules(SimpleNamespace(), '扩展'))


class TestDeclaredExtension(unittest.TestCase):
    def test_loaded_extension_tools_state_and_manual(self):
        import json
        import tempfile
        from pathlib import Path
        from host import GameHost, SaveFormatError

        class Extension(GameMod):
            def mixins(self): return ()
            def state_keys(self): return ('extension_counter',)
            def initialize_world(self, world): world.extension_counter = 0
            def tools(self):
                def increment(world, actor, amount: int):
                    world.extension_counter += amount
                    return world.extension_counter
                return (Tool('extension_increment', increment, ('扩展计数',)),)
            def stages(self):
                return (Stage('extension.stage', lambda w, c: None,
                    hook='relations', needs=('relations.applied',), title='扩展结算'),)
            def rule_sections(self, world=None):
                return (RuleSection('扩展说明', '这是扩展 Mod 的规则', ('扩展规则',)),)
            def save_state(self, world): return {'counter': world.extension_counter}
            def load_state(self, world, state): world.extension_counter = state['counter']

        # 真实声明装载路径；类发布在当前测试模块以避免生成外部代码。
        import sys
        sys.modules[__name__].DeclaredExtension = Extension
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            import tomllib
            manifest = Path('mods/native/mod.toml').resolve()
            source = manifest.read_text()
            cfg = tomllib.loads(source)
            members = [str(manifest.parent / member) for member in cfg['Distribution']['Members']]
            import re
            source = re.sub(r'^Members = .*$', 'Members = '+json.dumps(members), source, flags=re.M)
            (directory/'native.toml').write_text(source)
            (directory/'extension.toml').write_text(
                '[Mod]\nId="extension"\nVersion="1"\nEntry="'+__name__+':DeclaredExtension"\nRequires=["native:government"]\nAfter=["native:government"]\n')
            runtime = Runtime.load(directory)
            cfg={'map_size':16,'seed':3,'nations':[{'id':'scenario:qin','name':'秦','client':'rule'}]}
            host = GameHost.new(cfg,runtime=runtime)
            w = host.world
            self.assertEqual(host.player('scenario:qin').submit('extension_increment', {'amount':'7'})['data'], 7)
            self.assertIn('扩展说明', runtime.manual())
            self.assertIn('扩展 Mod', runtime.rules(w,'扩展规则'))
            self.assertLess(runtime.stage_order.index('extension.stage'), runtime.stage_order.index('native:army.supply'))
            p = directory/'synthetic.json'
            # Save before player actions, at a complete cycle boundary.
            host.cycle();host.save(p)
            restored = GameHost.load(p,cfg,runtime=runtime)
            self.assertEqual(restored.world.extension_counter, 7)
            with self.assertRaises(SaveFormatError):
                GameHost.load(p,cfg)
