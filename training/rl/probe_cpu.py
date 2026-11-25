from pathlib import Path
import hashlib
import subprocess
root = Path('/content/demos/w3t')
print('Completed games', len(list(root.glob('*.done'))), 'retained winners', len(list(root.glob('*.pkl.gz'))))
for line in subprocess.getoutput('ps -eo pid,args').splitlines():
    if ' -m rl.collect ' in line and 'grep' not in line:
        pid, *args = line.split()
        cwd = Path(f'/proc/{pid}/cwd').resolve()
        print(cwd, ' '.join(args))
        for relative in ('rl/opponents.py', 'rl/obs.py', 'game.py'):
            path = cwd / relative
            if path.is_file(): print(relative, hashlib.sha256(path.read_bytes()).hexdigest())
        break
