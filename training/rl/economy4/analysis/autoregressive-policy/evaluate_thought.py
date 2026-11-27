"""Paired native games, fixed thought-budget ablations, and value-only warmup."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

import torch
from torch.nn import functional as F

from bc_train import SEED, atomic_save, emit, publish, sha
from model import Policy
from native_eval import Engine
from rollout_trace import SeatTrace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cpp"))
from rewarding import territory_gdp


MODES = {"0": (0, False), "8": (8, False), "30": (30, False), "8-kv": (8, True)}
SCALE = 3000.0


def sample(logits, rng):
    return int(torch.multinomial(logits.float().softmax(dim=-1), 1, generator=rng).item())


def make_targets(states, turns, settlements, initial_land):
    settlement_rewards = {
        row["turn"]: row["nominal_gdp"] + territory_gdp(max(0, row["land"] - initial_land))
        for row in settlements
    }
    suffix, running = {}, 0.0
    for turn, reward in sorted(settlement_rewards.items(), reverse=True):
        running += reward / SCALE
        suffix[turn] = running
    if any(turn not in suffix for turn in turns):
        raise ValueError("cannot align a decision with its subsequent settlement")
    return {"features": torch.cat(states), "targets": torch.tensor([suffix[turn] for turn in turns])}


@torch.inference_mode()
def play(policy, engine, *, mode, seed, size, horizon, collect_value):
    thought_budget, keep_kv = MODES[mode]
    trace = SeatTrace(policy, thought_budget=thought_budget, retain_thought_kv=keep_kv)
    device = trace.device
    action_rng = torch.Generator(device=device).manual_seed(seed)
    thought_rng = torch.Generator(device=device).manual_seed(seed ^ 0x3D9012)
    settlements, value_states, value_turns = [], [], []
    thoughts, last_turn = 0, 0
    start = time.monotonic()
    with engine.match(seed, size, horizon) as game:
        result = game.result()
        while not result.done:
            turn, new_turn, obs = game.observe()
            if new_turn != (turn != last_turn):
                raise ValueError("engine turn marker disagrees with seat timeline")
            if last_turn and new_turn:
                trace.next_turn()
            last_turn = turn
            trace.observe(result.decisions, obs)
            while not trace.thought.closed:
                encoded = trace.score_thought()
                trace.commit_thought(sample(encoded, thought_rng))
                thoughts += 1
            logits, _ = trace.score_game()
            action = sample(logits[0], action_rng)
            if collect_value:
                value_states.append(trace.pending.state.cpu())
                value_turns.append(turn)
            # Logs the game record and the BC-format ACTION event. Real outcomes stay in the rollout record.
            trace.commit_game(action)
            previous_period = result.turn
            result = game.play(action)
            if result.turn != previous_period:
                if result.turn != previous_period + 1:
                    raise ValueError("native evaluation skipped a settlement")
                settlements.append(result.as_dict())
    gdp = sum(row["nominal_gdp"] for row in settlements)
    if abs(gdp - result.total_nominal_gdp) > 0.02:
        raise ValueError("native settlement GDP does not match total")
    stats = result.as_dict()
    stats.update(seed=seed, mode=mode, horizon=horizon, thoughts=thoughts,
                 elapsed=time.monotonic()-start, mean_nominal_gdp=gdp/horizon,
                 won=bool(result.alive and result.opponents_alive == 0),
                 invalid_rate=result.invalid/max(result.decisions,1))
    targets = make_targets(value_states, value_turns, settlements, result.initial_land) if collect_value else None
    return stats, targets


def restore(output, seed, mode, group, identity, collect_value):
    stem = output / group / f"{mode}-{seed}"
    report = stem.with_suffix(".json")
    if not report.exists():
        return None
    saved = json.loads(report.read_text())
    if saved["identity"] != identity:
        raise ValueError("existing rollout has different policy or contract")
    data = stem.with_suffix(".pt")
    if collect_value and sha(data) != saved["targets_sha"]:
        raise ValueError("rollout targets checksum mismatch")
    return saved["stats"], torch.load(data, weights_only=True) if collect_value else None


def collect(policy, engine, args, *, seed, mode, group, identity, collect_value):
    old = restore(args.output, seed, mode, group, identity, collect_value)
    if old is not None:
        return
    stem = args.output / group / f"{mode}-{seed}"
    stem.parent.mkdir(parents=True, exist_ok=True)
    data = stem.with_suffix(".pt")
    report = stem.with_suffix(".json")
    stats, targets = play(policy, engine, mode=mode, seed=seed, size=args.size, horizon=args.turns,
                          collect_value=collect_value)
    if targets is not None:
        atomic_save(data, targets)
    publish(report, {"identity":identity, "stats":stats, "targets_sha":sha(data) if targets is not None else None})
    emit("thought_game_complete", group=group, **stats)


def run_shard(rank, args, path, identity):
    torch.set_num_threads(args.threads)
    device = args.device
    if args.workers > 1 and args.device == "cuda":
        device = f"cuda:{rank}"
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    policy = Policy().to(device).eval()
    policy.load_state_dict(checkpoint["model"])
    del checkpoint
    engine = Engine(args.library)
    for n in range(rank, args.maps, args.workers):
        for mode in args.modes:
            collect(policy, engine, args, seed=args.seed+n, mode=mode, group="heldout", identity=identity,
                    collect_value=mode=="0" and args.warmup_maps > 0)
    for n in range(rank, args.warmup_maps, args.workers):
        collect(policy, engine, args, seed=args.seed+args.maps+n, mode="0", group="value-train", identity=identity,
                collect_value=True)


def value_mse(value, data):
    with torch.no_grad():
        estimate = value(data["features"]).squeeze(-1)
        return float(F.mse_loss(estimate, data["targets"]))


def fit_value(policy, checkpoint, train, validation, output, parent_sha):
    value = policy.value.cpu().train()
    optimizer = torch.optim.AdamW(value.parameters(), lr=1e-3, weight_decay=0)
    before = value_mse(value, validation)
    generator = torch.Generator().manual_seed(SEED)
    for _ in range(12):
        indices = torch.randperm(len(train["targets"]), generator=generator)
        for block in indices.split(256):
            estimate = value(train["features"][block]).squeeze(-1)
            loss = F.mse_loss(estimate, train["targets"][block])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
    after = value_mse(value, validation)
    report = {"heldout_mse_before":before,"heldout_mse_after":after,
              "accepted":before>after,"parent_sha256":parent_sha,"objective":"nominal_gdp+territory_holding",
              "scale":SCALE,"train_decisions":len(train["targets"]),"validation_decisions":len(validation["targets"])}
    if report["accepted"]:
        warm = dict(checkpoint)
        # A new value checkpoint publishes only after an explicit held-out improvement.
        warm["model"] = dict(checkpoint["model"])
        warm["model"].update({f"value.{name}": tensor.cpu() for name,tensor in value.state_dict().items()})
        warm.pop("optimizer", None)
        warm.pop("scaler", None)
        warm["value_warmup"] = report.copy()
        path = output / "value-warm.pt"
        atomic_save(path, warm)
        report.update(checkpoint=path.name, sha256=sha(path))
    publish(output / "value.json", report)
    emit("value_warmup_complete", **report)


def concatenate(rows):
    return {key:torch.cat([row[key] for row in rows]) for key in ("features","targets")}


def summarize(rows):
    measurements = ("mean_nominal_gdp","land","recruits","attacks","starved","shortage","invalid_rate","won",
                    "thoughts","elapsed")
    return {name:statistics.mean(row[name] for row in rows) for name in measurements}


def checkpoint_path(args):
    if args.checkpoint:
        if not args.checkpoint.is_file():
            raise FileNotFoundError(args.checkpoint)
        return args.checkpoint, sha(args.checkpoint)
    while True:
        if args.verified.exists():
            marker = json.loads(args.verified.read_text())
            if marker["epoch"] == 10 and not marker["preflight"]:
                path = Path(marker["local_checkpoint"])
                if sha(path) != marker["sha256"]:
                    raise ValueError("BC10 checksum does not match")
                return path, marker["sha256"]
        publish(args.output / "live.json", {"state":"waiting_for_verified_bc10","time":time.time()})
        time.sleep(30)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--library",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    gate = p.add_mutually_exclusive_group(required=True)
    gate.add_argument("--checkpoint",type=Path)
    gate.add_argument("--verified",type=Path)
    p.add_argument("--maps",type=int,default=8)
    p.add_argument("--warmup-maps",type=int,default=8)
    p.add_argument("--modes",nargs="+",choices=MODES,default=list(MODES))
    p.add_argument("--seed",type=int,default=620000)
    p.add_argument("--size",type=int,default=24)
    p.add_argument("--turns",type=int,default=100)
    p.add_argument("--device",default="cpu")
    p.add_argument("--threads",type=int,default=2)
    p.add_argument("--workers",type=int,default=1)
    args = p.parse_args()
    if args.maps < 1 or args.warmup_maps < 0 or len(set(args.modes)) != len(args.modes) or "0" not in args.modes:
        p.error("use unique modes including 0 with a nonempty paired evaluation")
    if args.workers not in (1, 2) or args.workers > 1 and args.device not in ("cpu", "cuda"):
        p.error("two workers require cpu or separately assigned cuda devices")
    args.output.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(args.threads)
    path,parent_sha = checkpoint_path(args)
    sources=hashlib.sha256()
    for name in ("evaluate_thought.py", "rollout_trace.py", "batch_policy.py", "native_eval.py", "model.py", "history.py", "memory_map.py"):
        sources.update(Path(__file__).with_name(name).read_bytes())
    identity = {"parent_sha256":parent_sha,"engine_sha256":sha(args.library),"seed":args.seed,
                "horizon":args.turns,"size":args.size,"device":args.device,"torch":str(torch.__version__),
                "maps":args.maps,"warmup_maps":args.warmup_maps,"source_sha256":sources.hexdigest()}
    publish(args.output / "live.json", {"state":"evaluating","identity":identity,"time":time.time()})
    if args.workers == 1:
        run_shard(0, args, path, identity)
    else:
        torch.multiprocessing.spawn(run_shard, args=(args,path,identity), nprocs=args.workers, join=True)
    scores, validation = {mode:[] for mode in args.modes},[]
    for n in range(args.maps):
        for mode in args.modes:
            stats, targets = restore(args.output,seed=args.seed+n,mode=mode,group="heldout",identity=identity,
                                     collect_value=mode=="0" and args.warmup_maps > 0)
            scores[mode].append(stats)
            if targets is not None:
                validation.append(targets)
    result = {"identity":identity, "maps":args.maps, "modes":{mode:summarize(rows) for mode,rows in scores.items()}}
    result["paired_delta_gdp"]={mode:statistics.mean(a["mean_nominal_gdp"]-b["mean_nominal_gdp"]
                                                      for a,b in zip(rows,scores["0"],strict=True))
                               for mode,rows in scores.items() if mode!="0"}
    publish(args.output / "evaluation.json", result)
    emit("evaluation_complete", **result)
    if args.warmup_maps:
        training = [restore(args.output,seed=args.seed+args.maps+n,mode="0",group="value-train",
                            identity=identity,collect_value=True)[1] for n in range(args.warmup_maps)]
        checkpoint = torch.load(path,map_location="cpu",weights_only=True)
        policy = Policy().eval()
        policy.load_state_dict(checkpoint["model"])
        fit_value(policy,checkpoint,concatenate(training),concatenate(validation),args.output,parent_sha)
    publish(args.output / "live.json", {"state":"completed","identity":identity,"time":time.time()})


if __name__ == "__main__":
    main()
