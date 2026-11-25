from pathlib import Path
root = Path('/content/codex-takeover')
for name in ('stage.log', 'rebuild-native.log', 'ppo.log'):
    path = root / name
    if path.is_file(): print(name, '\n', '\n'.join(path.read_text().splitlines()[-10:]))
print('cache files:', len(list((root / 'demo-cache').glob('*.pkl.gz'))))
