"""Export the selected distribution's declared rules, stages and parameters."""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

from engine.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'docs/游戏说明书.md'


def render(runtime):
    distribution = runtime.distribution
    title = f'{distribution.get("Id", "custom")} {distribution.get("Version", "")}'.strip()
    lines = [f'# 游戏说明书 · {title}', '',
             '由 `python -m docs.generate_manual` 从所选发行版声明生成。',
             '此文档列出模块公开规则、实际结算顺序与声明数值；领域实现及玩家 API 以对应模块为准。', '',
             runtime.manual().partition('\n\n')[2].rstrip(), '',
             '## 实际结算顺序', '', runtime.settlement_table(), '',
             '## 模块数值', '']
    for module, values in runtime.enumerate_numbers().items():
        if not values:
            continue
        lines.extend([f'### {module}', '', '```json',
                      json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False),
                      '```', ''])
    return '\n'.join(lines).rstrip() + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mods', type=Path, default=ROOT / 'mods.toml')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--check', action='store_true', help='Check without writing')
    args = parser.parse_args(argv)
    generated = render(Runtime.load(args.mods))
    if args.check:
        previous = args.output.read_text(encoding='utf-8') if args.output.exists() else ''
        if previous == generated:
            print(f'已同步：{args.output}')
            return 0
        print(''.join(difflib.unified_diff(previous.splitlines(True), generated.splitlines(True),
                                         fromfile=str(args.output), tofile='发行版声明')), end='')
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generated, encoding='utf-8')
    print(f'已生成：{args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
