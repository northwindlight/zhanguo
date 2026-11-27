"""No-GRU policy prototype, using the existing RL observation/candidate schema.

Inputs and inference caches are scoped to one policy snapshot and one seat.
Caller provides authorized visible events and versioned engine observations.
"""

from dataclasses import dataclass
from enum import IntEnum

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from rl.model import CandBlock
from rl.obs import G_DIM, M_DIM, N_DIM, T_DIM, A_DIM
from rl.candidates import FEAT_DIM

from history import DeltaBlock, DeltaState, SparseBlock, SparseCache, SwiGLU, rope
from memory_map import CAPACITY, EXTENDED_THOUGHTS, MAX_THOUGHTS, SLOTS, SYMBOLS, Op


GAME_OBSERVATION_FIELDS = (
    ("globals", G_DIM), ("market", M_DIM), ("nations", N_DIM), ("tiles", T_DIM), ("armies", A_DIM),
)
EVENT_WIDTH = 128


class Event(IntEnum):
    TURN_OPEN = 0
    RECEIPT = 1
    SETTLEMENT = 2
    VISIBLE_CHANGE = 3
    LETTER = 4
    OBSERVATION = 5
    THOUGHT = 6
    THOUGHT_LIMIT = 7
    ACTION = 8


@dataclass(frozen=True)
class History:
    delta: tuple[DeltaState, ...]
    sparse: SparseCache
    latest: Tensor


@dataclass(frozen=True)
class KV:
    key: Tensor
    value: Tensor


@dataclass(frozen=True)
class Snapshot:
    revision: int
    encoded: Tensor
    valid: Tensor
    entity_slices: tuple[slice, slice, slice]
    global_token: Tensor
    projected: tuple[KV, ...]


class Board(nn.Module):
    def __init__(self, width: int = 192):
        super().__init__()
        features = dict(GAME_OBSERVATION_FIELDS)
        features["cand"] = FEAT_DIM
        self.proj = nn.ModuleDict({name: nn.Linear(dim, width) for name, dim in features.items()})
        self.kind = nn.Embedding(len(features), width)
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(width, 8, 4 * width, dropout=0, batch_first=True, norm_first=True),
            num_layers=4, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)
        self.candidates = nn.ModuleList(CandBlock(width, 8, self_attn=True) for _ in range(2))
        self.candidate_norm = nn.LayerNorm(width)

    @staticmethod
    def layout(obs: dict[str, Tensor]):
        lengths = [obs[field].shape[1] for field, _ in GAME_OBSERVATION_FIELDS]
        valid = torch.cat([obs[field + "_mask"] for field, _ in GAME_OBSERVATION_FIELDS], dim=1)
        offset = lengths[0] + lengths[1]
        slices = []
        for length in lengths[2:]:
            end = offset + length
            slices.append(slice(offset, end))
            offset = end
        return valid, tuple(slices)

    def encode(self, obs: dict[str, Tensor]):
        groups = [self.proj[field](obs[field]) + self.kind.weight[i]
                  for i, (field, _) in enumerate(GAME_OBSERVATION_FIELDS)]
        valid, slices = self.layout(obs)
        encoded = self.norm(self.encoder(torch.cat(groups, dim=1), src_key_padding_mask=~valid))
        return encoded, valid, slices

    @staticmethod
    def _gather(tokens: Tensor, pointer: Tensor):
        if tokens.shape[1] == 0:
            return tokens.new_zeros((*pointer.shape, tokens.shape[-1]))
        index = pointer.clamp_min(0).long()[..., None].expand(-1, -1, tokens.shape[-1])
        return tokens.gather(1, index) * (pointer >= 0)[..., None]

    def candidate_features(self, obs: dict[str, Tensor], snapshot: Snapshot):
        c = self.proj["cand"](obs["cand"]) + self.kind.weight[-1]
        # Candidate pointer order is tile, army, nation.
        nation, tile, army = snapshot.entity_slices
        for i, entity in enumerate((tile, army, nation)):
            c = c + self._gather(snapshot.encoded[:, entity], obs["cand_ptr"][..., i])
        for layer in self.candidates:
            c = layer(c, snapshot.encoded, ~snapshot.valid, ~obs["cand_mask"])
        return self.candidate_norm(c)


