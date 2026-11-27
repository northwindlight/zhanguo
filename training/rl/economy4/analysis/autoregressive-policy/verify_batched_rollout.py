"""Compare world-batched behavior with serial sampling and both PPO replay paths."""
import argparse
from pathlib import Path

import torch

from batched_rollout import batch_decisions, rollout_worlds
from model import Policy
from replay import max_errors
from rollout_trace import GameRecord, SeatTrace
from thought_ppo import coverage, replay_turn, replay_turn_stepwise, rollout


def actions(windows):
    return [(r.revision, r.index if isinstance(r, GameRecord) else r.operation)
            for window in windows for r in window.records if getattr(r, "logp", None) is not None]


def verify(policy, library, seeds, size, turns, *, check_actions):
    for config in ({"retain_thought_kv": True}, {"thought_budget": 8}):
        trace = SeatTrace(policy, **config)
        try:
            batch_decisions(policy, [(trace, 0, None, None, None)] * 2)
        except ValueError:
            pass
        else:
            raise AssertionError(f"world-batched PPO accepted incompatible trace {config}")
    with torch.no_grad():
        results = rollout_worlds(policy, library, seeds, size, turns)
        for seed, (windows, row) in zip(seeds, results, strict=True):
            assert seed == row["seed"]
            assert row["batched_games"] > 0 and row["batched_thoughts"] > 0
            assert coverage(windows) == dict(trained_turns=row["seat_turns"], trained_game_actions=row["game_decisions"],
                                             trained_thoughts=row["thoughts"])
            if check_actions:
                reference, reference_row = rollout(policy, library, seed, size, turns)
                assert actions(reference) == actions(windows), f"Paired behavior diverged for seed {seed}"
                assert row["seats"] == reference_row["seats"]
            worst_fast = worst_reference = 0.
            retried = 0
            for w in windows:
                args = (policy, w.header, w.records, w.observations, w.history, w.graph, w.game_records)
                fast = replay_turn(*args, game_phase=w.game_phase)
                reference = replay_turn_stepwise(*args, game_phase=w.game_phase)
                assert {d.record for d in fast} == {d.record for d in reference} == w.targets.keys()
                fast_error, reference_error = max(max_errors(fast)), max(max_errors(reference))
                worst_fast, worst_reference = max(worst_fast, fast_error), max(worst_reference, reference_error)
                retried += fast_error > 2e-5
                assert reference_error <= 2e-5, (seed, reference_error)
            # The reference path also supplies the training gradient when the fast
            # sequence replay crosses a discrete routing boundary.
            sample = next(w for w in windows if not w.game_phase)
            policy.zero_grad(set_to_none=True)
            with torch.enable_grad():
                decisions = replay_turn_stepwise(policy, sample.header, sample.records, sample.observations,
                                                sample.history, sample.graph, sample.game_records)
                assert max(max_errors(decisions)) <= 2e-5
                torch.stack([d.logp + d.value for d in decisions]).mean().backward()
            for part in (policy.board, policy.delta, policy.decoder, policy.value):
                assert any(p.grad is not None and torch.isfinite(p.grad).all() and p.grad.norm() > 0
                           for p in part.parameters())
            print(dict(seed=seed, windows=len(windows), **coverage(windows), batched_games=row["batched_games"],
                       batched_thoughts=row["batched_thoughts"], fast_error=worst_fast,
                       reference_error=worst_reference, fallback_windows=retried), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--turns", type=int, default=2)
    parser.add_argument("--size", type=int, default=24)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--seeds", nargs="+", type=int, default=[640000, 640005])
    parser.add_argument("--check-actions", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.manual_seed(759)
    torch.backends.mha.set_fastpath_enabled(False)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    policy = Policy() if args.checkpoint else Policy(width=64, heads=4, inner=128, board_width=32)
    if args.checkpoint:
        policy.load_state_dict(torch.load(args.checkpoint, weights_only=True, map_location="cpu")["model"])
    verify(policy.to(args.device).eval(), args.library, args.seeds, args.size, args.turns, check_actions=args.check_actions)
