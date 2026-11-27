"""Schedule and back up the adaptive-thought PPO campaign."""
import argparse
from base64 import b64encode
import json
from pathlib import Path
import subprocess
import time

from watch_bc import SOURCES, pull_files, remote, status
from watch_thought_eval import KERNEL as WARMUP_KERNEL, source_bundle


HERE = Path(__file__).resolve().parent
KERNEL = "northwindlight/zhanguo-full-turn-ppo"


def kernel_ref(start):
    return KERNEL if start == 0 else f"{KERNEL}-from-{start:04}"


def campaign_sources(output):
    # Both execution modes implement the same reward rule. Preserve pre-migration bundles.
    files = output/"campaign-gdp60-land-v1.zip"
    if not files.exists():
        bundle = source_bundle(("thought_ppo.py", "replay.py", "parallel_ppo.py", "batched_rollout.py", "land_reward.py"))
        temp = files.with_suffix(".partial")
        temp.write_bytes(bundle)
        temp.replace(files)
    return files.read_bytes()


def package(output, start, stop, parent, execution=None):
    stage = output/"kernel"
    stage.mkdir(exist_ok=True)
    template = (HERE/"kaggle_thought_ppo.py").read_text()
    fields = dict(SOURCES=b64encode(campaign_sources(output)).decode(), PARENT_SHA=parent["sha256"],
                  PARENT_NAME=parent["checkpoint"], START=start, STOP=stop, EXECUTION=execution)
    for name, value in fields.items():
        template = template.replace(f"{name} = None\n", f"{name} = {value!r}\n", 1)
    compile(template, str(stage/"kernel.py"), "exec")
    (stage/"kernel.py").write_text(template)
    title = "Zhanguo Full Turn PPO" if not start else f"Zhanguo Full Turn PPO From {start:04}"
    remote.publish(stage/"kernel-metadata.json", dict(id=kernel_ref(start), title=title,
        code_file="kernel.py", language="python", kernel_type="script", is_private=True, enable_gpu=True,
        enable_internet=False, dataset_sources=[SOURCES[1]], competition_sources=[], kernel_sources=[parent["kernel"]]))
    return stage


def submit(output, start, stop, parent, execution=None):
    marker = output/f"submitted-{start:04}.json"
    job = kernel_ref(start)
    if marker.exists():
        old = json.loads(marker.read_text())
        if old["parent_sha256"] != parent["sha256"] or old["stop"] != stop:
            raise ValueError("Remote job was created with a different contract")
        return
    if status(job) in ("QUEUED", "RUNNING", "COMPLETE"):
        raise RuntimeError("Existing adaptive PPO job has no local submission record")
    stage = package(output, start, stop, parent, execution)
    log = output/f"submit-{start:04}.log"
    while True:
        code = remote.command(["kaggle", "kernels", "push", "-p", stage, "--accelerator", "NvidiaTeslaT4",
                               "-t", "25200"], log, timeout=600)
        message = log.read_text(errors="replace")
        if "Maximum batch GPU session count" in message:
            remote.publish(output/"live.json", dict(state="waiting_for_gpu_slot", kernel=job, verified_games=start,
                                                    segment_goal=stop, time=time.time()))
            time.sleep(45)
            continue
        if code or ("successfully pushed" not in message.lower() and
                    status(job) not in ("QUEUED", "RUNNING", "COMPLETE")):
            raise RuntimeError(f"Adaptive PPO submission failed; see {log}")
        break
    remote.publish(marker, dict(kernel=job, parent_sha256=parent["sha256"], stop=stop,
                                execution=execution, time=time.time()))


def watch(output, start, stop, goal):
    job = kernel_ref(start)
    log = output/f"run-{start:04}.log"
    follower = None
    with log.open("ab", buffering=0) as stream:
        try:
            while True:
                current = status(job)
                if current == "RUNNING" and (follower is None or follower.poll() is not None):
                    follower = subprocess.Popen(["kaggle", "kernels", "logs", job, "-f"],
                                                stdout=stream, stderr=subprocess.STDOUT)
                report = dict(state=current, kernel=job, verified_games=start, segment_goal=stop,
                              campaign_goal=goal, time=time.time())
                production = False
                for line in log.read_text(errors="replace").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    tag = row.get("stage")
                    if tag == "ppo_production_begin":
                        production = True
                        report["production"] = row
                    if tag and tag.startswith("ppo_cuda_"):
                        report[tag] = row
                    if production and tag in ("ppo_game_complete","ppo_batch_complete","ppo_replay_verified",
                                              "ppo_replay_reference","ppo_segment_interrupted","ppo_coverage","ppo_replay_progress"):
                        report[tag] = row
                remote.publish(output/"live.json", report)
                if current == "COMPLETE":
                    return
                if current in ("ERROR","CANCELLED","MISSING"):
                    raise RuntimeError(f"Adaptive PPO remote status {current}")
                time.sleep(22)
        finally:
            if follower is not None and follower.poll() is None:
                follower.terminate()
                follower.wait(timeout=10)


