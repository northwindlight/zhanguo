"""Warm-start military actions from W3 while anchoring the economic policy."""

import argparse
import copy
import gzip
import json
import math
from pathlib import Path
import pickle
import sys
from zipfile import ZipFile

import numpy as np
import torch
from torch.nn import functional as F


def teacher_episodes(path):
    output = []
    for file in sorted(path.glob('*.pkl.gz')):
        with gzip.open(file, 'rb') as stream:
            output.extend(pickle.load(stream))
    return output


def mixed_episodes(path):
    teachers, anchors = [], []
    for archive in sorted(path.glob('chunk-*.zip')):
        with ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if not name.endswith('.pkl'):
                    continue
                episode = pickle.loads(bundle.read(name))
                source = dict(seed=episode['seed'], features=episode['features'])
                if episode['teacher']:
                    teachers.append(dict(source, steps=episode['teacher']))
                if episode['anchor']:
                    anchors.append(dict(source, steps=episode['anchor']))
    return teachers, anchors


def anchor_episodes(path, rng):
    from read_trajectory import episodes

    result = []
    for decisions, _, summary, features, _ in episodes(path):
        if not decisions:
            continue
        indices = rng.choice(len(decisions), min(32, len(decisions)), replace=False)
        steps = [(decisions[i]['obs'], decisions[i]['a'], decisions[i]['ti'], 'anchor') for i in indices]
        result.append(dict(seed=summary['seed'], features=np.stack(features), steps=steps))
    return result


def supply_episodes(path):
    from read_trajectory import episodes
    from rl.candidates import TYPE_ID

    result = []
    for decisions, _, summary, features, _ in episodes(path):
        steps = []
        labeled_turns = set()
        for decision in decisions:
            turn = decision['ti']
            if turn in labeled_turns:
                continue
            obs = decision['obs']
            tiles = obs['tiles']
            size = math.isqrt(len(tiles))
            supply = float(obs['globals'][9])*100
            need = 0
            station = {}
            for unit in obs['armies']:
                if unit[6] < 0.5:
                    continue
                kind = int(unit[:3].argmax())
                if kind == 2:
                    cell = round(float(unit[4])*size)*size + round(float(unit[5])*size)
                    camps = round(float(tiles[cell, 36])*5) if tiles[cell, 17] > 0.5 else 0
                    if station.get(cell, 0) < 2*camps:
                        station[cell] = station.get(cell, 0)+1
                        continue
                need += 2 if kind == 1 else 1
            deficit = 2*need-supply
            if not need or deficit < 0.1:
                continue
            legal = np.flatnonzero((obs['cand_type'] == TYPE_ID['buy'])
                                   & (obs['cand_exec'] > 0.5) & (obs['cand'][:, 11] > 0.5))
            if not len(legal):
                continue
            sizes = np.rint(obs['cand'][legal, 2].astype(np.float32)*60)
            index = int(legal[min(range(len(legal)),
                                  key=lambda i: (sizes[i] < deficit-0.1,
                                                 sizes[i] if sizes[i] >= deficit-0.1 else -sizes[i]))])
            steps.append((obs, index, turn, 'supply'))
            labeled_turns.add(turn)
        if steps:
            result.append(dict(seed=summary['seed'], features=np.stack(features), steps=steps))
    return result


def prepare(group, policy, device):
    steps = []
    features = np.zeros((len(group), max(len(e['features']) for e in group), policy.gru.input_size),
                        np.float32)
    for i, episode in enumerate(group):
        features[i, :len(episode['features'])] = episode['features']
        steps.extend((obs, action, i, turn, kind) for obs, action, turn, kind in episode['steps'])
    with torch.no_grad():
        memory = policy.mem_seq(torch.from_numpy(features).to(device))
    return steps, memory


def model_inputs(group, indices, device):
    from rl.obs import collate

    steps, memory = group
    rows = [steps[i] for i in indices]
    observations, actions, episodes, turns, _ = zip(*rows, strict=True)
    batch = {k: torch.from_numpy(v).to(device) for k, v in collate(observations).items()}
    batch['cand_ptr'] = batch['cand_ptr'].long()
    batch['mem'] = memory[list(episodes), list(turns)]
    return batch, torch.tensor(actions, device=device)


@torch.no_grad()
def evaluate(model, original, teachers, anchors, device, batch_size):
    ce = correct = count = 0
    for start in range(0, len(teachers[0]), batch_size):
        batch, actions = model_inputs(teachers, range(start,min(start+batch_size,len(teachers[0]))), device)
        logits, _, _ = model(batch)
        ce += F.cross_entropy(logits, actions, reduction='sum').item()
        correct += logits.argmax(-1).eq(actions).sum().item()
        count += len(actions)
    kl = anchor_count = 0
    for start in range(0, len(anchors[0]), batch_size):
        batch, actions = model_inputs(anchors, range(start,min(start+batch_size,len(anchors[0]))), device)
        current = F.log_softmax(model(batch)[0], -1)
        target = original(batch)[0].softmax(-1)
        kl += F.kl_div(current, target, reduction='sum').item()
        anchor_count += len(actions)
    return dict(teacher_ce=ce/count, teacher_accuracy=correct/count, anchor_kl=kl/anchor_count)


