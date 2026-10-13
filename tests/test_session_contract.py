"""Optional administration and grid policies must not narrow generic sessions."""
from unittest import TestCase
from unittest.mock import patch

from engine.runtime import Runtime, ModError
from engine.session import Session
from engine.scenario import GridSession
from host import GameHost
from mp_run import Observer
from tests import test_session_api as counter


class TestSessionContract(TestCase):
    def cfg(self):
        return {'map_size':12,'seed':3,'nations':[
            {'id':'probe:left','name':'甲','client':'rule'},
            {'id':'probe:right','name':'乙','client':'rule'}]}

    def test_observer_bad_bindings_fail_during_assembly(self):
        for binding in ('Records','Announce','Send'):
            for method in ('missing_method','_private','_events'):
                with self.subTest(binding=binding,method=method):
                    runtime=Runtime.load('mods.toml')
                    runtime.distribution['Session']['Observer'][binding]=method
                    with self.assertRaises(ModError):GameHost.new(self.cfg(),runtime=runtime)
        runtime=Runtime.load('mods.toml')
        runtime.distribution['Session']['Observer']['Capability']=[]
        with self.assertRaises(ModError):GameHost.new(self.cfg(),runtime=runtime)

    def test_noncallable_public_observer_method_is_rejected(self):
        runtime=Runtime.load('mods.toml')
        from mods.native.intelligence.objects import Intelligence
        with patch.object(Intelligence,'announce',42):
            with self.assertRaisesRegex(ModError,'不可调用'):GameHost.new(self.cfg(),runtime=runtime)

    def test_management_is_optional_for_no_map_game(self):
        host,_,_=counter.TestDistributionAPI().host()
        self.assertNotIsInstance(host.session,GridSession)
        self.assertFalse(hasattr(host.session,'grid'))
        self.assertEqual(host.management(),())
        # No scheduled identities: the generic default requires no map hook.
        with patch.object(counter.CounterSession,'scheduled',Session.scheduled):host.cycle()
        self.assertEqual(host.clock(),1)
        with self.assertRaisesRegex(ModError,'不支持终端面板'):host.board()
        with self.assertRaisesRegex(ModError,'不支持手动登场'):host.add('someone')
        with self.assertRaisesRegex(ValueError,'终端管理接口'):Observer(host,'unused-journal.md')

    def test_inherited_stubs_do_not_advertise_management(self):
        host,_,_=counter.TestDistributionAPI().host()
        class Panel(counter.CounterSession):
            def board_rows(self):return []
        host.session.__class__=Panel
        self.assertEqual(host.management(),('board',))
        class NoPanel(Panel):
            board=None
        host.session.__class__=NoPanel
        self.assertNotIn('board',host.management())

    def test_observer_partial_binding_has_only_available_interfaces(self):
        runtime=Runtime.load('mods.toml')
        spec=runtime.distribution['Session']['Observer']
        del spec['Send']
        host=GameHost.new(self.cfg(),runtime=runtime)
        self.assertEqual(host.management(),('add','records','announce','board'))
        with self.assertRaisesRegex(ValueError,'send'):Observer(host,'unused-journal.md')

    def test_grid_distribution_preserves_public_and_private_messages(self):
        host=GameHost.new(self.cfg())
        self.assertIsInstance(host.session,GridSession)
        self.assertEqual(host.management(),('add','records','announce','send','board'))
        host.announce('公开测试')
        host.send('probe:left','仅甲可见')
        left=host.player('probe:left').events()['items']
        right=host.player('probe:right').events()['items']
        self.assertTrue(any(row['text']=='公开测试' for row in left))
        self.assertTrue(any(row['text']=='公开测试' for row in right))
        self.assertTrue(any(row['text']=='仅甲可见' for row in left))
        self.assertFalse(any(row['text']=='仅甲可见' for row in right))
        self.assertTrue(any(row['text']=='仅甲可见' for row in host.records()['events']))

    def test_scheduled_grid_arrival_is_seeded_and_resume_is_identical(self):
        cfg=self.cfg()
        cfg['nations'][1].update(appearance='scheduled',enable_turn=1,enable_turn_max=2)
        host=GameHost.new(cfg)
        restored=GameHost.new(cfg)
        restored.restore(host.to_save())
        for _ in range(3):
            host.cycle();restored.cycle()
            self.assertEqual(host.to_save(),restored.to_save())
        self.assertIn('probe:right',host.actors())
