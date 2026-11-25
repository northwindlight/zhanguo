"""One-seat reference rollout and exact observation archive."""

from dataclasses import dataclass

import torch
from torch import Tensor

from memory_map import MAX_THOUGHTS, MemoryMap, Op, TurnThought
from model import EVENT_WIDTH, Event, Policy


@dataclass(frozen=True)
class Record:
    kind: Event
    revision: int
    operation: int | None = None
    graph: MemoryMap | None = None
    writes: int | None = None
    logp: float | None = None
    payload: Tensor | None = None


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
    """

    def __init__(self, policy: Policy):
        self.policy = policy
        self.history = None
        self.board = None
        self.obs = None
        self.thought = TurnThought()
        self.previous_operation = Op.START
        self.decoder_cache = None
        self.pending = None
        self.records: list[Record] = []
        self.observations = ObservationArchive()

    @property
    def device(self):
        return self.policy.event_proj.weight.device

    def _number(self, value: int):
        return torch.tensor([[value]], device=self.device, dtype=torch.long)

    def _map(self, graph: MemoryMap):
        return torch.tensor([[graph.padded()]], device=self.device, dtype=torch.long)

    @torch.inference_mode()
    def observe(self, revision: int, obs: dict[str, Tensor]):
        if self.pending is not None:
            raise ValueError("commit the pending action against its original observation")
        if self.board is not None and self.board.revision == revision:
            return
        if obs["globals"].shape[0] != 1:
            raise ValueError("a trace owns exactly one seat")
        current = {k: v.to(device=self.device, copy=True) for k, v in obs.items()}
        board = self.policy.snapshot(current, revision, self.board)
        _, history, _ = self.policy.observation_event(board, self.history)
        self.observations.append(revision, obs)
        self.board, self.obs, self.history = board, current, history
        self.records.append(Record(Event.OBSERVATION, revision))

    @torch.inference_mode()
    def add_event(self, kind: Event, visible_payload: Tensor):
        if kind in (Event.OBSERVATION, Event.THOUGHT, Event.THOUGHT_LIMIT):
            raise ValueError("use observe or the thought transition to record this event")
        if self.board is None or self.pending is not None:
            raise ValueError("events require a current observation and no undecided action")
        if visible_payload.shape != (EVENT_WIDTH,):
            raise ValueError("expected one event payload")
        _, self.history, _ = self.policy.events(self._number(kind), visible_payload.to(self.device)[None, None],
                                                self.history)
        self.records.append(Record(kind, self.board.revision,
                                   payload=visible_payload.detach().to("cpu", copy=True)))

    @torch.inference_mode()
    def score_thought(self):
        if self.board is None or self.thought.closed or self.pending is not None:
            raise ValueError("a current snapshot and an open thought step are required")
        hidden, next_cache, _ = self.policy.decode(
            self._map(self.thought.graph), self._number(self.previous_operation),
            self._number(self.thought.writes), self.board, self.history, self.decoder_cache)
        mask = torch.tensor(self.thought.mask(), dtype=torch.bool, device=self.device)
        logits = self.policy.thought_logits(hidden[0, 0], mask)
        self.pending = (logits, next_cache)
        return logits

    @torch.inference_mode()
    def commit_thought(self, operation: int):
        if self.pending is None:
            raise ValueError("score the thought before committing it")
        updated = self.thought.choose(operation)
        logits, cache = self.pending
        logp = float(logits.log_softmax(dim=-1)[operation])
        revision = self.board.revision
        _, self.history, _ = self.policy.thought_event(
            Event.THOUGHT, self._map(updated.graph), self._number(operation),
            self._number(updated.writes), self.board, self.history)
        self.records.append(Record(Event.THOUGHT, revision, operation, updated.graph, updated.writes, logp))
        if operation != Op.STOP and updated.writes == MAX_THOUGHTS:
            _, self.history, _ = self.policy.thought_event(
                Event.THOUGHT_LIMIT, self._map(updated.graph), self._number(Op.GAME),
                self._number(updated.writes), self.board, self.history)
            self.records.append(Record(Event.THOUGHT_LIMIT, revision, graph=updated.graph, writes=updated.writes))

        self.thought = updated
        self.previous_operation = operation
        self.decoder_cache = None if updated.closed else cache
        self.pending = None

    @torch.inference_mode()
    def score_game(self):
        if self.board is None or not self.thought.closed:
            raise ValueError("finish thought before scoring game actions")
        hidden, _, _ = self.policy.decode(
            self._map(self.thought.graph), self._number(Op.GAME),
            self._number(0), self.board, self.history)
        state = hidden[:, 0]
        return self.policy.action_logits(state, self.obs, self.board), self.policy.estimate_value(state)

    def next_turn(self):
        self.thought = self.thought.next_turn()
        self.previous_operation = Op.START
