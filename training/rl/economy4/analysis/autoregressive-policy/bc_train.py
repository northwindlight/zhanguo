"""Action-only behavior cloning for the autoregressive policy."""

import argparse
import hashlib
import json
from pathlib import Path
import pickle
import random
import tempfile
import time
from zipfile import ZipFile

import torch
import torch.distributed as dist
from torch import nn
from torch.nn import functional as F
from torch.nn.parallel import DistributedDataParallel
from torch.utils.checkpoint import checkpoint

from rl.obs import collate

from model import EVENT_WIDTH, Event, Policy


SEED = 20261125


def emit(stage, **fields):
    print(json.dumps(dict(stage=stage, **fields), allow_nan=False), flush=True)


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def publish(path, value):
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(value, allow_nan=False, sort_keys=True) + "\n")
    temporary.replace(path)


def inventory(data):
    train, validation = [], []
    seen = set()
    for path in sorted((data / "chunks").glob("chunk-*.zip")):
        with ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.endswith(".pkl"):
                    continue
                episode = pickle.loads(archive.read(name))
                seed = episode["seed"]
                if seed in seen or episode.get("anchor") or episode["contract"]["source"] != "pure_script":
                    raise ValueError("invalid or mixed corpus")
                seen.add(seed)
                if episode["accepted"] and episode["teacher"]:
                    item = (path, name, seed, len(episode["teacher"]))
                    (validation if seed % 5 == 0 else train).append(item)
    return train, validation


def load(item, chunk, device):
    path, name, seed, _ = item
    with ZipFile(path) as archive:
        rows = pickle.loads(archive.read(name))["teacher"]
    turns = [row[2] for row in rows]
    if turns != sorted(turns):
        raise ValueError(f"non-causal teacher order for map {seed}")
    batches, labels = [], []
    for start in range(0, len(rows), chunk):
        batch = rows[start:start + chunk]
        raw = collate([row[0] for row in batch])
        batches.append({k: torch.from_numpy(v).to(device) for k, v in raw.items()})
        labels.append(torch.tensor([row[1] for row in batch], device=device))
    return batches, labels


def action_payload(batch, labels):
    """Encode the chosen action as a historical observation after its decision."""
    row = torch.arange(labels.numel(), device=labels.device)
    features = batch["cand"][row, labels]
    # Candidate features already carry the observable action type and arguments.
    return F.pad(features, (0, EVENT_WIDTH - features.shape[-1]))


class ActionBC(nn.Module):
    def __init__(self, policy: Policy, checkpoint_board: bool = True):
        super().__init__()
        self.policy = policy
        self.checkpoint_board = checkpoint_board
        self.policy.thought.requires_grad_(False)
        self.policy.value.requires_grad_(False)

    def run(self, function, *args):
        if self.training and self.checkpoint_board:
            return checkpoint(function, *args, use_reentrant=False)
        return function(*args)

    def encode_board(self, batch):
        return self.policy.board.encode(batch)[0]

    def score(self, encoded, retrieved, batch):
        valid, slices = self.policy.board.layout(batch)
        board = self.policy.project_board(-1, encoded, valid, slices)
        hidden, _ = self.policy.decode_tokens(retrieved[:, None], board)
        return self.policy.action_logits(hidden[:, 0], batch, board)

    def forward(self, batches, labels, *, sparse: bool, distill: bool = True):
        policy = self.policy
        boards = [self.run(self.encode_board, batch) for batch in batches]
        global_token = torch.cat([policy.board_proj(x[:, :1]).squeeze(1) for x in boards])[None]
        action = torch.cat([action_payload(batch, target) for batch, target in zip(batches, labels)])[None]
        obs = global_token + policy.event_kind.weight[Event.OBSERVATION]
        recorded_action = policy.event_proj(action) + policy.event_kind.weight[Event.ACTION]
        events = torch.stack((obs, recorded_action), dim=2).flatten(1, 2)
        timeline, history, _ = policy._encode_events(events, None, sparse=sparse)
        n = events.shape[1] // 2
        graph = torch.zeros((1, n, 4, 4), device=events.device, dtype=torch.long)
        query = policy.game_query(global_token, graph) + timeline[:, ::2]
        cutoff = 2 * torch.arange(n, device=events.device)
        retrieved, kl = policy.sparse.read(query, history.sparse, cutoff=cutoff, sparse=sparse, distill=distill)
        lengths = [len(target) for target in labels]
        logits = [self.run(self.score, encoded, context, batch)
                  for encoded, context, batch in zip(boards, retrieved[0].split(lengths), batches)]
        return logits, kl


