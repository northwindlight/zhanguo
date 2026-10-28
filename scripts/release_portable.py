"""Publish a verified CI Windows artifact and the matching source as a pre-release.

Version changes opt a completed build into publication. Public assets are
immutable: rerunning the same source returns the existing complete release;
reusing its version for another source is an error.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def version(root=ROOT):
    value=(root/'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value):raise ValueError('VERSION must contain a three-part version')
    return value


def gh(*args, allow_missing=False):
    result=subprocess.run(['gh',*args],capture_output=True,text=True,check=False)
    if result.returncode:
        if allow_missing and 'HTTP 404' in result.stderr:return None
        raise RuntimeError(result.stderr.strip() or 'GitHub request failed')
    return (json.loads(result.stdout) if args[0]=='api' else result.stdout.strip()) if result.stdout.strip() else None


def find_release(repo, tag):
    published=gh('api',f'repos/{repo}/releases/tags/{tag}',allow_missing=True)
    if published:return published
    # Drafts have no published tag and the by-tag endpoint returns 404, even
    # after their assets have uploaded. Authenticated listing includes drafts.
    page=1
    while True:
        rows=gh('api',f'repos/{repo}/releases?per_page=100&page={page}')
        found=next((r for r in rows if r['tag_name']==tag),None)
        if found or len(rows)<100:return found
        page+=1


def prepare(artifact, output, commit, root=ROOT):
    v=version(root);output.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(artifact) as archive:
        metadata=json.loads(archive.read('Zhanguo-Windows-x64/portable.json'))
    if metadata.get('commit')!=commit or metadata.get('version')!=v:
        raise ValueError('Windows artifact source/version does not match this CI checkout')
    windows=output/f'zhanguo-{v}-windows-x64.zip';shutil.copyfile(artifact,windows)
    source=output/f'zhanguo-{v}-source.zip'
    subprocess.run(['git','archive','--format=zip',f'--prefix=zhanguo-{v}/',f'--output={source.resolve()}',commit],cwd=root,check=True)
    sums=output/'SHA256SUMS'
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    sums.write_text(''.join(f'{digest(p)}  {p.name}\n' for p in (windows,source)),encoding='ascii')
    return [windows,source,sums]


def publish(artifact, output, repo, root=ROOT):
    v=version(root);tag='v'+v
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    existing=find_release(repo,tag)
    expected={f'zhanguo-{v}-windows-x64.zip',f'zhanguo-{v}-source.zip','SHA256SUMS'}
    if existing:
        if existing['target_commitish']!=commit:raise ValueError('Version already belongs to another commit; bump VERSION')
        if not existing['draft']:
            assets=existing.get('assets',[])
            if {a['name'] for a in assets}!=expected or any(a['state']!='uploaded' or a['size']<=0 for a in assets):
                raise ValueError('Published release has incomplete assets; refusing to overwrite it')
            print('PRE_RELEASE_ALREADY_EXISTS',existing['html_url']);return
    # Reject a reused/moved tag too, including a failed draft from another run.
    ref=gh('api',f'repos/{repo}/git/ref/tags/{tag}',allow_missing=True)
    if ref and (ref['object']['type']!='commit' or ref['object']['sha']!=commit):
        raise ValueError('Version tag already points to another source')
    files=prepare(artifact,output,commit,root)
    notes=root/'docs/releases'/f'{tag}.md'
    if not notes.is_file():raise ValueError('Missing release notes: '+str(notes))
    if not existing:
        gh('release','create',tag,'--repo',repo,'--target',commit,'--title',f'战国 {v} 试玩版',
            '--notes-file',str(notes),'--draft','--prerelease','--latest=false')
        existing=find_release(repo,tag)
        if not existing:raise RuntimeError('Created draft is not visible; retry this CI job')
    # Only drafts may be retried/replaced after an interrupted upload.
    gh('release','upload',tag,*map(str,files),'--repo',repo,'--clobber')
    uploaded=gh('api',f'repos/{repo}/releases/{existing["id"]}')
    actual={a['name']:a for a in uploaded['assets']}
    if set(actual)!=expected:raise ValueError('Unexpected release attachments')
    for path in files:
        a=actual[path.name]
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if a['state']!='uploaded' or a['size']!=path.stat().st_size or a.get('digest')!='sha256:'+digest:
            raise ValueError('Uploaded attachment did not verify: '+path.name)
    gh('release','edit',tag,'--repo',repo,'--draft=false','--prerelease','--latest=false')
    print('PRE_RELEASE_PUBLISHED',uploaded['html_url'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repo',default=os.environ.get('GITHUB_REPOSITORY'))
    args=parser.parse_args()
    if not args.repo:parser.error('--repo or GITHUB_REPOSITORY is required')
    publish(args.artifact.resolve(),args.output.resolve(),args.repo)
