"""Schedule the paired evaluation once the actual BC10 checkpoint is verified."""

import argparse
from base64 import b64encode
from io import BytesIO
import json
from pathlib import Path
import subprocess
import time
from zipfile import ZIP_DEFLATED, ZipFile

from watch_bc import OUT as DEFAULT_BC, ROOT, SOURCES as BC_DATASETS, kernel_ref, pull_files, remote, status


HERE = Path(__file__).resolve().parent
KERNEL = "northwindlight/zhanguo-thought-budget-eval"
ARTIFACTS = r"^work/(evaluation\.json|value\.json|value-warm\.pt|live\.json|(heldout|value-train)/[^/]+\.json)$"


def wait_parent(args):
    verified = args.bc_results / "verified.json"
    while True:
        epoch = 0
        if verified.exists():
            record = json.loads(verified.read_text())
            epoch = record["epoch"]
            if epoch == 10 and not record["preflight"]:
                if remote.sha(Path(record["local_checkpoint"])) != record["sha256"]:
                    raise ValueError("BC10 parent checkpoint hash mismatch")
                return record
        remote.publish(args.output / "live.json",
                       dict(state="waiting_for_verified_bc10", verified_epoch=epoch, time=time.time()))
        time.sleep(30)


def source_bundle(extra=()):
    bundle = BytesIO()
    with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "cpp").iterdir()):
            if path.suffix in (".cpp", ".hpp", ".py") or path.name == "CMakeLists.txt":
                archive.write(path, path.relative_to(ROOT))
        for name in ("evaluate_thought.py","native_eval.py","rollout_trace.py","batch_policy.py","model.py","history.py",
                     "memory_map.py","bc_train.py",*extra):
            archive.write(HERE / name, f"analysis/autoregressive-policy/{name}")
    return bundle.getvalue()


def package(stage, parent):
    template = (HERE / "kaggle_thought_eval.py").read_text()
    code = template.replace("CHECKPOINT_SHA = None", f"CHECKPOINT_SHA = {parent['sha256']!r}", 1)
    code = code.replace("SOURCES = None", f"SOURCES = {b64encode(source_bundle()).decode()!r}", 1)
    compile(code, str(stage / "kernel.py"), "exec")
    (stage / "kernel.py").write_text(code)
    remote.publish(stage / "kernel-metadata.json", dict(id=KERNEL, title="Zhanguo Thought Budget Eval",
        code_file="kernel.py", language="python", kernel_type="script", is_private=True, enable_gpu=True,
        enable_internet=False, dataset_sources=[BC_DATASETS[1]], competition_sources=[],
        kernel_sources=[parent.get("kernel",kernel_ref(8))]))


def watch(args, parent):
    log = args.output / "run.log"
    follower = None
    with log.open("ab", buffering=0) as stream:
        try:
            while True:
                current = status(KERNEL)
                if current == "RUNNING" and (follower is None or follower.poll() is not None):
                    follower = subprocess.Popen(["kaggle","kernels","logs",KERNEL,"-f"],stdout=stream,stderr=subprocess.STDOUT)
                record=dict(state=current,parent_sha256=parent["sha256"],kernel=KERNEL,time=time.time())
                games={}
                for line in log.read_text(errors="replace").splitlines():
                    try:
                        row=json.loads(line)
                    except ValueError:
                        continue
                    tag = row.get("stage")
                    if tag == "thought_eval_cuda_ready":
                        record["hardware"] = row["devices"]
                    elif tag == "thought_game_complete":
                        games[(row["group"],row["mode"],row["seed"])] = row
                record["games_complete"] = len(games)
                record["games_total"] = 24
                remote.publish(args.output / "live.json",record)
                if current == "COMPLETE":
                    return
                if current in ("ERROR","CANCELLED","MISSING"):
                    raise RuntimeError(f"paired evaluation ended with {current}")
                time.sleep(25)
        finally:
            if follower is not None and follower.poll() is None:
                follower.terminate()
                try:
                    follower.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    follower.kill()
                    follower.wait()


def main(args):
    args.output.mkdir(parents=True,exist_ok=True)
    parent=wait_parent(args)
    stage=args.output/"kernel"
    stage.mkdir(exist_ok=True)
    submitted=args.output/"submitted.json"
    if not submitted.exists():
        package(stage,parent)
        if remote.command(["kaggle","kernels","push","-p",stage,"--accelerator","NvidiaTeslaT4","-t","21600"],
                          args.output/"submit.log",timeout=600):
            raise RuntimeError("paired evaluation submission rejected")
        remote.publish(submitted,dict(kernel=KERNEL,parent_sha256=parent["sha256"],time=time.time()))
    if json.loads(submitted.read_text())["parent_sha256"] != parent["sha256"]:
        raise ValueError("queued evaluation has a different parent checkpoint")
    watch(args,parent)
    results=args.output/"download"
    pull_files(KERNEL,results,ARTIFACTS,args.output/"download.log")
    work=results/"work"
    eval_report=json.loads((work/"evaluation.json").read_text())
    value=json.loads((work/"value.json").read_text())
    if (eval_report["identity"]["parent_sha256"] != parent["sha256"]
        or value["parent_sha256"] != parent["sha256"]
        or eval_report["maps"] != 4
        or set(eval_report["modes"]) != {"0","8","30","8-kv"}):
        raise ValueError("eval report belongs to another checkpoint or protocol")
    if value["accepted"] and remote.sha(work/value["checkpoint"]) != value["sha256"]:
        raise ValueError("value-only checkpoint hash mismatch")
    remote.publish(args.output/"verified.json",dict(state="complete",parent_sha256=parent["sha256"],
                   evaluation=eval_report,value=value,time=time.time()))
    remote.publish(args.output/"live.json",dict(state="complete",verified=str(args.output/"verified.json"),time=time.time()))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--bc-results",type=Path,default=DEFAULT_BC)
    parser.add_argument("--output",type=Path,required=True)
    arguments=parser.parse_args()
    try:
        main(arguments)
    except Exception as error:
        remote.publish(arguments.output/"failure.json",dict(error=type(error).__name__,message=str(error),time=time.time()))
        raise
