"""Counterfactual reads and paired, full-match memory ablation of one rotating self-play seat."""
import argparse
from collections import Counter
import json
from pathlib import Path

import torch
import torch.multiprocessing as mp

from bc_train import sha
from memory_map import MemoryMap, Op
from model import Policy
from native_eval import Engine
from rollout_trace import SeatTrace


EMPTY = MemoryMap()


class AuditTrace(SeatTrace):
    def __init__(self, policy, mode):
        super().__init__(policy)
        self.read_mode = mode
        self.recording = False

    def _map(self, graph):
        # Leave the real write state, the legal action mask and the separate RNGs intact.
        if self.read_mode == "blank":
            graph = EMPTY
        elif self.read_mode == "rotate":
            graph = MemoryMap(tuple(tuple(v % 4 + 1 for v in row) for row in graph.slots))
        return super()._map(graph)

    def probe(self, mode):
        initial = self.read_mode
        try:
            self.read_mode = mode
            hidden = self.game_embedding()
            return self.policy.action_logits(hidden, self.obs, self.board)[0].float().softmax(-1)
        finally:
            self.read_mode = initial


@torch.inference_mode()
def match(policy, library, seed, turns, mode):
    target = seed % 4
    traces = [AuditTrace(policy, mode if seat == target else 'normal') for seat in range(4)]
    device = traces[0].device
    streams = [torch.Generator(device=device).manual_seed(seed * 16 + i) for i in range(8)]
    last_turn = [0] * 4
    previous_graph = EMPTY
    samples, graphs = [], []
    counts = Counter()
    symbol_writes = [0] * 4
    revision = 0
    with Engine(library).self_play(seed, 24, turns) as world:
        while (view := world.observe()) is not None:
            seat, turn, new_turn, obs = view
            trace = traces[seat]
            if new_turn != (turn != last_turn[seat]):
                raise AssertionError('wrong C++ turn boundary')
            if new_turn:
                if last_turn[seat]:
                    trace.next_turn()
                last_turn[seat] = turn
            trace.observe(revision, obs)
            focus = seat == target
            if focus and new_turn:
                assert trace.thought.graph == previous_graph
                surviving = [len(row) for row in previous_graph.slots]
                counts['turns'] += 1
                if turn > 1:
                    counts['later_turns'] += 1
                    counts['opened_nonempty'] += sum(surviving) > 0
            while not trace.thought.closed:
                logits = trace.score_thought()
                op = int(torch.multinomial(logits.float().softmax(-1), 1, generator=streams[seat+4]))
                if focus:
                    counts['thought_draws'] += 1
                    if op < Op.CLEAR_FIRST:
                        counts['appends'] += 1
                        symbol_writes[op % 4] += 1
                    elif op < Op.STOP:
                        slot = op - Op.CLEAR_FIRST
                        counts['clears'] += 1
                        counts['empty_clears'] += not trace.thought.graph.slots[slot]
                        surviving[slot] = 0
                    else:
                        counts['stops'] += 1
                trace.commit_thought(op)
            if focus and new_turn:
                g = trace.thought.graph
                previous_graph = g
                amounts = [len(slot) for slot in g.slots]
                counts['occupied'] += sum(amounts)
                counts['closed_nonempty'] += int(any(amounts))
                counts['previous_tokens_survived'] += int(any(surviving))
                graphs.append(g.padded())
            logits, _ = trace.score_game()
            if focus and mode == 'normal' and int(obs['cand_mask'].sum()) >= 2:
                p = logits[0].float().softmax(-1)
                if not samples:
                    assert torch.equal(p, trace.probe('normal')), 'sham read changed game policy'
                row = dict(turn=turn, first=new_turn)
                for variant in ('blank', 'rotate'):
                    q = trace.probe(variant)
                    p_arg, q_arg = int(p.argmax()), int(q.argmax())
                    types = obs['cand_type'][0]
                    row[variant] = [float((p-q).abs().sum()/2), int(p_arg != q_arg),
                                    int(types[p_arg]) != int(types[q_arg])]
                samples.append(row)
            action = int(torch.multinomial(logits[0].float().softmax(-1), 1, generator=streams[seat]))
            trace.commit_game(action)
            world.play(action)
            revision += 1
        final = world.result()[target]
    return dict(seed=seed, seat=target, mode=mode, final=final, counts=dict(counts),
                symbol_writes=symbol_writes, graphs=graphs, probes=samples)


def worker(rank, args):
    torch.set_num_threads(1)
    torch.backends.mha.set_fastpath_enabled(False)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    device = 'cpu' if args.device == 'cpu' else f'cuda:{rank}'
    if args.device == 'cuda':
        torch.cuda.set_device(rank)
    state = torch.load(args.checkpoint, map_location='cpu', mmap=True, weights_only=True)
    policy = Policy().eval().to(device)
    policy.load_state_dict(state['model'])
    for index in range(rank, args.maps, args.workers):
        seed = args.seed + index
        for mode in ('normal', 'blank'):
            row = match(policy, args.library, seed, args.turns, mode)
            row.update(games=state['games'], sha256=state.get('source_checkpoint_sha256', args.digest),
                       inference_file_sha256=args.digest)
            target = args.output / f'{seed}-{mode}.json'
            tmp = target.with_suffix('.partial')
            tmp.write_text(json.dumps(row, separators=(',', ':')))
            tmp.replace(target)
            print(json.dumps(dict(stage='audit_game', seed=seed, mode=mode, probes=len(row['probes']),
                                  mean_gdp=row['final']['total_nominal_gdp']/args.turns)),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--digest',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=931200)
    parser.add_argument('--maps',type=int,default=8)
    parser.add_argument('--turns',type=int,default=100)
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    args=parser.parse_args()
    assert sha(args.checkpoint)==args.digest, 'wrong checkpoint'
    args.output.mkdir(parents=True,exist_ok=True)
    if args.workers==1:
        worker(0,args)
    else:
        mp.spawn(worker,args=(args,),nprocs=args.workers,join=True)
