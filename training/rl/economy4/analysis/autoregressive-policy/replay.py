"""Replay one seat trace from its records: a stepwise reference and the batched PPO recomputation.

Both paths rebuild every sampled decision from the trace header, the event records and
the exact observation archive. The batched path keeps autograd so PPO can call it directly:

* history events are encoded as one causal sequence;
* thought steps of a turn decode along time, splitting only where the snapshot revision changes;
* the game tokens of a turn sit on the batch axis. Each reads the shared thought K/V (when the
  header retains it), its own token and the board K/V of its own revision, so every game token
  has RoPE position equal to the thought cache length and game actions never read each other.
"""

from dataclasses import dataclass

import torch
from torch import Tensor
from torch.nn import functional as F

from history import DeltaState, SparseCache
from memory_map import MemoryMap, Op
from model import KV, Event, History, Policy, Snapshot
from rollout_trace import (GameRecord, ObservationArchive, Record, SeatTrace, TraceHeader, TurnBoundary,
                           action_event, advance_thought, opening_thought, thought_mask)


@dataclass(frozen=True)
class Decision:
    """One sampled decision. `logp` and `value` are recomputed; `sampled_*` are what the trace logged."""

    kind: str
    record: int
    logp: Tensor
    value: Tensor
    sampled_logp: float
    sampled_value: float


def require_adaptive(header: TraceHeader, *, retain_thought_kv: bool):
    """Adaptive PPO accepts only adaptive-budget traces sampled with the same K/V contract."""
    if header.thought_budget is not None:
        raise ValueError(f"fixed thought budget {header.thought_budget} is an ablation trace, not PPO data")
    if header.retain_thought_kv != retain_thought_kv:
        raise ValueError("trace thought K/V setting differs from the training contract")


def max_errors(decisions: list[Decision]):
    logp = max((abs(float(d.logp.detach()) - d.sampled_logp) for d in decisions), default=0.0)
    value = max((abs(float(d.value.detach()) - d.sampled_value) for d in decisions), default=0.0)
    return logp, value


@torch.inference_mode()
def replay_stepwise(policy: Policy, header: TraceHeader, records, observations: ObservationArchive):
    """Re-run the sampling-time code path record by record."""
    trace = SeatTrace.from_header(policy, header)
    decisions = []
    action_pending = None
    for n, record in enumerate(records):
        if action_pending is not None:
            if not (isinstance(record, Record) and record.kind is Event.ACTION and record.operation == action_pending):
                raise ValueError(f"record {n}: game action must be followed by its ACTION event")
            replayed = trace.records[-1]
            if not torch.equal(replayed.payload, record.payload):
                raise ValueError(f"record {n}: ACTION payload differs from the archived candidate")
            action_pending = None
            continue
        if isinstance(record, TurnBoundary):
            trace.next_turn()
        elif isinstance(record, GameRecord):
            if trace.board is None or trace.board.revision != record.revision:
                raise ValueError(f"record {n}: game action revision is not the current snapshot")
            logits, value = trace.score_game()
            logp = logits[0].float().log_softmax(dim=-1)[record.index]
            decisions.append(Decision("game", n, logp, value[0], record.logp, record.value))
            trace.commit_game(record.index)
            action_pending = record.index
        elif record.kind is Event.OBSERVATION:
            trace.observe(record.revision, observations[record.revision])
        elif record.kind is Event.THOUGHT:
            if trace.thought.closed:
                raise ValueError(f"record {n}: thought recorded after the phase closed under this header")
            logits = trace.score_thought()
            value = trace.pending.value
            decisions.append(Decision("thought", n, logits.log_softmax(dim=-1)[record.operation],
                                      value, record.logp, record.value))
            trace.commit_thought(record.operation)
            if trace.thought.graph != record.graph or trace.thought.writes != record.writes:
                raise ValueError(f"record {n}: thought state diverged")
        elif record.kind is Event.THOUGHT_LIMIT:
            if not (trace.thought.closed and trace.records[-1] == record):
                raise ValueError(f"record {n}: forced switch does not match this header")
        elif record.kind is Event.ACTION:
            raise ValueError(f"record {n}: ACTION event without a game record")
        else:
            trace.add_event(record.kind, record.payload)
    if action_pending is not None:
        raise ValueError("trace ends between a game action and its ACTION event")
    return decisions, trace


@dataclass
class _Thought:
    record: int
    cutoff: int
    revision: int
    graph: MemoryMap
    previous: int
    writes: int
    operation: int
    legal: tuple[bool, ...]
    sampled: Record


