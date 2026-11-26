"""Exercise the full-coverage sampler and update on the native four-country game."""
import argparse
import random
from pathlib import Path
from types import SimpleNamespace

import torch

import thought_ppo as ppo


def verify(library, checkpoint):
    torch.set_num_threads(2)
    torch.backends.mha.set_fastpath_enabled(False)
    policy = ppo.Policy().eval()
    policy.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True)["model"])
    for size in range(1, 61):
        actions = tuple(range(size))
        groups = ppo.partition_games(actions, random.Random(size))
        assert tuple(i for group in groups for i in group) == actions
        assert all(min(size, 2) <= len(group) <= 4 for group in groups)

    windows, stats = ppo.rollout(policy, library, 640000, 24, 3)
    counts = ppo.coverage(windows)
    assert counts == dict(trained_turns=stats["seat_turns"], trained_game_actions=stats["game_decisions"],
                          trained_thoughts=stats["thoughts"])
    assert stats["seat_turns"] == 12
    for window in windows:
        assert tuple(i for group in window.groups for i in group) == window.game_records
    for game_phase in (False, True):
        window = next(w for w in windows if w.game_phase == game_phase and w.history is not None)
        for method in (ppo.replay_turn, ppo.replay_turn_stepwise):
            with torch.no_grad():
                decisions = method(policy, window.header, window.records, window.observations,
                                   window.history, window.graph, window.game_records, game_phase=game_phase)
            assert {d.record for d in decisions} == window.targets.keys()
            assert max(ppo.max_errors(decisions)) <= 2e-5
    print("Verified coverage and both replay paths", counts, flush=True)
    actor = torch.optim.AdamW([w for name, w in policy.named_parameters() if not name.startswith("value.")],
                              lr=1e-4, weight_decay=0)
    critic = torch.optim.AdamW(policy.value.parameters(), lr=1e-4, weight_decay=0)
    rows = ppo.optimize(policy, windows, actor, critic, SimpleNamespace(ppo_epochs=1, lr=1e-4, target_kl=.02), 123)
    assert rows[0]["accepted"] and all(rows[0][k] == v for k, v in counts.items())
    print("Verified full-batch update", rows[0], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    options = parser.parse_args()
    verify(options.library, options.checkpoint)
