"""Latency-oriented FP32 execution of the economy4 policy."""

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from model import Board, Policy, Snapshot


class _InferenceAttention(nn.MultiheadAttention):
    """Keep packed projections and use scaled dot-product attention in inference."""

    def __init__(self, original: nn.MultiheadAttention, attention):
        super().__init__(original.embed_dim, original.num_heads, batch_first=True)
        self.attention = attention

    def forward(self, query, key, value, key_padding_mask=None, need_weights=True):
        if need_weights:
            raise ValueError("Policy inference only returns attention activations")

        weight, bias = self.in_proj_weight, self.in_proj_bias
        width = self.embed_dim
        if query is key and key is value:
            projected = F.linear(query, weight, bias).chunk(3, dim=-1)
        elif key is value:
            projected = (F.linear(query, weight[:width], bias[:width]),
                         *F.linear(key, weight[width:], bias[width:]).chunk(2, dim=-1))
        else:
            projected = tuple(F.linear(x, w, b) for x, w, b in
                              zip((query, key, value), weight.chunk(3), bias.chunk(3), strict=True))

        q, k, v = (tensor.unflatten(-1, (self.num_heads, width // self.num_heads)).transpose(1, 2)
                   for tensor in projected)
        mask = None if key_padding_mask is None else ~key_padding_mask[:, None, None, :]
        if mask is not None and bool(mask.all()):
            mask = None
        output = self.attention(q, k, v, attn_mask=mask).transpose(1, 2).flatten(-2)
        return self.out_proj(output), None


class _InferenceBoard(Board):
    def __init__(self, width, attention):
        super().__init__(width)
        for layer in self.encoder.layers:
            layer.self_attn = _InferenceAttention(layer.self_attn, attention)
        for block in self.candidates:
            block.sattn = _InferenceAttention(block.sattn, attention)
            block.attn = _InferenceAttention(block.attn, attention)

    def encode_tokens(self, tokens: Tensor, valid: Tensor):
        # Call SDPA directly to avoid materializing the CPU attention matrix.
        pad = ~valid
        for layer in self.encoder.layers:
            x = layer.norm1(tokens)
            attended, _ = layer.self_attn(x, x, x, key_padding_mask=pad, need_weights=False)
            tokens = tokens + attended
            x = layer.norm2(tokens)
            tokens = tokens + layer.linear2(layer.activation(layer.linear1(x)))
        return tokens


class InferencePolicy(Policy):
    """Use the original neural state and packed weights with an inference attention callable."""

    def __init__(self, attention=F.scaled_dot_product_attention):
        super().__init__()
        self.board = _InferenceBoard(self.board.norm.normalized_shape[0], attention)
        self.attention = attention
        self.register_buffer("_board_to_kv", None, persistent=False)
        self._kv_widths = tuple(layer.cross_kv.out_features for layer in self.decoder)

    def load_state_dict(self, state_dict, strict=True, assign=False):
        result = super().load_state_dict(state_dict, strict=strict, assign=assign)
        with torch.no_grad():
            cross = torch.cat([layer.cross_kv.weight for layer in self.decoder])
            self._board_to_kv = cross @ self.board_proj.weight
        return result

    def project_board(self, revision, encoded, valid, slices):
        if self._board_to_kv is None:
            return super().project_board(revision, encoded, valid, slices)
        # Compose the two frozen linear projections once, then score all K/V in one GEMM.
        blocks = F.linear(encoded, self._board_to_kv).split(self._kv_widths, dim=-1)
        projected = tuple(layer.split_board_kv(block) for layer, block in zip(self.decoder, blocks, strict=True))
        global_token = self.board_proj(encoded[:, :1])
        return Snapshot(revision, encoded, valid, slices, global_token, projected)

    def decode_tokens(self, x, board, previous=None):
        return super().decode_tokens(x, board, previous, attention=self.attention)
