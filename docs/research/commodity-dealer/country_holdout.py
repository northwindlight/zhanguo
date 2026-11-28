"""Score one immutable 18M country checkpoint in fresh legacy-engine games."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import sys

from country_gate import save, sha


def main():
    p=argparse.ArgumentParser()
    for arg in ("candidate","checkpoint","library","policy_source","output"):
        p.add_argument(arg,type=Path)
    p.add_argument("--rank",type=int,choices=(0,1),required=True)
    args=p.parse_args()
    sys.path.insert(0,str(args.policy_source))
    import torch
    from batched_rollout import rollout_worlds
    from history import DeltaStepGraph
    from model import Policy
    from rl import obs as legacy_obs

    meta=json.loads(args.candidate.read_text())
    if sha(args.checkpoint)!=meta["sha256"]:
        raise ValueError("Remote candidate checksum mismatch")
    args.output.mkdir(parents=True,exist_ok=True)
    rank=args.rank
    # Keep shard pairs when the node or its tunnel is interrupted.
    identity = dict(sha256=meta["sha256"],seeds_start=meta["seeds_start"],size=24,turns=100,
                    library_sha256=sha(args.library), evaluator_sha256=sha(Path(__file__)))
    files=("batched_rollout.py","thought_ppo.py","rollout_trace.py","model.py","memory_map.py",
           "native_eval.py","history.py","replay.py")
    identity["country_source"]=hashlib.sha256(b"".join((args.policy_source/f).read_bytes() for f in files)).hexdigest()
    identity["collate_sha256"] = sha(Path(legacy_obs.__file__))
    identity["torch"] = str(torch.__version__)
    with (args.output/f".rank-{rank}.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        torch.set_num_threads(2)
        torch.backends.mha.set_fastpath_enabled(False)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.cuda.set_device(rank)
        state=torch.load(args.checkpoint,map_location="cpu",weights_only=True,mmap=True)
        if state["games"]!=meta["behavior_games"] or state["initial_sha256"]!=meta["lineage"]:
            raise ValueError("Country checkpoint metadata mismatch")
        policy=Policy().to(f"cuda:{rank}").eval()
        if any(state["contract"].get(k) != v for k, v in {"size": 24, "turns": 100, "seats": 4}.items()):
            raise ValueError("Unexpected country game contract")
        policy.load_state_dict(state["model"])
        del state
        policy.requires_grad_(False)
        policy.sampling_delta_graph = DeltaStepGraph(policy.delta)
        for i in range(rank*2,32,4):
            target=args.output/f"pair-{i:02}.json"
            seeds=[meta["seeds_start"]+i,meta["seeds_start"]+i+1]
            if target.exists():
                old=json.loads(target.read_text())
                if old["identity"]!=identity or old["seeds"]!=seeds:
                    raise ValueError("Held-out pair has different provenance")
                continue
            with torch.inference_mode():
                worlds=rollout_worlds(policy,args.library,seeds,24,100,0.)
                rows=[]
                for index, seed in enumerate(seeds):
                    metrics = worlds[index][1]
                    if metrics["seed"] != seed or metrics["turns"] != 100:
                        raise ValueError("Incomplete country rollout")
                    rows.append([s["nominal_gdp"] for s in metrics["seats"]])
                del worlds
            save(target,dict(identity=identity,seeds=seeds,seats=rows))


if __name__=="__main__":
    main()
