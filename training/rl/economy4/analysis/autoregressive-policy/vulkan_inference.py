"""NCNN Vulkan attention for the economy4 policy's inference path."""

import numpy as np
import torch
from torch import Tensor, nn
from torch.nn import functional as F

from model import Board, Policy


class VulkanAttention:
    """Run FP32 scaled dot-product attention on an explicitly selected GPU."""

    def __init__(self, device_index: int | None = None):
        try:
            import ncnn
        except ImportError as error:
            raise RuntimeError("Vulkan inference requires the ncnn Python package") from error
        if not hasattr(ncnn, "get_gpu_count"):
            raise RuntimeError("The installed ncnn does not include Vulkan support")

        devices = {i: ncnn.get_gpu_info(i) for i in range(ncnn.get_gpu_count())}
        if device_index is None:
            candidates = (index for index, info in devices.items() if info.type() in (0, 1, 2))
            device_index = next(candidates, next(iter(devices), None))
        if device_index not in devices:
            names = ", ".join(f"{i}: {info.device_name()}" for i, info in devices.items())
            raise RuntimeError(f"No matching Vulkan GPU (devices: {names or 'none'})")

        self.ncnn = ncnn
        self.device_index = device_index
        self.device_name = devices[device_index].device_name()
        self.calls = 0
        self.networks = {masked: self._network(masked) for masked in (False, True)}

    def _network(self, masked: bool):
        net = self.ncnn.Net()
        net.opt.use_vulkan_compute = True
        net.opt.use_fp16_packed = False
        net.opt.use_fp16_storage = False
        net.opt.use_fp16_arithmetic = False
        net.opt.num_threads = 1
        net.set_vulkan_device(self.device_index)

        inputs = ("q", "k", "v", "mask") if masked else ("q", "k", "v")
        size = len(inputs) + 1
        lines = ["7767517", f"{size} {size}"]
        lines.extend(f"Input {name} 0 1 {name}" for name in inputs)
        lines.append(f"SDPA attention {len(inputs)} 1 {' '.join(inputs)} out 5={int(masked)}")
        if net.load_param_mem("\n".join(lines) + "\n") != 0:
            raise RuntimeError("ncnn could not load the SDPA graph")
        # SDPA has no learned weights. Loading the empty payload initializes its pipelines.
        net.load_model_mem(bytes(16))
        layer = net.layers()[-1]
        if not net.opt.use_vulkan_compute or not layer.support_vulkan:
            raise RuntimeError("ncnn SDPA does not support Vulkan on this device")
        return net

    def _extract(self, query, key, value, mask):
        named = [("q", query), ("k", key), ("v", value)]
        if mask is not None:
            named.append(("mask", mask))
        arrays = [(name, tensor.contiguous().numpy()) for name, tensor in named]
        with self.networks[mask is not None].create_extractor() as extractor:
            for name, array in arrays:
                if extractor.input(name, self.ncnn.Mat(array)) != 0:
                    raise RuntimeError(f"ncnn rejected SDPA input {name}")
            status, output = extractor.extract("out")
            if status != 0 or output.empty():
                raise RuntimeError(f"ncnn Vulkan SDPA failed ({status}) on {self.device_name}")
            result = torch.from_numpy(np.array(output, copy=True))
        if result.shape != query.shape:
            raise RuntimeError(f"Unexpected ncnn SDPA output shape: {result.shape}, wanted {query.shape}")
        self.calls += 1
        return result

    def __call__(self, query: Tensor, key: Tensor, value: Tensor, *, attn_mask: Tensor | None = None):
        if any(t.device.type != "cpu" or t.dtype != torch.float32 or t.ndim != 4
               for t in (query, key, value)):
            raise ValueError("Vulkan SDPA expects CPU FP32 tensors of shape [batch, heads, tokens, width]")
        if attn_mask is not None and attn_mask.dtype != torch.bool:
            raise ValueError("Policy Vulkan attention requires a boolean allow mask")
        batch, _, queries, _ = query.shape
        keys = key.shape[-2]
        if keys == 0:
            return torch.zeros_like(query)

        key_masks = None
        matrix_masks = None
        if attn_mask is not None:
            if attn_mask.shape[-2] == 1:
                key_masks = torch.broadcast_to(attn_mask, (batch, 1, 1, keys))[:, 0, 0]
            else:
                matrix_masks = torch.broadcast_to(attn_mask, (batch, 1, queries, keys))[:, 0]

        rows = []
        for index, (q, k, v) in enumerate(zip(query, key, value, strict=True)):
            mask = None
            valid_queries = None
            if key_masks is not None:
                allowed = key_masks[index]
                if not allowed.any():
                    rows.append(torch.zeros_like(q))
                    continue
                if not allowed.all():
                    k, v = k[:, allowed], v[:, allowed]
            if matrix_masks is not None:
                allowed = matrix_masks[index]
                valid_queries = allowed.any(-1)
                allowed = allowed | ~valid_queries[:, None]
                # ncnn expects finite additive masking and one visible key per query.
                mask = q.new_zeros((queries, keys)).masked_fill_(~allowed, -1e9)
            result = self._extract(q, k, v, mask)
            if valid_queries is not None:
                result.masked_fill_(~valid_queries[None, :, None], 0)
            rows.append(result)
        return torch.stack(rows)


