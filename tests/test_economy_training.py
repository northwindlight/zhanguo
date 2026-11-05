"""Candidate subsampling and device plumbing for the economic BC pipeline."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys
import unittest

import numpy as np

from rl.economy.adapter import PROJECT_FINGERPRINT, WIDTH
from rl.economy.data import Dataset, Writer
from rl.economy.model import collate, device_of, prepare
from tests.test_economy_bc import torch


def rows(count, candidates, seed=0):
    """Synthetic teacher rows: dense context, ``candidates`` candidates each."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(count):
        features = np.zeros((candidates, WIDTH), dtype=np.float32)
        features[:, 5 + rng.integers(0, 3)] = 1.      # 动作组：finish/build/recruit
        features[:, 17] = rng.random(candidates)      # 可区分每一行
        out.append(dict(context=np.zeros((6, WIDTH), dtype=np.float32), candidates=features,
                        legal=np.ones(candidates, dtype=bool),
                        limits=np.zeros(candidates, dtype=np.int64),
                        target=int(rng.integers(0, candidates)), quantity=0.))
    return out


def write_world(path, seed, count=8, candidates=200):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = Writer(PROJECT_FINGERPRINT)
    for row in rows(count, candidates, seed):
        writer.add(row)
    writer.save(path, dict(seed=seed, turns=150, source='synthetic', teacher_probability=1.))


