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


def require_dotnet_godot(godot, env):
    """Reject the standard editor before building or importing C# resources."""
    try:
        result = subprocess.run([godot, '--headless', '--version'], check=True,
            capture_output=True, encoding='utf-8', errors='replace', timeout=20, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f'无法检查 Godot 版本（{godot}）：{exc}') from exc
    version = result.stdout.strip()
    if not any('mono' in line.split('.') for line in version.splitlines()):
        raise ValueError(
            f'当前 Godot 不含 C#/.NET 支持（{version or "版本未知"}）：{godot}\n'
            '需要 Godot 4.7.2 .NET（Mono）版。Windows 请安装 '
            'GodotEngine.GodotEngine.Mono，并用 --godot 指定该版本；'
            'GodotEngine.GodotEngine 是标准版，无法加载 Desktop.cs。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='启动战国 Godot 上帝视角离线演示')
    parser.add_argument('--godot', default=os.environ.get('GODOT'))
    parser.add_argument('--dotnet', default=os.environ.get('DOTNET'))
    parser.add_argument('--ui-mods', default=str(ROOT/'ui.toml'))
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--rendering-method', choices=['gl_compatibility', 'mobile', 'forward_plus'],
                        help='临时指定 Godot 渲染器；不修改项目默认配置')
    parser.add_argument('--rendering-driver', help='临时指定 Godot 图形驱动，如 vulkan 或 opengl3')
    parser.add_argument('--log-file', type=Path, help='将 Godot 运行日志保存到指定文件')
    parser.add_argument('--smoke-output', type=Path)
    parser.add_argument('--menu-output', type=Path, help='打开主菜单自检：截取各面板并核对生成的配置后退出')
    parser.add_argument('--ui-review-output', type=Path, help='离线界面样例：检查长文本、半身图和多个窗口尺寸，不调用模型')
    parser.add_argument('--resume', type=Path, help='从旧演示局 session.json 复制续看；旧档保留')
    parser.add_argument('--config', type=Path, help='mp_config.json 路径；提供后桌面运行该对局（可含 LLM 国家，思考与正文在气泡流式显示）')
    parser.add_argument('--sample', choices=['terrain'], help='打开独立合成地貌样板，不启动 Python 对局')
    parser.add_argument('--sample-tour', action='store_true', help='地貌样板预热后执行32秒镜头巡览并记录性能')
    args = parser.parse_args(argv)
    if sum(bool(x) for x in (args.menu_output,args.ui_review_output,args.smoke_output))>1:
        parser.error('--menu-output、--ui-review-output 与 --smoke-output 只能选一个')
    if args.sample and args.resume:parser.error('--sample 与 --resume 不能同时使用')
    if args.config and not Path(args.config).is_file():parser.error(f'--config 文件不存在：{args.config}')
    if args.sample_tour and (not args.sample or not args.smoke_output):
        parser.error('--sample-tour 需要 --sample terrain 和 --smoke-output')
    ui = load_ui({'ui_mods': args.ui_mods})
    project = getattr(ui, 'desktop_project', None)
    if not callable(project):parser.error('当前 UI Mod 未提供桌面入口 desktop_project')
    project = project()
    scene = project/'Samples'/'Terrain.tscn' if args.sample else None
    if scene and not scene.is_file():parser.error('当前 UI Mod 未提供地貌样板 Samples/Terrain.tscn')
    local = Path.home()/'tools'/'zhanguo-desktop'
    godot = args.godot or shutil.which('godot_console') or shutil.which('godot') or shutil.which('godot4')
    if not godot:
        godot = next(iter(local.glob('Godot*mono_linux_arm64/Godot*mono_linux.arm64')), None)
    dotnet = args.dotnet or shutil.which('dotnet')
    if not dotnet and (local/'dotnet'/'dotnet').is_file():dotnet = str(local/'dotnet'/'dotnet')
    if not godot or not dotnet:parser.error('需要 Godot 4.7.2 .NET 与 .NET 8 SDK；可用 --godot / --dotnet 指定路径')
    godot = str(Path(godot).resolve()); dotnet = str(Path(dotnet).resolve())
    print(f'Godot：{godot}', flush=True)
    env = dict(os.environ, DOTNET_ROOT=str(Path(dotnet).parent), ZHANGUO_ROOT=str(ROOT), ZHANGUO_PYTHON=sys.executable,
               ZHANGUO_UI_MODS=str(Path(args.ui_mods).resolve()))
    env['PATH'] = str(Path(dotnet).parent)+os.pathsep+env.get('PATH', '')
    try:require_dotnet_godot(godot, env)
    except ValueError as exc:parser.error(str(exc))
    if args.resume:env['ZHANGUO_RESUME'] = str(args.resume.resolve())
    if args.config:env['ZHANGUO_DESKTOP_CONFIG'] = str(Path(args.config).resolve())
    build = [dotnet, 'build', str(project/'Zhanguo.Desktop.csproj')]
    packages = Path(godot).parent/'GodotSharp'/'Tools'/'nupkgs'
    if packages.is_dir():build += ['--source', str(packages), '--source', 'https://api.nuget.org/v3/index.json']
    subprocess.run(build, check=True, env=env)
    subprocess.run([godot, '--headless', '--editor', '--path', str(project), '--import'], check=True, env=env)
    if args.build_only:return
    command = [godot, '--path', str(project)]
    if args.rendering_method:command += ['--rendering-method', args.rendering_method]
    if args.rendering_driver:command += ['--rendering-driver', args.rendering_driver]
    if args.log_file:command += ['--log-file', str(args.log_file.resolve())]
    if scene:command.append('res://Samples/Terrain.tscn')
    if args.smoke_output:command += ['--', '--smoke-output', str(args.smoke_output.resolve())]
    if args.menu_output:command += ['--', '--menu-output', str(args.menu_output.resolve())]
    if args.ui_review_output:command += ['--', '--ui-review-output', str(args.ui_review_output.resolve())]
    if args.sample_tour:command.append('--sample-tour')
    raise SystemExit(subprocess.call(command, env=env))


if __name__ == '__main__':main()
