#!/usr/bin/env python3
"""Run the locked frozen-policy evaluation on an existing two-GPU training host."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys

from country_gate import save


def main():
    candidate, checkpoint, result = map(Path, sys.argv[1:])
    cfg = json.loads(Path(os.environ["DEALER_HOLDOUT_CONFIG"]).read_text())
    meta = json.loads(candidate.read_text())
    remote = PurePosixPath(cfg["spool"])/meta["sha256"]
    source = Path(__file__).resolve().parent

    def run(cmd, **kwargs):
        return subprocess.run([*cfg["ssh"], cmd], **kwargs)

    def put(local, dest):
        temp = str(dest)+".upload-"+str(os.getpid())
        with local.open("rb") as f:
            run("cat > "+shlex.quote(temp)+" && "+shlex.join(["mv","--",temp,str(dest)]),
                stdin=f, check=True)

    run(shlex.join(["mkdir","-p",str(remote)]),check=True)
    pinned = remote/"country.pt"
    existing = PurePosixPath(cfg["work"])/f"games-{meta['behavior_games']:04}.pt"
    link = (shlex.join(["test","-f",str(pinned)])+" || "+
            shlex.join(["ln","--",str(existing),str(pinned)]))
    if run(link).returncode:
        put(checkpoint,pinned)
    put(candidate,remote/"candidate.json")
    for f in ("country_gate.py","country_holdout.py"):
        put(source/f, remote/f)

    def rank(i):
        cmd=shlex.join(["env","OPENBLAS_NUM_THREADS=1","OMP_NUM_THREADS=2",
            "PYTHONPATH="+cfg["legacy_python"],cfg["python"],"-Bu",str(remote/"country_holdout.py"),
            str(remote/"candidate.json"),str(pinned),cfg["library"],cfg["policy_source"],
            str(remote/"scores"),"--rank",str(i)])
        run(cmd,check=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(rank, (0,1)))
    pairfiles=[str(remote/"scores"/f"pair-{i:02}.json") for i in range(0,32,2)]
    # Stream the array as a single parseable JSON document.
    program="import json,sys;print(json.dumps([json.load(open(p)) for p in sys.argv[1:]]))"
    pairs=json.loads(run(shlex.join([cfg["python"],"-c",program,*pairfiles]),
                        capture_output=True,text=True,check=True).stdout)
    identity=pairs[0]["identity"]
    if any(pair["identity"]!=identity for pair in pairs):
        raise ValueError("Mixed holdout sources")
    save(result,{**identity,"seeds":[s for pair in pairs for s in pair["seeds"]],
                 "final_nominal_gdp_by_seat":[s for pair in pairs for s in pair["seats"]]})


if __name__=="__main__":
    main()
