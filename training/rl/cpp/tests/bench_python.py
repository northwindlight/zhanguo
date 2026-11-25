"""Measure the original Python worker/policy loop on the same fixed seed pool."""

import argparse
from multiprocessing.connection import wait
from pathlib import Path
import sys
import time


def worker(pipe, index, size, turns):
    from rl.actors import episode

    with pipe:
        while (seed := pipe.recv()) is not None:
            if not episode(pipe, index, seed, size, turns, (0.25, 0.30, 0.25, 0.20)):
                return


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("checkpoint")
    p.add_argument("--python-root", type=Path, default=Path.home() / "zhanguo-rl")
    p.add_argument("--games", type=int, default=100)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--size", type=int, default=13)
    p.add_argument("--turns", type=int, default=60)
    p.add_argument("--seed", type=int, default=7001)
    p.add_argument("--device", default="cuda")
    p.add_argument("--fp32", action="store_true")
    opts = p.parse_args()
    sys.path.insert(0, str(opts.python_root))

    import multiprocessing as mp

    ctx = mp.get_context("fork")
    conns, children = [], []
    for i in range(min(opts.games, opts.workers)):
        parent, child = ctx.Pipe()
        process = ctx.Process(target=worker, args=(child, i, opts.size, opts.turns), daemon=True)
        process.start()
        child.close()
        conns.append(parent)
        children.append(process)

    import numpy as np
    import torch
    from rl.model import Policy
    from rl.obs import collate

    torch.set_num_threads(1)
    torch.manual_seed(opts.seed)
    device = torch.device(opts.device)
    ckpt = torch.load(opts.checkpoint, map_location="cpu", weights_only=True)
    model = Policy(**ckpt["cfg"])
    model.load_state_dict(ckpt["model"])
    model.to(device).eval()
    torch.backends.mha.set_fastpath_enabled(False)
    hidden = {}
    seeds = iter(range(opts.seed, opts.seed + opts.games))
    current_seeds = {}
    total, reward, complete = 0, 0.0, 0
    start = time.perf_counter()
    for i, conn in enumerate(conns):
        current_seeds[i] = next(seeds)
        hidden[i] = torch.zeros(model.mem_h, device=device)
        conn.send(current_seeds[i])
    try:
        with torch.inference_mode():
            while conns:
                reqs = []
                ready = wait(conns, timeout=30)
                if not ready:
                    raise RuntimeError("Python environment stalled")
                for conn in ready:
                    msg = conn.recv()
                    if msg[0] == "req":
                        reqs.append((conn, msg[1], msg[2], msg[3]))
                        continue
                    _, i, summary = msg
                    total += summary["decisions"]
                    reward += summary["reward"]
                    complete += 1
                    print(f"game seed={current_seeds[i]} opp={summary['opp']} seat={summary['seat']} "
                          f"winner={summary['winner'] if summary['winner'] is not None else -1} "
                          f"turns={summary['turns']} reward={summary['reward']} decisions={summary['decisions']} "
                          f"invalid={summary['invalid']}", flush=True)
                    nxt = next(seeds, None)
                    if nxt is None:
                        conn.send(None)
                        conn.close()
                        conns.remove(conn)
                    else:
                        current_seeds[i] = nxt
                        hidden[i].zero_()
                        conn.send(nxt)
                if not reqs:
                    continue
                new = [(i, meta["xt"]) for _, i, _, meta in reqs if meta["xt"] is not None]
                if new:
                    x = torch.from_numpy(np.stack([value for _, value in new])).to(device)
                    h = model.mem_step(x, torch.stack([hidden[i] for i, _ in new]))
                    for (i, _), row in zip(new, h, strict=True):
                        hidden[i] = row
                batch = {k: torch.from_numpy(v).to(device) for k, v in collate([r[2] for r in reqs]).items()}
                batch["cand_ptr"] = batch["cand_ptr"].long()
                batch["mem"] = torch.stack([hidden[i] for _, i, _, _ in reqs])
                with torch.autocast(device.type, dtype=torch.bfloat16, enabled=device.type == "cuda" and not opts.fp32):
                    logits, _, _ = model(batch)
                prob = torch.softmax(logits.float(), -1)
                choice = torch.multinomial(prob, 1).squeeze(1).tolist()
                for (conn, _, _, _), picked in zip(reqs, choice, strict=True):
                    conn.send(picked)
    finally:
        for child in children:
            child.join(timeout=1)
            if child.is_alive():
                child.terminate()
                child.join()
    elapsed = time.perf_counter() - start
    print(f"completed={complete} decisions={total} seconds={elapsed:.6f} "
          f"decisions_per_second={total / elapsed:.6f} reward_mean={reward / complete:.6f}", flush=True)


if __name__ == "__main__":
    main()
