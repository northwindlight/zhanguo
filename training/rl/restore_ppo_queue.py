from pathlib import Path
import os
import py_compile
import select
import signal
import subprocess
import sys
import time

root = Path('/content/codex-takeover')
original = root / 'run_ppo.py'
warm = root / 'run_ppo_value.py'
warm_pid_file = root / 'ppo-value.pid'
plain_pid_file = root / 'ppo-original.pid'
py_compile.compile(str(original), doraise=True)


def runs(pid, path):
    try:
        return str(path).encode() in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    except FileNotFoundError:
        return False


plain_pid = int(plain_pid_file.read_text()) if plain_pid_file.exists() else 9250
if runs(plain_pid, original):
    print(f'Original PPO supervisor already active: PID {plain_pid}', flush=True)
else:
    if ('PIPE_DONE' in Path('/content/pipe.log').read_text().splitlines()
            or (root / 'ppo/ck0000.pt').exists()):
        raise RuntimeError('PPO startup may have begun')
    warm_pid = int(warm_pid_file.read_text())
    if runs(warm_pid, warm):
        with os.fdopen(os.pidfd_open(warm_pid), 'rb') as proc:
            signal.pidfd_send_signal(proc.fileno(), signal.SIGTERM)
            if not select.select([proc], [], [], 5)[0]:
                raise RuntimeError('Queued warmup supervisor did not exit')
    with (root / 'ppo.log').open('ab') as log:
        log.write(b'Original PPO queue restored. Waiting for BC evaluation.\n')
        log.flush()
        job = subprocess.Popen([sys.executable, '-u', original], stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    plain_pid_file.write_text(f'{job.pid}\n')
    time.sleep(2)
    if job.poll() is not None:
        raise RuntimeError(f'Original supervisor exited with {job.returncode}')
    print(f'Original PPO supervisor active: PID {job.pid}', flush=True)
