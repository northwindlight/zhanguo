"""FP32 world-batched board and decoder operations, including single-row reproduction."""

from dataclasses import dataclass

import torch
from torch.nn import functional as F

from model import GAME_OBSERVATION_FIELDS, KV, Snapshot


FIELDS = tuple(name for name, _ in GAME_OBSERVATION_FIELDS) + ("cand",)


@dataclass(frozen=True)
class BatchRow:
    count: int
    index: int


@dataclass(frozen=True)
class BoardLayout:
    row: BatchRow
    lengths: tuple[int, ...]


def pack_observations(observations, device, lengths=None):
    """Pad within each entity type so that candidate pointers stay relative to that type."""
    if lengths is None:
        lengths = tuple(max(obs[key].size(1) for obs in observations) for key in FIELDS)
    sizes = dict(zip(FIELDS, lengths, strict=True))
    packed = {}
    for name in observations[0]:
        field = name.split("_")[0]
        rows = []
        for obs in observations:
            value = obs[name]
            extra = sizes[field] - value.size(1)
            if extra < 0:
                raise ValueError(f"Invalid archived length for {name}")
            if extra:
                pad = (0, extra) if value.ndim == 2 else (0, 0, 0, extra)
                value = F.pad(value, pad, value=-1 if name == "cand_ptr" else 0)
            rows.append(value)
        packed[name] = torch.cat(rows, dim=0).to(device=device, non_blocking=True)
    return packed, lengths


def packed_snapshot(policy, obs, revision):
    board = policy.snapshot(obs, revision)
    # The full group and its row-replicated replay use the same decoder attention layout.
    projected = tuple(KV(kv.key.contiguous(), kv.value.contiguous()) for kv in board.projected)
    return Snapshot(revision, board.encoded, board.valid, board.entity_slices, board.global_token, projected)


def board_row(board, index, revision):
    row = slice(index, index + 1)
    return Snapshot(revision, board.encoded[row], board.valid[row], board.entity_slices,
                    board.global_token[row], cache_row(board.projected, index))


def cache_row(cache, index):
    return tuple(KV(kv.key[index:index+1], kv.value[index:index+1]) for kv in cache)


def join_cache(caches):
    if caches[0] is None:
        if any(c is not None for c in caches):
            raise ValueError("Cannot combine different decoder positions")
        return None
    return tuple(KV(torch.cat([kv.key for kv in layer], dim=0),
                    torch.cat([kv.value for kv in layer], dim=0))
                 for layer in zip(*caches, strict=True))


def decoder_boards(boards):
    """Rows originate in the same padded board batch; only decoder K/V is read."""
    return Snapshot(-1, None, torch.cat([b.valid for b in boards]), (), None,
                    join_cache([b.projected for b in boards]))


def repeat_row(tensor, repetitions):
    return tensor.expand(repetitions, *tensor.shape[1:]).contiguous()


def repeat_board(board, count):
    return Snapshot(board.revision, repeat_row(board.encoded, count), repeat_row(board.valid, count),
                    board.entity_slices, repeat_row(board.global_token, count),
                    join_cache([board.projected] * count))


def reproduce_snapshot(policy, raw, revision, layout):
    device = policy.event_proj.weight.device
    obs, _ = pack_observations([raw] * layout.row.count, device, layout.lengths)
    board = packed_snapshot(policy, obs, revision)
    i = layout.row.index
    return {k: v[i:i+1] for k, v in obs.items()}, board_row(board, i, revision)


def reproduce_decode(policy, query, board, previous, row):
    if row is None:
        return policy.decode_tokens(query, board, previous)
    hidden, cache = policy.decode_tokens(repeat_row(query, row.count), decoder_boards([board] * row.count),
                                         join_cache([previous] * row.count))
    return hidden[row.index:row.index+1], cache_row(cache, row.index)


def reproduce_action_logits(policy, state, obs, board, layout):
    if layout is None:
        return policy.action_logits(state, obs, board)
    row = layout.row
    repeated = {k: repeat_row(v, row.count) for k, v in obs.items()}
    logits = policy.action_logits(repeat_row(state, row.count), repeated, repeat_board(board, row.count))
    return logits[row.index:row.index+1]
