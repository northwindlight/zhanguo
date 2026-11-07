import copy
import ast
import unittest

from rl.economy.provenance import contract_files,contracts_from_files,contracts,require_compatible,legacy_registry,stable_ast,canonical


class ProvenanceChecks(unittest.TestCase):
    def test_contract_serialization_does_not_depend_on_stdlib_dump_format(self):
        from unittest.mock import patch
        files=contract_files();before=contracts_from_files(files)
        with patch('ast.dump',return_value='different interpreter representation'):
            canonical.cache_clear()
            self.assertEqual(contracts_from_files(files),before)
        self.assertEqual(stable_ast(ast.parse('x = None')),
            "Module(body=[Assign(targets=[Name(id='x', ctx=Store())], value=Constant(value=None))])")
        self.assertIn('MatchSingleton(value=None)',stable_ast(ast.parse('match x:\n case None: pass')))
        self.assertNotEqual(stable_ast(ast.parse('x = None')),stable_ast(ast.parse('x = []')))

    def test_trainer_changes_do_not_invalidate_feature_contract(self):
        files=contract_files();before=contracts_from_files(files)
        files['rl/economy/online.py']=files['rl/economy/online.py'].replace(b'lr=3e-5',b'lr=1e-5')
        files['rl/economy/train.py']=b'irrelevant implementation change'
        self.assertEqual(contracts_from_files(files),before)

    def test_real_feature_or_reward_change_is_rejected(self):
        original=contract_files();before=contracts_from_files(original)
        for path,old,new in [('rl/economy/adapter.py',b'WIDTH = 96',b'WIDTH = 97'),
            ('rl/economy/online.py',b'S_SCALE=1000.',b'S_SCALE=2000.')]:
            files=dict(original);self.assertIn(old,files[path]);files[path]=files[path].replace(old,new)
            changed=contracts_from_files(files)
            self.assertNotEqual(before['feature_contract'],changed['feature_contract'])
            with self.assertRaisesRegex(ValueError,'contract mismatch'):require_compatible(before,changed)

    def test_s2_s3_legacy_snapshots_are_verified_without_rewriting_origin(self):
        expected=contracts()
        for prefix in ('a6a8c5a5','f39f3569'):
            source=next(key for key in legacy_registry() if key.startswith(prefix))
            metadata=dict(source=source);original=copy.deepcopy(metadata)
            self.assertEqual(require_compatible(metadata,expected)['feature_contract'],expected['feature_contract'])
            self.assertEqual(metadata,original)
        with self.assertRaisesRegex(ValueError,'Unverified legacy'):require_compatible(dict(source='unknown'))

    def test_audited_query_cache_keeps_existing_data_contract(self):
        self.assertEqual(contracts(),dict(
            feature_contract='fc7f507e1efde4439b69fdc1676190a6350054bead891af9d999948862eedeb9',
            teacher_contract='e8e612dc144bb2b46cdd6b09b6ccbc202a88112f213b6af3d5e7f81c2cb5e80a'))

    def test_cache_or_encoder_edits_need_a_new_equivalence_audit(self):
        before=contracts();files=contract_files()
        path='rl/economy/query_cache.py'
        self.assertIn(b'quantity >= 0',files[path])
        files[path]=files[path].replace(b'quantity >= 0',b'quantity > 0')
        changed=contracts_from_files(files)
        self.assertNotEqual(changed['feature_contract'],before['feature_contract'])
        with self.assertRaisesRegex(ValueError,'contract mismatch'):require_compatible(before,changed)

    def test_inference_cache_ignores_training_but_tracks_model_and_controller(self):
        from rl.economy.provenance import ROOT,inference_implementation
        files=contract_files()
        for name in ('model.py','layers.py','evaluate.py','client.py','benchmark.py'):
            path='rl/economy/'+name;files[path]=(ROOT/path).read_bytes()
        before=inference_implementation(files)
        files['rl/economy/train.py']=b'irrelevant trainer'
        files['rl/economy/online.py']=files['rl/economy/online.py'].replace(b'lr=3e-5',b'lr=1e-5')
        self.assertEqual(inference_implementation(files),before)
        changed=dict(files);changed['rl/economy/model.py']+=b'\nchanged_inference=True\n'
        self.assertNotEqual(inference_implementation(changed),before)
        changed=dict(files);changed['rl/economy/projects.py']=changed['rl/economy/projects.py'].replace(
            b'self.max_economy_actions=max_economy_actions',b'self.max_economy_actions=max_economy_actions+1')
        self.assertNotEqual(inference_implementation(changed),before)

    def test_cache_import_cannot_switch_to_an_untracked_helper(self):
        files=contract_files();path='rl/economy/projects.py'
        files[path]=files[path].replace(b'from .query_cache import memo_read_queries',
                                        b'from .different_cache import memo_read_queries')
        with self.assertRaisesRegex(ValueError,'Unverified query cache binding'):
            contracts_from_files(files)
