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
from evaluate_thought import SCALE, territory_gdp
from history import DeltaState, SparseCache
from land_reward import RULE as LAND_REWARD_RULE, LandRewardSchedule
from memory_map import EXTENDED_THOUGHTS, MAX_THOUGHTS, Op
from model import Event, History, Policy
from native_eval import Engine
from replay import max_errors, replay_turn, replay_turn_stepwise
from rollout_trace import GameRecord, ObservationArchive, SeatTrace

WINDOW_GAMES = 8

CONTRACT = dict(thought_budget=[8, MAX_THOUGHTS], thought_limit_after_gdp_trigger=EXTENDED_THOUGHTS, retain_thought_kv=False, actor_observation_gdp="nominal_only",
                reward="nominal_gdp+territory_holding", reward_scale=SCALE, land_reward_schedule=LAND_REWARD_RULE,
                clock="global_events", gamma=1., gae_lambda=1., game_sequences=[2, 4],
                coverage="all_turns_all_decisions", window_games=WINDOW_GAMES,
                replay="window_truncated_with_behavior_prefix", seats=4, shared_policy=True,
                precision="fp32")


@dataclass
class Window:
    header: object
    records: list
    observations: ObservationArchive
    history: object
    graph: object
    game_records: tuple
    groups: tuple
    game_phase: bool
    targets: dict
    stepwise: bool = False


@dataclass
class Opening:
    delta: tuple
    latest: torch.Tensor
    tail: torch.Tensor
    tokens: int
    blocks: int

    @classmethod
    def capture(cls, history):
        if history is None:
            return None

        def cpu(tensor):
            return tensor.detach().to(device="cpu", copy=True)

        delta = tuple(DeltaState(cpu(x.matrix), cpu(x.conv_tail)) for x in history.delta)
        sparse = history.sparse
        return cls(delta, cpu(history.latest), cpu(sparse.index_tail),
                   sparse.key.size(1), sparse.block_key.size(1))

    def prefix(self, key, value, block_key):
        # Completed K/V rows are append-only. All openings share the final CPU backing store.
        return History(self.delta, SparseCache(key[:, :self.tokens], value[:, :self.tokens],
                                               block_key[:, :self.blocks], self.tail), self.latest)


def partition_games(games, rng):
    groups = []
    while games:
        count = min(len(games), rng.randint(2, 4))
        if len(games) - count == 1:
            count = count + 1 if count < 4 else count - 1
        groups.append(games[:count])
        games = games[count:]
    return tuple(groups)


def training_windows(trace, starts, returns, rng):
    if not starts:
        if returns:
            raise ValueError("Missing replay windows for a live seat")
        return []
    sparse = trace.history.sparse
    shared = tuple(t.detach().to(device="cpu", copy=True) for t in (sparse.key, sparse.value, sparse.block_key))
    result, covered = [], set()
    for start, stop, opening, graph, game_phase in starts:
        records = trace.records[start:stop]
        games = tuple(i for i, r in enumerate(records) if isinstance(r, GameRecord))
        decisions = [start + i for i, r in enumerate(records) if getattr(r, "logp", None) is not None]
        if not games or not covered.isdisjoint(decisions):
            raise ValueError("Empty or overlapping replay window")
        covered.update(decisions)
        history = opening.prefix(*shared) if opening is not None else None
        result.append(Window(trace.header, records, trace.observations, history, graph, games,
                             partition_games(games, rng), game_phase,
                             {i-start: returns[i] for i in decisions}))
    if covered != returns.keys():
        raise ValueError("Replay windows omitted decisions")
    return result


