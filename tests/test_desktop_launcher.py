"""Prevent successful C# builds from masking the wrong Godot executable."""
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import main, require_dotnet_godot


class TestDesktopLauncher(unittest.TestCase):
    def test_vulkan_tour_uses_resolved_tools_and_sdk_environment(self):
        # WinGet aliases can leave Godot looking for GodotSharp beside Links.
        # Resolve a real symlink, including non-ASCII and spaced paths, before
        # invoking the version check, import, and windowed run.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root/'安装目录'/'Godot .NET'
            links = root/'WinGet'/'Links'
            package.mkdir(parents=True); links.mkdir(parents=True)
            godot = package/'godot.exe'; godot.touch()
            godot_link = links/'godot_console.exe'
            dotnet = root/'dotnet'/'dotnet.exe'
            dotnet.parent.mkdir(); dotnet.touch()
            dotnet_link = links/'dotnet.exe'
            try:
                godot_link.symlink_to(godot); dotnet_link.symlink_to(dotnet)
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('当前 Windows 用户无权创建测试用符号链接')
                raise
            project = root/'工程'; (project/'Samples').mkdir(parents=True)
            (project/'Samples'/'Terrain.tscn').touch()
            ui = Mock(); ui.desktop_project.return_value = project
            output = root/'测试结果'/'windows vulkan.png'
            log = output.with_suffix('.log')
            with patch('desktop.load_ui', return_value=ui), patch('desktop.subprocess.run') as run, \
                    patch('desktop.subprocess.call', return_value=0) as launch, patch('builtins.print'), \
                    self.assertRaises(SystemExit) as raised:
                run.return_value = subprocess.CompletedProcess([], 0, '4.7.2.stable.mono.official.ed1daf0bf\n', '')
                main(['--godot', str(godot_link), '--dotnet', str(dotnet_link),
                      '--sample', 'terrain', '--rendering-method', 'mobile', '--rendering-driver', 'vulkan',
                      '--log-file', str(log), '--sample-tour', '--smoke-output', str(output)])
            self.assertEqual(raised.exception.code, 0)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[0].args[0][0], str(godot.resolve()))
            self.assertEqual(run.call_args_list[1].args[0][0], str(dotnet.resolve()))
            self.assertEqual(run.call_args_list[2].args[0][0], str(godot.resolve()))
            self.assertEqual(launch.call_args.args[0], [
                str(godot.resolve()), '--path', str(project), '--rendering-method', 'mobile',
                '--rendering-driver', 'vulkan', '--log-file', str(log.resolve()),
                'res://Samples/Terrain.tscn', '--', '--smoke-output', str(output.resolve()), '--sample-tour'])
            launch_env = launch.call_args.kwargs['env']
            self.assertEqual(launch_env['DOTNET_ROOT'], str(dotnet.resolve().parent))
            for call in run.call_args_list:
                self.assertEqual(call.kwargs['env'], launch_env)

    def test_tour_rejects_missing_sample_or_output_before_start(self):
        for args in [['--sample-tour'], ['--sample', 'terrain', '--sample-tour']]:
            with self.subTest(args=args), patch('desktop.subprocess.run') as run, patch('sys.stderr'), self.assertRaises(SystemExit):
                main(args)
            run.assert_not_called()

    def test_sample_rejects_resume_before_any_process_starts(self):
        with patch('desktop.subprocess.run') as run, patch('sys.stderr'), self.assertRaises(SystemExit):
            main(['--sample', 'terrain', '--resume', '/tmp/unopened-session.json'])
        run.assert_not_called()

    def test_sample_requires_scene_from_selected_ui_mod(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = Mock(); ui.desktop_project.return_value = Path(directory)
            with patch('desktop.load_ui', return_value=ui), patch('desktop.subprocess.run') as run, patch('sys.stderr'), self.assertRaises(SystemExit):
                main(['--sample', 'terrain'])
        run.assert_not_called()

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