def loss_and_hits(logits, labels):
    ce = sum(F.cross_entropy(scores.float(), target, reduction="sum") for scores, target in zip(logits, labels))
    hits = sum((scores.argmax(-1) == target).sum() for scores, target in zip(logits, labels))
    return ce, hits, sum(target.numel() for target in labels)


def consolidate(metric, distributed, device):
    result = torch.tensor(metric, device=device, dtype=torch.float64)
    if distributed:
        dist.all_reduce(result, op=dist.ReduceOp.SUM)
    loss, hits, rows = result.tolist()
    return dict(ce=loss / rows, accuracy=hits / rows, rows=int(rows))


def atomic_save(path, checkpoint_state):
    temp = path.with_suffix(".partial")
    torch.save(checkpoint_state, temp)
    temp.replace(path)


def train(rank, args, rendezvous=None):
    world_size = args.workers
    distributed = world_size > 1
    is_cuda = args.device.startswith("cuda")
    device = torch.device(f"cuda:{rank}" if is_cuda else "cpu")
    if is_cuda:
        torch.cuda.set_device(device)
    if distributed:
        dist.init_process_group(backend="nccl" if is_cuda else "gloo", init_method=rendezvous,
                                world_size=world_size, rank=rank)
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.mha.set_fastpath_enabled(False)
    amp = is_cuda and args.precision == "fp16"
    if args.index:
        train_items, val_items = pickle.loads(args.index.read_bytes())
    else:
        train_items, val_items = inventory(args.data)
    if args.limit:
        train_items, val_items = train_items[:args.limit], val_items[:args.limit]
    if not train_items or not val_items:
        raise ValueError("BC requires nonempty training and validation maps")
    maps_per_update = world_size * args.accumulate
    if len(train_items) % maps_per_update:
        raise ValueError("training split must contain complete optimizer batches")
    args.output.mkdir(parents=True, exist_ok=True)
    raw = ActionBC(Policy(), checkpoint_board=not args.no_checkpoint).to(device)
    optimizer = torch.optim.AdamW((p for p in raw.parameters() if p.requires_grad), lr=args.lr, weight_decay=0)
    scaler = torch.amp.GradScaler("cuda", init_scale=1024.0, enabled=amp)
    start_epoch = 0
    if args.resume:
        state = torch.load(args.resume, map_location="cpu", weights_only=True)
        if state["seed"] != args.seed or state["precision"] != args.precision or state["lr"] != args.lr:
            raise ValueError("BC task identity changed")
        raw.policy.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scaler.load_state_dict(state["scaler"])
        start_epoch = state["epoch"]
    model = DistributedDataParallel(raw, device_ids=[device.index] if is_cuda else None) if distributed else raw
    if rank == 0:
        emit("bc_actual_start", epoch=start_epoch + 1, stop=args.stop_epoch, precision=args.precision,
             training_maps=len(train_items), validation_maps=len(val_items),
             training_rows=sum(item[3] for item in train_items), validation_rows=sum(item[3] for item in val_items),
             parameters=sum(p.numel() for p in raw.parameters()), world=world_size,
             maps_per_update=maps_per_update, preflight=args.preflight)

    for epoch in range(start_epoch + 1, args.stop_epoch + 1):
        sparse = epoch > 1
        order = list(train_items)
        random.Random(args.seed + epoch).shuffle(order)
        model.train()
        totals = [0.0, 0.0, 0.0]
        begin = time.monotonic()
        for microstep, item in enumerate(order[rank::world_size], 1):
            batch, labels = load(item, args.chunk, device)
            if (microstep - 1) % args.accumulate == 0:
                optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device.type, dtype=torch.float16, enabled=amp):
                logits, kl = model(batch, labels, sparse=sparse)
                ce, hits, count = loss_and_hits(logits, labels)
                loss = ce / count + args.index_weight * kl
            if not torch.isfinite(loss):
                raise FloatingPointError(f"loss stopped being finite at epoch={epoch}, microstep={microstep}")
            scaler.scale(loss / args.accumulate).backward()
            totals[0] += float(ce.detach())
            totals[1] += float(hits)
            totals[2] += count
            if microstep % args.accumulate:
                continue
            step = microstep // args.accumulate
            scale_before = scaler.get_scale()
            scaler.unscale_(optimizer)
            finite_norm = torch.nn.utils.clip_grad_norm_(
                model.parameters(), 1.0, error_if_nonfinite=not amp)
            scaler.step(optimizer)
            scaler.update()
            if rank == 0 and (step <= 2 or step % 20 == 0):
                emit("bc_step", epoch=epoch, step=step, steps=len(order) // maps_per_update,
                     ce=float(ce.detach()) / count, exact=float(hits) / count,
                     index_kl=float(kl.detach()), weight_update=scaler.get_scale() >= scale_before,
                     grad_norm=float(finite_norm) if torch.isfinite(finite_norm) else None,
                     elapsed=time.monotonic() - begin)
        train_metrics = consolidate(totals, distributed, device)
        raw.eval()
        validation = [0.0, 0.0, 0.0]
        with torch.no_grad():
            for item in val_items[rank::world_size]:
                batch, labels = load(item, args.chunk, device)
                with torch.autocast(device.type, dtype=torch.float16, enabled=amp):
                    logits, _ = raw(batch, labels, sparse=True, distill=False)
                ce, hits, count = loss_and_hits(logits, labels)
                validation[0] += float(ce)
                validation[1] += float(hits)
                validation[2] += count
        validation_metrics = consolidate(validation, distributed, device)
        if rank == 0:
            checkpoint_path = args.output / f"epoch-{epoch:02d}.pt"
            atomic_save(checkpoint_path, dict(epoch=epoch, seed=args.seed, precision=args.precision, lr=args.lr,
                                              model=raw.policy.state_dict(), optimizer=optimizer.state_dict(),
                                              scaler=scaler.state_dict()))
            report = dict(epoch=epoch, checkpoint=checkpoint_path.name, sha256=sha(checkpoint_path),
                          train=train_metrics, validation=validation_metrics, seconds=time.monotonic() - begin,
                          preflight=args.preflight, sparse=sparse, goal=10)
            publish(args.output / f"epoch-{epoch:02d}.json", report)
            publish(args.output / "state.json", report)
            emit("bc_epoch_complete", **report)
        if distributed:
            dist.barrier()
    if distributed:
        dist.destroy_process_group()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--index", type=Path)
    p.add_argument("--resume", type=Path)
    p.add_argument("--stop-epoch", type=int, default=10)
    p.add_argument("--device", default="cuda")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--accumulate", type=int, default=1)
    p.add_argument("--precision", choices=("fp16", "fp32"), default="fp16")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--index-weight", type=float, default=0.01)
    p.add_argument("--chunk", type=int, default=12)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--limit", type=int)
    p.add_argument("--preflight", action="store_true")
    p.add_argument("--no-checkpoint", action="store_true")
    p.add_argument("--write-index", type=Path)
    args = p.parse_args()
    if args.write_index:
        args.write_index.write_bytes(pickle.dumps(inventory(args.data), protocol=5))
        return
    if not 1 <= args.stop_epoch <= 10:
        p.error("stop epoch must be between 1 and 10")
    if args.accumulate < 1 or args.workers > 1 and args.accumulate > 1:
        p.error("use gradient accumulation with a single worker")
    if args.workers < 1 or args.device.startswith("cuda") and args.workers > torch.cuda.device_count():
        p.error("invalid worker count")
    if args.workers == 1:
        train(0, args)
    else:
        with tempfile.TemporaryDirectory(prefix="zhanguo-bc-ddp-") as directory:
            store = (Path(directory) / "rendezvous").as_uri()
            torch.multiprocessing.spawn(train, args=(args, store), nprocs=args.workers, join=True)


if __name__ == "__main__":
    main()
