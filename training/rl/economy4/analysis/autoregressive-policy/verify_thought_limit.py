"""Verify the GDP-triggered capacity change, Adam migration, and replay across step 30."""
import copy

import torch

from land_reward import LandRewardSchedule
from memory_map import EXTENDED_THOUGHTS, MAX_THOUGHTS
from model import Policy
from replay import max_errors, replay_batched, replay_stepwise, replay_turn, replay_turn_stepwise
from rollout_trace import GameRecord, TraceHeader, TurnBoundary
from verify_replay import GAMES_PER_TURN, play, turn_shapes, wrong_header_rejected
from memory_map import MemoryMap


def verify():
    torch.manual_seed(184)
    torch.set_num_threads(1)
    torch.backends.mha.set_fastpath_enabled(False)
    def new_policy():
        return Policy(width=64, heads=4, inner=96, board_width=32).eval()
    policy = new_policy()
    old_header = TraceHeader.from_dict(dict(format="zhanguo.seat-trace/1", thought_budget=None, retain_thought_kv=False))
    before = play(policy, old_header, seed=110, forbid_stop=True)
    assert all(t[:3] == (30, 1, 0) for t in turn_shapes(before.records))
    # Initialise both real Adam moments without changing the behavior weights.
    actor = torch.optim.AdamW(policy.parameters(), lr=0., weight_decay=0)
    table = policy.thought_position.weight
    table.square().sum().backward()
    actor.step()
    actor.zero_grad(set_to_none=True)
    moments = copy.deepcopy(actor.state[table])
    assert LandRewardSchedule().after_batch(16, [{"final_nominal_gdp": 59.9}]*16).triggered_at is None
    gate = LandRewardSchedule().after_batch(16, [{"final_nominal_gdp": 60.}]*16)
    assert gate.triggered_at == gate.after_batch(20, [{"final_nominal_gdp": 0.}]*4).triggered_at == 16
    policy.extend_thought_positions(actor)
    new = policy.thought_position.weight
    assert any(p is new for p in actor.param_groups[0]["params"]) and table not in actor.state
    assert torch.equal(table, new[:31]) and torch.equal(new[31:], table[-1:].expand(70,-1))
    for name in ("exp_avg", "exp_avg_sq"):
        assert torch.equal(moments[name], actor.state[new][name][:31]) and not actor.state[new][name][31:].any()
    assert torch.equal(actor.state[new]["step"], moments["step"])
    old_decisions, _ = replay_stepwise(policy, old_header, before.records, before.observations)
    assert max(max_errors(old_decisions)) == 0.
    header = TraceHeader(None, False, thought_limit=EXTENDED_THOUGHTS)
    assert TraceHeader.from_dict(header.as_dict()) == header
    for args, shape in ((dict(stop_after=74), (75,0,1,GAMES_PER_TURN)), (dict(forbid_stop=True), (100,1,0,GAMES_PER_TURN))):
        trace = play(policy, header, seed=111, **args)
        assert all(t == shape for t in turn_shapes(trace.records))
        with torch.no_grad():
            step, _ = replay_stepwise(policy, header, trace.records, trace.observations)
            batch = replay_batched(policy, header, trace.records, trace.observations)
            assert max(max_errors(step)) < 2e-5 and max(max_errors(batch)) < 2e-5
            wrong_header_rejected(policy, trace, old_header)
    end = next(i for i,r in enumerate(trace.records) if isinstance(r,TurnBoundary))
    records = trace.records[:end]
    games = tuple(i for i,r in enumerate(records) if isinstance(r,GameRecord))
    for method in (replay_turn, replay_turn_stepwise):
        decisions = method(policy,header,records,trace.observations,None,MemoryMap(),games)
        error = max(max_errors(decisions))
        assert error < 2e-5, error
        actor.zero_grad(set_to_none=True)
        torch.stack([d.logp + d.value for d in decisions]).mean().backward()
        assert new.grad[31:].isfinite().all() and new.grad[31:].norm()>0
    actor.param_groups[0]["lr"] = 1e-4
    actor.step()
    restored = new_policy()
    restored.load_state_dict(policy.state_dict())
    opt = torch.optim.AdamW(restored.parameters())
    opt.load_state_dict(actor.state_dict())
    assert torch.equal(restored.thought_position.weight, new)
    assert torch.equal(opt.state[restored.thought_position.weight]["exp_avg"], actor.state[new]["exp_avg"])
    print("Old 30-step behavior unchanged, STOP at 74 writes, force at 100, both PPO replays and Adam resume verified.")


if __name__ == "__main__":
    verify()
