"""Focused design checks, with a full-size native observation smoke run."""

import argparse
import json
import pickle
import time
from pathlib import Path
from zipfile import ZipFile

import torch
from torch.nn import functional as F

from rl.obs import collate

from memory_map import Draft, MemoryMap, Op, TurnThought
from model import EVENT_WIDTH, Event, Policy
from replay import max_errors, replay_batched, replay_stepwise
from rollout_trace import GameRecord, ObservationArchive, SeatTrace, is_event


def close(a, b, atol=2e-5):
    torch.testing.assert_close(a, b, atol=atol, rtol=2e-5)
    return float((a - b).abs().max())


def rejects(call, *args):
    try:
        call(*args)
    except ValueError:
        return
    raise AssertionError("expected rejection")


def example_episode():
    root = Path(__file__).resolve().parents[2]
    with ZipFile(root / "script-bc-results/chunks/chunk-0000.zip") as archive:
        for name in archive.namelist():
            if name.endswith(".pkl"):
                episode = pickle.loads(archive.read(name))
                if episode["accepted"] and episode["teacher"]:
                    return episode
    raise AssertionError("no usable BC observation")


def map_protocol():
    state = TurnThought()
    rejects(state.choose, Op.STOP)
    for _ in range(4):
        state = state.choose(0)
    assert not state.mask()[0] and state.graph.padded()[0] == (1, 1, 1, 1)
    rejects(state.choose, 0)
    for _ in range(4):
        state = state.choose(4)
    assert state.mask()[Op.STOP]
    stopped = state.choose(Op.STOP)
    assert stopped.closed and stopped.writes == 8
    assert stopped.next_turn().graph == state.graph
    for _ in range(22):
        state = state.choose(Op.CLEAR_FIRST)
    assert state.closed and state.writes == 30 and not any(state.mask())
    draft = Draft(2).write(4).write(9)
    letter = draft.send(0, 12)
    assert (letter.sender, letter.recipient, letter.turn) == (0, 2, 12)
    assert letter.graph.slots == ((), (1,), (2,), ())
    assert draft.write(Op.CLEAR_FIRST + 1).graph != letter.graph
    assert MemoryMap().slots == ((), (), (), ())
    return dict(earliest_stop=stopped.writes, forced_stop=state.writes, letter_slots=4)


def collated(obs):
    return {k: torch.from_numpy(v) for k, v in collate([obs]).items()}


def tensor_bytes(tensors):
    return sum(t.numel() * t.element_size() for t in tensors)


def patch_bytes(patch):
    return sum(tensor_bytes([value] if idx is None else [idx, value])
               for idx, value in patch.fields.values())


def archive_check(obs, episode):
    archive = ObservationArchive(interval=3)
    for revision in range(8):
        current = dict(obs, globals=obs["globals"].clone(), tiles=obs["tiles"].clone())
        current["globals"][0, 0] += revision
        current["tiles"][0, 2, 0] += revision
        if revision == 4:
            current["armies"] = current["armies"][:, :0]
            current["armies_mask"] = current["armies_mask"][:, :0]
        archive.append(revision, current)
        recovered = archive[revision]
        for field in current:
            assert torch.equal(current[field], recovered[field]), field
    assert all(torch.equal(v, archive[0][k]) for k, v in obs.items())
    for revision in (3, 6):
        assert archive.snapshots[revision].parent is None
    base = tensor_bytes(obs.values())
    delta = patch_bytes(archive.snapshots[1])
    assert delta < base / 100
    actual = ObservationArchive()
    raw_bytes = 0
    for i, (obs, _, _, _) in enumerate(episode["teacher"]):
        current = collated(obs)
        raw_bytes += tensor_bytes(current.values())
        actual.append(i, current)
        recovered = actual[i]
        assert all(torch.equal(v, recovered[k]) for k, v in current.items())
    actual_bytes = sum(patch_bytes(patch) for patch in actual.snapshots.values())
    return dict(full_snapshot_tensor_bytes=base, changed_fields_tensor_bytes=delta,
                episode_snapshots=len(actual), episode_raw_tensor_bytes=raw_bytes,
                episode_delta_tensor_bytes=actual_bytes)


