"""Single-host data parallelism for full-coverage PPO, one process per device."""
from pathlib import Path
import tempfile

import torch
import torch.distributed as dist
import torch.multiprocessing as mp


class ReplayGroup:
    def __init__(self, rank, size, device):
        self.rank = rank
        self.size = size
        self.device = torch.device(device)

    def gather(self, value):
        gathered = [None] * self.size
        dist.all_gather_object(gathered, value)
        return gathered

    def totals(self, values, op=dist.ReduceOp.SUM):
        tensor = torch.tensor(values, dtype=torch.float64, device=self.device)
        dist.all_reduce(tensor, op=op)
        return tensor.tolist()

    @torch.no_grad()
    def sum_gradients(self, policy):
        # One full-batch update: each shard has already divided by the global loss denominators.
        # Preserve None for parameters unused everywhere, including the sparse retrieval indexer.
        parameters = [p for p in policy.parameters() if p.requires_grad]
        used = torch.tensor([p.grad is not None for p in parameters], device=self.device, dtype=torch.int32)
        dist.all_reduce(used, op=dist.ReduceOp.MAX)
        active = [p for p, flag in zip(parameters, used.tolist()) if flag]
        gradients = [p.grad if p.grad is not None else torch.zeros_like(p) for p in active]
        if gradients:
            flat = torch.cat([g.reshape(-1) for g in gradients])
            dist.all_reduce(flat)
            for p, values in zip(active, flat.split([p.numel() for p in active])):
                if p.grad is None:
                    p.grad = torch.empty_like(p)
                p.grad.copy_(values.view_as(p))

    def global_windows(self, windows):
        phases = self.gather([w.game_phase for w in windows])
        return [phase for shard in phases for phase in shard], sum(len(shard) for shard in phases[:self.rank])


def run_rank(rank, args, rendezvous):
    from thought_ppo import train
    if args.device == "cuda":
        torch.cuda.set_device(rank)
        device = f"cuda:{rank}"
        backend = "nccl"
    else:
        device, backend = "cpu", "gloo"
    dist.init_process_group(backend, init_method=rendezvous, rank=rank, world_size=args.workers)
    try:
        train(args, group=ReplayGroup(rank, args.workers, device))
    finally:
        dist.destroy_process_group()


def launch(args):
    with tempfile.TemporaryDirectory(prefix="zhanguo-ppo-ranks-") as root:
        rendezvous = (Path(root) / "rendezvous").as_uri()
        mp.spawn(run_rank, args=(args, rendezvous), nprocs=args.workers, join=True)
