import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import prepare_global_ppo as package
from mixed_bc_run import digest


class GlobalPrepareTests(unittest.TestCase):
    def test_verified_bc10_fork_keeps_reward_private_and_resume_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);work=root/'bc/work';work.mkdir(parents=True);ready=root/'script-bc-kaggle-results';ready.mkdir()
            model=work/'epoch-10.pt';model.write_bytes(b'pure script BC model');native=work/'epoch-10-native.pt';native.write_bytes(b'LibTorch baseline')
            (work/'state.json').write_text(json.dumps(dict(epoch=10,initialization='random')))
            (work/'segment.json').write_text(json.dumps(dict(native_sha256=digest(native))))
            (ready/'next-stage-ready.json').write_text(json.dumps(dict(work=str(work),checkpoint=model.name,sha256=digest(model))))
            (root/'industry-bc-results').mkdir()
            with ZipFile(root/'industry-bc-results/code.zip','w') as z:z.writestr('python/rl/model.py','# fixture')
            for name in ('global_ppo_run.py','ab_ppo_run.py','land_gdp_ppo_run.py','mixed_bc_run.py'):(root/name).write_text('# fixture')
            stage=root/'stage'
            with patch.object(package,'ROOT',root),patch.object(package,'STAGE',stage),patch.object(package,'source_digest',return_value='fixture'):
                package.prepare();state=json.loads((stage/'state-data/resume-state.json').read_text())
                self.assertEqual(state['contract']['actor_observation_gdp'],'nominal_only')
                self.assertEqual(state['contract']['land_gdp_mode'],'holding')
                self.assertEqual(state['contract']['gae_lambda'],1.)
                self.assertEqual(state['contract']['sequence_lengths'],[2,4])
                # Validate the generated contract against the real consumers,
                # including the GPU preflight path missed by earlier fixtures.
                source_root=Path(__file__).resolve().parents[2]
                required=set()
                for filename in ('ab_ppo_run.py','global_ppo_run.py'):
                    tree=ast.parse((source_root/filename).read_text())
                    for node in ast.walk(tree):
                        if (isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name)
                                and node.value.id=='config' and isinstance(node.slice,ast.Constant)
                                and isinstance(node.slice.value,str)):
                            required.add(node.slice.value)
                self.assertFalse(required-set(state['contract']), 'Missing runtime configuration keys')
                self.assertNotIn(state['contract']['preflight_seed'],
                    range(state['contract']['seed'],state['contract']['seed']+state['contract']['games']))
                for row in state['models'].values():self.assertEqual((stage/'state-data'/row['latest']).read_bytes(),model.read_bytes())
                state['games']=200;(stage/'state-data/resume-state.json').write_text(json.dumps(state));package.prepare()
                self.assertEqual(json.loads((stage/'state-data/resume-state.json').read_text())['games'],200)


if __name__=='__main__':unittest.main()