def returns_from_events(events, land_weight=1.0):
    """Reverse the global event clock, with an independent reward channel per country."""
    returns, accumulated = [{}, {}, {}, {}], [0., 0., 0., 0.]
    for kind, data in reversed(events):
        if kind == "settlement":
            for seat, row in enumerate(data):
                accumulated[seat] += (row["nominal_gdp"] +
                                      land_weight * territory_gdp(max(0, row["land"] - row["initial_land"]))) / SCALE
        else:
            seat, record = data
            if record is None:
                continue
            if record in returns[seat]:
                raise ValueError("Duplicate self-play decision in global timeline")
            returns[seat][record] = accumulated[seat]
    return returns


def world_steps(policy, library, seed, size, turns, land_weight=1.0, *, no_thought=False):
    """Yield one native game decision request at a time, keeping each seat trajectory private."""
    start_time = time.monotonic()
    rng = random.Random(seed)
    chunk_games = [0] * 4
    seat_turns = 0
    traces = [SeatTrace(policy, thought_budget=0 if no_thought else None) for _ in range(4)]
    starts = [[] for _ in traces]
    last_turn, open_window = [0] * 4, [None] * 4
    device = traces[0].device
    streams = [torch.Generator(device=device).manual_seed(seed * 16 + i) for i in range(8)]
    events = []
    thought_stops, forced_stops, thoughts, games = 0, 0, 0, 0
    period = 0
    batched_games = batched_thoughts = 0
    with Engine(library).self_play(seed, size, turns) as world:
        while (view := world.observe()) is not None:
            seat, turn, new_turn, obs = view
            trace = traces[seat]
            if new_turn != (turn != last_turn[seat]):
                raise ValueError("Native self-play turn marker mismatch")
            if new_turn or chunk_games[seat] == WINDOW_GAMES:
                if open_window[seat] is not None:
                    start, opening, graph, game_phase = open_window[seat]
                    starts[seat].append((start, len(trace.records), opening, graph, game_phase))
                if new_turn:
                    if last_turn[seat]:
                        trace.next_turn()
                    last_turn[seat] = turn
                    seat_turns += 1
                open_window[seat] = (len(trace.records), Opening.capture(trace.history),
                                     trace.thought.graph, not new_turn)
                chunk_games[seat] = 0
            first = len(trace.records)
            action = yield trace, len(events), obs, streams[seat + 4], streams[seat]
            for index in range(first, len(trace.records)):
                record = trace.records[index]
                if isinstance(record, GameRecord):
                    events.append(("game", (seat, index)))
                    batched_games += trace.board_layout is not None
                elif record.kind is Event.THOUGHT:
                    events.append(("thought", (seat, index)))
                    thoughts += 1
                    thought_stops += record.operation == Op.STOP
                    batched_thoughts += record.decoder_row is not None
                elif record.kind is Event.THOUGHT_LIMIT:
                    forced_stops += 1
            games += 1
            chunk_games[seat] += 1
            settled = world.play(action)
            if settled != period:
                if settled != period + 1:
                    raise ValueError("Missing global settlement")
                events.append(("settlement", world.result()))
                period = settled
        final = world.result()

    targets = returns_from_events(events, land_weight)
    for seat, trace in enumerate(traces):
        if open_window[seat] is not None:
            start, opening, graph, game_phase = open_window[seat]
            starts[seat].append((start, len(trace.records), opening, graph, game_phase))
    if len(events) != games + thoughts + period or sum(r["decisions"] for r in final) != games:
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
    counts = coverage(windows)
    if (counts["trained_turns"], counts["trained_game_actions"], counts["trained_thoughts"]) != (seat_turns, games, thoughts):
        raise ValueError("Full PPO coverage differs from the collected rollout")
    stats = dict(seed=seed, turns=period, seat_turns=seat_turns, game_decisions=games, thoughts=thoughts,
                 batched_games=batched_games, batched_thoughts=batched_thoughts,
                 active_stops=thought_stops, forced_stops=forced_stops,
                 minutes=(time.monotonic()-start_time)/60,
                 nominal_gdp=statistics.mean(r["total_nominal_gdp"]/period for r in final),
                 final_nominal_gdp=statistics.mean(r["nominal_gdp"] for r in final),
                 land=statistics.mean(r["land"] for r in final), seats=final)
    return windows, stats


