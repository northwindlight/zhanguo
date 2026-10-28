"""Select Windows builds from all shipped runtime paths and pending versions."""
import os
from pathlib import Path
import subprocess

from scripts.release_portable import version


def scope(paths, pending):
    roots={'engine','clients','mods','presentation','ruleai','scenarios','ui','packaging'}
    runtime=any(Path(p).parts[0] in roots or len(Path(p).parts)==1 and Path(p).suffix in ('.py','.toml') or
        p in ('VERSION','scripts/package_windows.py','scripts/verify_windows_package.ps1',
              '.github/workflows/ci.yml','.github/workflows/windows-portable.yml') for p in paths)
    return {'portable':pending or runtime,'release':pending}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    base=os.environ.get('BASE_SHA','')
    if not base or set(base)=={'0'}:base='HEAD^'
    diff=subprocess.run(['git','diff','--name-only',base,'HEAD'],cwd=root,capture_output=True,text=True,check=False)
    paths=diff.stdout.splitlines() if diff.returncode==0 else subprocess.check_output(['git','ls-files'],cwd=root,text=True).splitlines()
    tag=subprocess.run(['git','rev-parse','--verify','--quiet','refs/tags/v'+version(root)+'^{commit}'],cwd=root,capture_output=True,text=True)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    # A retry of this exact commit may need to finish uploading its draft.
    pending=tag.returncode!=0 or tag.stdout.strip()==head
    result=scope(paths,pending)
    with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as output:
        for key,value in result.items():output.write(f'{key}={str(value).lower()}\n')
