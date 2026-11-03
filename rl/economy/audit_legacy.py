"""Reproduce legacy source and semantic contracts from exact Git blobs.

Read-only by default. Registering a source requires its exact hash to match the
specified revision; original datasets and checkpoints are never rewritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from .provenance import ROOT,contracts_from_files,legacy_registry

FOLDERS=('engine','mods/native','mods/economic_system','ruleai','rl/economy')


class GitSnapshots:
    def __init__(self,root=ROOT):
        self.root=root;self.blobs={};self.snapshots={}
        self.reader=subprocess.Popen(['git','cat-file','--batch'],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE)

    def close(self):
        self.reader.stdin.close();self.reader.stdout.close();self.reader.wait()

    def files(self,revision):
        commit=subprocess.check_output(['git','rev-parse','--verify',revision+'^{commit}'],cwd=self.root,text=True).strip()
        if commit not in self.snapshots:
            files={}
            tree=subprocess.check_output(['git','ls-tree','-rz',commit,'--','host.py',*FOLDERS],cwd=self.root)
            for entry in tree.split(b'\0'):
                if not entry:continue
                header,path=entry.split(b'\t',1);_,kind,sha=header.split();name=path.decode()
                if kind!=b'blob' or Path(name).suffix not in ('.py','.toml','.c'):continue
                if sha not in self.blobs:
                    self.reader.stdin.write(sha+b'\n');self.reader.stdin.flush()
                    header=self.reader.stdout.readline().split()
                    if len(header)!=3 or header[1]!=b'blob':raise ValueError('Git blob missing')
                    self.blobs[sha]=self.reader.stdout.read(int(header[2]));self.reader.stdout.read(1)
                files[name]=self.blobs[sha]
            self.snapshots[commit]=files
        return commit,self.snapshots[commit]


def historical_source(files,include_host=True):
    digest=hashlib.sha256()
    if include_host:digest.update(b'host.py');digest.update(files['host.py'])
    for folder in FOLDERS:
        for name in sorted(p for p in files if p.startswith(folder+'/') and Path(p).suffix in ('.py','.toml')):
            digest.update(name.encode());digest.update(files[name])
    return digest.hexdigest()


def audit(reader,source,revision,include_host=True):
    commit,files=reader.files(revision)
    if historical_source(files,include_host)!=source:raise ValueError('Source hash does not match the specified Git snapshot')
    return dict(commit=commit,host=include_host,**contracts_from_files(files))


def main():
    p=argparse.ArgumentParser();p.add_argument('--source');p.add_argument('--commit')
    p.add_argument('--without-host',action='store_true');p.add_argument('--register',action='store_true')
    p.add_argument('--verify-registry',action='store_true');a=p.parse_args()
    if not a.verify_registry and not(a.source and a.commit):p.error('Use --verify-registry or both --source and --commit')
    if a.register and (a.verify_registry or not a.source):p.error('--register requires one explicit source and commit')
    reader=GitSnapshots()
    try:
        if a.verify_registry:
            for source,entry in legacy_registry().items():
                actual=audit(reader,source,entry['commit'],entry['host'])
                if actual!=entry:raise ValueError(f'Legacy registry mismatch: {source}')
            print(json.dumps(dict(verified_sources=len(legacy_registry())),indent=2))
        else:
            entry=audit(reader,a.source,a.commit,not a.without_host)
            print(json.dumps({a.source:entry},indent=2))
            if a.register:
                registry=dict(legacy_registry());registry[a.source]=entry
                path=Path(__file__).with_name('legacy_contracts.json');tmp=path.with_suffix('.tmp')
                tmp.write_text(json.dumps(registry,indent=2)+'\n');tmp.replace(path)
    finally:reader.close()


if __name__=='__main__':main()
