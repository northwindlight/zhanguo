"""在 Linux（Xvfb + Mesa 软件渲染）上运行桌面离线验收并保存截图。

供 GitHub Actions 的界面截图任务使用，也可在有 Godot .NET 的 Linux 机器上手动运行：

    xvfb-run -a -s '-screen 0 1600x1000x24' python3 scripts/review_desktop_linux.py \
        --godot /path/Godot_v4.7.2-stable_mono_linux.x86_64 --output archive/previews/ui-linux

各项验收与 Windows 便携包验收共用同一套 Godot 内部检查（--human-review-output 等），
这里只负责构建、准备离线样例、逐项启动并汇总结果。软件渲染只证明布局与流程，
不代表实体显卡上的性能。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'mods' / 'ui' / 'desktop'
CHECKS = ('menu', 'human', 'finance', 'extinction', 'effects', 'ledger', 'startup', 'world')
TIMEOUTS = {'menu': 240, 'human': 1500, 'world': 600}


def run_check(name: str, godot: str, env: dict, out: Path) -> dict:
    flag = {'world': '--smoke-output'}.get(name, f'--{name}-review-output' if name != 'menu' else '--menu-output')
    shot = out / f'{name}.png'
    check_env = dict(env)
    if name == 'extinction' or name == 'finance':
        fixture = out / f'{name}-fixture.json'
        subprocess.run([sys.executable, '-X', 'utf8', '-m', 'mods.ui.desktop_review', f'--{name}', '--output', str(fixture)],
                       cwd=ROOT, check=True, env=env)
        check_env[f'ZHANGUO_{name.upper()}_REVIEW'] = str(fixture)
    if name == 'startup':
        check_env['ZHANGUO_STARTUP_FIXTURES'] = str(ROOT / 'tests' / 'fixtures')
    if name == 'ledger':
        fixtures = sorted(out.glob('effects-fixture-*.json'))
        if not fixtures:
            return {'name': name, 'ok': False, 'error': '缺少 effects 样例（先运行 effects）'}
        check_env['ZHANGUO_LEDGER_REVIEW'] = str(fixtures[0])
    command = [godot, '--path', str(PROJECT), '--audio-driver', 'Dummy', '--rendering-method', 'gl_compatibility',
               '--rendering-driver', 'opengl3', '--', flag, str(shot)]
    started = time.monotonic()
    stdout, stderr = out / f'{name}.stdout.txt', out / f'{name}.stderr.txt'
    with stdout.open('w', encoding='utf-8') as so, stderr.open('w', encoding='utf-8') as se:
        try:
            code = subprocess.run(command, env=check_env, stdout=so, stderr=se, timeout=TIMEOUTS.get(name, 420)).returncode
        except subprocess.TimeoutExpired:
            code = 'timeout'
    errors = [line for line in stderr.read_text(encoding='utf-8', errors='replace').splitlines() if re.match(r'^(ERROR|SCRIPT ERROR):', line)]
    report = out / f'{name}.png.json'
    return {'name': name, 'ok': code == 0 and not errors, 'exit': code, 'seconds': round(time.monotonic() - started, 1),
            'errors': errors[:20], 'report': report.name if report.exists() else None,
            'screenshots': sorted(p.name for p in out.glob(f'{name}*.png'))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--godot', default=os.environ.get('GODOT'))
    parser.add_argument('--dotnet', default=shutil.which('dotnet'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checks', default='menu,human,finance', help='逗号分隔：' + ','.join(CHECKS))
    args = parser.parse_args(argv)
    checks = [c for c in args.checks.split(',') if c]
    if unknown := set(checks) - set(CHECKS):
        parser.error(f'未知检查：{sorted(unknown)}')
    if not args.godot or not args.dotnet:
        parser.error('需要 --godot（Godot 4.7.2 .NET）与 dotnet')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / 'desktop.py'), '--build-only', '--godot', args.godot, '--dotnet', args.dotnet],
                   cwd=ROOT, check=True)
    env = dict(os.environ, DOTNET_ROOT=str(Path(args.dotnet).resolve().parent), ZHANGUO_ROOT=str(ROOT),
               ZHANGUO_PYTHON=sys.executable, ZHANGUO_UI_MODS=str((ROOT / 'ui.toml').resolve()))
    results = []
    for name in checks:
        print(f'== {name}', flush=True)
        result = run_check(name, str(Path(args.godot).resolve()), env, out)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
    (out / 'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    failed = [r['name'] for r in results if not r['ok']]
    if failed:
        print('失败：' + ', '.join(failed), file=sys.stderr)
        raise SystemExit(1)
    print('LINUX_UI_REVIEW_OK')


if __name__ == '__main__':
    main()