class DecoderLayer(nn.Module):
    def __init__(self, width: int, heads: int, inner: int):
        super().__init__()
        self.heads, self.head_width = heads, width // heads
        self.self_norm = nn.RMSNorm(width)
        self.qkv = nn.Linear(width, 3 * width, bias=False)
        self.self_out = nn.Linear(width, width, bias=False)
        self.cross_norm = nn.RMSNorm(width)
        self.cross_query = nn.Linear(width, width, bias=False)
        self.cross_kv = nn.Linear(width, 2 * width, bias=False)
        self.cross_out = nn.Linear(width, width, bias=False)
        self.ff_norm = nn.RMSNorm(width)
        self.ff = SwiGLU(width, inner)

    def board_kv(self, board: Tensor) -> KV:
        shape = (*board.shape[:2], 2, self.heads, self.head_width)
        k, v = self.cross_kv(board).reshape(shape).unbind(2)
        return KV(k.transpose(1, 2), v.transpose(1, 2))

    def forward(self, x: Tensor, board: KV, board_valid: Tensor, previous: KV | None):
        batch, steps, width = x.shape
        offset = 0 if previous is None else previous.key.shape[2]
        positions = offset + torch.arange(steps, device=x.device)
        q, k, v = self.qkv(self.self_norm(x)).reshape(batch, steps, 3, self.heads, self.head_width).unbind(2)
        q = rope(q, positions, self.head_width).transpose(1, 2)
        k = rope(k, positions, self.head_width).transpose(1, 2)
        v = v.transpose(1, 2)
        if previous is not None:
            k = torch.cat((previous.key, k), dim=2)
            v = torch.cat((previous.value, v), dim=2)
        causal = torch.arange(k.shape[2], device=x.device)[None, :] <= positions[:, None]
        y = F.scaled_dot_product_attention(q, k, v, attn_mask=causal)
        x = x + self.self_out(y.transpose(1, 2).reshape(batch, steps, width))

        q = self.cross_query(self.cross_norm(x)).reshape(batch, steps, self.heads, self.head_width)
        y = F.scaled_dot_product_attention(q.transpose(1, 2), board.key, board.value,
                                           attn_mask=board_valid[:, None, None, :])
        x = x + self.cross_out(y.transpose(1, 2).reshape(batch, steps, width))
        return x + self.ff(self.ff_norm(x)), KV(k, v)


