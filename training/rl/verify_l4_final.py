import hashlib
from pathlib import Path
from datetime import datetime
root=Path('/content')
for local, path in [('bc-epoch08.pt','zl/runs/bcT1/bc.pt'),('bc-native.pt','bcT1-native.pt'),
                    ('ppo/ck0030.pt','codex-takeover/ppo/ck0030.pt'),
                    ('ppo/ppo-native.pt','codex-takeover/ppo/ppo-native.pt')]:
    with (root/path).open('rb') as f:
        print(hashlib.file_digest(f,'sha256').hexdigest()+'  '+local,flush=True)
for path in ('zl/runs/bcT1/bc.pt','codex-takeover/ppo/done.json'):
    print(path, datetime.fromtimestamp((root/path).stat().st_mtime).astimezone().isoformat())