class DatasetChecks(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'world-121.npz'
        write_world(self.path, 121)

    def tearDown(self):
        self.temp.cleanup()

    def test_cap_keeps_teacher_choice_and_bounds_candidates(self):
        full = Dataset([self.path], fingerprint=PROJECT_FINGERPRINT)
        capped = Dataset([self.path], fingerprint=PROJECT_FINGERPRINT, max_candidates=32)
        for index in range(len(full)):
            expected = full[index]
            actual = capped[index]
            self.assertLessEqual(len(actual['candidates']), 32)
            self.assertEqual(len(actual['legal']), len(actual['candidates']))
            self.assertEqual(len(actual['limits']), len(actual['candidates']))
            # 教师选中的那一项必须逐位保留。
            np.testing.assert_array_equal(actual['candidates'][actual['target']],
                                          expected['candidates'][expected['target']])
            self.assertTrue(actual['legal'][actual['target']])

    def test_subsampling_is_stable_within_epoch_and_changes_across_epochs(self):
        first = Dataset([self.path], fingerprint=PROJECT_FINGERPRINT, max_candidates=24, seed=7)
        second = Dataset([self.path], fingerprint=PROJECT_FINGERPRINT, max_candidates=24, seed=7)
        np.testing.assert_array_equal(first[0]['candidates'], second[0]['candidates'])
        before = first[0]['candidates'].copy()
        first.set_epoch(1)
        self.assertFalse(np.array_equal(before, first[0]['candidates']))

    def test_zero_cap_keeps_every_candidate(self):
        full = Dataset([self.path], fingerprint=PROJECT_FINGERPRINT, max_candidates=0)
        self.assertEqual(len(full[0]['candidates']), 200)

    def test_validation_subset_is_deterministic(self):
        validation = Dataset([self.path], fingerprint=PROJECT_FINGERPRINT, max_candidates=16)
        first = validation[0]['candidates'].copy()
        for _ in range(3):
            np.testing.assert_array_equal(first, validation[0]['candidates'])


@unittest.skipIf(torch is None, 'Optional training dependencies')
class DeviceChecks(unittest.TestCase):
    @unittest.skipUnless(torch is not None and torch.cuda.is_available(), 'CUDA hardware required')
    def test_cuda_curriculum_and_loss_backward_share_device(self):
        from rl.economy.model import Policy
        from rl.economy.train import metrics
        model=Policy(action_mode='projects').cuda()
        optimizer=torch.optim.AdamW(model.parameters(),lr=1e-4)
        before=model.score[-1].weight.detach().clone()
        result=metrics(model,[collate(rows(2,32))],optimizer,
                       opening_weight=4.,mobilization_weight=3.)
        self.assertTrue(np.isfinite(result['loss']))
        self.assertFalse(torch.equal(before,model.score[-1].weight))

    def test_prepare_moves_every_tensor_and_keeps_other_keys(self):
        batch = collate(rows(2, 5))
        batch['meta'] = 'keep'
        prepared = prepare(batch)
        self.assertIs(prepared, batch)
        self.assertEqual(batch['meta'], 'keep')
        for key in ('context', 'candidates', 'legal', 'limits', 'target', 'quantity'):
            self.assertEqual(batch[key].device.type, 'cpu')

    def test_device_of_reads_model_parameters(self):
        from rl.economy.model import Policy
        self.assertEqual(device_of(Policy(action_mode='projects')).type, 'cpu')


@unittest.skipIf(torch is None, 'Optional training dependencies')
class TrainCliChecks(unittest.TestCase):
    def test_one_epoch_with_candidate_cap_and_recorded_device(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            write_world(root / 'data' / 'world-121.npz', 121, count=6, candidates=180)
            write_world(root / 'data' / 'world-222.npz', 222, count=3, candidates=180)
            out = root / 'model'
            command = [sys.executable, '-m', 'rl.economy.train', '--projects',
                       '--data', str(root / 'data'), '--valid-seeds', '222',
                       '--epochs', '1', '--batch', '3', '--threads', '1',
                       '--max-candidates', '16', '--device', 'cpu', '--out', str(out)]
            subprocess.run(command, check=True, cwd=Path(__file__).resolve().parents[1],
                           capture_output=True)
            saved = json.loads((out / 'run.json').read_text())
            self.assertEqual(saved['args']['max_candidates'], 16)
            self.assertEqual(saved['args']['device'], 'cpu')
            self.assertTrue((out / 'best.pt').exists())
            checkpoint = torch.load(out / 'best.pt', map_location='cpu', weights_only=True)
            self.assertEqual(checkpoint['epoch'], 1)


if __name__ == '__main__':
    unittest.main()


@unittest.skipIf(torch is None, 'Optional training dependencies')
class FamilyHeadChecks(unittest.TestCase):
    """动作族辅助头：修「教师建设被判成征兵」这类族级混淆。"""

    def batch(self, family, rows=4, candidates=12):
        from rl.economy.adapter import WIDTH
        examples = []
        for _ in range(rows):
            features = np.zeros((candidates, WIDTH), dtype=np.float32)
            features[:, 5] = 1.
            features[:, 5 + family] = 1.
            examples.append(dict(context=np.zeros((5, WIDTH), dtype=np.float32),
                                 candidates=features, legal=np.ones(candidates, dtype=bool),
                                 limits=np.zeros(candidates, dtype=np.int64),
                                 target=1, quantity=0.))
        return collate(examples)

    def test_family_head_changes_logits_and_trains(self):
        from rl.economy.model import Policy
        from rl.economy.train import metrics
        model = Policy(action_mode='projects', family_aux=True)
        plain = Policy(action_mode='projects')
        batch = self.batch(1)                      # build 族
        with torch.inference_mode():
            plain_logits = plain(batch)[0]
            blended = model(batch)[0]
        self.assertFalse(torch.allclose(plain_logits, blended))
        # 族头只看局面：形状是 (batch, 3)，与候选数无关（否则会读候选自己的 one-hot）
        self.assertEqual(tuple(model.last_family_logits.shape), (4, 3))
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        before = float(model.family_head.weight.detach().abs().sum())
        result = metrics(model, [batch], optimizer)
        self.assertIn('family_accuracy', result)
        self.assertTrue(np.isfinite(result['family_loss']))
        self.assertNotEqual(before, float(model.family_head.weight.detach().abs().sum()))

    def test_checkpoint_without_family_head_still_loads(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from rl.economy.model import Policy, load_policy
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'old.pt'
            model = Policy(action_mode='projects')
            torch.save(dict(config=model.config, model=model.state_dict(),
                            fingerprint=model.fingerprint, train_seeds=[1], valid_seeds=[2]),
                       path)
            loaded, _ = load_policy(path)
            self.assertIsNone(loaded.family_head)

    def test_family_head_survives_critic_transfer(self):
        from rl.economy.model import Policy
        from rl.economy.online import with_critic
        model = Policy(action_mode='projects', family_aux=True)
        critic = with_critic(model)
        self.assertTrue(critic.config.get('family_aux'))
        self.assertIsNotNone(critic.family_head)

    def test_family_head_rejects_atomic_mode(self):
        from rl.economy.model import Policy
        with self.assertRaises(ValueError):
            Policy(action_mode='atomic', family_aux=True)
