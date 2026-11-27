"""PPO replay alignment on synthetic observations: no demo data or engine required.

Each scenario samples a short two-turn trace with the full-size policy, then requires the
stepwise replay and the batched PPO recomputation to reproduce every logged log-probability
and pre-decision value within 2e-5. Game observations change the army count between
decisions, so the batched path also covers padded board K/V of different lengths.
"""

import argparse
import json
import time

import numpy as np
import torch

from rl.candidates import FEAT_DIM
from rl.obs import A_DIM, G_DIM, M_DIM, N_DIM, T_DIM, collate

from memory_map import MAX_THOUGHTS, MIN_THOUGHTS, Op
from model import EVENT_WIDTH, Event, Policy
from replay import max_errors, replay_batched, replay_stepwise, require_adaptive
from rollout_trace import GameRecord, Record, SeatTrace, TraceHeader, TurnBoundary

TOLERANCE = 2e-5
GAMES_PER_TURN = 3


def observation(rng: np.random.Generator, armies: int):
    tiles, nations, candidates = 24, 4, 7 + armies
    pointer = -np.ones((candidates, 3), np.int32)
    pointer[:, 0] = rng.integers(0, tiles, candidates)
    pointer[::2, 1] = rng.integers(0, armies, (candidates + 1) // 2)
    pointer[::3, 2] = rng.integers(0, nations, (candidates + 2) // 3)
    raw = {
        "globals": rng.normal(size=G_DIM).astype(np.float32),
        "market": rng.normal(size=(6, M_DIM)).astype(np.float32),
        "nations": rng.normal(size=(nations, N_DIM)).astype(np.float32),
        "tiles": rng.normal(size=(tiles, T_DIM)).astype(np.float32),
        "armies": rng.normal(size=(armies, A_DIM)).astype(np.float32),
        "cand": rng.normal(size=(candidates, FEAT_DIM)).astype(np.float32),
        "cand_ptr": pointer,
        "cand_exec": np.ones(candidates, np.float32),
        "cand_type": rng.integers(0, 8, candidates).astype(np.int32),
    }
    return {k: torch.from_numpy(v) for k, v in collate([raw]).items()}


def sample(logits: torch.Tensor, generator: torch.Generator, *, forbid_stop=False):
    logits = logits.float().clone()
    if forbid_stop and logits.shape[-1] == Op.STOP + 1:
        logits[Op.STOP] = -torch.inf
    return int(torch.multinomial(logits.softmax(dim=-1), 1, generator=generator))


def play(policy, header: TraceHeader, *, seed: int, stop_after=None, refresh_at=None, forbid_stop=False):
    """Two turns of thought + game decisions. `stop_after` forces an adaptive STOP after that many writes."""
    rng = np.random.default_rng(seed)
    generator = torch.Generator().manual_seed(seed)
    trace = SeatTrace.from_header(policy, header)
    revision = 0
    for turn in range(2):
        if turn:
            trace.next_turn()
        revision += 1
        trace.observe(revision, observation(rng, armies=3))
        trace.add_event(Event.TURN_OPEN, torch.from_numpy(rng.normal(size=EVENT_WIDTH).astype(np.float32)))
        while not trace.thought.closed:
            if refresh_at is not None and trace.thought.writes == refresh_at and trace.board.revision == revision:
                # A refreshed snapshot mid-phase: later steps read the new board, earlier ones keep theirs.
                revision += 1
                trace.observe(revision, observation(rng, armies=4))
            logits = trace.score_thought()
            if stop_after is not None and trace.thought.writes == stop_after:
                operation = Op.STOP
            else:
                operation = sample(logits, generator, forbid_stop=forbid_stop or stop_after is not None)
            trace.commit_thought(operation)
        for game in range(GAMES_PER_TURN):
            if game:
                revision += 1
                trace.observe(revision, observation(rng, armies=3 + game))
            logits, _ = trace.score_game()
            trace.commit_game(sample(logits[0], generator))
    return trace


def turn_shapes(records):
    """Per turn: (thought records, forced switches, STOP choices, game records)."""
    shapes, current = [], [0, 0, 0, 0]
    for record in records:
        if isinstance(record, TurnBoundary):
            shapes.append(tuple(current))
            current = [0, 0, 0, 0]
        elif isinstance(record, GameRecord):
            current[3] += 1
        elif record.kind is Event.THOUGHT:
            current[0] += 1
            current[2] += record.operation == Op.STOP
        elif record.kind is Event.THOUGHT_LIMIT:
            current[1] += 1
    shapes.append(tuple(current))
    return shapes


SCENARIOS = {
    # name: (header, play options, expected per-turn shape)
    "0": (TraceHeader(0, False), {}, (0, 0, 0, GAMES_PER_TURN)),
    "8": (TraceHeader(MIN_THOUGHTS, False), {}, (MIN_THOUGHTS, 1, 0, GAMES_PER_TURN)),
    "30": (TraceHeader(MAX_THOUGHTS, False), {}, (MAX_THOUGHTS, 1, 0, GAMES_PER_TURN)),
    "8-kv": (TraceHeader(MIN_THOUGHTS, True), {"refresh_at": 4}, (MIN_THOUGHTS, 1, 0, GAMES_PER_TURN)),
    "adaptive-stop": (TraceHeader(None, False), {"stop_after": 11, "refresh_at": 5}, (12, 0, 1, GAMES_PER_TURN)),
    "adaptive-cap": (TraceHeader(None, False), {"forbid_stop": True}, (MAX_THOUGHTS, 1, 0, GAMES_PER_TURN)),
    "adaptive-stop-kv": (TraceHeader(None, True), {"stop_after": 9}, (10, 0, 1, GAMES_PER_TURN)),
}


def check_scenario(policy, name, seed):
    header, options, shape = SCENARIOS[name]
    begin = time.perf_counter()
    trace = play(policy, header, seed=seed, **options)
    shapes = turn_shapes(trace.records)
    assert shapes == [shape, shape], (name, shapes)
    for record in trace.records:
        if isinstance(record, GameRecord) or isinstance(record, Record) and record.kind is Event.THOUGHT:
            assert record.logp is not None and record.value is not None, (name, record)
        if isinstance(record, Record) and record.kind is Event.THOUGHT_LIMIT:
            assert record.logp is None, name
    stepwise, replayed = replay_stepwise(policy, header, trace.records, trace.observations)
    torch.testing.assert_close(replayed.history.sparse.key, trace.history.sparse.key, atol=TOLERANCE, rtol=TOLERANCE)
    with torch.no_grad():
        batched = replay_batched(policy, header, trace.records, trace.observations)
    assert [d.record for d in batched] == [d.record for d in stepwise], name
    step_error, batch_error = max(max_errors(stepwise)), max(max_errors(batched))
    assert step_error < TOLERANCE and batch_error < TOLERANCE, (name, step_error, batch_error)
    return trace, dict(decisions=len(batched), games=sum(d.kind == "game" for d in batched),
                       stepwise_max_error=step_error, batched_max_error=batch_error,
                       seconds=round(time.perf_counter() - begin, 2))


def wrong_header_rejected(policy, trace, header: TraceHeader):
    """Replaying under a different sampling contract must fail loudly or visibly disagree."""
    for replay in (replay_stepwise, replay_batched):
        try:
            result = replay(policy, header, trace.records, trace.observations)
        except ValueError:
            continue
        decisions = result[0] if isinstance(result, tuple) else result
        assert max(max_errors(decisions)) > 1e-3, (replay.__name__, header)


def gradient_check(policy, trace):
    decisions = replay_batched(policy, trace.header, trace.records, trace.observations)
    policy.zero_grad(set_to_none=True)
    loss = -torch.stack([d.logp for d in decisions]).sum() + torch.stack([d.value for d in decisions]).square().sum()
    loss.backward()
    groups = {"board": policy.board, "delta": policy.delta, "sparse": policy.sparse, "decoder": policy.decoder,
              "thought": policy.thought, "value": policy.value}
    norms = {}
    for name, module in groups.items():
        grads = [p.grad for p in module.parameters() if p.grad is not None]
        assert grads and all(torch.isfinite(g).all() for g in grads), name
        norms[name] = float(torch.stack([g.norm() for g in grads]).norm())
        assert norms[name] > 0, name
    policy.zero_grad(set_to_none=True)
    return norms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20261125)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    policy = Policy().eval()
    results, traces = {}, {}
    for n, name in enumerate(SCENARIOS):
        traces[name], results[name] = check_scenario(policy, name, args.seed + n)

    # The trace header gates adaptive PPO and replay.
    require_adaptive(traces["adaptive-stop"].header, retain_thought_kv=False)
    for name in ("0", "8", "30", "8-kv"):
        try:
            require_adaptive(traces[name].header, retain_thought_kv=traces[name].header.retain_thought_kv)
        except ValueError:
            continue
        raise AssertionError(f"adaptive PPO accepted the {name} ablation trace")
    try:
        require_adaptive(traces["adaptive-stop"].header, retain_thought_kv=True)
        raise AssertionError("adaptive PPO accepted a trace with a different K/V setting")
    except ValueError:
        pass
    assert TraceHeader.from_dict(traces["8-kv"].header.as_dict()) == traces["8-kv"].header
    wrong_header_rejected(policy, traces["8"], TraceHeader(None, False))
    wrong_header_rejected(policy, traces["30"], TraceHeader(None, False))
    wrong_header_rejected(policy, traces["8-kv"], TraceHeader(MIN_THOUGHTS, False))
    wrong_header_rejected(policy, traces["adaptive-stop"], TraceHeader(MAX_THOUGHTS, False))

    report = {
        "torch": torch.__version__,
        "threads": args.threads,
        "parameters": sum(p.numel() for p in policy.parameters()),
        "tolerance": TOLERANCE,
        "scenarios": results,
        "gradient_norms": gradient_check(policy.train(), traces["adaptive-stop-kv"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
