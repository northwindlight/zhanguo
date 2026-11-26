"""One-seat reference rollout and exact observation archive."""

from dataclasses import dataclass

import torch
from torch import Tensor

from torch.nn import functional as F

from memory_map import MAX_THOUGHTS, MIN_THOUGHTS, MemoryMap, Op, TurnThought
from model import EVENT_WIDTH, FEAT_DIM, Event, Policy


TRACE_FORMAT = "zhanguo.seat-trace/1"


@dataclass(frozen=True)
class Record:
    kind: Event
    revision: int
    operation: int | None = None
    graph: MemoryMap | None = None
    writes: int | None = None
    logp: float | None = None
    payload: Tensor | None = None
    value: float | None = None


@dataclass(frozen=True)
class GameRecord:
    """A sampled game action, logged before its ACTION history event."""

    revision: int
    index: int
    logp: float
    value: float


@dataclass(frozen=True)
class TurnBoundary:
    """The seat entered a new turn; thought state and decoder cache restart."""


@dataclass(frozen=True)
class TraceHeader:
    """Sampling contract that a replay must reproduce before scoring any record."""

    thought_budget: int | None
    retain_thought_kv: bool
    format: str = TRACE_FORMAT

    def __post_init__(self):
        if self.format != TRACE_FORMAT:
            raise ValueError(f"unsupported trace format {self.format!r}")
        if self.thought_budget not in (None, 0, MIN_THOUGHTS, MAX_THOUGHTS):
            raise ValueError("thought budget must be adaptive, 0, 8, or 30")
        if self.retain_thought_kv and self.thought_budget == 0:
            raise ValueError("thought K/V requires at least one thought")

    def as_dict(self):
        return {"format": self.format, "thought_budget": self.thought_budget,
                "retain_thought_kv": self.retain_thought_kv}

    @classmethod
    def from_dict(cls, value: dict):
        return cls(value["thought_budget"], value["retain_thought_kv"], value["format"])


def thought_mask(thought: TurnThought, budget: int | None) -> tuple[bool, ...]:
    """Fixed budgets never offer STOP; the engine closes the phase at the limit."""
    legal = thought.mask()
    return legal if budget is None else legal[:-1] + (False,)


def advance_thought(thought: TurnThought, operation: int, budget: int | None):
    """Apply one sampled operation. Returns the new state and whether the engine forced the switch."""
    if budget is not None and operation == Op.STOP:
        raise ValueError("fixed thought budgets end automatically")
    if not thought_mask(thought, budget)[operation]:
        raise ValueError("illegal thought action for this phase")
    updated = thought.choose(operation)
    limit = MAX_THOUGHTS if budget is None else budget
    forced = operation != Op.STOP and updated.writes == limit
    if forced:
        updated = TurnThought(updated.graph, updated.writes, closed=True)
    return updated, forced


def opening_thought(graph: MemoryMap, budget: int | None):
    return TurnThought(graph, closed=budget == 0)


def action_event(obs: dict[str, Tensor], index: int) -> Tensor:
    """The BC observation/action contract: the chosen candidate's features as the ACTION payload."""
    return F.pad(obs["cand"][0, index], (0, EVENT_WIDTH - FEAT_DIM))


def is_event(record) -> bool:
    return isinstance(record, Record)


@dataclass(frozen=True)
class _ThoughtPending:
    logits: Tensor
    cache: tuple | None
    value: Tensor | None


@dataclass(frozen=True)
class _GamePending:
    logits: Tensor
    value: Tensor | None
    state: Tensor


@dataclass(frozen=True)
class SnapshotDiff:
    parent: int | None
    fields: dict[str, tuple[Tensor | None, Tensor]]


class ObservationArchive:
    """Exact CPU snapshots with bounded replay and row-level deltas."""

    def __init__(self, interval: int = 32):
        if interval < 1:
            raise ValueError("checkpoint interval must be positive")
        self.interval = interval
        self.snapshots: dict[int, SnapshotDiff] = {}
        self.latest_revision = None
        self.latest: dict[str, Tensor] = {}

    def append(self, revision: int, obs: dict[str, Tensor]):
        if self.latest_revision is not None and revision <= self.latest_revision:
            raise ValueError("observations must have increasing revisions")
        current = {k: v.detach().to(device="cpu", copy=True).contiguous() for k, v in obs.items()}
        if len(self.snapshots) % self.interval == 0 or current.keys() != self.latest.keys():
            patch = SnapshotDiff(None, {k: (None, value) for k, value in current.items()})
        else:
            fields = {}
            for k, value in current.items():
                old = self.latest[k]
                if value.shape != old.shape or value.dtype != old.dtype:
                    fields[k] = (None, value)
                elif value.numel() > 0:
                    rows = value.shape[1]
                    a = value.view(torch.uint8).reshape(rows, -1)
                    b = old.view(torch.uint8).reshape(rows, -1)
                    idx = (a != b).any(dim=-1).nonzero().flatten()
                    if idx.numel() == rows:
                        fields[k] = (None, value)
                    elif idx.numel():
                        fields[k] = (idx, value.index_select(1, idx))
            patch = SnapshotDiff(self.latest_revision, fields)
        self.snapshots[revision] = patch
        self.latest, self.latest_revision = current, revision

    def __contains__(self, revision: int):
        return revision in self.snapshots

    def __len__(self):
        return len(self.snapshots)

    def __getitem__(self, revision: int):
        chain = []
        while revision is not None:
            patch = self.snapshots[revision]
            chain.append(patch)
            revision = patch.parent
        result = {}
        for patch in reversed(chain):
            for k, (idx, value) in patch.fields.items():
                if idx is None:
                    result[k] = value.clone()
                else:
                    result[k].index_copy_(1, idx, value)
        return result


