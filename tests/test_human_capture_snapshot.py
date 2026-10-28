"""Playback retains full authorized drawing facts without rebuilding controls."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from host import GameHost
from mods.native.human import HumanAPI
from mods.ui.desktop_player import frame
from mods.ui.desktop_human import PlayerCapture
from tests.test_human import config,A,B


class TestCaptureSnapshot(TestCase):
    def test_visual_snapshot_has_identical_facts_map_and_projection_and_is_readonly(self):
        for mods in ('mods.toml','mods.experimental.toml'):
            with self.subTest(mods=mods):
                h=GameHost.new(config(mods));api=HumanAPI(h)
                self.assertTrue(api.submit(A,'buy_map',{'target':B})['ok'])
                full=api.state(A);before=deepcopy(h.runtime.save_mod_state(h.world))
                with patch.object(h,'observer_snapshot',side_effect=AssertionError('observer leak')):
                    visual=api.state(A,controls=False)
                for key in ('facts','map','actor','period','action_state','can_act','colors'):
                    self.assertEqual(full[key],visual[key])
                self.assertEqual(frame(h.ui,full),frame(h.ui,visual))
                self.assertEqual(before,h.runtime.save_mod_state(h.world))
                self.assertTrue(any(t['visibility']=='memory' for t in visual['map']['tiles']))
                self.assertTrue(any(t['visibility']=='unknown' for t in visual['map']['tiles']))
                for key in ('operations','army_orders','production_queue','reports','map_offers','pending_end'):
                    self.assertIn(key,full);self.assertNotIn(key,visual)

    def test_capture_does_not_request_control_catalog_and_default_state_still_does(self):
        h=GameHost.new(config());api=HumanAPI(h)
        control=SimpleNamespace(host=h,human_api=api,viewer=A)
        with patch.object(api,'operations',side_effect=AssertionError('unneeded controls')):
            capture=PlayerCapture(control);capture.capture(B,'buy',{}, {'ok':True})
            self.assertIsNone(capture.warning)
            with self.assertRaisesRegex(AssertionError,'unneeded controls'):api.state(A)
