"""Start the prepared expansion curriculum on the current Colab runtime."""
from pathlib import Path
import subprocess
import sys

root=Path('/content/zhanguo-expansion-v2')
root.mkdir(exist_ok=True)
worker=r'''from pathlib import Path
import fcntl
import hashlib
import json
import shutil
import subprocess
import sys
from zipfile import ZipFile

root=Path('/content/zhanguo-expansion-v2')
deck=Path('/content')
with (root/'.schedule-lock').open('a') as lock:
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('Expansion v2 already running',flush=True)
        raise SystemExit
    if (root/'done.json').exists():
        print('Expansion v2 already completed',flush=True)
        raise SystemExit
    code=root/'code'
    code.mkdir(exist_ok=True)
    with ZipFile(deck/'zhanguo-expansion-v2-code.zip') as z:
        z.extractall(code)
    old=deck/'zhanguo-mixed-bc'
    old.mkdir(exist_ok=True)
    (old/'chunks').mkdir(exist_ok=True)
    for name in ('base.pt','base-native.pt'):
        if not (old/name).exists():
            parts=sorted(deck.glob('v2-original-'+name+'.[0-9][0-9][0-9]'))
            if not parts:
                raise RuntimeError('Original economic weights are missing')
            with (old/name).open('wb') as dest:
                for p in parts:
                    with p.open('rb') as source:shutil.copyfileobj(source,dest)
    for p in deck.glob('v2-original-chunk-*.zip'):
        target=old/'chunks'/p.name.removeprefix('v2-original-')
        if not target.exists():p.replace(target)
    if not (old/'done.json').exists():
        shutil.copyfile(code/'original-done.json',old/'done.json')
    restore=deck/'zhanguo-expansion-v2-restore.json'
    if restore.exists():
        for relative,item in json.loads(restore.read_text()).items():
            target=root/relative
            if target.exists():continue
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_suffix(target.suffix+'.partial')
            with temporary.open('wb') as dest:
                for part in item['parts']:
                    with (deck/part).open('rb') as source:shutil.copyfileobj(source,dest)
            with temporary.open('rb') as source:
                if hashlib.file_digest(source,'sha256').hexdigest()!=item['sha256']:
                    raise RuntimeError('Recovery checksum mismatch')
            temporary.replace(target)
        restore.unlink()
    command=[sys.executable,'-u',str(code/'expansion_v2_run.py'),'--output',str(root),
             '--existing',str(old),'--python-root',str(code/'python')]
    subprocess.run(command,check=True)
'''
script=root/'worker.py'
script.write_text(worker)
with (root/'driver.log').open('ab') as log:
    child=subprocess.Popen([sys.executable,'-u',str(script)],stdin=subprocess.DEVNULL,stdout=log,
                           stderr=subprocess.STDOUT,start_new_session=True)
print('Expansion v2 supervisor PID',child.pid,flush=True)
