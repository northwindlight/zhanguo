#!/usr/bin/env python3
"""Build and launch the selected UI Mod's Godot project (offline sample)."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ui import load_ui

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description='启动战国 Godot 上帝视角离线演示')
    parser.add_argument('--godot', default=os.environ.get('GODOT'))
    parser.add_argument('--dotnet', default=os.environ.get('DOTNET'))
    parser.add_argument('--ui-mods', default=str(ROOT/'ui.toml'))
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--smoke-output', type=Path)
    parser.add_argument('--resume', type=Path, help='从旧演示局 session.json 复制续看；旧档保留')
    args = parser.parse_args(argv)
    ui = load_ui({'ui_mods': args.ui_mods})
    project = getattr(ui, 'desktop_project', None)
    if not callable(project):parser.error('当前 UI Mod 未提供桌面入口 desktop_project')
    project = project()
    local = Path.home()/'tools'/'zhanguo-desktop'
    godot = args.godot or shutil.which('godot') or shutil.which('godot4')
    if not godot:
        godot = next(iter(local.glob('Godot*mono_linux_arm64/Godot*mono_linux.arm64')), None)
    dotnet = args.dotnet or shutil.which('dotnet')
    if not dotnet and (local/'dotnet'/'dotnet').is_file():dotnet = str(local/'dotnet'/'dotnet')
    if not godot or not dotnet:parser.error('需要 Godot 4.7.2 .NET 与 .NET 8 SDK；可用 --godot / --dotnet 指定路径')
    godot = str(Path(godot).resolve()); dotnet = str(Path(dotnet).resolve())
    env = dict(os.environ, DOTNET_ROOT=str(Path(dotnet).parent), ZHANGUO_ROOT=str(ROOT), ZHANGUO_PYTHON=sys.executable,
               ZHANGUO_UI_MODS=str(Path(args.ui_mods).resolve()))
    env['PATH'] = str(Path(dotnet).parent)+os.pathsep+env.get('PATH', '')
    if args.resume:env['ZHANGUO_RESUME'] = str(args.resume.resolve())
    build = [dotnet, 'build', str(project/'Zhanguo.Desktop.csproj')]
    packages = Path(godot).parent/'GodotSharp'/'Tools'/'nupkgs'
    if packages.is_dir():build += ['--source', str(packages), '--source', 'https://api.nuget.org/v3/index.json']
    subprocess.run(build, check=True, env=env)
    subprocess.run([godot, '--headless', '--editor', '--path', str(project), '--import'], check=True, env=env)
    if args.build_only:return
    command = [godot, '--path', str(project)]
    if args.smoke_output:command += ['--', '--smoke-output', str(args.smoke_output.resolve())]
    raise SystemExit(subprocess.call(command, env=env))


if __name__ == '__main__':main()