def serial_decision(request):
    trace, revision, obs, thought_rng, game_rng = request
    trace.observe(revision, obs)
    while not trace.thought.closed:
        trace.sample_thought(thought_rng)
    return trace.sample_game(game_rng)


@torch.inference_mode()
def rollout(policy, library, seed, size, turns, land_weight=1.0, *, no_thought=False):
    generator = world_steps(policy, library, seed, size, turns, land_weight, no_thought=no_thought)
    try:
        request = next(generator)
        while True:
            request = generator.send(serial_decision(request))
    except StopIteration as done:
        return done.value
    finally:
        generator.close()


def replay(policy, window):
    method = replay_turn_stepwise if window.stepwise else replay_turn
    return method(policy, window.header, window.records, window.observations, window.history,
                  window.graph, window.game_records, game_phase=window.game_phase)


def rollout_on_stream(policy, *args, **kwargs):
    device = policy.event_proj.weight.device
    if device.type != "cuda":
        return rollout(policy, *args, **kwargs)
    stream = torch.cuda.Stream(device=device)
    # Model weights are loaded on the default stream before read-only sampling.
    stream.wait_stream(torch.cuda.default_stream(device))
    with torch.cuda.stream(stream):
        result = rollout(policy, *args, **kwargs)
    stream.synchronize()
    return result


def action_group(decisions, window):
    thoughts = [(d.logp, d.sampled_logp, window.targets[d.record] - d.sampled_value)
                for d in decisions if d.kind == "thought"]
    games = {d.record: d for d in decisions if d.kind == "game"}
    groups = []
    for indices in window.groups:
        first = games[indices[0]]
        group = [games[i] for i in indices]
        groups.append((torch.stack([d.logp for d in group]).sum(), sum(d.sampled_logp for d in group),
                       window.targets[first.record] - first.sampled_value))
    return thoughts, groups


def coverage(windows):
    games = sum(len(w.game_records) for w in windows)
    return dict(trained_turns=sum(not w.game_phase for w in windows), trained_game_actions=games,
                trained_thoughts=sum(len(w.targets) for w in windows) - games)


def clipped(new, old, advantage):
    ratio = (new - old).clamp(-30, 30).exp()
    return -torch.minimum(ratio * advantage, ratio.clamp(.8, 1.2) * advantage)


def fragment_loss(rows, mean, std):
    if not rows:
        return 0.
    new = torch.stack([item[0] for item in rows])
    old = new.new_tensor([item[1] for item in rows])
    advantage = new.new_tensor([(item[2] - mean) / std for item in rows])
    return clipped(new, old, advantage).sum()