def train(opts):
    sys.path[:0] = [str(opts.python_root), str(Path(__file__).resolve().parent/'cpp')]
    from rl.model import Policy

    torch.set_num_threads(2)
    torch.manual_seed(opts.seed)
    torch.set_float32_matmul_precision('highest')
    torch.backends.mha.set_fastpath_enabled(False)
    rng = np.random.default_rng(opts.seed)
    device = torch.device(opts.device)
    base = torch.load(opts.checkpoint, map_location='cpu', weights_only=True)
    model = Policy(**base['cfg']).to(device)
    model.load_state_dict(base['model'])
    original = copy.deepcopy(model).eval().requires_grad_(False)
    if opts.initial:
        trained = torch.load(opts.initial, map_location=device, weights_only=True)
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                parameter.lerp_(trained['model'][name], opts.initial_mix)
    model.requires_grad_(False)
    model.cross.requires_grad_(True)
    model.cnorm.requires_grad_(True)
    model.logit.requires_grad_(True)
    parameters = [p for p in model.parameters() if p.requires_grad]
    optim = torch.optim.AdamW(parameters, lr=opts.lr, weight_decay=0)
    if opts.mixed:
        teachers, anchors = mixed_episodes(opts.demos)
    else:
        teachers = teacher_episodes(opts.demos)
        anchors = anchor_episodes(opts.anchors, rng)
    maps = sorted({e['seed'] for e in teachers})
    held_out = set(maps[::5])
    train_teacher = [e for e in teachers if e['seed'] not in held_out]
    valid_teacher = [e for e in teachers if e['seed'] in held_out]
    rescue = supply_episodes(opts.supply_rollouts) if opts.supply_rollouts else []
    if rescue:
        rescue_seeds = sorted({e['seed'] for e in rescue})
        held_out_rescue = set(rescue_seeds[::4])
        train_teacher += [e for e in rescue if e['seed'] not in held_out_rescue]
        valid_teacher += [e for e in rescue if e['seed'] in held_out_rescue]
    if not train_teacher or not valid_teacher or not anchors:
        raise ValueError('Military BC needs demonstrations from separate maps and an economy anchor')
    teach_train = prepare(train_teacher, original, device)
    teach_valid = prepare(valid_teacher, original, device)
    anchor = prepare(anchors, original, device)
    print(json.dumps(dict(stage='dataset', train=len(teach_train[0]), validation=len(teach_valid[0]),
                          anchors=len(anchor[0]), maps=len(maps), captures=len(teachers),
                          supply_labels=sum(len(e['steps']) for e in rescue))), flush=True)
    before = evaluate(model, original, teach_valid, anchor, device, opts.batch)
    print(json.dumps(dict(stage='before', **before)), flush=True)
    best = before['teacher_ce'] + opts.anchor_weight*before['anchor_kl']
    chosen = None
    for epoch in range(1, opts.epochs+1):
        for indices in np.array_split(rng.permutation(len(teach_train[0])),
                                      (len(teach_train[0])+opts.batch-1)//opts.batch):
            batch, actions = model_inputs(teach_train, indices, device)
            teacher_rows = teach_train[0]
            weight = torch.tensor([4 if teacher_rows[i][4] == 'supply' else 1 for i in indices], device=device)
            teacher_loss = (F.cross_entropy(model(batch)[0], actions, reduction='none')*weight).sum()/weight.sum()
            anchor_idx = rng.choice(len(anchor[0]), opts.batch, replace=len(anchor[0]) < opts.batch)
            economy, _ = model_inputs(anchor, anchor_idx, device)
            with torch.no_grad():
                target = original(economy)[0].softmax(-1)
            current = F.log_softmax(model(economy)[0], -1)
            loss = teacher_loss + opts.anchor_weight*F.kl_div(current, target, reduction='batchmean')
            optim.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(parameters, 1.0)
            optim.step()
        after = evaluate(model, original, teach_valid, anchor, device, opts.batch)
        score = after['teacher_ce'] + opts.anchor_weight*after['anchor_kl']
        print(json.dumps(dict(stage='validation', epoch=epoch, **after)), flush=True)
        if score < best:
            best = score
            chosen = dict(epoch=epoch, **after)
            # Adam moments from the GDP PPO belong to the pre-BC parameters.
            output = dict(cfg=base['cfg'], model=model.state_dict(), upd=base.get('upd', 0),
                          military_bc=dict(before=before, chosen=chosen))
            temporary = opts.output.with_suffix('.tmp')
            torch.save(output, temporary)
            temporary.replace(opts.output)
    if chosen is None:
        raise ValueError('Military BC did not improve the held-out objective')
    print(json.dumps(dict(stage='saved', **chosen)), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('demos', type=Path)
    parser.add_argument('anchors', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--python-root', type=Path, required=True)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--seed', type=int, default=250100)
    parser.add_argument('--lr', type=float, default=1e-4)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--anchor-weight', type=float, default=3)
    parser.add_argument('--supply-rollouts', type=Path)
    parser.add_argument('--initial', type=Path)
    parser.add_argument('--initial-mix', type=float, default=0.5)
    parser.add_argument('--mixed', action='store_true', help='Read independent teacher and anchor labels from mixed chunks')
    args = parser.parse_args()
    if not 0 <= args.initial_mix <= 1:
        parser.error('--initial-mix must be in [0, 1]')
    train(args)


if __name__ == '__main__':
    main()
