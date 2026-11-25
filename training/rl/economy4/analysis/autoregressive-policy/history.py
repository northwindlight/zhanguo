"""Causal reference kernels for Gated DeltaNet and Qwen Sparse Attention."""

from dataclasses import dataclass
from math import log

import torch
from torch import Tensor, nn
from torch.nn import functional as F


class SwiGLU(nn.Module):
    def __init__(self, width: int, inner: int):
        super().__init__()
        self.up = nn.Linear(width, 2 * inner, bias=False)
        self.down = nn.Linear(inner, width, bias=False)

    def forward(self, x: Tensor) -> Tensor:
        gate, value = self.up(x).chunk(2, -1)
        return self.down(F.silu(gate) * value)


def rope(x: Tensor, position: Tensor, rotary: int) -> Tensor:
    """Input is [batch, time, heads, head_width], positions are absolute."""
    freq = torch.arange(0, rotary, 2, device=x.device, dtype=torch.float32) / rotary
    phase = position.float()[:, None] * 10000.0 ** (-freq)
    cos, sin = phase.cos()[None, :, None], phase.sin()[None, :, None]
    a, b = x[..., :rotary].float().chunk(2, -1)
    rotated = torch.cat((a * cos - b * sin, a * sin + b * cos), dim=-1)
    return torch.cat((rotated.to(x.dtype), x[..., rotary:]), dim=-1)


@dataclass(frozen=True)
class DeltaState:
    matrix: Tensor
    conv_tail: Tensor


class DeltaBlock(nn.Module):
    def __init__(self, width: int, heads: int, inner: int, kernel: int = 4):
        super().__init__()
        self.heads, self.head_width, self.kernel = heads, width // heads, kernel
        self.norm = nn.RMSNorm(width)
        self.qkv = nn.Linear(width, 3 * width, bias=False)
        self.conv = nn.Conv1d(3 * width, 3 * width, kernel, groups=3 * width, bias=False)
        self.rates = nn.Linear(width, 2 * heads, bias=False)
        self.log_rate = nn.Parameter(torch.zeros(heads))
        # Separate fast and slow heads at startup. Learned rates remain input dependent.
        half_life = 2.0 ** torch.linspace(3, 11, heads)
        self.decay_bias = nn.Parameter(torch.expm1(log(2) / half_life).log())
        self.out_gate = nn.Linear(width, width, bias=False)
        self.head_norm = nn.RMSNorm(self.head_width)
        self.out = nn.Linear(width, width, bias=False)
        self.ff_norm = nn.RMSNorm(width)
        self.ff = SwiGLU(width, inner)

    def forward(self, x: Tensor, state: DeltaState | None = None):
        n, steps, width = x.shape
        h, d = self.heads, self.head_width
        u = self.norm(x)
        projected = self.qkv(u).transpose(1, 2)
        if state is None:
            matrix = torch.zeros(n, h, d, d, device=x.device, dtype=torch.float32)
            conv_tail = projected.new_zeros(n, 3 * width, self.kernel - 1)
        else:
            matrix, conv_tail = state.matrix, state.conv_tail
        conv_input = torch.cat((conv_tail, projected), dim=-1)
        q, k, v = F.silu(self.conv(conv_input)).transpose(1, 2).reshape(n, steps, 3, h, d).unbind(2)
        q = F.normalize(q.float(), dim=-1)
        k = F.normalize(k.float(), dim=-1)
        write, decay = self.rates(u).float().chunk(2, dim=-1)
        beta = write.sigmoid()
        log_alpha = -self.log_rate.float().exp() * F.softplus(decay + self.decay_bias.float())
        if steps == 1:
            key = k[:, 0, :, :, None]
            matrix = log_alpha[:, 0, :, None, None].exp() * matrix
            residual = v[:, 0].float() - (matrix * key).sum(dim=-2)
            matrix = matrix + beta[:, 0, :, None, None] * key * residual.unsqueeze(-2)
            out = (matrix * q[:, 0, :, :, None]).sum(dim=-2).unsqueeze(1)
        else:
            with torch.autocast(device_type=x.device.type, enabled=False):
                out, matrix = self.scan(q, k, v.float(), log_alpha, beta, matrix)
        y = self.head_norm(out.to(u.dtype)).flatten(-2)
        x = x + self.out(y * self.out_gate(u).sigmoid())
        tail = conv_input[..., -(self.kernel - 1):].clone()
        return x + self.ff(self.ff_norm(x)), DeltaState(matrix, tail)

    @staticmethod
    def scan(q: Tensor, k: Tensor, v: Tensor, log_alpha: Tensor, beta: Tensor, matrix: Tensor):
        """Solve the same delta recurrence in chunks using a triangular system."""
        q, k, v = (x.transpose(1, 2) for x in (q, k, v))
        log_alpha, beta = log_alpha.transpose(1, 2), beta.transpose(1, 2)
        outputs = []
        for start in range(0, q.shape[2], 32):
            end = start + 32
            qc, kc, vc = (x[:, :, start:end] for x in (q, k, v))
            log_g = log_alpha[:, :, start:end].cumsum(dim=-1)
            strength = beta[:, :, start:end, None]
            g = log_g.exp().unsqueeze(-1)
            decays = (log_g[..., :, None] - log_g[..., None, :]).tril().exp().tril()
            lower = (strength * decays * (kc @ kc.transpose(-1, -2))).tril(-1)
            residual = strength * (vc - g * (kc @ matrix))
            updates = torch.linalg.solve_triangular(lower, residual, upper=False, unitriangular=True)
            outputs.append(g * (qc @ matrix) + (decays * (qc @ kc.transpose(-1, -2))) @ updates)
            matrix = g[..., -1:, :] * matrix + kc.transpose(-1, -2) @ (decays[..., -1, :, None] * updates)
        return torch.cat(outputs, dim=2).transpose(1, 2), matrix


