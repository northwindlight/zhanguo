"""Build the optional CPython extension using the running interpreter."""
from pathlib import Path
import os
import shlex
import subprocess
import sysconfig


def build():
    root=Path(__file__).resolve().parent
    output=root/('_combat_fast'+sysconfig.get_config_var('EXT_SUFFIX'))
    command=shlex.split(os.environ.get('CC',sysconfig.get_config_var('CC') or 'cc'))
    command+=['-O2','-ffp-contract=off','-fPIC','-shared',
              '-I'+sysconfig.get_path('include'),str(root/'_combat_fast.c'),'-o',str(output)]
    subprocess.run(command,check=True)
    print(output)


if __name__=='__main__':build()
