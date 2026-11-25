from pathlib import Path
import subprocess

root = Path('/content')
print(list(root.glob('cx/**/economy.cpp')))
print(subprocess.getoutput('nvidia-smi --query-gpu=name,memory.used,utilization.gpu --format=csv,noheader'))
print(subprocess.getoutput('free -h'))
print('\n'.join(line for line in subprocess.getoutput('ps -eo pid,etimes,%cpu,rss,args').splitlines()
                if any(entry in line for entry in ('rl.bc ', 'run_ppo.py', 'zhanguo_torch sample', 'ppo_update.py'))))
print([(path.name, path.stat().st_size) for path in root.glob('*.log')])
for name in ('pipe.log', 'build.log', 'bcT1.log'):
    path = root / name
    if path.is_file():
        print(name, '\n', '\n'.join(path.read_text(errors='replace').splitlines()[-6:]))
