import copy
import unittest

from rl.economy.provenance import contract_files,contracts_from_files,contracts,require_compatible,legacy_registry


class ProvenanceChecks(unittest.TestCase):
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