@dataclass(frozen=True)
class SparseCache:
    key: Tensor
    value: Tensor
    block_key: Tensor
    index_tail: Tensor


class SparseBlock(nn.Module):
    def __init__(self, width: int, heads: int, kv_heads: int, inner: int, *,
                 block_size: int = 4, top_blocks: int = 16, index_heads: int = 4, index_width: int = 32):
        super().__init__()
        self.kv_heads, self.groups = kv_heads, heads // kv_heads
        self.head_width, self.index_width = width // heads, index_width
        self.index_heads, self.block_size, self.top_blocks = index_heads, block_size, top_blocks
        self.norm = nn.RMSNorm(width)
        self.query = nn.Linear(width, width, bias=False)
        self.key_value = nn.Linear(width, 2 * kv_heads * self.head_width, bias=False)
        self.index_query = nn.Linear(width, index_heads * index_width, bias=False)
        self.index_key = nn.Linear(width, index_width, bias=False)
        self.index_qnorm = nn.RMSNorm(index_width)
        self.index_knorm = nn.RMSNorm(index_width)
        self.gate = nn.Linear(width, width, bias=False)
        self.out = nn.Linear(width, width, bias=False)
        self.ff_norm = nn.RMSNorm(width)
        self.ff = SwiGLU(width, inner)

    def _append(self, u: Tensor, positions: Tensor, previous: SparseCache | None) -> SparseCache:
        batch, steps, _ = u.shape
        key, value = self.key_value(u).reshape(batch, steps, 2, self.kv_heads, self.head_width).unbind(2)
        key = rope(key, positions, self.head_width // 2)
        index = self.index_key(u.detach())
        if previous is not None:
            key = torch.cat((previous.key, key), dim=1)
            value = torch.cat((previous.value, value), dim=1)
            index = torch.cat((previous.index_tail, index), dim=1)
        r = self.block_size
        complete = index.shape[1] // r
        pooled = index[:, :complete * r].reshape(batch, complete, r, self.index_width).mean(dim=2)
        start = 0 if previous is None else previous.block_key.shape[1] * r
        block_positions = start + torch.arange(complete, device=u.device) * r
        block_key = rope(self.index_knorm(pooled).unsqueeze(2), block_positions, self.index_width // 2).squeeze(2)
        if previous is not None:
            block_key = torch.cat((previous.block_key, block_key), dim=1)
        return SparseCache(key, value, block_key, index[:, complete * r:].clone())

    def _scores(self, u: Tensor, positions: Tensor, cache: SparseCache, cutoff: Tensor):
        batch, steps, _ = u.shape
        q = self.query(u).reshape(batch, steps, -1, self.head_width)
        q = rope(q, positions, self.head_width // 2)
        q = q.reshape(batch, steps, self.kv_heads, self.groups, self.head_width).float()

        iq = self.index_query(u.detach()).reshape(batch, steps, self.index_heads, self.index_width)
        iq = rope(self.index_qnorm(iq), positions, self.index_width // 2)
        index_scores = torch.einsum("bthd,bkd->bthk", iq.float(), cache.block_key.float()).float().relu().sum(dim=2)
        block_end = torch.arange(cache.block_key.shape[1], device=u.device) * self.block_size + self.block_size - 1
        complete = block_end[None, :] <= cutoff[:, None]
        return q, index_scores, complete

    def _dense(self, q: Tensor, cache: SparseCache, cutoff: Tensor):
        scores = torch.einsum("bthgd,bshd->bthgs", q, cache.key.float()).float() * self.head_width ** -0.5
        allowed = torch.arange(cache.key.shape[1], device=q.device)[None, :] <= cutoff[:, None]
        prob = scores.masked_fill(~allowed[None, :, None, None], -torch.inf).softmax(dim=-1)
        return torch.einsum("bthgs,bshd->bthgd", prob, cache.value.float()), prob

    def _select(self, scores: Tensor, complete: Tensor):
        # Stable ties yield the same positions in replay and in prefix-at-a-time decoding.
        count = min(self.top_blocks, scores.shape[-1])
        return scores.masked_fill(~complete, -torch.inf).argsort(dim=-1, descending=True, stable=True)[..., :count]

    def _sparse(self, q: Tensor, selected: Tensor, complete: Tensor, cache: SparseCache, cutoff: Tensor):
        batch, steps = selected.shape[:2]
        r = self.block_size
        within = torch.arange(r, device=q.device)
        take = (selected[..., None] * r + within).flatten(-2)
        take_mask = complete.expand(batch, -1, -1).gather(-1, selected).repeat_interleave(r, dim=-1)
        tail = ((cutoff + 1) // r * r)[:, None] + within
        positions = torch.cat((take, tail.expand(batch, -1, -1)), dim=-1)
        valid = torch.cat((take_mask, (tail <= cutoff[:, None]).expand(batch, -1, -1)), dim=-1)
        # Unused tail indices can exceed the buffer on a block boundary.
        positions = positions.clamp_max(cache.key.shape[1] - 1)
        row = torch.arange(batch, device=q.device)[:, None, None]
        key, value = cache.key[row, positions].float(), cache.value[row, positions].float()
        scores = torch.einsum("bthgd,btlhd->bthgl", q, key).float() * self.head_width ** -0.5
        prob = scores.masked_fill(~valid[:, :, None, None], -torch.inf).softmax(dim=-1)
        return torch.einsum("bthgl,btlhd->bthgd", prob, value)

    def _index_loss(self, scores: Tensor, complete: Tensor, prob: Tensor, selected: Tensor | None):
        nblocks = scores.shape[-1]
        if nblocks == 0:
            return scores.sum()
        target = prob.detach().sum(dim=(2, 3))[..., :nblocks * self.block_size]
        target = target.unflatten(-1, (nblocks, self.block_size)).amax(dim=-1)
        mask = complete.expand_as(scores)
        if selected is not None:
            scores, mask, target = (a.gather(-1, selected) for a in (scores, mask, target))
        target = target.masked_fill(~mask, 0)
        target = target / target.sum(dim=-1, keepdim=True).clamp_min(1e-12)
        log_prob = scores.masked_fill(~mask, -1e9).log_softmax(dim=-1)
        kl = (target * (target.clamp_min(1e-12).log() - log_prob)).sum(dim=-1)
        return kl.sum() / mask.any(dim=-1).sum().clamp_min(1)

    def _apply_attention(self, x: Tensor, u: Tensor, positions: Tensor, cache: SparseCache,
                         cutoff: Tensor, sparse: bool, distill: bool):
        q, scores, complete = self._scores(u, positions, cache, cutoff)
        if sparse:
            selected = self._select(scores, complete)
            y = self._sparse(q, selected, complete, cache, cutoff)
        else:
            selected = None
            y, dense_prob = self._dense(q, cache, cutoff)
        aux = None
        if distill:
            if sparse:
                with torch.no_grad():
                    _, dense_prob = self._dense(q, cache, cutoff)
            aux = self._index_loss(scores, complete, dense_prob, selected)
        x = x + self.out(y.flatten(2).to(u.dtype) * self.gate(u).sigmoid())
        return x + self.ff(self.ff_norm(x)), aux

    def forward(self, x: Tensor, previous: SparseCache | None = None, *, sparse: bool = True,
                distill: bool = False):
        offset = 0 if previous is None else previous.key.shape[1]
        positions = offset + torch.arange(x.shape[1], device=x.device)
        u = self.norm(x)
        cache = self._append(u, positions, previous)
        y, aux = self._apply_attention(x, u, positions, cache, positions, sparse, distill)
        return y, cache, aux

    def read(self, query: Tensor, cache: SparseCache, *, cutoff: Tensor | None = None,
             sparse: bool = True, distill: bool = False):
        """Queries read complete committed prefixes without writing them back."""
        end = cache.key.shape[1]
        if cutoff is None:
            cutoff = torch.full((query.shape[1],), end - 1, device=query.device)
        return self._apply_attention(query, self.norm(query), cutoff + 1, cache, cutoff, sparse, distill)
