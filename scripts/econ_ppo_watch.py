"""Back up continuous Colab PPO and queue independent local evaluations.

No training or model promotion runs on this machine. CLI calls have bounded
timeouts; failed evaluations never stop the exploratory training chain.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import zipfile


CLI = '/home/northwind/.local/bin/colab'
PYTHON = '/home/northwind/zhanguo/.venv/bin/python'
COLAB_PYTHON = '/home/northwind/.local/share/pipx/venvs/google-colab-cli/bin/python'
REMOTE = '/content/zhanguo-ppo-cycle'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(at=time.time(), **data), ensure_ascii=False, indent=2)+'\n')
    tmp.replace(path)


def command(args, timeout):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    # CLI error URLs can contain authentication; never echo their raw output.
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}): {args[:2]}')
    return result.stdout


def install_backup(partial, live, manifest):
    if partial.stat().st_size != manifest['bytes'] or digest(partial) != manifest['sha256']:
        raise ValueError('Backup checksum/size mismatch')
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip():
            raise ValueError('Backup CRC failed')
        for item in archive.infolist():
            path = Path(item.filename)
            if path.is_absolute() or '..' in path.parts or item.file_size > 128*1024**2:
                raise ValueError('Unexpected backup entry')
            if item.filename not in manifest['changed']:
                raise ValueError('Backup entry absent from manifest')
        if set(archive.namelist()) != set(manifest['changed']):
            raise ValueError('Incomplete backup archive')
        for item in archive.infolist():
            data = archive.read(item)
            if hashlib.sha256(data).hexdigest() != manifest['files'][item.filename]['sha256']:
                raise ValueError('Backup entry digest mismatch')
            dest = live/item.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix+'.tmp')
            tmp.write_bytes(data)
            tmp.replace(dest)


def remote_code(known, stop=False):
    return f'''import hashlib,json,time,zipfile
from pathlib import Path
root=Path({REMOTE!r});run=root/'rl/runs/ppo-cycle'
if {stop!r} and run.exists():(run/'STOP').touch()
known={known!r}
files={{}};changed=[];payload={{}}
if run.exists():
 for p in sorted(run.rglob('*')):
  if not p.is_file() or p.suffix not in ('.json','.jsonl','.pt','.gz'):continue
  if p.stat().st_size>128*1024**2:raise RuntimeError('Snapshot entry too large')
  name=str(p.relative_to(run));data=p.read_bytes()
  files[name]=dict(sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
  if known.get(name)!=files[name]:changed.append(name);payload[name]=data
backup=Path('/content/econ-ppo-backup.zip')
with zipfile.ZipFile(backup,'w',compression=zipfile.ZIP_STORED) as z:
 for name in changed:z.writestr(name,payload[name])
pid=int((root/'manager.pid').read_text()) if (root/'manager.pid').exists() else 0
proc=Path(f'/proc/{{pid}}/stat')
alive=proc.exists() and proc.read_text().split()[2]!='Z'
states={{str(p.relative_to(root)):json.loads(p.read_text()) for p in root.glob('rl/runs/*/state.json')}}
logs={{str(p.relative_to(root)):p.read_text()[-400:] for p in root.glob('rl/runs/*/round-*/world-*.log')}}
data=dict(files=files,changed=changed,bytes=backup.stat().st_size,sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),alive=alive,pid=pid,states=states,world_progress=logs,manager_tail=(root/'manager.log').read_text()[-2000:] if (root/'manager.log').exists() else '')
print('BACKUP '+json.dumps(data))
'''


def queue_evaluation(root, out, live):
    active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet',
                             'zhanguo-ppo-evaluation-20261107'], capture_output=True).returncode == 0
    if active:
        return
    candidates = sorted(live.glob('round-*/candidate.json'))
    for record_path in reversed(candidates):
        folder = record_path.parent
        record = json.loads(record_path.read_text())
        checkpoint = folder/'candidate.pt'
        if not checkpoint.exists() or digest(checkpoint) != record['sha256']:
            continue
        marker = out/'evaluations'/folder.name/'summary.json'
        if marker.exists() and json.loads(marker.read_text()).get('phase') == 'complete':
            continue
        # Reuse baseline/BC15 games with their full cache identities intact.
        cache = out/'evaluation-cache'
        cache.mkdir(exist_ok=True)
        for p in (root/'archive/previews/econ14-20261107/evaluation').glob('*-*-*-*-*.json'):
            if not (cache/p.name).exists():
                shutil.copyfile(p, cache/p.name)
        command(['systemd-run', '--user', '--collect', '--unit=zhanguo-ppo-evaluation-20261107',
                 '--working-directory='+str(root), '-p', 'MemoryMax=4G', '-p', 'CPUQuota=100%',
                 '-p', 'StandardOutput=append:'+str(out/'evaluation.log'),
                 '-p', 'StandardError=append:'+str(out/'evaluation.log'),
                 PYTHON, '-u', str(root/'scripts/econ_ppo_evaluate.py'), '--checkpoint', str(checkpoint),
                 '--out', str(out/'evaluations'/folder.name), '--cache', str(cache)], 15)
        return


def refresh(args, reset=False):
    helper=Path(__file__).with_name('econ_colab_session.py')
    cmd=[COLAB_PYTHON, str(helper), '--session', args.session,
         '--endpoint-file', str(args.out/'endpoint.txt')]
    if reset:cmd.append('--reset-kernel')
    result=subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    if result.returncode==2:return False
    if result.returncode:raise RuntimeError('Could not refresh owned Colab assignment')
    return True


def recover(args, live):
    # A genuinely reclaimed runtime is replaced through the normal allocator.
    # No SSH/anti-idle or paid upgrade is used.
    if not refresh(args, reset=True):raise RuntimeError('Owned runtime reclaimed')
    command([CLI, 'exec', '-s', args.session, '-f', str(args.out/'probe.py'), '--timeout', '25'], 35)
    # If the existing runtime responds, restart only its detached launcher.
    command([CLI, 'exec', '-s', args.session, '-f', str(args.out/'bootstrap.py'), '--timeout', '35'], 45)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--interval', type=int, default=180)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    live = args.out/'live';live.mkdir(parents=True, exist_ok=True)
    failures = 0;last_recovery = 0
    while True:
        try:
            if shutil.disk_usage(args.out).free < 1024**3:
                raise RuntimeError('Local free space below 1 GiB reserve')
            known = {str(f.relative_to(live)): dict(sha256=digest(f), bytes=f.stat().st_size)
                     for f in live.rglob('*') if f.is_file() and f.suffix in ('.json','.jsonl','.pt','.gz')}
            request = args.out/'snapshot-request.py'
            accepted = any(json.loads(f.read_text()).get('accepted') is True
                           for f in (args.out/'evaluations').glob('*/summary.json'))
            stop = accepted or (args.out/'STOP').exists()
            request.write_text(remote_code(known, stop=stop))
            if not refresh(args):raise RuntimeError('Owned runtime reclaimed')
            result = command([CLI, 'exec', '-s', args.session, '-f', str(request), '--timeout', '35'], 45)
            manifest = json.loads(next(line[7:] for line in result.splitlines() if line.startswith('BACKUP ')))
            partial = args.out/'backup.partial.zip'
            if not refresh(args):raise RuntimeError('Owned runtime reclaimed during backup')
            command([CLI, 'download', '-s', args.session, '/content/econ-ppo-backup.zip', str(partial)], 180)
            install_backup(partial, live, manifest)
            partial.unlink()
            # Remote retention keeps the latest two completed trajectory rounds.
            # Only mirror deletions after the replacement snapshot is verified.
            for f in live.glob('round-*/trajectory-*'):
                if str(f.relative_to(live)) not in manifest['files']:
                    f.unlink()
            write(args.out/'watch-state.json', dict(phase='monitoring', **manifest))
            if not stop:
                queue_evaluation(root, args.out, live)
            failures = 0
            if stop and not manifest['alive']:
                write(args.out/'watch-state.json', dict(phase='accepted' if accepted else 'stopped', **manifest))
                return
            if not manifest['alive']:
                recover(args, live)
            print(json.dumps(dict(event='backup', changed=len(manifest['changed']),
                                  alive=manifest['alive'], states=manifest['states'])), flush=True)
        except Exception as error:
            failures += 1
            write(args.out/'watch-state.json', dict(phase='retrying', failures=failures, error=str(error)))
            print(f'Monitor retry {failures}: {type(error).__name__}', flush=True)
            if failures >= 2 and time.time()-last_recovery >= 600:
                last_recovery = time.time()
                try:
                    recover(args, live)
                except Exception:
                    try:
                        if refresh(args):
                            raise RuntimeError('Existing runtime still assigned; defer allocation')
                        args.out.joinpath('endpoint.txt').unlink(missing_ok=True)
                        command([CLI, 'new', '-s', args.session, '--gpu', 'T4'], 50)
                        if not refresh(args):raise RuntimeError('New assignment unavailable')
                        command([CLI, 'upload', '-s', args.session, str(args.out/'runtime.tar.gz'),
                                 '/content/econ-ppo-runtime.tar.gz'], 180)
                        restore = args.out/'restore.zip'
                        with zipfile.ZipFile(restore, 'w', compression=zipfile.ZIP_STORED) as archive:
                            for f in live.rglob('*'):
                                if f.is_file():archive.write(f, str(f.relative_to(live)))
                        if restore.stat().st_size > 1024**3:
                            raise RuntimeError('Recovery archive exceeds 1 GiB')
                        command([CLI, 'upload', '-s', args.session, str(restore), '/content/econ-ppo-restore.zip'], 180)
                        restore.unlink()
                        command([CLI, 'exec', '-s', args.session, '-f', str(args.out/'bootstrap.py'), '--timeout', '35'], 45)
                    except Exception as recovery_error:
                        write(args.out/'recovery-state.json', dict(phase='waiting_for_free_T4', error=type(recovery_error).__name__))
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
