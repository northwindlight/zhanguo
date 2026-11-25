"""Run one Python PPO update from native sampler trajectories."""

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import torch
from torch.nn import functional as F

from read_trajectory import episodes


def tensor_batch(obs, device, collate):
    batch = {k: torch.from_numpy(v).to(device) for k, v in collate(obs).items()}
    batch["cand_ptr"] = batch["cand_ptr"].long()
    return batch


def train(opts):
    sys.path.insert(0, str(opts.python_root))
    from rl.model import Policy, aux_loss
    from rl.obs import F_DIM, M_AUX, collate

    device = torch.device(opts.device)
    if device.type == "cpu":
        torch.set_num_threads(opts.threads)
    torch.manual_seed(opts.seed)
    torch.backends.mha.set_fastpath_enabled(False)
    rng = np.random.default_rng(opts.seed)
    checkpoint = torch.load(opts.checkpoint, map_location="cpu", weights_only=True)
    model = Policy(**checkpoint["cfg"])
    model.load_state_dict(checkpoint["model"], strict=True)
    model.to(device)
    ref = None
    if opts.kl_ref:
        ref = copy.deepcopy(model).eval().requires_grad_(False)
        if opts.reference:
            anchor = torch.load(opts.reference, map_location="cpu", weights_only=True)
            ref.load_state_dict(anchor["model"], strict=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=opts.lr, weight_decay=0)
    if "optimizer" in checkpoint:
        optimizer.load_state_dict(checkpoint["optimizer"])
        for group in optimizer.param_groups:
            group["lr"] = opts.lr
    data = list(episodes(opts.trajectories))
    if not data:
        raise ValueError("no completed .zgrl trajectories")
    features = np.zeros((len(data), max(len(e[3]) for e in data), F_DIM), np.float32)
    auxiliary = np.zeros((*features.shape[:2], M_AUX), np.float32)
    aux_mask = np.zeros(features.shape[:2], bool)
    decisions, scores, positions = [], [], []
    for e, (samples, reward, _, summaries, targets) in enumerate(data):
        features[e, :len(summaries)] = np.stack(summaries)
        for ti, target in enumerate(targets):
            if target is not None:
                aux_mask[e, ti] = True
                auxiliary[e, ti] = target
        decisions.extend(samples)
        scores.extend([reward] * len(samples))
        positions.extend((e, d["ti"]) for d in samples)
    if not decisions:
        raise ValueError("no decisions in trajectories")
    if opts.gae_lambda is not None:
        scores = []
        for samples, reward, *_ in data:
            turns = np.asarray([d["ti"] for d in samples], dtype=np.int32)
            unique, first, mapping = np.unique(turns, return_index=True, return_inverse=True)
            values = np.asarray([samples[i]["v"] for i in first], dtype=np.float64)
            targets = np.empty(len(unique), dtype=np.float32)
            next_value = cumulative = 0.0
            for i in range(len(unique) - 1, -1, -1):
                terminal = reward if i == len(unique) - 1 else 0.0
                delta = terminal + opts.gamma * next_value - values[i]
                cumulative = delta + opts.gamma * opts.gae_lambda * cumulative
                targets[i] = values[i] + cumulative
                next_value = values[i]
            scores.extend(targets[mapping].tolist())
    advantage = np.asarray([
        reward - d["v"] - (opts.invalid_penalty if d["obs"]["cand_exec"][d["a"]] < 0.5 else 0)
        for d, reward in zip(decisions, scores, strict=True)
    ], np.float32)
    advantage = (advantage - advantage.mean()) / (advantage.std() + 1e-6)
    features, auxiliary, aux_mask = [torch.from_numpy(x).to(device) for x in (features, auxiliary, aux_mask)]
    positions = torch.tensor(positions, device=device)
    actions = torch.tensor([d["a"] for d in decisions], device=device)
    previous = torch.tensor([d["lp"] for d in decisions], device=device, dtype=torch.float32)
    advantages = torch.from_numpy(advantage).to(device)
    returns = torch.tensor(scores, device=device, dtype=torch.float32)
    reference_hidden = None
    if ref is not None:
        with torch.no_grad():
            reference_hidden = ref.mem_seq(features)
    # 旧策略 logp 用更新时同样的数值条件重算：采样端（C++，fp32 或 bf16）的数值差不进 PPO 比值。
    old = copy.deepcopy(model).eval().requires_grad_(False)
    with torch.no_grad():
        old_hidden = old.mem_seq(features)
    autocast = dict(device_type=device.type, dtype=torch.bfloat16, enabled=opts.amp and device.type == "cuda")
    count = 0
    loss_total = kl_total = sampler_gap = clipped = 0.0
    model.train()
    for _ in range(opts.epochs):
        subset = rng.permutation(len(decisions))[:max(opts.minibatch, int(len(decisions) * opts.subsample))]
        for offset in range(0, len(subset), opts.minibatch):
            selected = subset[offset:offset + opts.minibatch]
            index = torch.from_numpy(selected).to(device)
            b = tensor_batch([decisions[j]["obs"] for j in selected], device, collate)
            e, t = positions[index].unbind(1)
            memory = model.mem_seq(features)
            b["mem"] = memory[e, t]
            memory_loss, _ = aux_loss(model.mem_aux(memory), auxiliary, aux_mask)
            with torch.no_grad(), torch.autocast(**autocast):
                old_logits = old(dict(b, mem=old_hidden[e, t]))[0]
            old_logp = F.log_softmax(old_logits.float(), -1).gather(1, actions[index, None]).squeeze(1)
            with torch.autocast(**autocast):
                logits, exec_logits, values = model(b)
            logp_all = F.log_softmax(logits.float(), -1)
            logp = logp_all.gather(1, actions[index, None]).squeeze(1)
            delta = logp - old_logp
            ratio = delta.exp()
            adv = advantages[index]
            policy_loss = -torch.minimum(ratio * adv, ratio.clamp(1 - opts.clip, 1 + opts.clip) * adv).mean()
            value_loss = F.mse_loss(values.float(), returns[index])
            mask = b["cand_mask"]
            exec_loss = F.binary_cross_entropy_with_logits(exec_logits.float(), b["cand_exec"], reduction="none")
            exec_loss = (exec_loss * mask).sum() / mask.sum()
            prob = logp_all.exp()
            entropy = -(prob * logp_all.masked_fill(~mask, 0)).sum(1).mean()
            kl_ref = logits.new_zeros((), dtype=torch.float32)
            if ref is not None:
                with torch.no_grad(), torch.autocast(**autocast):
                    ref_logits = ref(dict(b, mem=reference_hidden[e, t]))[0]
                reference_logp = F.log_softmax(ref_logits.float(), -1)
                kl_ref = (prob * (logp_all - reference_logp).masked_fill(~mask, 0)).sum(1).mean()
            loss = (policy_loss + 0.5 * value_loss + opts.exec_weight * exec_loss
                    - opts.entropy * entropy + opts.kl_ref * kl_ref + opts.aux_weight * memory_loss)
            if not torch.isfinite(loss):
                raise ValueError("non-finite PPO loss")
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            kl = -delta.mean().item()
            sampler_gap += (old_logp - previous[index]).abs().mean().item()
            clipped += ((ratio.detach() - 1).abs() > opts.clip).float().mean().item()
            count += 1
            loss_total += loss.item()
            kl_total += kl
            if kl > opts.target_kl or (opts.max_batches and count >= opts.max_batches):
                break
        if opts.max_batches and count >= opts.max_batches:
            break

    opts.output.parent.mkdir(parents=True, exist_ok=True)
    if opts.output.exists():
        raise FileExistsError(opts.output)
    with tempfile.NamedTemporaryFile(dir=opts.output.parent, suffix=".pt", delete=False) as f:
        temporary = Path(f.name)
    try:
        torch.save(dict(model=model.state_dict(), cfg=model.cfg, optimizer=optimizer.state_dict(),
                        upd=checkpoint.get("upd", 0) + 1), temporary)
        temporary.rename(opts.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(dict(episodes=len(data), decisions=len(decisions), minibatches=count,
                          loss=loss_total / count, approx_kl=kl_total / count,
                          sampler_dlogp=sampler_gap / count, clip_frac=clipped / count, output=str(opts.output))))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("checkpoint", type=Path)
    p.add_argument("trajectories", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--python-root", type=Path, default=Path.home() / "zhanguo-rl")
    p.add_argument("--device", default="cuda")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--minibatch", type=int, default=128)
    p.add_argument("--max-batches", type=int, default=0)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--subsample", type=float, default=0.3)
    p.add_argument("--clip", type=float, default=0.1)
    p.add_argument("--entropy", type=float, default=0)
    p.add_argument("--invalid-penalty", type=float, default=0.05)
    p.add_argument("--aux-weight", type=float, default=0.0)
    p.add_argument("--exec-weight", type=float, default=0.5)
    p.add_argument("--kl-ref", type=float, default=0.05)
    p.add_argument("--reference", type=Path)
    p.add_argument("--target-kl", type=float, default=0.03)
    p.add_argument("--gae-lambda", type=float, help="回合级 GAE λ，省略时使用整局终局奖励")
    p.add_argument("--gamma", type=float, default=1.0, help="GAE 的回合折扣")
    p.add_argument("--amp", action="store_true", help="更新前向用 bf16 autocast（默认 fp32；旧 logp 按同样条件重算）")
    opts = p.parse_args()
    if opts.epochs < 1 or opts.minibatch < 1 or not 0 < opts.subsample <= 1:
        p.error("epochs, minibatch and subsample must be positive, with subsample at most 1")
    if opts.gae_lambda is not None and not 0 <= opts.gae_lambda <= 1:
        p.error("--gae-lambda must be between 0 and 1")
    if not 0 <= opts.gamma <= 1:
        p.error("--gamma must be between 0 and 1")
    train(opts)


if __name__ == "__main__":
    main()