@torch.no_grad()
def history_checks(model):
    kind = torch.arange(87).remainder(5)[None].repeat(2, 1)
    x = torch.randn(2, 87, EVENT_WIDTH)
    full, whole, _ = model.events(kind, x)
    past = None
    parts = []
    for start, end in ((0, 1), (1, 9), (9, 18), (18, 51), (51, 87)):
        out, past, _ = model.events(kind[:, start:end], x[:, start:end], past)
        parts.append(out)
    history_error = close(full, torch.cat(parts, dim=1))
    recurrence_error = max(close(a.matrix, b.matrix) for a, b in zip(whole.delta, past.delta))
    assert len(whole.delta) == 3 and whole.sparse.index_tail.shape[1] == 3
    close(whole.sparse.block_key, past.sparse.block_key)
    altered = x.clone()
    altered[:, 21:] = torch.randn_like(altered[:, 21:]) * 20
    causal_error = close(model.events(kind, altered)[0][:, :21], full[:, :21])
    short_x, short_k = x[:, :29], kind[:, :29]
    dense = model.events(short_k, short_x, sparse=False)[0]
    sparse = model.events(short_k, short_x)[0]
    all_block_error = close(dense, sparse)
    query = torch.randn(2, 3, model.event_proj.out_features)
    query_positions = torch.full((3,), 87)
    _, scores, complete = model.sparse._scores(model.sparse.norm(query), query_positions, whole.sparse,
                                               query_positions - 1)
    selected = model.sparse._select(scores, complete)
    assert selected.shape[-1] == 16 < whole.sparse.block_key.shape[1]
    assert complete.expand_as(scores).gather(-1, selected).all()
    return dict(chunk_error=history_error, recurrence_error=recurrence_error,
                future_suffix_error=causal_error, dense_equivalence_error=all_block_error,
                history_blocks=whole.sparse.block_key.shape[1], selected_blocks=selected.shape[-1])


@torch.no_grad()
def decoder_checks(model, obs):
    board = model.snapshot(obs, 1)
    assert model.snapshot(obs, 1, board) is board
    rejects(model.snapshot, obs, 0, board)
    x = torch.randn(1, 11, EVENT_WIDTH)
    past = model.events(torch.arange(11)[None].remainder(5), x)[1]
    graphs = torch.randint(0, 5, (1, 9, 4, 4))
    previous = torch.randint(0, int(Op.START) + 1, (1, 9))
    writes = torch.arange(9)[None]
    full = model.decode(graphs, previous, writes, board, past)[0]
    cache, parts = None, []
    for i in range(9):
        step, cache, _ = model.decode(graphs[:, i:i+1], previous[:, i:i+1], writes[:, i:i+1], board, past, cache)
        parts.append(step)
    decoder_error = close(full, torch.cat(parts, dim=1))
    shuffled_suffix = graphs.clone()
    shuffled_suffix[:, 5:] = 1
    result = model.decode(shuffled_suffix, previous, writes, board, past)[0]
    causal_error = close(full[:, :5], result[:, :5])
    assert not torch.allclose(full[:, -1], result[:, -1])
    return dict(chunk_error=decoder_error, future_thought_error=causal_error)


def backward_check(model, obs, action):
    board = model.snapshot(obs, 1)
    kind = torch.arange(68)[None].remainder(5)
    event = torch.randn(1, 68, EVENT_WIDTH)
    _, past, warmup = model.events(kind, event, sparse=False, distill=True)
    index_grad, = torch.autograd.grad(warmup, model.sparse.index_query.weight, retain_graph=False)
    assert torch.isfinite(index_grad).all() and index_grad.norm() > 0
    del past
    _, past, _ = model.events(kind, event, sparse=True)
    hidden, _, index_kl = model.decode(torch.zeros(1, 1, 4, 4, dtype=torch.long),
                                       torch.tensor([[Op.GAME]]), torch.tensor([[8]]),
                                       board, past, sparse=True, distill=True)
    ce = F.cross_entropy(model.action_logits(hidden[:, 0], obs, board), torch.tensor([action]))
    value = model.estimate_value(hidden[:, 0]).square().mean()
    (ce + index_kl + value).backward()
    gradients = {}
    for name, param in (("board", model.board.proj["globals"].weight), ("delta", model.delta[0].qkv.weight),
                        ("indexer", model.sparse.index_query.weight), ("decoder", model.decoder[0].cross_query.weight),
                        ("value", model.value[0].weight)):
        assert torch.isfinite(param.grad).all() and param.grad.norm() > 0, name
        gradients[name] = float(param.grad.norm())
    return dict(dense_kl=float(warmup.detach()), sparse_kl=float(index_kl.detach()), gradients=gradients)