class SeatTrace:
    """Own this instance per game, seat and immutable sampling checkpoint.

    Observation revisions and visibility are supplied by the engine adapter.
    The archive can be serialized independently of the model's inference state.
    `records` interleaves history events (`Record`), sampled game actions
    (`GameRecord`) and turn boundaries, in the order the seat experienced them.
    """

    def __init__(self, policy: Policy, *, thought_budget: int | None = None, retain_thought_kv: bool = False):
        self.header = TraceHeader(thought_budget, retain_thought_kv)
        self.policy = policy
        self.history = None
        self.board = None
        self.obs = None
        self.thought = opening_thought(MemoryMap(), thought_budget)
        self.previous_operation = Op.START
        self.decoder_cache = None
        self.pending = None
        self.recording = True
        self.records: list[Record | GameRecord | TurnBoundary] = []
        self.observations = ObservationArchive()
        self._numbers = torch.arange(MAX_THOUGHTS + 1, device=self.device).view(-1, 1, 1)
        self._graph_input = None
        self._thought_masks = {}

    @classmethod
    def from_header(cls, policy: Policy, header: TraceHeader):
        return cls(policy, thought_budget=header.thought_budget, retain_thought_kv=header.retain_thought_kv)

    @property
    def thought_budget(self):
        return self.header.thought_budget

    @property
    def retain_thought_kv(self):
        return self.header.retain_thought_kv

    @property
    def device(self):
        return self.policy.event_proj.weight.device

    def _number(self, value: int):
        return self._numbers[value]

    def _map(self, graph: MemoryMap):
        if self._graph_input is None or graph != self._graph_input[0]:
            self._graph_input = graph, torch.tensor([[graph.padded()]], device=self.device, dtype=torch.long)
        return self._graph_input[1]

    @torch.inference_mode()
    def observe(self, revision: int, obs: dict[str, Tensor]):
        if self.pending is not None:
            raise ValueError("commit the pending action against its original observation")
        if self.board is not None and self.board.revision == revision:
            return
        if obs["globals"].shape[0] != 1:
            raise ValueError("a trace owns exactly one seat")
        current = {k: v.to(device=self.device, copy=True, non_blocking=True) for k, v in obs.items()}
        board = self.policy.snapshot(current, revision, self.board)
        _, history, _ = self.policy.observation_event(board, self.history)
        self.candidate_mask = obs["cand_mask"].detach().to(device="cpu", copy=True)
        self.board, self.obs, self.history = board, current, history
        if self.recording:
            self.observations.append(revision, obs)
            self.records.append(Record(Event.OBSERVATION, revision))

    @torch.inference_mode()
    def add_event(self, kind: Event, visible_payload: Tensor):
        if kind in (Event.OBSERVATION, Event.THOUGHT, Event.THOUGHT_LIMIT, Event.ACTION):
            raise ValueError("use observe, the thought transition or commit_game to record this event")
        self._append_event(kind, visible_payload)

    def _append_event(self, kind: Event, visible_payload: Tensor, operation: int | None = None,
                      archive_payload: Tensor | None = None):
        if self.board is None or self.pending is not None:
            raise ValueError("events require a current observation and no undecided action")
        if visible_payload.shape != (EVENT_WIDTH,):
            raise ValueError("expected one event payload")
        _, self.history, _ = self.policy.events(self._number(kind), visible_payload.to(self.device)[None, None],
                                                self.history)
        if self.recording:
            payload = archive_payload if archive_payload is not None else visible_payload.detach().to("cpu", copy=True)
            self.records.append(Record(kind, self.board.revision, operation, payload=payload))

    @staticmethod
    def _draw(logits, value, rng):
        logits = logits.float()
        index = torch.multinomial(logits.softmax(dim=-1), 1, generator=rng)
        if value is None:
            return int(index.item()), None
        logp = logits.log_softmax(dim=-1).gather(0, index)
        action, lp, v = torch.cat((index.to(logp.dtype), logp, value.reshape(1))).tolist()
        return int(action), (lp, v)

    @staticmethod
    def _scores(logits, value, index):
        return tuple(torch.stack((logits.float().log_softmax(dim=-1)[index], value.reshape(()))).tolist())

    @torch.inference_mode()
    def score_thought(self):
        if self.board is None or self.thought.closed or self.pending is not None:
            raise ValueError("a current snapshot and an open thought step are required")
        hidden, next_cache, _ = self.policy.decode(
            self._map(self.thought.graph), self._number(self.previous_operation),
            self._number(self.thought.writes), self.board, self.history, self.decoder_cache)
        mask = thought_mask(self.thought, self.thought_budget)
        if mask not in self._thought_masks:
            self._thought_masks[mask] = torch.tensor(mask, dtype=torch.bool, device=self.device)
        logits = self.policy.thought_logits(hidden[0, 0], self._thought_masks[mask])
        value = self.policy.estimate_value(hidden[0, 0]) if self.recording else None
        self.pending = _ThoughtPending(logits, next_cache, value)
        return logits

    @torch.inference_mode()
    def sample_thought(self, rng):
        logits = self.score_thought()
        operation, scores = self._draw(logits, self.pending.value, rng)
        return self.commit_thought(operation, scores=scores)

    @torch.inference_mode()
    def commit_thought(self, operation: int, *, scores=None):
        if not isinstance(self.pending, _ThoughtPending):
            raise ValueError("score the thought before committing it")
        updated, forced = advance_thought(self.thought, operation, self.thought_budget)
        pending = self.pending
        revision = self.board.revision
        _, self.history, _ = self.policy.thought_event(
            Event.THOUGHT, self._map(updated.graph), self._number(operation),
            self._number(updated.writes), self.board, self.history)
        if self.recording:
            logp, value = scores if scores is not None else self._scores(pending.logits, pending.value, operation)
            self.records.append(Record(Event.THOUGHT, revision, operation, updated.graph, updated.writes, logp,
                                       value=value))
        if forced:
            # The engine's switch is an event, not a policy choice, and carries no probability.
            _, self.history, _ = self.policy.thought_event(
                Event.THOUGHT_LIMIT, self._map(updated.graph), self._number(Op.GAME),
                self._number(updated.writes), self.board, self.history)
            if self.recording:
                self.records.append(Record(Event.THOUGHT_LIMIT, revision, graph=updated.graph, writes=updated.writes))

        self.thought = updated
        self.previous_operation = operation
        self.decoder_cache = None if updated.closed and not self.retain_thought_kv else pending.cache
        self.pending = None
        return forced

    @torch.inference_mode()
    def game_embedding(self):
        if self.board is None or not self.thought.closed:
            raise ValueError("finish thought before scoring game actions")
        hidden, _, _ = self.policy.decode(
            self._map(self.thought.graph), self._number(Op.GAME),
            self._number(0), self.board, self.history, self.decoder_cache)
        return hidden[:, 0]

    @torch.inference_mode()
    def score_game(self):
        if self.pending is not None:
            raise ValueError("commit the pending decision first")
        state = self.game_embedding()
        logits = self.policy.action_logits(state, self.obs, self.board)
        value = self.policy.estimate_value(state) if self.recording else None
        self.pending = _GamePending(logits, value, state)
        return logits, value

    @torch.inference_mode()
    def sample_game(self, rng):
        logits, value = self.score_game()
        index, scores = self._draw(logits[0], value, rng)
        self.commit_game(index, scores=scores)
        return index

    @torch.inference_mode()
    def commit_game(self, index: int, *, scores=None):
        """Log the sampled candidate with its sampling log-probability and pre-action value."""
        if not isinstance(self.pending, _GamePending):
            raise ValueError("score the game action before committing it")
        if not 0 <= index < self.candidate_mask.shape[1] or not bool(self.candidate_mask[0, index]):
            raise ValueError("game action is not an offered candidate")
        pending = self.pending
        archive_payload = None
        if self.recording:
            logp, value = scores if scores is not None else self._scores(pending.logits[0], pending.value, index)
            self.records.append(GameRecord(self.board.revision, index, logp, value))
            if self.observations.latest_revision == self.board.revision:
                archive_payload = action_event(self.observations.latest, index)
        self.pending = None
        self._append_event(Event.ACTION, action_event(self.obs, index), index, archive_payload)

    def next_turn(self):
        if self.pending is not None:
            raise ValueError("commit the pending decision before the turn ends")
        self.thought = opening_thought(self.thought.next_turn().graph, self.thought_budget)
        self.decoder_cache = None
        self.previous_operation = Op.START
        self.records.append(TurnBoundary())