def learning_pass(policy, windows, actor, critic, check, group=None):
    game_adv, thought_adv, total = [], [], 0
    for w in windows:
        group_starts = {fragment[0] for fragment in w.groups}
        for i, record in enumerate(w.records):
            if i not in w.targets:
                continue
            if i in w.game_records:
                if i in group_starts:
                    game_adv.append(w.targets[i] - record.value)
                total += 1
            elif not isinstance(record, GameRecord):
                thought_adv.append(w.targets[i] - record.value)
                total += 1
    advantages = thought_adv + game_adv
    game_count, thought_count = len(game_adv), len(thought_adv)
    if group is None:
        mean = statistics.mean(advantages)
        std = statistics.pstdev(advantages) + 1e-6
    else:
        game_count, thought_count, total, advantage_sum = group.totals(
            [game_count, thought_count, total, math.fsum(advantages)])
        count = game_count + thought_count
        mean = advantage_sum / count
        variance_sum, = group.totals([math.fsum((a - mean) ** 2 for a in advantages)])
        std = math.sqrt(variance_sum / count) + 1e-6
    logp_error, value_error = 0., 0.
    reference_windows = 0
    totals = dict(actor=0., critic=0.)
    actor.zero_grad(set_to_none=True)
    critic.zero_grad(set_to_none=True)
    for number, w in enumerate(windows, 1):
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
        if len(decisions) != len(w.targets) or {d.record for d in decisions} != w.targets.keys():
            raise ValueError("Replayed decision IDs differ from sampled decision IDs")
        thought, groups = action_group(decisions, w)
        thought_loss = fragment_loss(thought, mean, std)
        game_loss = fragment_loss(groups, mean, std)
        actor_loss = .5 * (game_loss / game_count)
        if thought_count:
            actor_loss = actor_loss + .5 * (thought_loss / thought_count)
        current_value = torch.stack([d.value for d in decisions])
        target = current_value.new_tensor([w.targets[d.record] for d in decisions])
        critic_loss = F.mse_loss(current_value, target, reduction="sum") / total
        loss = actor_loss + critic_loss
        if not torch.isfinite(loss):
            raise ValueError("Nonfinite adaptive PPO objective")
        loss.backward()
        totals["actor"] += float(actor_loss.detach())
        totals["critic"] += float(critic_loss.detach())
        if number % 250 == 0:
            emit("ppo_replay_progress", windows=number, total_windows=len(windows), first_epoch=check,
                 rank=0 if group is None else group.rank)
    counts = coverage(windows)
    if group is not None:
        group.sum_gradients(policy)
        sums = group.totals([*counts.values(), reference_windows, totals["actor"], totals["critic"]])
        counts = dict(zip(counts, map(int, sums[:len(counts)])))
        reference_windows, totals["actor"], totals["critic"] = int(sums[-3]), sums[-2], sums[-1]
        logp_error, value_error = group.totals([logp_error, value_error], torch.distributed.ReduceOp.MAX)
    actor_norm = torch.nn.utils.clip_grad_norm_(actor.param_groups[0]["params"], 1., error_if_nonfinite=True)
    critic_norm = torch.nn.utils.clip_grad_norm_(critic.param_groups[0]["params"], 1., error_if_nonfinite=True)
    return dict(**totals, actor_norm=float(actor_norm), critic_norm=float(critic_norm),
                game_fragments=int(game_count), **counts,
                replay_logp_error=logp_error, replay_value_error=value_error,
                reference_windows=reference_windows)


@torch.no_grad()
def anchor_kl(policy, anchors, group=None):
    game, thought = [], []
    for w in anchors:
        decisions = replay(policy, w)
        sampled = [d.logp - d.sampled_logp for d in decisions if d.kind == "thought"]
        groups = action_group(decisions, w)[1]
        thought.extend(sampled)
        game.extend(new - old for new, old, _ in groups)
    if group is None:
        means = []
        for ratios in (thought, game):
            r = torch.stack(ratios) if ratios else policy.operation.weight.new_empty(0)
            means.append(float((r.expm1() - r).mean()) if ratios else 0.)
        return tuple(means)
    stats = []
    for ratios in (thought, game):
        r = policy.operation.weight.new_empty(0) if not ratios else torch.stack(ratios)
        stats.extend((float((r.expm1() - r).sum(dtype=torch.float64)), len(ratios)))
    thought_sum, thoughts, game_sum, games = group.totals(stats)
    return thought_sum / thoughts if thoughts else 0., game_sum / games


