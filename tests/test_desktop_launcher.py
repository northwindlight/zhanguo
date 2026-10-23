"""Prevent successful C# builds from masking the wrong Godot executable."""
import subprocess
import unittest
from unittest.mock import patch

from desktop import main, require_dotnet_godot


class TestDesktopLauncher(unittest.TestCase):
    @patch('desktop.subprocess.run')
    def test_accepts_mono_version(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '4.7.2.stable.mono.official.ed1daf0bf\n', '')
        require_dotnet_godot('godot_console', {})
        self.assertEqual(run.call_args.args[0], ['godot_console', '--headless', '--version'])

    @patch('desktop.subprocess.run')
    def test_standard_editor_stops_before_build_and_import(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '4.7.2.stable.official.ed1daf0bf\n', '')
        with patch('sys.stderr') as stderr, self.assertRaises(SystemExit) as raised:
            main(['--godot', '/tmp/godot-standard', '--dotnet', '/tmp/dotnet'])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(run.call_count, 1)
        self.assertIn('GodotEngine.GodotEngine.Mono', ''.join(call.args[0] for call in stderr.write.call_args_list))


if __name__ == '__main__':unittest.main()
