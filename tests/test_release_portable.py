"""Offline checks for immutable publication, exact-source artifacts and scope."""
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from scripts.ci_release_scope import scope
from scripts.release_portable import prepare, publish


class TestPortableRelease(unittest.TestCase):
    def checkout(self, tmp):
        root=Path(tmp)/'checkout';root.mkdir()
        (root/'VERSION').write_text('0.6.3\n')
        notes=root/'docs/releases/v0.6.3.md';notes.parent.mkdir(parents=True);notes.write_text('Offline release fixture\n')
        (root/'source.py').write_text('version = 1\n')
        subprocess.run(['git','init','-q',str(root)],check=True)
        subprocess.run(['git','add','.'],cwd=root,check=True)
        subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],cwd=root,check=True)
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        artifact=Path(tmp)/'artifact.zip'
        with zipfile.ZipFile(artifact,'w') as z:
            z.writestr('Zhanguo-Windows-x64/portable.json',json.dumps({'commit':commit,'version':'0.6.3'}))
        return root,commit,artifact,Path(tmp)/'output'

    def test_sources_and_checksums_match_verified_ci_artifact(self):
        with TemporaryDirectory() as tmp:
            root,commit,artifact,out=self.checkout(tmp)
            files=prepare(artifact,out,commit,root)
            self.assertEqual(files[0].read_bytes(),artifact.read_bytes())
            with zipfile.ZipFile(files[1]) as z:self.assertEqual(z.read('zhanguo-0.6.3/source.py'),b'version = 1\n')
            sums=files[2].read_text().splitlines()
            for path,row in zip(files,sums):self.assertEqual(row,hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
            with self.assertRaisesRegex(ValueError,'does not match'):prepare(artifact,out,'0'*40,root)

    def test_published_version_cannot_be_reused_or_overwritten(self):
        with TemporaryDirectory() as tmp:
            root,commit,artifact,out=self.checkout(tmp)
            with patch('scripts.release_portable.gh',return_value={'target_commitish':'0'*40,'draft':False}) as remote:
                with self.assertRaisesRegex(ValueError,'another commit'):publish(artifact,out,'fixture/repo',root)
                self.assertEqual(remote.call_count,1)
            expected=['zhanguo-0.6.3-windows-x64.zip','zhanguo-0.6.3-source.zip','SHA256SUMS']
            existing={'target_commitish':commit,'draft':False,'html_url':'https://example.invalid/release',
                'assets':[{'name':n,'size':1,'state':'uploaded'} for n in expected]}
            with patch('scripts.release_portable.gh',return_value=existing) as remote:
                publish(artifact,out,'fixture/repo',root);self.assertEqual(remote.call_count,1)
            existing['assets'].pop()
            with patch('scripts.release_portable.gh',return_value=existing):
                with self.assertRaisesRegex(ValueError,'incomplete'):publish(artifact,out,'fixture/repo',root)

    def test_only_complete_verified_upload_is_made_public(self):
        for corrupt in (True,False):
            with self.subTest(corrupt=corrupt),TemporaryDirectory() as tmp:
                root,commit,artifact,out=self.checkout(tmp)
                reads=0;calls=[]
                def remote(*args,**kwargs):
                    nonlocal reads
                    calls.append(args)
                    if args[0]=='api' and '/releases/tags/' in args[1]:
                        reads+=1
                        if reads==1:return None
                        return {'html_url':'https://example.invalid/release','assets':[
                            {'name':p.name,'size':p.stat().st_size,'state':'uploaded',
                             'digest':'sha256:'+('0'*64 if corrupt else hashlib.sha256(p.read_bytes()).hexdigest())}
                            for p in sorted(out.iterdir())]}
                    return None
                with patch('scripts.release_portable.gh',side_effect=remote):
                    if corrupt:
                        with self.assertRaisesRegex(ValueError,'did not verify'):publish(artifact,out,'fixture/repo',root)
                    else:publish(artifact,out,'fixture/repo',root)
                edits=[c for c in calls if c[:2]==('release','edit')]
                self.assertEqual(len(edits),0 if corrupt else 1)
                if edits:self.assertIn('--prerelease',edits[0]);self.assertIn('--latest=false',edits[0])

    def test_rule_only_changes_build_windows_and_docs_only_do_not_publish(self):
        for path in ('mods/native/army/navigation.py','engine/runtime.py','clients/provider.py','ruleai/v13/client.py','mods.toml','host.py'):
            self.assertEqual(scope([path],False),{'portable':True,'release':False})
        self.assertEqual(scope(['docs/plans/example.md'],False),{'portable':False,'release':False})
        self.assertEqual(scope(['docs/plans/example.md'],True),{'portable':True,'release':True})


if __name__=='__main__':unittest.main()
