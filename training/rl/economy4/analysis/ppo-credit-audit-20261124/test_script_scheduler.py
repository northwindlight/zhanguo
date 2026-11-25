import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import watch_script_bc as scheduler
from mixed_bc_run import digest


class ScriptSchedulerTests(unittest.TestCase):
    def test_all_five_segments_finish_ten_epochs_and_publish_new_ppo_parent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);stage=root/'stage';out=root/'out';out.mkdir();(root/'script-bc-results').mkdir()
            (root/'script-bc-results/done.json').write_text('{}')
            for name in ('state-data','code-data','corpus-data'):(stage/name).mkdir(parents=True)
            (stage/'prepared.json').write_text('{}')
            (stage/'state-data/resume-state.json').write_text(json.dumps(dict(epoch=0,latest=None,sha256=None,initialization='random',seed=20261124,epochs=10)))
            (stage/'kernel.py').write_text('EXPECTED_EPOCH = -1 # SCRIPT_EXPECTED_EPOCH\nEXPECTED_SHA = None # SCRIPT_EXPECTED_SHA\n')
            downloads=[]
            def command(args,*unused,**kwargs):
                if args[1:3]==['kernels','output']:
                    path=Path(args[args.index('-p')+1]);number=int(path.name);epoch=number*2;downloads.append(epoch)
                    work=path/'work';work.mkdir(parents=True);name=f'epoch-{epoch:02}.pt';(work/name).write_bytes(f'random pure script epoch {epoch}'.encode())
                    sha=digest(work/name);state=dict(epoch=epoch,latest=name,sha256=sha,initialization='random',seed=20261124,epochs=10)
                    (work/'state.json').write_text(json.dumps(state));segment=dict(epoch=epoch,sha256=sha)
                    if epoch==10:
                        (work/'epoch-10-native.pt').write_bytes(b'verified native policy');segment['native_sha256']=digest(work/'epoch-10-native.pt')
                    (work/'segment.json').write_text(json.dumps(segment))
                return 0
            with patch.object(scheduler,'ROOT',root),patch.object(scheduler,'STAGE',stage),patch.object(scheduler,'OUT',out),\
                 patch.object(scheduler.shared,'upload_data'),patch.object(scheduler.shared,'command',side_effect=command),patch.object(scheduler,'watch'):
                scheduler.main()
            self.assertEqual(downloads,[2,4,6,8,10]);ready=json.loads((out/'next-stage-ready.json').read_text())
            self.assertEqual(ready['reward'],'nominal_gdp+territory_holding');self.assertEqual(ready['actor_observation_gdp'],'nominal_only')
            self.assertIn('EXPECTED_EPOCH = 8',(stage/'kernel.py').read_text())


if __name__=='__main__':unittest.main()