def actor_step(policy, optimizer, anchors, args, group=None):
    parameters = optimizer.param_groups[0]["params"]
    saved = [p.detach().clone() for p in parameters]
    optimizer_state = copy.deepcopy(optimizer.state_dict())
    lr = min(args.lr, optimizer.param_groups[0]["lr"] * 2)
    attempts = 9
    for attempt in range(attempts):
        optimizer.param_groups[0]["lr"] = lr
        optimizer.step()
        thought_kl, game_kl = anchor_kl(policy, anchors, group)
        if all(math.isfinite(kl) and kl <= args.target_kl for kl in (thought_kl, game_kl)):
            return dict(accepted=True, thought_kl=thought_kl, game_kl=game_kl,
                        backtracks=attempt, accepted_lr=lr)
        if group is None or group.rank == 0:
            emit("ppo_actor_backtrack", attempt=attempt + 1, lr=lr, thought_kl=thought_kl, game_kl=game_kl)
        with torch.no_grad():
            for p, original in zip(parameters, saved):
                p.copy_(original)
        optimizer.load_state_dict(copy.deepcopy(optimizer_state))
        lr *= .5
    return dict(accepted=False, thought_kl=thought_kl, game_kl=game_kl,
                backtracks=attempts, accepted_lr=None)


def optimize(policy, windows, actor, critic, args, seed, group=None):
    rng = random.Random(seed)
    phases, offset = ([w.game_phase for w in windows], 0) if group is None else group.global_windows(windows)
    order = list(range(len(phases)))
    first = rng.choice([i for i, phase in enumerate(phases) if not phase])
    others = [i for i in order if i != first]
    anchor_ids = [first, *rng.sample(others, min(5, len(others)))]

    def local(indices):
        return [windows[i-offset] for i in indices if offset <= i < offset + len(windows)]

    anchors = local(anchor_ids)
    rows = []
    for i in range(args.ppo_epochs):
        rng.shuffle(order)
        row = learning_pass(policy, local(order), actor, critic, i == 0, group)
        if i == 0 and (group is None or group.rank == 0):
            emit("ppo_replay_verified", logp_error=row["replay_logp_error"],
                 value_error=row["replay_value_error"], trained_thoughts=row["trained_thoughts"],
                 game_fragments=row["game_fragments"], reference_windows=row["reference_windows"])
        result = actor_step(policy, actor, anchors, args, group)
        critic.step()
        rows.append(dict(row, **result))
        if not result["accepted"]:
            break
    if not all(torch.isfinite(p).all().item() for p in policy.parameters()):
        raise ValueError("Nonfinite parameters after adaptive PPO")
    return rows


def sample_batch(pool, policies, seeds, args, land_weight, *, no_thought=False):
    parallel = len(policies) * args.rollouts_per_device
    sample_world = rollout if args.rollouts_per_device == 1 else rollout_on_stream
    windows, statistics_rows = [], []
    for start in range(0, len(seeds), parallel):
        wave = seeds[start:start + parallel]
        if args.batch_inference:
            from batched_rollout import rollout_worlds
            jobs = [(i, pool.submit(rollout_worlds, policy_copy, args.library,
                                    wave[i::len(policies)], args.size, args.turns, land_weight,
                                    no_thought=no_thought))
                    for i, policy_copy in enumerate(policies) if i < len(wave)]
            results = [None] * len(wave)
            for i, job in jobs:
                results[i::len(policies)] = job.result()
        else:
            jobs = [pool.submit(sample_world, policies[i % len(policies)], args.library, seed,
                                args.size, args.turns, land_weight, no_thought=no_thought)
                    for i, seed in enumerate(wave)]
            results = [job.result() for job in jobs]
        for selections, row in results:
            windows.extend(selections)
            statistics_rows.append(row)
            emit("ppo_game_complete", **row)
    return windows, statistics_rows


