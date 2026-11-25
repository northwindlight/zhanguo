"""Export the Python RL checkpoint for the C++ sampler and verify dynamic batching."""

import argparse
import copy
import sys
from pathlib import Path

import torch


def example(policy, batch, armies, candidates, nations=2, tiles=169):
    tokens = (("globals", "g", 1), ("market", "m", 6), ("nations", "n", nations),
              ("tiles", "t", tiles), ("armies", "a", armies), ("cand", "c", candidates))
    result = {}
    for key, projection, count in tokens:
        dim = policy.proj[projection].in_features
        result[key] = torch.randn(batch, count, dim)
        result[key + "_mask"] = torch.ones(batch, count, dtype=torch.bool)
    result["cand_ptr"] = torch.randint(0, min(armies, 2), (batch, candidates, 3))
    result["cand_ptr"][:, ::2] = -1
    result["mem"] = torch.randn(batch, policy.mem_h)
    return result


class Inference(torch.nn.Module):
    def __init__(self, policy):
        super().__init__()
        self.policy = policy
        self.mem_h = policy.mem_h
        self.xattn = getattr(policy, "arch", "full") == "xattn"

    def forward(self, b):
        # Keep tensor factories device-relative so a CPU export can run on CUDA.
        policy = self.policy
        p = policy.proj
        dtype = p["g"].weight.dtype
        g = p["g"](b["globals"].to(dtype)) + policy.kind.weight[0]
        m = p["m"](b["market"].to(dtype)) + policy.kind.weight[1]
        n = p["n"](b["nations"].to(dtype)) + policy.kind.weight[2]
        t = p["t"](b["tiles"].to(dtype)) + policy.kind.weight[3]
        a = p["a"](b["armies"].to(dtype)) + policy.kind.weight[4]
        c = p["c"](b["cand"].to(dtype)) + policy.kind.weight[5]
        ptr = b["cand_ptr"]

        def gather(token, index):
            # C++ pads empty army batches to one masked row; its pointer is always -1.
            i = index.clamp(min=0).unsqueeze(-1).expand(-1, -1, token.shape[2])
            return torch.gather(token, 1, i) * (index >= 0).unsqueeze(-1).to(token.dtype)

        mem = b["mem"].to(dtype)
        mt = (policy.mem_proj(mem) + policy.kind.weight[6]).unsqueeze(1)
        board = (g, mt, m, n, t, a)
        board_masks = (b["globals_mask"], torch.ones_like(b["globals_mask"]),
                       b["market_mask"], b["nations_mask"], b["tiles_mask"], b["armies_mask"])
        if self.xattn:
            board_pad = ~torch.cat(board_masks, 1)
            h = policy.norm(policy.enc(torch.cat(board, 1), src_key_padding_mask=board_pad))
            nations_start = g.shape[1] + mt.shape[1] + m.shape[1]
            tiles_start = nations_start + n.shape[1]
            armies_start = tiles_start + t.shape[1]
            hn, ht, ha = h[:, nations_start:tiles_start], h[:, tiles_start:armies_start], h[:, armies_start:]
            c = c + gather(ht, ptr[..., 0]) + gather(ha, ptr[..., 1]) + gather(hn, ptr[..., 2])
            cand_pad = ~b["cand_mask"]
            for layer in policy.cross:
                c = layer(c, h, board_pad, cand_pad)
            hc = policy.cnorm(c)
        else:
            c = c + gather(t, ptr[..., 0]) + gather(a, ptr[..., 1]) + gather(n, ptr[..., 2])
            x = torch.cat((*board, c), 1)
            pad = ~torch.cat((*board_masks, b["cand_mask"]), 1)
            h = policy.norm(policy.enc(x, src_key_padding_mask=pad))
            hc = h[:, -c.shape[1]:]
        logits = policy.logit(hc).squeeze(-1)
        executable = policy.exec_head(hc).squeeze(-1)
        if policy.xw:
            logits = logits + policy.xw * torch.nn.functional.logsigmoid(executable)
        logits = logits.masked_fill(~b["cand_mask"], -1e9)
        raw = torch.cat((b["globals"][:, 0], policy._mmean(b["tiles"], b["tiles_mask"]),
                         policy._mmean(b["armies"], b["armies_mask"]), b["market"].flatten(1), mem.detach()), 1)
        return logits, executable, policy.value(raw.to(dtype)).squeeze(-1)

    def mem_step(self, features, hidden):
        # A single GRU transition, using the same parameters and gate order as Policy.mem_step.
        gru = self.policy.gru
        ir, iz, ic = torch.nn.functional.linear(features, gru.weight_ih_l0, gru.bias_ih_l0).chunk(3, dim=-1)
        hr, hz, hc = torch.nn.functional.linear(hidden, gru.weight_hh_l0, gru.bias_hh_l0).chunk(3, dim=-1)
        reset = torch.sigmoid(ir + hr)
        update = torch.sigmoid(iz + hz)
        candidate = torch.tanh(ic + reset * hc)
        return candidate + update * (hidden - candidate)

    def memory_width(self):
        return self.policy.gru.weight_hh_l0.shape[1] + torch.zeros((), dtype=torch.int64)


