"""Exercise the full-coverage sampler and update on the native four-country game."""
import argparse
import math
import random
from pathlib import Path
from types import SimpleNamespace

import torch

import thought_ppo as ppo


def verify_land_reward():
    from land_reward import FADE_GAMES, LandRewardSchedule, WINDOW

    # Even a high lifetime (or shaped) GDP cannot trigger the terminal nominal GDP rule.
    schedule = LandRewardSchedule()
    for completed in range(1, WINDOW + 1):
        schedule = schedule.after_batch(completed, [dict(final_nominal_gdp=59., nominal_gdp=100.)])
    assert schedule.triggered_at is None and schedule.weight(WINDOW + 4) == 1.
    batch = [dict(final_nominal_gdp=63.)] * 4
    trigger = WINDOW + 4
    schedule = schedule.after_batch(trigger, batch)
    assert schedule.triggered_at == trigger and schedule.rolling_gdp == 60.
    assert schedule.weight(trigger + 4) == .98
    dipped = schedule.after_batch(trigger + 4, [dict(final_nominal_gdp=1.)] * 4)
    checkpoint = dict(games=trigger + 4, contract=ppo.CONTRACT, lr=1e-4, land_reward=dipped.state_dict())
    resumed = LandRewardSchedule.from_checkpoint(checkpoint, None, ppo.CONTRACT, 1e-4)
    assert resumed == dipped and resumed.triggered_at == trigger
    assert [resumed.weight(trigger + n) for n in (100, FADE_GAMES, FADE_GAMES + 4)] == [.5, 0., 0.]

    seats = [dict(nominal_gdp=12., land=n, initial_land=5) for n in (7, 6, 5, 4)]
    events = [("game", (seat, 0)) for seat in range(4)] + [("settlement", seats)]
    for weight in (1., .5, 0.):
        returns = ppo.returns_from_events(events, weight)
        for values, bonus in zip(returns, (15, 10, 0, 0)):
            assert math.isclose(values[0] * ppo.SCALE, 12 + weight * bonus)


def verify(library, checkpoint, no_thought=False):
    verify_land_reward()
    torch.set_num_threads(2)
    torch.backends.mha.set_fastpath_enabled(False)
    policy = ppo.Policy().eval()
    policy.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True)["model"])
    for size in range(1, 61):
        actions = tuple(range(size))
        groups = ppo.partition_games(actions, random.Random(size))
        assert tuple(i for group in groups for i in group) == actions
        assert all(min(size, 2) <= len(group) <= 4 for group in groups)

    if no_thought:
        from batched_rollout import rollout_worlds

        results = rollout_worlds(policy, library, (640000, 640005), 24, 3, no_thought=True)
        windows, stats = results[0]
        for sampled, metrics in results:
            assert all(w.header.thought_budget == 0 for w in sampled)
            assert metrics["thoughts"] == metrics["active_stops"] == metrics["forced_stops"] == 0
            assert metrics["batched_games"] > 0
    else:
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
    if no_thought:
        assert rows[0]["thought_kl"] == 0. and rows[0]["game_kl"] > 0.
    print("Verified full-batch update", rows[0], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--no-thought", action="store_true")
    options = parser.parse_args()
    verify(options.library, options.checkpoint, options.no_thought)
