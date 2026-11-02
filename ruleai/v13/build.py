"""Build the optional CPython extension for the v13 economy solver.

The extension embeds the SHA-256 of ``_econ_fast.c`` at compile time
(``ECON_FAST_DIGEST``). ``econ_fast`` refuses a build whose digest no longer
matches the source, so an edited solver can never silently run an old binary.
"""
from hashlib import sha256
from pathlib import Path
import os
import shlex
import subprocess
import sysconfig


def digest():
    return sha256((Path(__file__).resolve().parent / '_econ_fast.c').read_bytes()).hexdigest()


def build():
    root = Path(__file__).resolve().parent
    output = root / ('_econ_fast' + sysconfig.get_config_var('EXT_SUFFIX'))
    command = shlex.split(os.environ.get('CC', sysconfig.get_config_var('CC') or 'cc'))
    # -ffp-contract=off 是硬要求：/ 与 +-* 的收缩会改变末位，C 与 Python 就对不上。
    command += ['-O2', '-ffp-contract=off', '-fPIC', '-shared',
                '-I' + sysconfig.get_path('include'),
                f'-DECON_FAST_DIGEST="{digest()}"',
                str(root / '_econ_fast.c'), '-o', str(output), '-lm']
    subprocess.run(command, check=True)
    print(output)


if __name__ == '__main__':
    build()