def train(args, group=None):
    torch.set_num_threads(args.threads)
    torch.backends.mha.set_fastpath_enabled(False)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    if args.device == "cuda" and torch.cuda.device_count() < args.workers:
        raise RuntimeError("Fewer CUDA devices than required by the self-play sampler")
    devices = [f"cuda:{i}" if args.device == "cuda" else "cpu" for i in range(args.workers)]
    local_devices = devices if group is None else [str(group.device)]
    leader = group is None or group.rank == 0
    state = torch.load(args.checkpoint, map_location="cpu", weights_only=True, mmap=True)
    if not state.get("value_warmup", {}).get("accepted"):
        raise ValueError("Expected the validated value warmup checkpoint")
    completed = state.get("games", 0)
    adaptive_contract = dict(CONTRACT, size=args.size, turns=args.turns, seed=args.seed)
    disabled_contract = dict(adaptive_contract, thought_budget=[0, 0], thought_limit_after_gdp_trigger=0)
    saved_contract = state.get("contract") or {}
    was_disabled = saved_contract.get("thought_budget") == [0, 0]
    no_thought = getattr(args, "no_thought", False) or was_disabled
    contract = disabled_contract if no_thought else adaptive_contract
    # The explicit switch updates the sampling protocol while preserving the reward schedule.
    parent_contract = disabled_contract if was_disabled else adaptive_contract
    land_schedule = LandRewardSchedule.from_checkpoint(state, args.checkpoint, parent_contract, args.lr)
    initial = state.get("initial_sha256") or sha(args.checkpoint)
    policies = [Policy().to(device).eval() for device in local_devices]
    for policy in policies:
        policy.load_state_dict(state["model"])
        if getattr(args, "cuda_graph_history", False):
            from history import DeltaStepGraph
            policy.sampling_delta_graph = DeltaStepGraph(policy.delta)
    policy = policies[0]
    actor = torch.optim.AdamW([p for name, p in policy.named_parameters() if not name.startswith("value.")],
                              lr=args.lr, weight_decay=0)
    critic = torch.optim.AdamW(policy.value.parameters(), lr=args.lr, weight_decay=0)
    for name, opt in (("actor_optimizer", actor), ("critic_optimizer", critic)):
        if name in state:
            opt.load_state_dict(state[name])
    args.output.mkdir(parents=True, exist_ok=True)
    if leader:
        emit("ppo_started", start=completed, target=args.stop_games, devices=devices, contract=contract,
             update_workers=1 if group is None else group.size, batch_inference=args.batch_inference,
             rolling_final_gdp=land_schedule.rolling_gdp, land_triggered_at=land_schedule.triggered_at)

    sample_threads = len(policies) if args.batch_inference else len(policies) * args.rollouts_per_device
    with ThreadPoolExecutor(max_workers=sample_threads) as pool:
        while completed < args.stop_games:
            batch_size = min(args.stop_games - completed, args.batch_games)
            seeds = [args.seed + position + 4 * (position % 4)
                     for position in range(completed, completed + batch_size)]
            if group is not None:
                # Contiguous shards reproduce the single-process world/window ordering after gather.
                begin = (batch_size * group.rank) // group.size
                end = (batch_size * (group.rank + 1)) // group.size
                seeds = seeds[begin:end]
            adaptive_limit = MAX_THOUGHTS if land_schedule.triggered_at is None else EXTENDED_THOUGHTS
            thought_limit = 0 if no_thought else adaptive_limit
            if thought_limit == EXTENDED_THOUGHTS:
                for replica in policies:
                    replica.extend_thought_positions(actor if replica is policy else None)
            elif not no_thought and policy.thought_position.num_embeddings != MAX_THOUGHTS + 1:
                raise ValueError("Extended positions in a checkpoint without a GDP trigger")
            sampled_at = time.monotonic()
            land_weight = land_schedule.weight(completed + batch_size)
            windows, statistics_rows = sample_batch(pool, policies, seeds, args, land_weight,
                                                   no_thought=no_thought)
            if any((w.header.thought_budget, w.header.thought_limit) !=
                   ((0, MAX_THOUGHTS) if no_thought else (None, thought_limit)) for w in windows):
                raise ValueError("Replay window uses a different thought budget from this batch")
            counts = coverage(windows)
            if no_thought and counts["trained_thoughts"]:
                raise ValueError("Thought-free PPO received thought decisions")
            window_count = len(windows)
            if group is not None:
                statistics_rows = [r for shard in group.gather(statistics_rows) for r in shard]
                counts = dict(zip(counts, map(int, group.totals(list(counts.values())))))
                window_count = int(group.totals([window_count])[0])
            sample_seconds = time.monotonic() - sampled_at
            if leader:
                emit("ppo_coverage", games=completed + batch_size, windows=window_count,
                     sample_seconds=sample_seconds, land_reward_weight=land_weight, thought_limit=thought_limit, **counts)
            update_at = time.monotonic()
            rows = optimize(policy, windows, actor, critic, args, args.seed + completed, group)
            update_seconds = time.monotonic() - update_at
            if not any(row["accepted"] for row in rows):
                raise RuntimeError("No accepted actor update in this PPO batch")
            completed += batch_size
            land_schedule = land_schedule.after_batch(completed, statistics_rows)
            if not leader:
                # Keep the next collection behind the durable checkpoint boundary on rank zero.
                torch.distributed.barrier()
                continue
            out = args.output / f"games-{completed:04}.pt"
            atomic_save(out, dict(model=policy.state_dict(), actor_optimizer=actor.state_dict(),
                                  critic_optimizer=critic.state_dict(), games=completed,
                                  contract=contract, lr=args.lr, initial_sha256=initial, land_reward=land_schedule.state_dict(),
                                  value_warmup=state["value_warmup"]))
            summary = dict(games=completed, checkpoint=out.name, sha256=sha(out),
                           initial_sha256=initial, metrics=statistics_rows, ppo=rows, land_reward=land_schedule.state_dict(),
                           land_reward_weight=land_weight, thought_limit=thought_limit, rolling_final_gdp=land_schedule.rolling_gdp,
                           sample_seconds=sample_seconds, update_seconds=update_seconds,
                           contract=contract)
            publish(args.output / f"games-{completed:04}.json", summary)
            publish(args.output / "state.json", summary)
            emit("ppo_batch_complete", **summary)
            # Keep each committed batch during the segment. The next Kaggle job
            # mounts the exact terminal report, model and both optimizer states.
            for other in policies[1:]:
                other.load_state_dict(policy.state_dict())
            if group is not None:
                torch.distributed.barrier()