class Policy(nn.Module):
    def __init__(self, width: int = 384, heads: int = 6, inner: int = 1024,
                 board_width: int = 192, top_blocks: int = 16):
        super().__init__()
        if heads % 2 or width % (heads * 4) or board_width % 8:
            raise ValueError("head dimensions must support GQA, partial RoPE, and eight board heads")
        self.board = Board(board_width)
        self.board_proj = nn.Linear(board_width, width, bias=False)
        self.event_proj = nn.Linear(EVENT_WIDTH, width, bias=False)
        self.event_kind = nn.Embedding(len(Event), width)
        self.delta = nn.ModuleList(DeltaBlock(width, heads, inner) for _ in range(3))
        self.sparse = SparseBlock(width, heads, 2, inner, top_blocks=top_blocks)
        self.decoder = nn.ModuleList(DecoderLayer(width, heads, inner) for _ in range(3))
        self.symbols = nn.Embedding(SYMBOLS + 1, 32, padding_idx=0)
        self.map_projection = nn.Linear(SLOTS * CAPACITY * 32, width)
        self.operation = nn.Embedding(Op.GAME + 1, width)
        self.thought_position = nn.Embedding(MAX_THOUGHTS + 1, width)
        self.decoder_norm = nn.RMSNorm(width)
        self.thought = nn.Linear(width, Op.STOP + 1)
        self.action_query = nn.Linear(width, board_width, bias=False)
        self.action_bias = nn.Linear(board_width, 1)
        self.value = nn.Sequential(nn.Linear(width, 256), nn.SiLU(), nn.Linear(256, 1))
        self.candidate_scale = board_width ** -0.5

    def extend_thought_positions(self, optimizer=None):
        """Keep old positions and Adam state, and give steps 31..100 independent trainable rows."""
        table = self.thought_position
        if table.num_embeddings == EXTENDED_THOUGHTS + 1:
            return
        old = table.weight
        extra = EXTENDED_THOUGHTS + 1 - table.num_embeddings
        rows = torch.cat((old.detach(), old[-1:].detach().expand(extra, -1)))
        new = nn.Parameter(rows, requires_grad=old.requires_grad)
        if optimizer is not None:
            locations = [(group["params"], index) for group in optimizer.param_groups
                         for index, param in enumerate(group["params"]) if param is old]
            if len(locations) != 1:
                raise ValueError("Expected exactly one actor optimizer entry for thought positions")
            params, index = locations[0]
            params[index] = new
            if old in optimizer.state:
                state = optimizer.state.pop(old)
                for key, value in state.items():
                    if isinstance(value, Tensor) and value.shape == old.shape:
                        state[key] = torch.cat((value, value.new_zeros(extra, value.shape[1])))
                optimizer.state[new] = state
        table.weight = new
        table.num_embeddings = rows.shape[0]

    def load_state_dict(self, state_dict, strict=True, assign=False):
        positions = state_dict.get("thought_position.weight")
        if positions is not None and positions.size(0) == EXTENDED_THOUGHTS + 1:
            self.extend_thought_positions()
        return super().load_state_dict(state_dict, strict=strict, assign=assign)

    def snapshot(self, obs: dict[str, Tensor], revision: int, previous: Snapshot | None = None):
        if previous is not None:
            if revision == previous.revision:
                return previous
            if revision < previous.revision:
                raise ValueError("snapshot revision went backwards")
        encoded, valid, slices = self.board.encode(obs)
        return self.project_board(revision, encoded, valid, slices)

    def project_board(self, revision: int, encoded: Tensor, valid: Tensor, slices: tuple[slice, ...]):
        projected = self.board_proj(encoded)
        return Snapshot(revision, encoded, valid, slices, projected[:, :1].clone(),
                        tuple(layer.board_kv(projected) for layer in self.decoder))

    def events(self, kind: Tensor, payload: Tensor, previous: History | None = None,
               *, sparse: bool = True, distill: bool = False):
        return self._encode_events(self.event_proj(payload) + self.event_kind(kind), previous,
                                   sparse=sparse, distill=distill)

    def encode_graph(self, graph: Tensor):
        return self.map_projection(self.symbols(graph).flatten(-3))

    def observation_event(self, board: Snapshot, previous: History | None):
        return self._encode_events(board.global_token + self.event_kind.weight[Event.OBSERVATION], previous)

    def thought_event(self, kind: Event, graph: Tensor, operation: Tensor, count: Tensor,
                      board: Snapshot, previous: History | None):
        event = self.encode_graph(graph) + self.operation(operation) + self.thought_position(count)
        event = event + board.global_token + self.event_kind.weight[kind]
        return self._encode_events(event, previous)

    def game_query(self, global_token: Tensor, graph: Tensor):
        return (self.encode_graph(graph) + self.operation.weight[Op.GAME]
                + self.thought_position.weight[0] + global_token)

    def _encode_events(self, x: Tensor, previous: History | None, *, sparse: bool = True,
                       distill: bool = False):
        if x.shape[1] == 0:
            raise ValueError("append requires at least one visible event")
        states = []
        for i, layer in enumerate(self.delta):
            x, state = layer(x, None if previous is None else previous.delta[i])
            states.append(state)
        x, cache, loss = self.sparse(x, None if previous is None else previous.sparse,
                                     sparse=sparse, distill=distill)
        # A copy keeps the latest state from pinning a complete inference input chunk.
        return x, History(tuple(states), cache, x[:, -1:].clone()), loss

    def history_query(self, graph, previous_operation, write_count, board, history, *, sparse=True, distill=False):
        x = self.encode_graph(graph) + self.operation(previous_operation) + self.thought_position(write_count)
        query = x + board.global_token
        loss = None
        if history is not None:
            # Reading the history prefix never commits a proposed operation.
            x, loss = self.sparse.read(query + history.latest, history.sparse, sparse=sparse, distill=distill)
        else:
            x = query
        return x, loss

    def decode(self, graph: Tensor, previous_operation: Tensor, write_count: Tensor,
               board: Snapshot, history: History | None, previous: tuple[KV, ...] | None = None,
               *, sparse: bool = True, distill: bool = False):
        x, loss = self.history_query(graph, previous_operation, write_count, board, history,
                                     sparse=sparse, distill=distill)
        x, states = self.decode_tokens(x, board, previous)
        return x, states, loss

    def decode_tokens(self, x: Tensor, board: Snapshot, previous: tuple[KV, ...] | None = None):
        states = []
        for i, layer in enumerate(self.decoder):
            x, kv = layer(x, board.projected[i], board.valid, None if previous is None else previous[i])
            states.append(kv)
        return self.decoder_norm(x), tuple(states)

    def thought_logits(self, state: Tensor, legal: Tensor):
        return self.thought(state).masked_fill(~legal, -torch.inf)

    def action_logits(self, state: Tensor, obs: dict[str, Tensor], board: Snapshot):
        candidates = self.board.candidate_features(obs, board)
        query = self.action_query(state)
        logits = torch.einsum("bd,bkd->bk", query, candidates) * self.candidate_scale
        return (logits + self.action_bias(candidates).squeeze(-1)).masked_fill(~obs["cand_mask"], -torch.inf)

    def estimate_value(self, state: Tensor):
        return self.value(state.detach()).squeeze(-1)
