"""Upgrade a zhanguo session's dependency manifest to the current tool contract.

The domain state format is unchanged; only the manifest (module/tool/panel/content
declarations) needs to be refreshed. This does not replay actions and does not
touch module state. Always writes a backup unless --output is used.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.session import SAVE_FORMAT, write_json
from host import GameHost


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--save', required=True)
    parser.add_argument('--output', help='write upgraded save here instead of in place')
    parser.add_argument('--no-verify', action='store_true')
    args = parser.parse_args()

    config_path = Path(args.config)
    save_path = Path(args.save)
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    prepared = GameHost.prepare(cfg)
    manifest = prepared.runtime.dependency_manifest()

    data = json.loads(save_path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != SAVE_FORMAT:
        raise SystemExit(f'unsupported session format: {data.get("format")!r}')
    old = data.get('dependencies') or {}
    old_dist = (old.get('distribution') or {}).get('Version')
    new_dist = (manifest.get('distribution') or {}).get('Version')
    data['dependencies'] = manifest

    target = Path(args.output) if args.output else save_path
    if not args.output:
        backup = save_path.with_suffix(save_path.suffix + '.bak-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        shutil.copy2(save_path, backup)
        print(f'backup: {backup}')
    write_json(target, data)
    print(f'manifest: {old_dist} -> {new_dist}')
    print(f'written: {target}')

    if not args.no_verify:
        GameHost.load(target, cfg)
        print('verify: GameHost.load OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
