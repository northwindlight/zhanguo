"""Keep each completed BC segment and resume until the ten-epoch target."""

from ast import Assign, Name, literal_eval, parse
from base64 import b64decode, b64encode
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from zipfile import ZipFile, ZIP_DEFLATED

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import watch_land_gdp_ppo as remote

OUT = ROOT / "autoregressive-bc-results"
STAGE = OUT / "kernel"
KERNEL = "northwindlight/zhanguo-autoregressive-bc"
OLD_KERNEL = "northwindlight/zhanguo-script-global-ppo"
SOURCES = (
    "northwindlight/zhanguo-pure-script-corpus-20261124",
    "northwindlight/zhanguo-pure-script-code-20261124",
)
FILES = ("history.py", "model.py", "memory_map.py", "bc_train.py")


def kernel_ref(epoch):
    return KERNEL if epoch == 0 else f"{KERNEL}-from-{epoch:02}"


def status(ref):
    try:
        run = subprocess.run(["kaggle", "kernels", "status", ref], capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return "UNKNOWN"
    text = run.stdout.strip()
    if run.returncode:
        if "not found" in (run.stderr + text).lower() or "404" in run.stderr:
            return "MISSING"
        return "UNKNOWN"
    states = re.findall(r"KernelWorkerStatus\.([A-Z]+)", text)
    return states[-1] if states else "UNKNOWN"


def pull_files(ref, destination, pattern, log):
    destination.mkdir(parents=True, exist_ok=True)
    command = ["kaggle", "kernels", "output", ref, "-p", destination, "-q", "--file-pattern", pattern]
    for attempt in range(4):
        if remote.command(command, log, timeout=1800) == 0:
            return
        if attempt < 3:
            time.sleep(20)
    raise RuntimeError(f"could not retrieve checkpoint from {ref}")


def save_ending_ppo():
    # This handler never submits another PPO segment.
    old = ROOT / "script-global-ppo-results"
    record = old / "stopped.json"
    if record.exists():
        return
    remote_state = status(OLD_KERNEL)
    if remote_state in ("RUNNING", "QUEUED", "PENDING", "UNKNOWN"):
        return
    if remote_state != "COMPLETE":
        remote.publish(record, dict(state="stopped", status=remote_state, verified_games=1200, time=time.time()))
        return
    stage = old / "segments" / "007"
    pull_files(OLD_KERNEL, stage, r"^work/(state|segment)\.json$", old / "stop-download.log")
    work = stage / "work"
    segment = json.loads((work / "segment.json").read_text())
    state = json.loads((work / "state.json").read_text())
    if segment["games"] != 1400 or segment["models"] != state["models"] or state["games"] != 1400:
        raise ValueError("wrong PPO segment at stopped boundary")
    weights = [entry["latest"] for entry in state["models"].values()]
    natives = [entry["path"] for entry in segment["natives"].values()]
    pattern = r"^work/(" + "|".join(map(re.escape, weights + natives)) + r")$"
    pull_files(OLD_KERNEL, stage, pattern, old / "stop-download.log")
    for seat, entry in state["models"].items():
        native = segment["natives"][seat]
        if remote.sha(work / entry["latest"]) != entry["sha256"] or remote.sha(work / native["path"]) != native["sha256"]:
            raise ValueError("PPO final checkpoint hash mismatch")
    stamp = dict(games=1400, models=state["models"], time=time.time())
    remote.publish(stage / "verified.json", stamp)
    remote.publish(record, dict(stamp, state="stopped", status="COMPLETE"))
    remote.publish(old / "live.json", dict(state="stopped", verified_games=1400, previous_goal=2000, time=time.time()))
    print("Original PPO stopped at a verified 1400-game checkpoint", flush=True)


def prepare(epoch, checkpoint_sha):
    frozen = OUT / "campaign-code.zip"
    if not frozen.exists():
        if (OUT / "submitted-00.json").exists():
            sent = parse((STAGE / "kernel.py").read_text())
            assignment = next(node for node in sent.body if isinstance(node, Assign)
                              and any(isinstance(t, Name) and t.id == "SOURCES" for t in node.targets))
            code = b64decode(literal_eval(assignment.value))
        else:
            package = BytesIO()
            with ZipFile(package, "w", ZIP_DEFLATED) as archive:
                for name in FILES:
                    archive.write(HERE / name, name)
            code = package.getvalue()
        temporary = frozen.with_suffix(".partial")
        temporary.write_bytes(code)
        temporary.replace(frozen)
    kernel = (HERE / "kaggle_kernel.py").read_text()
    for key, value in (("EPOCH", epoch), ("STATE_SHA", checkpoint_sha), ("SOURCES", b64encode(frozen.read_bytes()).decode())):
        kernel = kernel.replace(f"{key} = None\n", f"{key} = {value!r}\n", 1)
    STAGE.mkdir(parents=True, exist_ok=True)
    (STAGE / "kernel.py").write_text(kernel)
    title = "Zhanguo Autoregressive BC" if epoch == 0 else f"Zhanguo Autoregressive BC From {epoch:02}"
    remote.publish(STAGE / "kernel-metadata.json",
                   dict(id=kernel_ref(epoch), title=title, code_file="kernel.py",
                        language="python", kernel_type="script", is_private=True, enable_gpu=True,
                        enable_internet=False, dataset_sources=list(SOURCES), competition_sources=[],
                        kernel_sources=[kernel_ref(epoch - 2)] if epoch else []))


def submit(epoch, state):
    verified_sha = None if state is None else state["sha256"]
    if epoch:
        if state["checkpoint"] != f"epoch-{epoch:02}.pt" or remote.sha(Path(state["local_checkpoint"])) != verified_sha:
            raise ValueError("BC resume must use the verified local checkpoint identity")
    prepare(epoch, verified_sha)
    if remote.command(["kaggle", "kernels", "push", "-p", STAGE, "--accelerator", "NvidiaTeslaT4", "-t", "25200"],
                      OUT / f"submit-{epoch:02}.log", timeout=600):
        raise RuntimeError("Kaggle BC submission rejected")
    remote.publish(OUT / f"submitted-{epoch:02}.json", dict(epoch=epoch, sha256=verified_sha,
                   kernel=kernel_ref(epoch), predecessor=kernel_ref(epoch - 2) if epoch else None, time=time.time()))


def progress(epoch):
    kernel = kernel_ref(epoch)
    follower = None
    log = OUT / f"run-{epoch:02}-live.log"
    with log.open("ab", buffering=0) as stream:
        try:
            while True:
                current = status(kernel)
                if current == "RUNNING" and (follower is None or follower.poll() is not None):
                    follower = subprocess.Popen(["kaggle", "kernels", "logs", kernel, "-f"], stdout=stream, stderr=subprocess.STDOUT)
                observed = dict(state=current, verified_epoch=epoch, goal=10, kernel=kernel, time=time.time())
                production = False
                for line in log.read_text(errors="replace").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    kind = row.get("stage")
                    if kind == "bc_kernel_cuda":
                        observed["hardware"] = row
                    if kind == "bc_cuda_preflight_passed":
                        observed["preflight"] = row
                    if kind == "bc_resume_verified":
                        observed["resume"] = row
                    if kind == "bc_production_begin":
                        production = True
                    if production and kind in ("bc_actual_start", "bc_step", "bc_epoch_complete"):
                        observed["progress"] = row
                remote.publish(OUT / "live.json", observed)
                if current == "COMPLETE":
                    return
                if current in ("ERROR", "CANCELLED", "MISSING"):
                    raise RuntimeError(f"remote BC job ended with {current}")
                time.sleep(25)
        finally:
            if follower is not None and follower.poll() is None:
                follower.terminate()
                follower.wait(timeout=10)


def back_up(epoch):
    stop = min(epoch + 2, 10)
    stage = OUT / "segments" / f"{stop:02d}"
    pull_files(kernel_ref(epoch), stage, r"^work/(state\.json|epoch-\d+\.(json|pt))$", OUT / f"download-{stop:02}.log")
    work = stage / "work"
    state = json.loads((work / "state.json").read_text())
    if state["epoch"] != stop or state["preflight"]:
        raise ValueError("wrong BC stage output")
    for n in range(epoch + 1, stop + 1):
        checkpoint = json.loads((work / f"epoch-{n:02}.json").read_text())
        if n != checkpoint["epoch"] or remote.sha(work / checkpoint["checkpoint"]) != checkpoint["sha256"]:
            raise ValueError("BC stage checksum mismatch")
    path = work / state["checkpoint"]
    if remote.sha(path) != state["sha256"]:
        raise ValueError("BC resume state checksum mismatch")
    resume = dict(state, local_checkpoint=str(path), kernel=kernel_ref(epoch), time=time.time())
    remote.publish(OUT / "verified.json", resume)
    return resume


def main():
    OUT.mkdir(exist_ok=True)
    remote.OUT = OUT
    if sys.argv[1:] == ["--archive-old"]:
        while not (ROOT / "script-global-ppo-results" / "stopped.json").exists():
            try:
                save_ending_ppo()
            except (subprocess.TimeoutExpired, OSError, RuntimeError) as e:
                print(f"PPO archival pending: {type(e).__name__}", flush=True)
            if not (ROOT / "script-global-ppo-results" / "stopped.json").exists():
                time.sleep(30)
        return
    while True:
        verified = OUT / "verified.json"
        state = json.loads(verified.read_text()) if verified.exists() else None
        epoch = state["epoch"] if state is not None else 0
        if epoch == 10:
            remote.publish(OUT / "live.json", dict(state="complete", verified_epoch=10, checkpoint=state["local_checkpoint"],
                                                   validation=state["validation"], time=time.time()))
            return
        if not (OUT / f"submitted-{epoch:02}.json").exists():
            if status(OLD_KERNEL) in ("RUNNING", "QUEUED", "PENDING"):
                remote.publish(OUT / "live.json", dict(state="waiting_for_previous_gpu_segment", verified_epoch=epoch,
                                                       goal=10, time=time.time()))
                time.sleep(20)
                continue
            submit(epoch, state)
        progress(epoch)
        back_up(epoch)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        remote.publish(OUT / "failure.json", dict(error=type(exc).__name__, message=str(exc), time=time.time()))
        raise