class _MultiheadAttention(nn.MultiheadAttention):
    """Use the original packed q/k/v and output weights around ncnn SDPA."""

    def __init__(self, original: nn.MultiheadAttention, attention: VulkanAttention):
        super().__init__(original.embed_dim, original.num_heads, batch_first=True)
        self.load_state_dict(original.state_dict())
        self.attention = attention

    def forward(self, query, key, value, key_padding_mask=None, need_weights=True):
        if need_weights:
            raise ValueError("Vulkan policy attention only returns activations")
        weights = self.in_proj_weight.chunk(3)
        biases = self.in_proj_bias.chunk(3)
        projected = [F.linear(tensor, weight, bias)
                     for tensor, weight, bias in zip((query, key, value), weights, biases, strict=True)]
        head_width = self.embed_dim // self.num_heads
        q, k, v = (tensor.unflatten(-1, (self.num_heads, head_width)).transpose(1, 2)
                   for tensor in projected)
        valid = None if key_padding_mask is None else ~key_padding_mask[:, None, None, :]
        output = self.attention(q, k, v, attn_mask=valid).transpose(1, 2).flatten(-2)
        return self.out_proj(output), None


class _VulkanBoard(Board):
    def __init__(self, width: int, attention: VulkanAttention):
        super().__init__(width)
        for layer in self.encoder.layers:
            layer.self_attn = _MultiheadAttention(layer.self_attn, attention)
        for block in self.candidates:
            block.sattn = _MultiheadAttention(block.sattn, attention)
            block.attn = _MultiheadAttention(block.attn, attention)

    def encode_tokens(self, tokens: Tensor, valid: Tensor):
        # Keep the existing pre-norm encoder path and bypass PyTorch's fused CPU attention.
        pad = ~valid
        for layer in self.encoder.layers:
            x = layer.norm1(tokens)
            attended, _ = layer.self_attn(x, x, x, key_padding_mask=pad, need_weights=False)
            tokens = tokens + layer.dropout1(attended)
            x = layer.norm2(tokens)
            x = layer.linear2(layer.dropout(layer.activation(layer.linear1(x))))
            tokens = tokens + layer.dropout2(x)
        return tokens


class VulkanPolicy(Policy):
    """Run board, candidate and decoder attention with ncnn Vulkan."""

    def __init__(self, attention: VulkanAttention):
        super().__init__()
        self.board = _VulkanBoard(self.board.norm.normalized_shape[0], attention)
        self.attention = attention

    def decode_tokens(self, x, board, previous=None):
        return super().decode_tokens(x, board, previous, attention=self.attention)