@dataclass
class _Game:
    record: int
    cutoff: int
    revision: int
    graph: MemoryMap
    sampled: GameRecord


@dataclass
class _Turn:
    thoughts: list
    games: list


def _plan(header: TraceHeader, records, graph=MemoryMap()):
    """Walk the records with the same state machine as sampling, without touching the network."""
    events, turns = [], [_Turn([], [])]
    thought = opening_thought(graph, header.thought_budget)
    previous = Op.START
    revision = None
    expect_limit = expect_action = None
    for n, record in enumerate(records):
        if expect_limit is not None:
            if not (isinstance(record, Record) and record.kind is Event.THOUGHT_LIMIT
                    and record.graph == expect_limit.graph and record.writes == expect_limit.writes):
                raise ValueError(f"record {n}: the header requires a forced switch here")
            events.append(record)
            expect_limit = None
            continue
        if expect_action is not None:
            if not (isinstance(record, Record) and record.kind is Event.ACTION and record.operation == expect_action):
                raise ValueError(f"record {n}: game action must be followed by its ACTION event")
            events.append(record)
            expect_action = None
            continue
        if isinstance(record, TurnBoundary):
            thought = opening_thought(thought.next_turn().graph, header.thought_budget)
            previous = Op.START
            turns.append(_Turn([], []))
        elif isinstance(record, GameRecord):
            if not thought.closed or record.revision != revision:
                raise ValueError(f"record {n}: game action outside a closed phase or current snapshot")
            turns[-1].games.append(_Game(n, len(events) - 1, revision, thought.graph, record))
            expect_action = record.index
        elif record.kind is Event.OBSERVATION:
            if revision is not None and record.revision <= revision:
                raise ValueError(f"record {n}: snapshot revision did not increase")
            revision = record.revision
            events.append(record)
        elif record.kind is Event.THOUGHT:
            if thought.closed or record.revision != revision:
                raise ValueError(f"record {n}: thought outside an open phase or current snapshot")
            legal = thought_mask(thought, header.thought_budget)
            step = _Thought(n, len(events) - 1, revision, thought.graph, previous, thought.writes,
                            record.operation, legal, record)
            thought, forced = advance_thought(thought, record.operation, header.thought_budget)
            if thought.graph != record.graph or thought.writes != record.writes:
                raise ValueError(f"record {n}: thought state diverged")
            turns[-1].thoughts.append(step)
            previous = record.operation
            events.append(record)
            if forced:
                expect_limit = record
        elif record.kind in (Event.THOUGHT_LIMIT, Event.ACTION):
            raise ValueError(f"record {n}: {record.kind.name} without the decision that produces it")
        else:
            events.append(record)
    if expect_limit is not None or expect_action is not None:
        raise ValueError("trace ends inside a decision")
    if events and events[0].kind is not Event.OBSERVATION:
        raise ValueError("a trace starts with an observation")
    return events, turns


def _numbers(values, device):
    return torch.tensor(values, device=device, dtype=torch.long)[None]


def _graphs(graphs, device):
    return torch.tensor([[g.padded() for g in graphs]], device=device, dtype=torch.long)


def _event_inputs(policy: Policy, events, summaries, observations, device):
    width = policy.event_proj.weight.shape[0]
    x = torch.zeros(1, len(events), width, device=device)
    kind = policy.event_kind.weight
    for n, record in enumerate(events):
        if record.kind is Event.OBSERVATION:
            x[0, n] = summaries[record.revision][0, 0] + kind[Event.OBSERVATION]
        elif record.kind in (Event.THOUGHT, Event.THOUGHT_LIMIT):
            operation = record.operation if record.kind is Event.THOUGHT else Op.GAME
            vector = (policy.encode_graph(_graphs([record.graph], device))[0, 0]
                      + policy.operation.weight[operation] + policy.thought_position.weight[record.writes])
            x[0, n] = vector + summaries[record.revision][0, 0] + kind[record.kind]
        else:
            # Records are written under inference mode; a clone makes them usable in autograd.
            payload = record.payload.to(device).clone()
            if record.kind is Event.ACTION and not torch.equal(
                    payload, action_event(observations[record.revision], record.operation).to(device)):
                raise ValueError("ACTION payload differs from the archived candidate")
            x[0, n] = policy.event_proj(payload) + kind[record.kind]
    return x