def arguments():
    p = argparse.ArgumentParser()
    p.add_argument("--library", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--stop-games", type=int, required=True)
    p.add_argument("--batch-games", type=int, default=4)
    p.add_argument("--ppo-epochs", type=int, default=2)
    p.add_argument("--size", type=int, default=24)
    p.add_argument("--turns", type=int, default=100)
    p.add_argument("--seed", type=int, default=640000)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--target-kl", type=float, default=.02)
    p.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    p.add_argument("--workers", type=int, choices=(1, 2), default=2)
    p.add_argument("--distributed-update", action="store_true")
    p.add_argument("--batch-inference", action="store_true")
    p.add_argument("--no-thought", action="store_true",
                   help="Skip the thought phase and save that mode for automatic continuation")
    p.add_argument("--cuda-graph-history", action="store_true",
                   help="Replay the three single-event DeltaNet layers as one CUDA graph during sampling")
    p.add_argument("--rollouts-per-device", type=int, default=1,
                   help="Worlds per device in one wave (independent CUDA streams when batching is off)")
    p.add_argument("--threads", type=int, default=2)
    args = p.parse_args()
    if args.cuda_graph_history and (args.device != "cuda" or
                                    (args.rollouts_per_device > 1 and not args.batch_inference)):
        p.error("CUDA graph sampling requires one sampling thread per CUDA device")
    if args.rollouts_per_device < 1 or (not args.batch_inference and args.rollouts_per_device > 2):
        p.error("Use a positive --rollouts-per-device; more than two worlds requires --batch-inference")
    if min(args.stop_games, args.batch_games, args.ppo_epochs, args.turns) < 1:
        p.error("Training counts must be positive")
    return args


if __name__ == "__main__":
    options = arguments()
    if options.distributed_update and options.workers > 1:
        from parallel_ppo import launch
        launch(options)
    else:
        train(options)
