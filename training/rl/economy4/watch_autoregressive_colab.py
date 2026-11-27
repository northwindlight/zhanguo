"""Select the ready BC provider and back up each Colab epoch by checksum."""
import json
from pathlib import Path
from shutil import which
import subprocess
import sys
import time

import stage_autoregressive_colab as stage
import watch_script_bc_colab as transport

ROOT = stage.ROOT
OUT = stage.OUT
SRC = ROOT / "analysis/autoregressive-policy"
sys.path.insert(0, str(SRC))
from watch_thought_eval import source_bundle

CANONICAL = ROOT / "autoregressive-bc-results"
EVAL = ROOT / "autoregressive-thought-eval-results"
REMOTE = "/content/zhanguo-autoregressive-bc"
KAGGLE_WATCH = "zhanguo-autoregressive-bc-watch.service"
EVAL_WATCH = "zhanguo-autoregressive-thought-eval.service"
transport.OUT, transport.SESSION = OUT, stage.SESSION


def cli(command, *args, **kwargs):
    return transport.command([command, "-s", stage.SESSION, *args], **kwargs)


def remote_json(source):
    output = cli("exec", "--timeout", 45, input=source, timeout=65)
    for line in reversed(output.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("No Colab status response")


def publish(name, **row):
    transport.publish(OUT / name, dict(**row, time=time.time()))


def current_parent():
    return json.loads((CANONICAL / "verified.json").read_text())


def kaggle_status(epoch):
    job = f"northwindlight/zhanguo-autoregressive-bc-from-{epoch:02}"
    kaggle = which("kaggle") or str(Path.home() / ".local/bin/kaggle")
    result = subprocess.run([kaggle, "kernels", "status", job], capture_output=True,
                            text=True, timeout=35)
    for status in ("RUNNING", "COMPLETE", "QUEUED", "ERROR", "CANCELLED"):
        if result.returncode == 0 and f"KernelWorkerStatus.{status}" in result.stdout:
            return status
    return "UNKNOWN"


def local_services(action):
    subprocess.run(["systemctl", "--user", action, KAGGLE_WATCH, EVAL_WATCH], check=True)


def prepare_sources():
    transfer = OUT / "transfer"
    thought = transfer / "autoregressive-thought-eval.zip"
    thought.write_bytes(source_bundle())
    trainer = SRC / "bc_train.py"
    config = transfer / "autoregressive-colab-config.json"
    transport.publish(config, dict(thought_sha256=transport.digest(thought),
                                   trainer_sha256=transport.digest(trainer)))
    for file, name in ((trainer, "autoregressive-colab-bc-train.py"), (thought, thought.name),
                       (config, config.name), (ROOT / "autoregressive_colab_worker.py",
                                              "autoregressive_colab_worker.py")):
        transport.upload(file, name)


def launch(mode):
    source = f"""
import json, os, subprocess, sys
from pathlib import Path
p = Path({REMOTE!r})
p.mkdir(exist_ok=True)
with (p / ({mode!r} + ".log")).open("ab") as f:
    proc = subprocess.Popen([sys.executable, "-u", "/content/autoregressive_colab_worker.py", {mode!r}],
                            stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                            start_new_session=True)
print(json.dumps(dict(pid=proc.pid)))
"""
    return remote_json(source)["pid"]


def probe():
    return remote_json(f"""
from pathlib import Path
import json
p = Path({REMOTE!r})
work = p / "work"
result = {{}}
for key in ("preflight", "preflight-failure", "production-failure", "state", "done"):
    path = work / (key + ".json")
    if path.is_file():
        result[key] = json.loads(path.read_text())
for filename in ("bc", "evaluation"):
    path = work / (filename + ".log")
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.startswith("{{"):
                row = json.loads(line)
                if row.get("stage") in ("bc_actual_start", "bc_step", "bc_epoch_complete", "thought_game_complete"):
                    result[filename] = row
print(json.dumps(result))
""")


def download(directory, name, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".download")
    try:
        cli("download", f"{REMOTE}/{directory}/{name}", temp, timeout=150)
    except (RuntimeError, subprocess.TimeoutExpired):
        if target.suffix != ".pt":
            raise
        pieces = remote_json(f"""
from pathlib import Path
import json
source = Path({f"{REMOTE}/{directory}/{name}"!r})
parts = source.parent / ("parts-" + source.stem)
parts.mkdir(exist_ok=True)
with source.open("rb") as f:
    index = 0
    while data := f.read(8 * 1024 * 1024):
        (parts / str(index)).write_bytes(data)
        index += 1
print(json.dumps(dict(count=index)))
""")["count"]
        with temp.open("wb") as output:
            part = temp.with_suffix(".part")
            for index in range(pieces):
                cli("download", f"{REMOTE}/{directory}/parts-{Path(name).stem}/{index}", part,
                    timeout=120)
                output.write(part.read_bytes())
            part.unlink()
    temp.replace(target)


def back_up(epoch):
    work = OUT / "work"
    report_name = f"epoch-{epoch:02}.json"
    download("work", report_name, work / report_name)
    report = json.loads((work / report_name).read_text())
    name = f"epoch-{epoch:02}.pt"
    if report["epoch"] != epoch or report["checkpoint"] != name or report["preflight"]:
        raise ValueError("Wrong Colab epoch output")
    path = work / name
    if not path.exists() or transport.digest(path) != report["sha256"]:
        download("work", name, path)
    if transport.digest(path) != report["sha256"]:
        raise ValueError("Colab epoch checkpoint failed verification")
    verified = dict(report, backend="colab", local_checkpoint=str(path), time=time.time())
    transport.publish(OUT / "verified.json", verified)
    return verified


def finish_evaluation(parent):
    work = EVAL / "download/work"
    for name in ("evaluation.json", "value.json"):
        download("evaluation", name, work / name)
    evaluation = json.loads((work / "evaluation.json").read_text())
    value = json.loads((work / "value.json").read_text())
    if (evaluation["identity"]["parent_sha256"] != parent["sha256"] or
            value["parent_sha256"] != parent["sha256"] or evaluation["maps"] != 4 or
            set(evaluation["modes"]) != {"0", "8", "30", "8-kv"}):
        raise ValueError("Wrong Colab evaluation protocol or checkpoint")
    if value["accepted"]:
        name = value["checkpoint"]
        download("evaluation", name, work / name)
        if transport.digest(work / name) != value["sha256"]:
            raise ValueError("Colab warm value checkpoint checksum mismatch")
    verified = EVAL / "verified.json"
    transport.publish(verified, dict(state="complete", backend="colab", parent_sha256=parent["sha256"],
                                    evaluation=evaluation, value=value, time=time.time()))
    transport.publish(EVAL / "live.json", dict(state="complete", backend="colab",
                                              verified=str(verified), time=time.time()))


def select_kaggle(status, epoch):
    publish("handoff.json", state="kaggle_selected", verified_epoch=epoch, kaggle_status=status)
    cli("stop", timeout=90)


def main():
    staged = OUT / "staged.json"
    prepare_sources()
    while not staged.exists():
        if not (OUT / "transfer").exists():
            raise RuntimeError("Colab transfer was not initialized")
        if subprocess.run(["systemctl", "--user", "is-failed",
                           "zhanguo-autoregressive-colab-transfer.service"],
                          capture_output=True).returncode == 0:
            raise RuntimeError("Colab upload service failed")
        time.sleep(10)
    parent_epoch = json.loads(staged.read_text())["parent_epoch"]
    status = kaggle_status(parent_epoch)
    if status in ("RUNNING", "COMPLETE") or current_parent()["epoch"] > parent_epoch:
        select_kaggle(status, parent_epoch)
        return
    launch("preflight")
    publish("live.json", state="gpu_preflight", verified_epoch=parent_epoch)
    deadline = time.monotonic() + 600
    while True:
        remote = probe()
        if "preflight-failure" in remote:
            raise RuntimeError("Colab preflight failed: " + json.dumps(remote["preflight-failure"]))
        if "preflight" in remote:
            break
        if time.monotonic() > deadline:
            raise TimeoutError("Colab preflight did not finish within ten minutes")
        time.sleep(12)
    status = kaggle_status(parent_epoch)
    if status in ("RUNNING", "COMPLETE") or current_parent()["epoch"] > parent_epoch:
        select_kaggle(status, parent_epoch)
        return
    local_services("stop")
    try:
        launch("production")
        publish("handoff.json", state="colab_selected", verified_epoch=parent_epoch,
                kaggle_status=status, preflight=remote["preflight"])
    except Exception:
        local_services("start")
        raise

    backed = parent_epoch
    verified = None
    while True:
        try:
            remote = probe()
            remote_epoch = remote.get("state", {}).get("epoch", parent_epoch)
            for epoch in range(backed + 1, remote_epoch + 1):
                verified = back_up(epoch)
                backed = epoch
            if backed == 10 and verified is not None:
                transport.publish(CANONICAL / "verified.json", verified)
                transport.publish(CANONICAL / "live.json", dict(state="complete", backend="colab",
                    verified_epoch=10, checkpoint=verified["local_checkpoint"],
                    validation=verified["validation"], time=time.time()))
                verified = None
            publish("live.json", state="evaluation" if backed == 10 else "bc",
                    verified_epoch=backed, remote_epoch=remote_epoch, goal=10,
                    progress=remote.get("evaluation", remote.get("bc")))
            if "production-failure" in remote:
                raise ValueError("Colab worker failed: " + json.dumps(remote["production-failure"]))
            if "done" in remote:
                finish_evaluation(current_parent())
                publish("live.json", state="complete", verified_epoch=10)
                cli("stop", timeout=90)
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            publish("connection-retry.json", verified_epoch=backed)
        time.sleep(35)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        publish("watch-failure.json", error=type(error).__name__, message=str(error))
        raise