def _pad_boards(policy: Policy, boards: list[Snapshot]) -> Snapshot:
    """Stack per-revision decoder board K/V on the batch axis; padded keys are masked out."""
    length = max(board.valid.shape[1] for board in boards)
    valid = torch.zeros(len(boards), length, dtype=torch.bool, device=boards[0].valid.device)
    for i, board in enumerate(boards):
        valid[i, :board.valid.shape[1]] = board.valid[0]
    projected = []
    for layer in range(len(policy.decoder)):
        keys, values = [], []
        for board in boards:
            kv = board.projected[layer]
            pad = (0, 0, 0, length - kv.key.shape[2])
            keys.append(F.pad(kv.key, pad))
            values.append(F.pad(kv.value, pad))
        projected.append(KV(torch.cat(keys), torch.cat(values)))
    return Snapshot(-1, None, valid, (), None, tuple(projected))


def _expand(cache: tuple[KV, ...] | None, batch: int):
    if cache is None:
        return None
    return tuple(KV(kv.key.expand(batch, -1, -1, -1), kv.value.expand(batch, -1, -1, -1)) for kv in cache)


def replay_batched(policy: Policy, header: TraceHeader, records, observations: ObservationArchive,
                   *, sparse: bool = True) -> list[Decision]:
    """Teacher-forced recomputation of every sampled decision, differentiable for PPO."""
    events, turns = _plan(header, records)
    return _replay(policy, header, events, turns, observations, None, sparse, full_board_gradient=True)


def move_history(history, device):
    """Detach the recurrent prefix at a truncated training boundary."""
    if history is None:
        return None

    def copy(tensor):
        return tensor.detach().to(device=device, copy=True)

    sparse = history.sparse
    return History(tuple(DeltaState(copy(x.matrix), copy(x.conv_tail)) for x in history.delta),
                   SparseCache(copy(sparse.key), copy(sparse.value), copy(sparse.block_key), copy(sparse.index_tail)),
                   copy(history.latest))


def replay_turn(policy, header, records, observations, prefix, graph, game_records):
    """Train one turn, conditioning on the detached history at its opening.

    Replay all sampled thoughts and one consecutive game-action fragment. Earlier
    observations still enter history, but retain board gradients only at scored views.
    """
    events, turn = _turn_plan(header, records, graph, game_records)
    return _replay(policy, header, events, [turn], observations, prefix, True, full_board_gradient=False)


def _turn_plan(header, records, graph, game_records):
    require_adaptive(header, retain_thought_kv=False)
    events, turns = _plan(header, records, graph)
    if len(turns) != 1:
        raise ValueError("Expected a single turn fragment")
    turn = turns[0]
    turn.games = [g for g in turn.games if g.record in game_records]
    if len(turn.games) != len(game_records) or not turn.games:
        raise ValueError("Incomplete game action fragment")
    return events, turn


def replay_turn_stepwise(policy, header, records, observations, prefix, graph, game_records):
    """Rebuild a training window with sampler-shaped tensors and autograd enabled."""
    events, turn = _turn_plan(header, records, graph, game_records)
    scored = {s.cutoff: s for s in (*turn.thoughts, *turn.games)}
    gradient_views = {s.revision for s in scored.values()}
    device = policy.event_proj.weight.device
    history = move_history(prefix, device)
    cache = None
    decisions = []
    for index, record in enumerate(events):
        if record.kind is Event.OBSERVATION:
            obs = {k: v.to(device=device, copy=True) for k, v in observations[record.revision].items()}
            with torch.set_grad_enabled(torch.is_grad_enabled() and record.revision in gradient_views):
                board = policy.snapshot(obs, record.revision)
            _, history, _ = policy.observation_event(board, history)
        elif record.kind in (Event.THOUGHT, Event.THOUGHT_LIMIT):
            operation = record.operation if record.kind is Event.THOUGHT else Op.GAME
            _, history, _ = policy.thought_event(record.kind, _graphs([record.graph], device),
                                                  _numbers([operation], device),
                                                  _numbers([record.writes], device), board, history)
        else:
            payload = record.payload.to(device=device, copy=True)
            if record.kind is Event.ACTION and not torch.equal(payload, action_event(obs, record.operation)):
                raise ValueError("ACTION payload differs from the archived candidate")
            _, history, _ = policy.events(_numbers([record.kind], device), payload[None, None], history)

        step = scored.get(index)
        if step is None:
            continue
        if isinstance(step, _Thought):
            hidden, cache, _ = policy.decode(_graphs([step.graph], device), _numbers([step.previous], device),
                                             _numbers([step.writes], device), board, history, cache)
            mask = torch.tensor(step.legal, dtype=torch.bool, device=device)
            logits = policy.thought_logits(hidden[0, 0], mask)
            logp = logits.log_softmax(dim=-1)[step.operation]
            value = policy.estimate_value(hidden[0, 0])
            kind = "thought"
        else:
            hidden, _, _ = policy.decode(_graphs([step.graph], device), _numbers([Op.GAME], device),
                                         _numbers([0], device), board, history)
            logits = policy.action_logits(hidden[:, 0], obs, board)
            logp = logits[0].float().log_softmax(dim=-1)[step.sampled.index]
            value = policy.estimate_value(hidden[:, 0])[0]
            kind = "game"
        decisions.append(Decision(kind, step.record, logp, value, step.sampled.logp, step.sampled.value))
        if len(decisions) == len(scored):
            break
    return decisions


