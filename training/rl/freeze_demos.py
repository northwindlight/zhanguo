import hashlib
import json
from pathlib import Path
import os
import signal
import subprocess
import time

root = Path('/content/demos/w3t')
archiving = globals().get('archive_mode', False)
if archiving:
    collectors = {}
    for line in subprocess.check_output(['ps', '-eo', 'pid,ppid,args'], text=True).splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) == 3 and fields[2].startswith('python3 -m rl.collect --out /content/demos/w3t '):
            collectors[int(fields[0])] = int(fields[1])
    for pid, parent in collectors.items():
        if parent not in collectors:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
    time.sleep(2)
manifest = []
for marker in sorted(root.glob('s*.done')):
    try:
        info = json.loads(marker.read_text())
    except (FileNotFoundError, ValueError):
        continue
    for seat in info['kept']:
        record = info['infos'][seat]
        if record['seat'] != seat or record['winner'] != seat:
            raise ValueError(f'{marker.name}: expected a winning seat')
        path = root / f'{marker.stem}_k{seat}.pkl.gz'
        with path.open('rb') as episode:
            digest = hashlib.file_digest(episode, 'sha256').hexdigest()
        manifest.append(dict(name=path.name, size=path.stat().st_size, sha256=digest,
                             seed=info['seed'], seat=seat, decisions=info['decs'][seat]))

name = 'archive' if archiving else 'bc'
target = Path(f'/content/codex-cpu-{name}-manifest.json')
stage = target.with_suffix('.tmp')
stage.write_text(json.dumps(manifest) + '\n')
stage.replace(target)
print(dict(episodes=len(manifest), decisions=sum(item['decisions'] for item in manifest),
           bytes=sum(item['size'] for item in manifest)), flush=True)