@torch.inference_mode()
def trace_checks(model, obs, action):
    seat = SeatTrace(model)
    begin = time.perf_counter()
    seat.observe(1, obs)
    board_seconds = time.perf_counter() - begin
    original = seat.board
    seat.observe(1, obs)
    assert seat.board is original
    assert len(seat.records) == 1 and len(seat.observations) == 1
    seat.add_event(Event.TURN_OPEN, torch.zeros(EVENT_WIDTH))
    begin = time.perf_counter()
    for i in range(8):
        tokens_before = seat.history.sparse.key.shape[1]
        logits = seat.score_thought()
        assert seat.history.sparse.key.shape[1] == tokens_before
        assert torch.isneginf(logits[Op.STOP])
        op = i % 4 * 4
        seat.commit_thought(op)
        assert seat.history.sparse.key.shape[1] == tokens_before + 1
    thought_seconds = time.perf_counter() - begin

    # Refresh the observation mid-phase, keeping references to the original version.
    changed = dict(obs, globals=obs["globals"].clone())
    changed["globals"][0, 0] += 1
    seat.observe(2, changed)
    assert seat.board is not original
    close(seat.observations[1]["globals"], obs["globals"])
    assert not torch.equal(seat.observations[1]["globals"], seat.observations[2]["globals"])
    assert torch.isfinite(seat.score_thought()[Op.STOP])
    seat.commit_thought(Op.STOP)
    assert seat.records[-1].revision == 2
    assert seat.records[-1].graph == seat.thought.graph and seat.records[-1].logp is not None
    assert seat.decoder_cache is None

    begin = time.perf_counter()
    action_logits, value = seat.score_game()
    game_seconds = time.perf_counter() - begin
    assert action_logits.shape == obs["cand_mask"].shape
    assert torch.isfinite(action_logits[0, action]) and torch.isfinite(value).all()
    seat.commit_game(action)
    assert isinstance(seat.records[-2], GameRecord) and seat.records[-2].index == action
    assert seat.records[-1].kind is Event.ACTION
    graph = seat.thought.graph
    seat.next_turn()
    seat.add_event(Event.TURN_OPEN, torch.ones(EVENT_WIDTH))
    assert seat.thought.graph == graph
    for _ in range(30):
        seat.score_thought()
        seat.commit_thought(Op.CLEAR_FIRST)
    assert seat.records[-1].kind is Event.THOUGHT_LIMIT and seat.records[-1].logp is None
    assert seat.thought.closed
    events = [r for r in seat.records if is_event(r)]
    assert all(r.revision in seat.observations for r in events)
    assert sum(r.kind is Event.THOUGHT for r in events) == 39
    assert seat.history.sparse.key.shape[1] == len(events)
    rejects(seat.score_thought)
    decisions, replay = replay_stepwise(model, seat.header, seat.records, seat.observations)
    replay_error = max(max_errors(decisions))
    assert replay_error < 2e-5
    close(replay.history.sparse.key, seat.history.sparse.key)
    batched_error = max(max_errors(replay_batched(model, seat.header, seat.records, seat.observations)))
    assert batched_error < 2e-5
    return dict(record_count=len(seat.records), exact_snapshots=len(seat.observations),
                replay_logp_error=replay_error, batched_replay_error=batched_error,
                board_ms=round(board_seconds * 1000, 2), eight_thought_steps_ms=round(thought_seconds * 1000, 2),
                game_score_ms=round(game_seconds * 1000, 2))


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--threads", type=int, default=2)
    args = args.parse_args()
    torch.set_num_threads(args.threads)
    torch.manual_seed(20261125)
    episode = example_episode()
    raw_obs, action, turn, _ = episode["teacher"][0]
    obs = collated(raw_obs)
    model = Policy(width=96, heads=6, inner=192, board_width=64).eval()
    full = Policy().eval()
    result = {
        "torch": torch.__version__,
        "threads": args.threads,
        "parameters": sum(p.numel() for p in full.parameters()),
        "board_tokens": sum(obs[name].shape[1] for name in ("globals", "market", "nations", "tiles", "armies")),
        "observation_source": {"seed": episode["seed"], "turn": turn, "candidate_count": obs["cand"].shape[1]},
        "map": map_protocol(),
        "archive": archive_check(obs, episode),
        "history": history_checks(model),
        "decoder": decoder_checks(model, obs),
        "backward": backward_check(full, obs, action),
        "trace": trace_checks(full, obs, action),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