def fold_attention_head_dims(traced, policy):
    traced_modules = dict(traced.policy.named_modules())
    for name, attention in policy.named_modules():
        if not isinstance(attention, torch.nn.MultiheadAttention):
            continue
        graph = traced_modules[name]._c._get_method("forward").graph
        for div in list(graph.findAllNodes("aten::div")):
            args = list(div.inputs())
            if len(args) != 3 or args[2].toIValue() != "trunc":
                continue
            width = args[0].node()
            if width.kind() != "prim::NumToTensor":
                continue
            size = width.input().node()
            if size.kind() != "aten::size" or list(size.inputs())[1].toIValue() not in (2, -1):
                continue
            heads = args[1].toIValue()
            if isinstance(heads, torch.Tensor) and heads.ndim == 0:
                heads = heads.item()
            if not isinstance(heads, int) or heads != attention.num_heads:
                continue
            uses = list(div.output().uses())
            if not uses or any(use.user.kind() != "aten::Int" for use in uses):
                continue
            # Tracing tensorizes the fixed embedding width. On CUDA, the resulting
            # tensor-to-int conversion synchronizes the stream before each attention.
            with graph.insert_point_guard(div):
                head_dim = graph.insertConstant(attention.head_dim)
            for use in uses:
                use.user.output().replaceAllUsesWith(head_dim)
        torch._C._jit_pass_dce(graph)
        graph.lint()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("checkpoint", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--python-root", type=Path, default=Path.home() / "zhanguo-rl")
    p.add_argument("--new-test-policy", action="store_true",
                   help="write a small deterministic checkpoint for a CPU smoke test")
    p.add_argument("--native-bf16", action="store_true", help="export bfloat16 policy weights and FP32 GRU")
    p.add_argument("--bf16-tol", type=float, default=0.05,
                   help="native bf16 自检容差（随机输入；更新器重算旧 logp 时可放宽）")
    p.add_argument("--fixture", type=Path, help="also export synthetic inputs and Python outputs for C++ verification")
    args = p.parse_args()

    sys.path.insert(0, str(args.python_root))
    from rl.model import Policy

    torch.set_num_threads(1)
    torch.manual_seed(745)
    torch.backends.mha.set_fastpath_enabled(False)
    if args.new_test_policy:
        policy = Policy(d=32, layers=2, heads=4, mem_h=16, exec_prior_weight=0.5)
        torch.save(dict(model=policy.state_dict(), cfg=policy.cfg), args.checkpoint)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    policy = Policy(**checkpoint["cfg"]).eval()
    policy.load_state_dict(checkpoint["model"], strict=True)
    use_policy = policy
    if args.native_bf16:
        use_policy = copy.deepcopy(policy).bfloat16()
        use_policy.gru.float()
        use_policy.gru.load_state_dict(policy.gru.state_dict())
    model = Inference(use_policy).eval()
    h = policy.mem_h
    f_dim = policy.gru.input_size
    # The CPU exporter traces once and checks several shapes. Profiling those
    # shapes can stall BF16 graph optimization on ARM.
    with torch.no_grad(), torch.jit.optimized_execution(False):
        jit = torch.jit.trace_module(model, {
            "forward": (example(policy, 2, 3, 7),),
            "mem_step": (torch.randn(2, f_dim), torch.randn(2, h)),
            "memory_width": (),
        }, check_trace=False)
        fold_attention_head_dims(jit, policy)
        max_error = 0.0
        variable_board = {"nations": 3, "tiles": 47} if model.xattn else {}
        for b, a, k, board_dims in ((1, 1, 3, {}), (3, 6, 19, variable_board), (8, 2, 31, {})):
            inputs = example(policy, b, a, k, **board_dims)
            inputs["armies_mask"][0] = False
            inputs["armies"][0].zero_()
            inputs["cand_ptr"][0, :, 1] = -1
            inputs["cand_mask"][:, -1] = False
            inputs["cand_ptr"][:, -1] = -1
            features = torch.randn(b, f_dim)
            hidden = torch.randn(b, h)
            expected = policy.mem_step(features, hidden)
            actual = jit.mem_step(features, hidden)
            torch.testing.assert_close(actual, expected, atol=1e-5, rtol=1e-5)
            refs = policy(inputs)
            predictions = jit(inputs)
            for i, (ref, pred) in enumerate(zip(refs, predictions, strict=True)):
                if i == 0:
                    ref, pred = ref[inputs["cand_mask"]], pred[inputs["cand_mask"]]
                tol = args.bf16_tol if args.native_bf16 else 1e-5
                torch.testing.assert_close(ref, pred.float(), atol=tol, rtol=tol)
                max_error = max(max_error, (ref - pred.float()).abs().max().item())
        jit.save(str(args.output))
        if args.fixture:
            class Fixture(torch.nn.Module):
                def __init__(self, entries):
                    super().__init__()
                    for name, tensor in entries.items():
                        self.register_buffer(name, tensor)

                def forward(self, x):
                    return x

            entries = dict(inputs, summary=features, hidden=hidden, memory=expected,
                           logits=refs[0], executable=refs[1], value=refs[2])
            torch.jit.trace(Fixture(entries), (torch.zeros(1),)).save(str(args.fixture))
    print(f"exported {args.output} mem_h={h} max_forward_error={max_error:.3g}")


if __name__ == "__main__":
    main()
