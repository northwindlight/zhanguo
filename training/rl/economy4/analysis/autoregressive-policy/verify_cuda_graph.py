"""Compare CUDA graph and eager sampling on identical native worlds and checkpoint."""
import argparse
import json
from pathlib import Path
import time

import torch

from batched_rollout import rollout_worlds
from history import DeltaStepGraph, delta_stack
from model import Policy


@torch.inference_mode()
def main(checkpoint, library, turns):
    torch.set_num_threads(2)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.mha.set_fastpath_enabled(False)
    weights = torch.load(checkpoint, mmap=True, map_location="cpu", weights_only=True)["model"]
    policy = Policy().cuda().eval()
    policy.load_state_dict(weights)
    graph = DeltaStepGraph(policy.delta)
    x = torch.randn_like(graph.input)
    _, previous = delta_stack(policy.delta, x, None)
    for changed in (False, True):
        if changed:
            policy.delta[0].qkv.weight.add_(.001)
        expected, ref = delta_stack(policy.delta, x, previous)
        actual, states = graph(x, previous)
        graph(x + 1, previous)  # Earlier snapshots must survive the next seat's replay.
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)
        for a, b in zip(states, ref, strict=True):
            torch.testing.assert_close(a.matrix, b.matrix, atol=0, rtol=0)
            torch.testing.assert_close(a.conv_tail, b.conv_tail, atol=0, rtol=0)
    policy.load_state_dict(weights)
    reference = None
    for enabled in (False, True, True, False):
        policy.sampling_delta_graph = graph if enabled else None
        torch.cuda.synchronize()
        start = time.monotonic()
        runs = rollout_worlds(policy, library, [720104, 720109], 24, turns)
        torch.cuda.synchronize()
        seconds = time.monotonic() - start
        rows = []
        for windows, metrics in runs:
            decisions = [[(type(r).__name__, getattr(r, "kind", None), getattr(r, "operation", None),
                           getattr(r, "index", None), getattr(r, "logp", None), getattr(r, "value", None))
                          for r in w.records] for w in windows]
            rows.append((decisions, {k: v for k, v in metrics.items() if k != "minutes"}))
        if reference is None:
            reference = rows
        assert rows == reference, "Native trajectories, log probabilities, values or results differ"
        print(json.dumps(dict(graph=enabled, seconds=seconds,
                              decisions=sum(r["game_decisions"] + r["thoughts"] for _, r in runs))), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--turns", type=int, default=3)
    args = parser.parse_args()
    main(args.checkpoint, args.library, args.turns)