def _replay(policy, header, events, turns, observations, prefix, sparse, *, full_board_gradient):
    device = policy.event_proj.weight.device
    scored = {step.revision for turn in turns for step in (*turn.thoughts, *turn.games)}
    obs, boards, summaries = {}, {}, {}
    for record in events:
        if record.kind is not Event.OBSERVATION:
            continue
        rev = record.revision
        if full_board_gradient or rev in scored:
            obs[rev] = {k: v.to(device=device, copy=True) for k, v in observations[rev].items()}
            boards[rev] = policy.snapshot(obs[rev], rev)
            summaries[rev] = boards[rev].global_token
        else:
            obs[rev] = observations[rev]
            with torch.no_grad():
                view = {k: v.to(device) for k, v in obs[rev].items()}
                encoded, _, _ = policy.board.encode(view)
                summaries[rev] = policy.board_proj(encoded)[:, :1].clone()
    prefix = move_history(prefix, device)
    offset = 0 if prefix is None else prefix.sparse.key.shape[1]
    timeline, history, _ = policy._encode_events(_event_inputs(policy, events, summaries, obs, device), prefix,
                                                 sparse=sparse)
    decisions = []
    for turn in turns:
        cache = None
        if turn.thoughts:
            steps = turn.thoughts
            cutoff = torch.tensor([s.cutoff for s in steps], device=device)
            x = (policy.encode_graph(_graphs([s.graph for s in steps], device))
                 + policy.operation(_numbers([s.previous for s in steps], device))
                 + policy.thought_position(_numbers([s.writes for s in steps], device)))
            x = x + torch.cat([boards[s.revision].global_token for s in steps], dim=1)
            x, _ = policy.sparse.read(x + timeline[:, cutoff], history.sparse, cutoff=cutoff + offset, sparse=sparse)
            hidden = []
            start = 0
            while start < len(steps):
                # A refreshed snapshot mid-phase changes the board K/V for the remaining steps only.
                end = start
                while end < len(steps) and steps[end].revision == steps[start].revision:
                    end += 1
                h, cache = policy.decode_tokens(x[:, start:end], boards[steps[start].revision], cache)
                hidden.append(h)
                start = end
            hidden = torch.cat(hidden, dim=1)[0]
            legal = torch.tensor([s.legal for s in steps], device=device)
            logp = policy.thought_logits(hidden, legal).log_softmax(dim=-1)
            values = policy.estimate_value(hidden)
            for i, s in enumerate(steps):
                decisions.append(Decision("thought", s.record, logp[i, s.operation], values[i],
                                          s.sampled.logp, s.sampled.value))
        if not turn.games:
            continue
        games = turn.games
        cutoff = torch.tensor([g.cutoff for g in games], device=device)
        x = (policy.encode_graph(_graphs([g.graph for g in games], device))
             + policy.operation.weight[Op.GAME] + policy.thought_position.weight[0])
        x = x + torch.cat([boards[g.revision].global_token for g in games], dim=1)
        x, _ = policy.sparse.read(x + timeline[:, cutoff], history.sparse, cutoff=cutoff + offset, sparse=sparse)
        previous = _expand(cache, len(games)) if header.retain_thought_kv else None
        hidden, _ = policy.decode_tokens(x[0, :, None], _pad_boards(policy, [boards[g.revision] for g in games]),
                                         previous)
        values = policy.estimate_value(hidden[:, 0])
        for i, g in enumerate(games):
            logits = policy.action_logits(hidden[i:i + 1, 0], obs[g.revision], boards[g.revision])[0]
            logp = logits.float().log_softmax(dim=-1)[g.sampled.index]
            decisions.append(Decision("game", g.record, logp, values[i], g.sampled.logp, g.sampled.value))
    decisions.sort(key=lambda d: d.record)
    return decisions
