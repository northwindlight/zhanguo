"""Adaptive thought PPO on four-country native dynamic self-play."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import argparse
import copy
import math
from pathlib import Path
import random
import statistics
import time

import torch
from torch.nn import functional as F

from bc_train import atomic_save, emit, publish, sha
from evaluate_thought import SCALE, sample, territory_gdp
from model import Policy
from native_eval import Engine
from replay import max_errors, move_history, replay_turn, replay_turn_stepwise
from rollout_trace import GameRecord, SeatTrace

CONTRACT = dict(thought_budget=[8, 30], retain_thought_kv=False, actor_observation_gdp="nominal_only",
                reward="nominal_gdp+territory_holding", reward_scale=SCALE,
                clock="global_events", gamma=1., gae_lambda=1., game_sequences=[2, 4],
                replay="turn_truncated_with_behavior_prefix", seats=4, shared_policy=True,
                precision="fp32")


@dataclass
class Window:
    header: object
    records: list
    observations: dict
    history: object
    graph: object
    game_records: frozenset
    targets: dict
    stepwise: bool = False


def training_windows(trace, starts, returns, rng):
    result = []
    for start, stop, history, graph in starts:
        records = trace.records[start:stop]
        games = [i for i, r in enumerate(records) if isinstance(r, GameRecord)]
        if not games:
            continue
        count = min(len(games), rng.randint(2, 4))
        offset = rng.randrange(len(games) - count + 1)
        selected = frozenset(games[offset:offset + count])
        records = records[:max(selected) + 2]
        revisions = {r.revision for r in records if hasattr(r, "revision")}
        observations = {rev: trace.observations[rev] for rev in revisions}
        result.append(Window(trace.header, records, observations, history, graph, selected,
                             {i: returns[start+i] for i in range(len(records)) if start+i in returns}))
    return result


def returns_from_events(events):
    """Reverse the global event clock, with an independent reward channel per country."""
    returns, accumulated = [{}, {}, {}, {}], [0., 0., 0., 0.]
    for kind, data in reversed(events):
        if kind == "settlement":
            for seat, row in enumerate(data):
                accumulated[seat] += (row["nominal_gdp"] +
                                      territory_gdp(max(0, row["land"] - row["initial_land"]))) / SCALE
        else:
            seat, record = data
            if record in returns[seat]:
                raise ValueError("Duplicate self-play decision in global timeline")
            returns[seat][record] = accumulated[seat]
    return returns


def rollout(policy, library, seed, size, turns, samples_per_seat):
    start_time = time.monotonic()
    rng = random.Random(seed)
    chosen = [set(rng.sample(range(1, turns + 1), min(samples_per_seat, turns))) for _ in range(4)]
    traces = [SeatTrace(policy) for _ in range(4)]
    starts = [[] for _ in traces]
    last_turn, open_window = [0] * 4, [None] * 4
    device = traces[0].device
    streams = [torch.Generator(device=device).manual_seed(seed * 16 + i) for i in range(8)]
    events = []
    thought_stops, forced_stops, thoughts, games = 0, 0, 0, 0
    period = 0
    with torch.inference_mode(), Engine(library).self_play(seed, size, turns) as world:
        while (view := world.observe()) is not None:
            seat, turn, new_turn, obs = view
            trace = traces[seat]
            if new_turn != (turn != last_turn[seat]):
                raise ValueError("Native self-play turn marker mismatch")
            if new_turn:
                if open_window[seat] is not None:
                    start, history, graph = open_window[seat]
                    starts[seat].append((start, len(trace.records), history, graph))
                    open_window[seat] = None
                if last_turn[seat]:
                    trace.next_turn()
                last_turn[seat] = turn
                if turn in chosen[seat]:
                    open_window[seat] = (len(trace.records), move_history(trace.history, "cpu"),
                                         trace.thought.graph)
            trace.observe(len(events), obs)
            while not trace.thought.closed:
                logits = trace.score_thought()
                chosen_op = sample(logits, streams[seat + 4])
                event = len(trace.records)
                trace.commit_thought(chosen_op)
                events.append(("thought", (seat, event)))
                thoughts += 1
                if trace.thought.closed:
                    if len(trace.records) == event + 2:
                        forced_stops += 1
                    else:
                        thought_stops += 1
            logits, _ = trace.score_game()
            action = sample(logits[0], streams[seat])
            event = len(trace.records)
            trace.commit_game(action)
            events.append(("game", (seat, event)))
            games += 1
            world.play(action)
            # The next action turn follows the last completed settlement.
            rows = world.result()
            if rows[0]["turn"] != period:
                if rows[0]["turn"] != period + 1:
                    raise ValueError("Missing global settlement")
                events.append(("settlement", rows))
                period += 1
        final = world.result()

    targets = returns_from_events(events)
    for seat, trace in enumerate(traces):
        if open_window[seat] is not None:
            start, history, graph = open_window[seat]
            starts[seat].append((start, len(trace.records), history, graph))
    if sum(len(x) for x in targets) != games + thoughts or sum(r["decisions"] for r in final) != games:
        raise ValueError("World trace and native decision counts differ")
    for seat, trace in enumerate(traces):
        if sum(getattr(r,"logp",None) is not None for r in trace.records) != len(targets[seat]):
            raise ValueError("Unmatched seat probability record")
    settlements = [data for kind, data in events if kind == "settlement"]
    for seat, row in enumerate(final):
        gdp_sum = sum(s[seat]["nominal_gdp"] for s in settlements)
        if abs(gdp_sum - row["total_nominal_gdp"]) > .02:
            raise ValueError("Global rewards do not align with native GDP")
    windows = [w for i, trace in enumerate(traces)
               for w in training_windows(trace, starts[i], targets[i], rng)]
    if not windows or period < 1:
        raise ValueError("No usable adaptive self-play windows")
    stats = dict(seed=seed, turns=period, game_decisions=games, thoughts=thoughts,
                 active_stops=thought_stops, forced_stops=forced_stops,
                 minutes=(time.monotonic()-start_time)/60,
                 nominal_gdp=statistics.mean(r["total_nominal_gdp"]/period for r in final),
                 final_nominal_gdp=statistics.mean(r["nominal_gdp"] for r in final),
                 land=statistics.mean(r["land"] for r in final), seats=final)
    return windows, stats


def replay(policy, window):
    method = replay_turn_stepwise if window.stepwise else replay_turn
    return method(policy, window.header, window.records, window.observations, window.history,
                  window.graph, window.game_records)


def action_group(decisions, window):
    thoughts = [(d.logp, d.sampled_logp, window.targets[d.record] - d.sampled_value)
                for d in decisions if d.kind == "thought"]
    games = [d for d in decisions if d.kind == "game"]
    game = (torch.stack([d.logp for d in games]).sum(), sum(d.sampled_logp for d in games),
            window.targets[games[0].record] - games[0].sampled_value)
    return thoughts, game


def clipped(new, old, advantage):
    ratio = (new - old).clamp(-30, 30).exp()
    return -torch.minimum(ratio * advantage, ratio.clamp(.8, 1.2) * advantage)


def learning_pass(policy, windows, actor, critic, check):
    game_adv, thought_adv, total = [], [], 0
    for w in windows:
        for i, record in enumerate(w.records):
            if i not in w.targets:
                continue
            if i in w.game_records:
                if i == min(w.game_records):
                    game_adv.append(w.targets[i] - record.value)
                total += 1
            elif not isinstance(record, GameRecord):
                thought_adv.append(w.targets[i] - record.value)
                total += 1
    advantages = thought_adv + game_adv
    mean = statistics.mean(advantages)
    std = statistics.pstdev(advantages) + 1e-6
    logp_error, value_error = 0., 0.
    reference_windows = 0
    totals = dict(actor=0., critic=0.)
    actor.zero_grad(set_to_none=True)
    critic.zero_grad(set_to_none=True)
    for w in windows:
        decisions = replay(policy, w)
        if check:
            lp, ve = max_errors(decisions)
            if not (lp <= 2e-5 and ve <= 2e-5) and not w.stepwise:
                batched_logp, batched_value = lp, ve
                del decisions
                w.stepwise = True
                decisions = replay(policy, w)
                lp, ve = max_errors(decisions)
                reference_windows += 1
                emit("ppo_replay_reference", revision=w.records[0].revision,
                     batched_logp_error=batched_logp, batched_value_error=batched_value,
                     reference_logp_error=lp, reference_value_error=ve)
            logp_error, value_error = max(logp_error, lp), max(value_error, ve)
            if not (lp <= 2e-5 and ve <= 2e-5):
                raise ValueError(f"Adaptive rollout/replay drift: logp={lp:.7g}, V={ve:.7g}")
        thought, game = action_group(decisions, w)
        thought_loss = sum(clipped(new, old, (a - mean)/std) for new, old, a in thought)
        new, old, adv = game
        game_loss = clipped(new, old, (adv - mean)/std)
        actor_loss = .5 * (thought_loss / len(thought_adv) + game_loss / len(game_adv))
        current_value = torch.stack([d.value for d in decisions])
        target = current_value.new_tensor([w.targets[d.record] for d in decisions])
        critic_loss = F.mse_loss(current_value, target, reduction="sum") / total
        loss = actor_loss + critic_loss
        if not torch.isfinite(loss):
            raise ValueError("Nonfinite adaptive PPO objective")
        loss.backward()
        totals["actor"] += float(actor_loss.detach())
        totals["critic"] += float(critic_loss.detach())
    actor_norm = torch.nn.utils.clip_grad_norm_(actor.param_groups[0]["params"], 1., error_if_nonfinite=True)
    critic_norm = torch.nn.utils.clip_grad_norm_(critic.param_groups[0]["params"], 1., error_if_nonfinite=True)
    return dict(**totals, actor_norm=float(actor_norm), critic_norm=float(critic_norm),
                game_fragments=len(game_adv), trained_thoughts=len(thought_adv),
                replay_logp_error=logp_error, replay_value_error=value_error,
                reference_windows=reference_windows)


@torch.no_grad()
def anchor_kl(policy, anchors):
    game, thought = [], []
    for w in anchors:
        decisions = replay(policy, w)
        sampled = [d.logp - d.sampled_logp for d in decisions if d.kind == "thought"]
        new, old, _ = action_group(decisions, w)[1]
        thought.extend(sampled)
        game.append(new - old)
    return tuple(float((r.expm1() - r).mean()) for r in map(torch.stack, (thought, game)))


def actor_step(policy, optimizer, anchors, args):
    parameters = optimizer.param_groups[0]["params"]
    saved = [p.detach().clone() for p in parameters]
    optimizer_state = copy.deepcopy(optimizer.state_dict())
    lr = min(args.lr, optimizer.param_groups[0]["lr"] * 2)
    attempts = 9
    for attempt in range(attempts):
        optimizer.param_groups[0]["lr"] = lr
        optimizer.step()
        thought_kl, game_kl = anchor_kl(policy, anchors)
        if all(math.isfinite(kl) and kl <= args.target_kl for kl in (thought_kl, game_kl)):
            return dict(accepted=True, thought_kl=thought_kl, game_kl=game_kl,
                        backtracks=attempt, accepted_lr=lr)
        emit("ppo_actor_backtrack", attempt=attempt + 1, lr=lr, thought_kl=thought_kl, game_kl=game_kl)
        with torch.no_grad():
            for p, original in zip(parameters, saved):
                p.copy_(original)
        optimizer.load_state_dict(copy.deepcopy(optimizer_state))
        lr *= .5
    return dict(accepted=False, thought_kl=thought_kl, game_kl=game_kl,
                backtracks=attempts, accepted_lr=None)


def optimize(policy, windows, actor, critic, args, seed):
    rng = random.Random(seed)
    anchors = rng.sample(windows, min(6, len(windows)))
    rows = []
    for i in range(args.ppo_epochs):
        rng.shuffle(windows)
        row = learning_pass(policy, windows, actor, critic, i == 0)
        if i == 0:
            emit("ppo_replay_verified", logp_error=row["replay_logp_error"],
                 value_error=row["replay_value_error"], trained_thoughts=row["trained_thoughts"],
                 game_fragments=row["game_fragments"], reference_windows=row["reference_windows"])
        result = actor_step(policy, actor, anchors, args)
        critic.step()
        rows.append(dict(row, **result))
        if not result["accepted"]:
            break
    if not all(torch.isfinite(p).all().item() for p in policy.parameters()):
        raise ValueError("Nonfinite parameters after adaptive PPO")
    return rows


def train(args):
    torch.set_num_threads(args.threads)
    torch.backends.mha.set_fastpath_enabled(False)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    if args.device == "cuda" and torch.cuda.device_count() < args.workers:
        raise RuntimeError("Fewer CUDA devices than required by the self-play sampler")
    devices = [f"cuda:{i}" if args.device == "cuda" else "cpu" for i in range(args.workers)]
    state = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    if not state.get("value_warmup", {}).get("accepted"):
        raise ValueError("Expected the validated value warmup checkpoint")
    completed = state.get("games", 0)
    if "contract" in state:
        expected = dict(CONTRACT, size=args.size, turns=args.turns, seed=args.seed)
        if state["contract"] != expected or state["lr"] != args.lr:
            raise ValueError("PPO continuation changed the task contract")
    else:
        state["contract"] = dict(CONTRACT, size=args.size, turns=args.turns, seed=args.seed)
    initial = state.get("initial_sha256") or sha(args.checkpoint)
    policies = [Policy().to(device).eval() for device in devices]
    for policy in policies:
        policy.load_state_dict(state["model"])
    policy = policies[0]
    actor = torch.optim.AdamW([p for name, p in policy.named_parameters() if not name.startswith("value.")],
                              lr=args.lr, weight_decay=0)
    critic = torch.optim.AdamW(policy.value.parameters(), lr=args.lr, weight_decay=0)
    for name, opt in (("actor_optimizer", actor), ("critic_optimizer", critic)):
        if name in state:
            opt.load_state_dict(state[name])
    args.output.mkdir(parents=True, exist_ok=True)
    emit("ppo_started", start=completed, target=args.stop_games, devices=devices, contract=state["contract"])
    with ThreadPoolExecutor(max_workers=len(policies)) as pool:
        while completed < args.stop_games:
            batch_size = min(args.stop_games - completed, args.batch_games)
            windows, statistics_rows = [], []
            for start in range(0, batch_size, len(policies)):
                jobs = []
                for i in range(min(len(policies), batch_size - start)):
                    position = completed + start + i
                    seed = args.seed + position + 4 * (position % 4)
                    jobs.append(pool.submit(rollout, policies[i], args.library, seed,
                                            args.size, args.turns, args.samples_per_seat))
                for job in jobs:
                    selections, row = job.result()
                    windows.extend(selections)
                    statistics_rows.append(row)
                    emit("ppo_game_complete", **row)
            rows = optimize(policy, windows, actor, critic, args, args.seed + completed)
            if not any(row["accepted"] for row in rows):
                raise RuntimeError("No accepted actor update in this PPO batch")
            completed += batch_size
            out = args.output / f"games-{completed:04}.pt"
            atomic_save(out, dict(model=policy.state_dict(), actor_optimizer=actor.state_dict(),
                                  critic_optimizer=critic.state_dict(), games=completed,
                                  contract=state["contract"], lr=args.lr, initial_sha256=initial,
                                  value_warmup=state["value_warmup"]))
            summary = dict(games=completed, checkpoint=out.name, sha256=sha(out),
                           initial_sha256=initial, metrics=statistics_rows, ppo=rows,
                           contract=state["contract"])
            publish(args.output / f"games-{completed:04}.json", summary)
            publish(args.output / "state.json", summary)
            emit("ppo_batch_complete", **summary)
            # Keep each committed batch during the segment. The next Kaggle job
            # mounts the exact terminal report, model and both optimizer states.
            for other in policies[1:]:
                other.load_state_dict(policy.state_dict())


def arguments():
    p = argparse.ArgumentParser()
    p.add_argument("--library", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--stop-games", type=int, required=True)
    p.add_argument("--batch-games", type=int, default=4)
    p.add_argument("--samples-per-seat", type=int, default=4)
    p.add_argument("--ppo-epochs", type=int, default=2)
    p.add_argument("--size", type=int, default=24)
    p.add_argument("--turns", type=int, default=100)
    p.add_argument("--seed", type=int, default=640000)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--target-kl", type=float, default=.02)
    p.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    p.add_argument("--workers", type=int, choices=(1, 2), default=2)
    p.add_argument("--threads", type=int, default=2)
    args = p.parse_args()
    if min(args.stop_games, args.batch_games, args.samples_per_seat, args.ppo_epochs, args.turns) < 1:
        p.error("Training counts must be positive")
    return args


if __name__ == "__main__":
    train(arguments())
