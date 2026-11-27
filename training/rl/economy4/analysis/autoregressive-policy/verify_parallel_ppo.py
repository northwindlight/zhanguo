"""Compare globally normalized PPO gradients and updates against single-process replay."""
from pathlib import Path
from types import SimpleNamespace
import copy
import hashlib
import random
import tempfile

import torch
import torch.distributed as dist
import torch.multiprocessing as mp

from memory_map import MemoryMap
from model import Policy
from parallel_ppo import ReplayGroup
from rollout_trace import GameRecord, TraceHeader, TurnBoundary
from thought_ppo import Window, learning_pass, optimize, partition_games, replay
from verify_replay import play


def optimizers(model):
    actor = torch.optim.AdamW([p for n, p in model.named_parameters() if not n.startswith("value.")],
                              lr=1e-4, weight_decay=0)
    critic = torch.optim.AdamW(model.value.parameters(), lr=1e-4, weight_decay=0)
    return actor, critic


def examples(model):
    result = []
    for i in range(4):
        trace = play(model, TraceHeader(None, False), seed=1024 + i, stop_after=8 + i)
        end = next(j for j, r in enumerate(trace.records) if isinstance(r, TurnBoundary))
        records = trace.records[:end]
        games = tuple(j for j, record in enumerate(records) if isinstance(record, GameRecord))
        targets = {j: (i + 1) * .02 + j / 500 for j, record in enumerate(records)
                   if getattr(record, "logp", None) is not None}
        result.append(Window(trace.header, records, trace.observations, None, MemoryMap(), games, partition_games(games, random.Random(i)),
                             False, targets))
    return result


def check(rank, rendezvous):
    torch.set_num_threads(1)
    torch.manual_seed(1357)
    torch.backends.mha.set_fastpath_enabled(False)
    dist.init_process_group("gloo", init_method=rendezvous, rank=rank, world_size=2)
    try:
        group = ReplayGroup(rank, 2, "cpu")
        initial = Policy(width=64, heads=4, inner=96, board_width=32).eval()
        windows = examples(initial)
        reference = copy.deepcopy(initial)
        candidate = copy.deepcopy(initial)
        ref_opts = optimizers(reference)
        test_opts = optimizers(candidate)
        # Unequal shards and a completely empty shard both need global denominators.
        for split in (1, len(windows)):
            shard = windows[:split] if rank == 0 else windows[split:]
            expected = learning_pass(reference, windows, *ref_opts, check=True)
            actual = learning_pass(candidate, shard, *test_opts, check=True, group=group)
            for key in ("game_fragments", "trained_thoughts", "trained_turns", "trained_game_actions"):
                assert actual[key] == expected[key], key
            for key in ("actor", "critic", "actor_norm", "critic_norm"):
                assert abs(actual[key] - expected[key]) < 1e-5, key
            for (name, a), b in zip(reference.named_parameters(), candidate.parameters()):
                assert (a.grad is None) == (b.grad is None), name
                if a.grad is not None:
                    torch.testing.assert_close(a.grad, b.grad, atol=2e-6, rtol=2e-5, msg=name)
        args = SimpleNamespace(ppo_epochs=2, lr=1e-4, target_kl=.02)
        expected = optimize(reference, windows, *ref_opts, args, seed=711)
        shard = windows[:1] if rank == 0 else windows[1:]
        actual = optimize(candidate, shard, *test_opts, args, seed=711, group=group)
        for a, b in zip(expected, actual, strict=True):
            assert (a["accepted"], a["accepted_lr"]) == (b["accepted"], b["accepted_lr"])
            for key in ("game_kl", "thought_kl"):
                assert abs(a[key] - b[key]) < 2e-5
        digest = hashlib.sha256()
        for (name, a), b in zip(reference.named_parameters(), candidate.parameters()):
            # Both biases add a common scalar to all candidate logits. Their gradients
            # cancel in exact arithmetic; Adam can amplify FP32 residuals close to epsilon.
            shift = name in ("action_bias.bias", "board.candidate_norm.bias")
            torch.testing.assert_close(a, b, atol=2e-5 if shift else 7e-6, rtol=1e-5, msg=name)
            digest.update(b.detach().contiguous().numpy().tobytes())
        assert len(set(group.gather(digest.hexdigest()))) == 1
        with torch.no_grad():
            for window in windows:
                base, updated = replay(reference, window), replay(candidate, window)
                for a, b in zip(base, updated, strict=True):
                    torch.testing.assert_close(a.logp, b.logp, atol=2e-5, rtol=0)
                    torch.testing.assert_close(a.value, b.value, atol=2e-5, rtol=0)
        if not rank:
            print("Two-rank weights are identical; reference gradients, coverage, steps, logp and V agree.")
    finally:
        dist.destroy_process_group()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="zhanguo-verify-grad-") as root:
        mp.spawn(check, args=((Path(root) / "store").as_uri(),), nprocs=2, join=True)
