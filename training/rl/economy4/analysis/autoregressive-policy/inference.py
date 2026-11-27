"""Run one native game using the economy4 autoregressive policy."""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import torch

from model import Policy
from native_eval import Engine
from rollout_trace import SeatTrace


def load_policy(checkpoint: Path, *, backend: str = "cpu", vulkan_device: int | None = None):
    """Load an exported model or a production checkpoint with the original weight schema."""
    if backend == "cpu":
        if vulkan_device is not None:
            raise ValueError("GPU selection requires the Vulkan backend")
        policy = Policy()
    elif backend == "vulkan":
        from vulkan_inference import VulkanAttention, VulkanPolicy

        policy = VulkanPolicy(VulkanAttention(vulkan_device))
    else:
        raise ValueError(f"Unknown backend: {backend}")

    payload = torch.load(checkpoint, weights_only=True, map_location="cpu", mmap=True)
    if not isinstance(payload, dict):
        raise ValueError("Expected a state dictionary or a checkpoint with a model field")
    policy.load_state_dict(payload.get("model", payload), strict=True)
    return policy.eval().requires_grad_(False)


def _choose(logits, rng, selection):
    if selection == "greedy":
        return int(logits.argmax().item())
    return int(torch.multinomial(logits.softmax(-1), 1, generator=rng).item())


def _compare(got, expected):
    if got.shape != expected.shape or not torch.equal(got.isneginf(), expected.isneginf()):
        raise AssertionError("Inference backends disagree on tensor shape or legal masks")
    legal = ~expected.isneginf()
    got, expected = got[legal], expected[legal]
    if not got.isfinite().all() or not expected.isfinite().all():
        raise AssertionError("Inference produced a nonfinite score for a legal option")
    torch.testing.assert_close(got, expected, atol=1e-4, rtol=1e-4)
    return float((got - expected).abs().max().item()) if got.numel() else 0.0


@torch.inference_mode()
def play_match(policy, engine, *, seed, size, turns, thought_budget, selection, reference=None):
    """Drive the native engine with one policy and optionally score the same path on CPU."""
    trace = SeatTrace(policy, thought_budget=thought_budget)
    trace.recording = reference is not None
    check = None if reference is None else SeatTrace(reference, thought_budget=thought_budget)
    action_rng = torch.Generator().manual_seed(seed)
    thought_rng = torch.Generator().manual_seed(seed ^ 0x3D9012)
    differences = {"thought_logits": 0.0, "action_logits": 0.0, "value": 0.0}
    last_turn, thoughts, greedy_mismatches = 0, 0, 0
    start = perf_counter()

    with engine.match(seed, size, turns) as game:
        result = game.result()
        while not result.done:
            turn, new_turn, obs = game.observe()
            if new_turn != (turn != last_turn):
                raise RuntimeError("Engine turn marker disagrees with the inference trace")
            for seat in (trace,) if check is None else (trace, check):
                if last_turn and new_turn:
                    seat.next_turn()
                seat.observe(result.decisions, obs)
            last_turn = turn

            while not trace.thought.closed:
                logits = trace.score_thought()
                if check is not None:
                    cpu_logits = check.score_thought()
                    differences["thought_logits"] = max(differences["thought_logits"], _compare(logits, cpu_logits))
                    greedy_mismatches += int(logits.argmax() != cpu_logits.argmax())
                operation = _choose(logits, thought_rng, selection)
                trace.commit_thought(operation)
                if check is not None:
                    check.commit_thought(operation)
                thoughts += 1

            logits, value = trace.score_game()
            if check is not None:
                cpu_logits, cpu_value = check.score_game()
                differences["action_logits"] = max(differences["action_logits"], _compare(logits, cpu_logits))
                differences["value"] = max(differences["value"], _compare(value, cpu_value))
                greedy_mismatches += int(logits.argmax() != cpu_logits.argmax())
            action = _choose(logits[0], action_rng, selection)
            trace.commit_game(action)
            if check is not None:
                check.commit_game(action)
            result = game.play(action)
    report = result.as_dict()
    report.update(thoughts=thoughts, seconds=round(perf_counter() - start, 4))
    if check is not None:
        report["cpu_comparison"] = {"max_abs_error": differences, "argmax_mismatches": greedy_mismatches}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-sha256")
    parser.add_argument("--library", type=Path, required=True, help="Native libzhanguo_eval.so")
    parser.add_argument("--backend", choices=("cpu", "vulkan"), default="cpu")
    parser.add_argument("--vulkan-device", type=int, help="ncnn Vulkan device index")
    parser.add_argument("--compare-cpu", action="store_true", help="Verify Vulkan scores against CPU on identical actions")
    parser.add_argument("--seed", type=int, default=640020)
    parser.add_argument("--size", type=int, default=24)
    parser.add_argument("--turns", type=int, default=1)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--thought-budget", type=int, choices=(0, 8, 30), help="Use a fixed budget instead of adaptive thought")
    parser.add_argument("--selection", choices=("sample", "greedy"), default="sample")
    args = parser.parse_args()
    if args.turns < 1 or args.size < 1 or args.threads < 1 or not 0 <= args.seed < 2**64:
        parser.error("turns, size and threads must be positive; seed must fit in an unsigned 64-bit integer")
    if args.backend != "vulkan" and (args.vulkan_device is not None or args.compare_cpu):
        parser.error("--vulkan-device and --compare-cpu require --backend vulkan")

    with args.checkpoint.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if args.checkpoint_sha256 is not None and args.checkpoint_sha256.lower() != checksum:
        parser.error("checkpoint SHA-256 does not match")

    torch.set_num_threads(args.threads)
    policy = load_policy(args.checkpoint, backend=args.backend, vulkan_device=args.vulkan_device)
    reference = load_policy(args.checkpoint) if args.compare_cpu else None
    report = play_match(policy, Engine(args.library), seed=args.seed, size=args.size, turns=args.turns,
                        thought_budget=args.thought_budget, selection=args.selection, reference=reference)
    report.update(backend=args.backend, checkpoint_sha256=checksum, seed=args.seed, size=args.size,
                  thought_limit=policy.thought_position.num_embeddings - 1, selection=args.selection)
    if args.backend == "vulkan":
        report.update(vulkan_device=policy.attention.device_name, vulkan_attention_calls=policy.attention.calls)
        if report["decisions"] and not policy.attention.calls:
            raise RuntimeError("The match completed without executing Vulkan attention")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
