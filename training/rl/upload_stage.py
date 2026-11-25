import base64
import json
from pathlib import Path
import sys
import time

import requests

root = Path(__file__).parent
config_dir = Path(sys.argv[1])
gpu, cpu = [json.loads((config_dir / f'.probe-session-{index}.json').read_text())['w3-probe']
            for index in range(2)]
names = ['stage_l4.py']
if '--archive-extras' not in sys.argv[2:]:
    names += ['cpp/economy.cpp', 'cpp/rl_candidates.cpp']
files = [(name, (root / name).read_bytes()) for name in names]
files += [('.cpu-transfer', json.dumps({key: cpu[key] for key in ('url', 'token')}).encode())]
url = gpu['url'].rstrip('/') + '/api/contents/content/codex-takeover/'
headers = {'X-Colab-Runtime-Proxy-Token': gpu['token'], 'X-Colab-Client-Agent': 'colab-cli'}
params = {'authuser': '0', 'colab-runtime-proxy-token': gpu['token']}
with requests.Session() as session:
    for name, data in files:
        name = Path(name).name
        for attempt in range(5):
            try:
                response = session.put(url + name, params=params, headers=headers, timeout=(10, 20),
                                       json={'type': 'file', 'format': 'base64',
                                             'content': base64.b64encode(data).decode()})
                if response.status_code in (200, 201):
                    print(f'Staged file {name} ({len(data)} bytes)', flush=True)
                    break
                print(f'{name}: HTTP {response.status_code}', flush=True)
            except requests.RequestException as error:
                print(f'{name}: {type(error).__name__}', flush=True)
            time.sleep(attempt + 1)
        else:
            raise SystemExit(f'Upload failed: {name}')
