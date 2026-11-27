"""Advance independent native worlds one game decision per world per wave."""
import torch

from batch_policy import BatchRow, BoardLayout, board_row, cache_row, decoder_boards, join_cache, pack_observations, packed_snapshot
from memory_map import Op
from replay import require_adaptive


def _draw_rows(logits, values, generators):
    """Preserve each seat's random stream and native candidate count, with one host transfer."""
    selections = []
    for row, value, generator in zip(logits, values, generators, strict=True):
        row = row.float()
        choice = torch.multinomial(row.softmax(-1), 1, generator=generator)
        logp = row.log_softmax(-1).gather(0, choice)
        selections.append(torch.cat((choice.to(row.dtype), logp, value.reshape(1))))
    return torch.stack(selections).tolist()


def batch_decisions(policy, requests):
    """Sample standard adaptive PPO trajectories whose game tokens start with an empty decoder cache."""
    traces = [q[0] for q in requests]
    for trace in traces:
        require_adaptive(trace.header, retain_thought_kv=False)
    raw = [q[2] for q in requests]
    count = len(requests)
    packed, lengths = pack_observations(raw, traces[0].device)
    board = packed_snapshot(policy, packed, -1)
    for i, (trace, revision, obs, _, _) in enumerate(requests):
        current = {k: v[i:i+1] for k, v in packed.items()}
        trace.accept_snapshot(revision, obs, current, board_row(board, i, revision),
                              BoardLayout(BatchRow(count, i), lengths))

    # Every newly-opened thought phase starts together at write 0. Finished rows drop
    # out of the decoder batch; sparse history lengths and causal lookups stay per seat.
    active = [i for i, t in enumerate(traces) if not t.thought.closed]
    while active:
        if len(active) == 1:
            i = active[0]
            trace = traces[i]
            while not trace.thought.closed:
                trace.sample_thought(requests[i][3])
            break
        thinking = [traces[i] for i in active]
        queries = [t.history_query(t.previous_operation, t.thought.writes) for t in thinking]
        views = board if len(active) == count else decoder_boards([t.board for t in thinking])
        hidden, cache = policy.decode_tokens(torch.cat(queries), views, join_cache([t.decoder_cache for t in thinking]))
        logits = [t.accept_thought(hidden[j:j+1], cache_row(cache, j), BatchRow(len(active), j))
                  for j, t in enumerate(thinking)]
        draws = _draw_rows(logits, [t.pending.value for t in thinking], [requests[i][3] for i in active])
        for t, (action, lp, value) in zip(thinking, draws, strict=True):
            t.commit_thought(int(action), scores=(lp, value))
        active = [i for i in active if not traces[i].thought.closed]

    query = torch.cat([t.history_query(Op.GAME, 0) for t in traces])
    hidden, _ = policy.decode_tokens(query, board, None)
    states = hidden[:, 0]
    logits = policy.action_logits(states, packed, board)
    rows = [t.accept_game(states[i:i+1], logits[i:i+1])[0][0] for i, t in enumerate(traces)]
    draws = _draw_rows(rows, [t.pending.value for t in traces], [q[4] for q in requests])
    choices = []
    for t, (action, lp, value) in zip(traces, draws, strict=True):
        choice = int(action)
        t.commit_game(choice, scores=(lp, value))
        choices.append(choice)
    return choices


@torch.inference_mode()
def rollout_worlds(policy, library, seeds, size, turns):
    """Sample independent adaptive PPO games on one device with retain_thought_kv=False."""
    from thought_ppo import serial_decision, world_steps

    generators = [world_steps(policy, library, seed, size, turns) for seed in seeds]
    results = [None] * len(generators)
    pending = {}
    try:
        for index, generator in enumerate(generators):
            pending[index] = next(generator)
        while pending:
            live = list(pending)
            requests = [pending[i] for i in live]
            actions = ([serial_decision(requests[0])] if len(live) == 1
                       else batch_decisions(policy, requests))
            for i, choice in zip(live, actions, strict=True):
                try:
                    pending[i] = generators[i].send(choice)
                except StopIteration as done:
                    results[i] = done.value
                    del pending[i]
    finally:
        for generator in generators:
            generator.close()
    return results