def archive(output, start, stop, parent):
    stage = output/"segments"/f"{stop:04}"
    work = stage/"work"
    job = kernel_ref(start)
    log = output/f"download-{stop:04}.log"
    pull_files(job, stage, r"^work/(state\.json|segment\.json|games-\d+\.json)$", log)
    state = json.loads((work/"state.json").read_text())
    segment = json.loads((work/"segment.json").read_text())
    games = state["games"]
    if (not start < games <= stop or segment["games"] != games or segment["sha256"] != state["sha256"]
            or segment["complete"] and games != stop or state["checkpoint"] != f"games-{games:04}.pt"):
        raise ValueError("Adaptive PPO segment manifest failed validation")
    if state["contract"].get("coverage") != "all_turns_all_decisions":
        raise ValueError("Checkpoint is not from the full-coverage campaign")
    for batch_file in work.glob("games-*.json"):
        batch = json.loads(batch_file.read_text())
        metrics = batch["metrics"]
        for epoch in batch["ppo"]:
            for trained, collected in (("trained_turns", "seat_turns"), ("trained_game_actions", "game_decisions"),
                                       ("trained_thoughts", "thoughts")):
                if epoch[trained] != sum(world[collected] for world in metrics):
                    raise ValueError(f"Incomplete PPO coverage in {batch_file.name}: {trained}")
    checkpoint = work/state["checkpoint"]
    pull_files(job, stage, rf"^work/games-{games:04}\.pt$", log)
    if state["initial_sha256"] != parent["initial_sha256"] or remote.sha(checkpoint) != state["sha256"]:
        raise ValueError("Adaptive PPO checkpoint failed validation")
    result = dict(state, kernel=job, local_checkpoint=str(checkpoint), segment=segment, time=time.time())
    remote.publish(stage/"verified.json", result)
    remote.publish(output/"verified.json", result)
    return result


def main(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    marker = output/"verified.json"
    if marker.exists():
        parent = json.loads(marker.read_text())
    else:
        warmup = json.loads((args.warmup/"verified.json").read_text())["value"]
        path = args.warmup/"download/work"/warmup["checkpoint"]
        if not warmup["accepted"]:
            raise ValueError("The value head did not pass warmup")
        parent = dict(games=0, kernel=WARMUP_KERNEL, local_checkpoint=str(path), checkpoint=path.name,
                      sha256=warmup["sha256"], initial_sha256=warmup["sha256"])
    if remote.sha(Path(parent["local_checkpoint"])) != parent["sha256"]:
        raise ValueError("Failed to verify local PPO parent hash")
    execution = None
    if args.distributed_update or args.batch_inference or args.rollouts_per_device != 1:
        execution = dict(distributed_update=args.distributed_update, batch_inference=args.batch_inference,
                         rollouts_per_device=args.rollouts_per_device)
    while parent["games"] < args.goal:
        segment = parent.get("segment")
        if segment is not None and not segment["complete"]:
            remote.publish(output/"live.json", dict(state="interrupted", kernel=parent["kernel"],
                verified_games=parent["games"], segment=segment, time=time.time()))
            return
        start = parent["games"]
        segment_games = min(4, args.segment) if start == 0 else args.segment
        stop = min(args.goal, start + segment_games)
        submit(output, start, stop, parent, execution)
        watch(output, start, stop, args.goal)
        parent = archive(output, start, stop, parent)
    remote.publish(output/"live.json", dict(state="complete", verified_games=parent["games"], time=time.time()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--goal", type=int, default=2000)
    parser.add_argument("--segment", type=int, default=16)
    parser.add_argument("--distributed-update", action="store_true")
    parser.add_argument("--batch-inference", action="store_true")
    parser.add_argument("--rollouts-per-device", type=int, default=1)
    options = parser.parse_args()
    if min(options.goal,options.segment) < 1:
        parser.error("Campaign and segment counts must be positive")
    if options.rollouts_per_device < 1 or (not options.batch_inference and options.rollouts_per_device > 2):
        parser.error("Use a positive --rollouts-per-device; more than two worlds requires --batch-inference")
    try:
        main(options)
    except Exception as error:
        remote.publish(options.output/"failure.json", dict(error=type(error).__name__, message=str(error), time=time.time()))
        raise
