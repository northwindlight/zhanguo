"""跨设备（CUDA / TPU-XLA / CPU）的策略网络吞吐基准：用真实示范观测，补齐成固定形状（XLA 只编译一次）。

量两件事：推理（无梯度前向，决策/秒）与训练步（前向+反向+AdamW，样本/秒）。
用法：python3 -m rl.bench_fwd --device cuda|xla|cpu --ckpt runs/bcS/bc.pt
"""
import argparse
import json
import time

import numpy as np
import torch
import torch.nn.functional as F

from .model import Policy
from .obs import collate


def fixed_batches(obs, B, n_batches):
    """把观测切成 n_batches 个大小 B 的批，再统一补齐到全体最大形状（mask=False，指针=-1）。"""
    rng = np.random.default_rng(0)
    raw = [collate([obs[j] for j in rng.choice(len(obs), B)]) for _ in range(n_batches)]
    shape = {k: tuple(max(r[k].shape[i] for r in raw) for i in range(r0.ndim)) for k, r0 in raw[0].items()}
    out = []
    for r in raw:
        b = {}
        for k, v in r.items():
            fill = -1 if k == "cand_ptr" else 0
            z = np.full(shape[k], fill, v.dtype)
            z[tuple(slice(0, s) for s in v.shape)] = v
            b[k] = z
        out.append(b)
    return out, shape


def to_dev(b, dev, mem_h, dtype):
    t = {k: torch.from_numpy(v).to(dev) for k, v in b.items()}
    t["cand_ptr"] = t["cand_ptr"].long()
    for k in ("globals", "market", "nations", "tiles", "armies", "cand"):
        t[k] = t[k].to(dtype)
    t["mem"] = torch.zeros(t["globals"].shape[0], mem_h, device=dev, dtype=dtype)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--ckpt", default="")
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--infer-B", default="128,256,512")
    ap.add_argument("--train-B", type=int, default=128)
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--dtypes", default="bf16,fp32")
    ap.add_argument("--compile", action="store_true", help="推理用 torch.compile 包一层")
    ap.add_argument("--arch", default="full", help="无 --ckpt 时随机权重的架构")
    ap.add_argument("--cand-layers", type=int, default=1)
    ap.add_argument("--cand-self", action="store_true")
    a = ap.parse_args()

    from .demo import collect
    t0 = time.time()
    obs = []
    for s in range(a.games):
        out = collect(9000 + s, 13, 80, ["rule", "w1"][s % 2])[0]
        obs += [o for o, _, _ in out]
    print(f"obs {len(obs)} from {a.games} games in {time.time() - t0:.0f}s", flush=True)

    if a.device == "xla":
        import torch_xla.core.xla_model as xm
        dev = xm.xla_device()
        sync = lambda: (xm.mark_step(), xm.wait_device_ops())
    elif a.device == "tpu":                      # torch_tpu：原生 PrivateUse1 后端
        import torch_tpu  # noqa: F401
        dev = torch.device("tpu")
        sync = lambda: None                      # 计时结束时把结果拷回 CPU 来同步（见 fence）
    else:
        dev = torch.device(a.device)
        sync = (lambda: torch.cuda.synchronize()) if a.device == "cuda" else (lambda: None)
    fence = lambda x: float(x.float().sum().cpu())
    torch.backends.mha.set_fastpath_enabled(False)

    if a.ckpt:
        ck = torch.load(a.ckpt, map_location="cpu", weights_only=True)
        cfg, sd = ck["cfg"], ck["model"]
    else:
        cfg, sd = dict(d=192, layers=4, heads=8, mem_h=128, exec_prior_weight=0.5), None
        if a.arch != "full":
            cfg.update(arch=a.arch, cand_layers=a.cand_layers, cand_self=a.cand_self)
    res = dict(device=a.device, cfg=cfg)
    if a.device == "cuda":
        res["gpu"] = torch.cuda.get_device_name(0)
    _mf = torch.Tensor.masked_fill                # fp16 放不下模型里的 -1e9 屏蔽值：仅基准里截到 fp16 下限

    def _mf16(self, mask, value):
        if self.dtype == torch.float16 and isinstance(value, (int, float)) and value < -6e4:
            value = -6e4
        return _mf(self, mask, value)
    torch.Tensor.masked_fill = _mf16
    for dt_name in a.dtypes.split(","):
        dtype = dict(bf16=torch.bfloat16, fp16=torch.float16, fp32=torch.float32, tf32=torch.float32)[dt_name]
        if a.device == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = dt_name == "tf32"
        model = Policy(**cfg)
        if sd is not None:
            model.load_state_dict(sd)
        model = model.to(dev).to(dtype).eval()
        fwd = torch.compile(model) if a.compile else model
        for B in map(int, a.infer_B.split(",")):
            bs, shape = fixed_batches(obs, B, 4)
            bs = [to_dev(b, dev, model.mem_h, dtype) for b in bs]
            with torch.no_grad():
                for i in range(3):
                    fence(fwd(bs[i % 4])[0])
                sync()
                t = time.time()
                for i in range(a.iters):
                    lg = fwd(bs[i % 4])[0]
                    if a.device == "xla":
                        xm.mark_step()
                sync()
                fence(lg)
                dt = time.time() - t
            res[f"infer_{dt_name}_B{B}"] = round(B * a.iters / dt, 1)
            res[f"tokens_B{B}"] = sum(shape[k][1] for k in ("globals", "market", "nations", "tiles", "armies", "cand")) + 1
            print(dt_name, "infer B", B, "dec/s", res[f"infer_{dt_name}_B{B}"], "tokens", res[f"tokens_B{B}"], flush=True)
        model.train()
        opt = torch.optim.AdamW(model.parameters(), lr=1e-6)
        bs, _ = fixed_batches(obs, a.train_B, 4)
        bs = [to_dev(b, dev, model.mem_h, dtype) for b in bs]
        acts = [torch.zeros(a.train_B, dtype=torch.long, device=dev) for _ in bs]

        def step(i):
            lg, ex, v = model(bs[i % 4])
            loss = F.cross_entropy(lg.float(), acts[i % 4]) + v.float().pow(2).mean() + ex.float().pow(2).mean()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            if a.device == "xla":
                xm.mark_step()
        for i in range(3):
            step(i)
        sync()
        fence(next(model.parameters()))
        t = time.time()
        for i in range(a.iters):
            step(i)
        sync()
        fence(next(model.parameters()))
        res[f"train_{dt_name}_B{a.train_B}"] = round(a.train_B * a.iters / (time.time() - t), 1)
        print(dt_name, "train B", a.train_B, "samples/s", res[f"train_{dt_name}_B{a.train_B}"], flush=True)
    print("RESULT " + json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
