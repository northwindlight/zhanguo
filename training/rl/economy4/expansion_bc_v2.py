"""Balanced expansion imitation with trainable recurrent policy and conditional economy KL."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import pickle
import sys
from zipfile import ZipFile

import numpy as np
import torch
from torch.nn import functional as F


GROUP_WEIGHTS = dict(setup=.18, recruit=.24, attack=.24, supply=.14,
                     materials=.08, finance=.07, end=.05)
PRODUCTION_WEIGHTS = {k:v*.82 for k,v in dict(extraction=.12,power=.08,supply_factory=.12,equipment_factory=.08,
    supply_inputs=.10,fuel=.05,recruit=.14,attack=.14,materials=.05,finance=.07,setup=.02,end=.03).items()}
PRODUCTION_WEIGHTS['economy']=.18
PRODUCTION_WEIGHTS['recruit']-=.04
PRODUCTION_WEIGHTS['attack']-=.04
PRODUCTION_WEIGHTS['demobilize']=.08


def category(obs, action, production_only=False):
    kind = int(obs['cand_type'][action])
    if production_only and kind==9:return 'demobilize'
    if production_only and kind in (1,4):
        choice=int(obs['cand'][action,6:22].argmax())
        if kind==1 and choice==8:return 'supply_factory'
        if kind==1 and choice==9:return 'equipment_factory'
        if kind==1 and choice in (1,2,3,4,5):return 'extraction'
        if kind==1 and choice in (6,7):return 'power'
        if kind==4 and choice==5:raise ValueError('Production-only BC contains a supply purchase')
        if kind==4 and choice in (0,2):return 'supply_inputs'
        if kind==4 and choice in (1,3):return 'fuel'
    if kind == 0:
        return 'end'
    if kind == 1:
        return 'setup'
    if kind == 3:
        return 'recruit'
    if kind in (6, 7, 8):
        return 'attack'
    if kind == 4:
        good = int(obs['cand'][action, 6:22].argmax())
        return 'supply' if good == 5 else 'materials' if good in (0, 4) else 'finance'
    return 'finance'


def load_episodes(paths):
    teachers, anchors, seen = [], [], set()
    for directory in paths:
        for archive in sorted(directory.glob('chunk-*.zip')):
            with ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    if not name.endswith('.pkl'):
                        continue
                    episode = pickle.loads(bundle.read(name))
                    seed = episode['seed']
                    if seed in seen:
                        raise ValueError(f'Duplicate curriculum seed {seed}')
                    seen.add(seed)
                    for field, output in (('teacher', teachers), ('anchor', anchors)):
                        if episode[field]:
                            output.append(dict(seed=seed, features=episode['features'], steps=episode[field]))
    return teachers, anchors


class Dataset:
    def __init__(self, episodes, original, device, production_only=False):
        from military_bc import prepare
        self.rows, self.original_memory = prepare(episodes, original, device)
        features = np.zeros((len(episodes), max(len(e['features']) for e in episodes), original.gru.input_size), np.float32)
        for i, episode in enumerate(episodes):
            features[i, :len(episode['features'])] = episode['features']
        self.features = torch.from_numpy(features).to(device)
        self.device = device
        self.production_only=production_only
        self.weights=PRODUCTION_WEIGHTS if production_only else GROUP_WEIGHTS
        self.groups = defaultdict(list)
        for index, (obs, action, _, _, role) in enumerate(self.rows):
            group='economy' if production_only and role=='economy' else category(obs,action,production_only)
            self.groups[group].append(index)

    def batch(self, indices, policy=None):
        from rl.obs import collate
        rows = [self.rows[int(i)] for i in indices]
        observations, actions, episodes, turns, _ = zip(*rows, strict=True)
        batch = {k: torch.from_numpy(v).to(self.device) for k, v in collate(observations).items()}
        batch['cand_ptr'] = batch['cand_ptr'].long()
        if policy is None:
            batch['mem'] = self.original_memory[list(episodes), list(turns)]
        else:
            unique, reverse = np.unique(episodes, return_inverse=True)
            memory = policy.mem_seq(self.features[unique])
            batch['mem'] = memory[reverse, list(turns)]
        return batch, torch.tensor(actions, device=self.device)

    def draw(self, rng, count):
        names = list(self.groups)
        weights = np.array([self.weights[n] for n in names])
        choices = rng.choice(len(names), count, p=weights/weights.sum())
        return np.array([rng.choice(self.groups[names[i]]) for i in choices])


def economic_mask(batch):
    """Anchor relative economic choices without anchoring the probability of doing military actions."""
    kind = batch['cand_type']
    economic = (kind == 0) | (kind == 1) | (kind == 2) | (kind == 4) | (kind == 5) | (kind == 26)
    military_build = (kind == 1) & ((batch['cand'][..., 16] > .5) | (batch['cand'][..., 21] > .5))
    supply_buy = (kind == 4) & (batch['cand'][..., 11] > .5)
    return economic & ~military_build & ~supply_buy & batch['cand_mask']


def conditional_kl(current, reference, mask):
    current = F.log_softmax(current.masked_fill(~mask, -1e9), -1)
    target = F.softmax(reference.masked_fill(~mask, -1e9), -1)
    return F.kl_div(current, target, reduction='batchmean')


def type_loss(logits, batch, actions):
    kinds = batch['cand_type'].gather(1, actions[:, None])
    matching = (batch['cand_type'] == kinds) & batch['cand_mask']
    return (torch.logsumexp(logits, -1) - torch.logsumexp(logits.masked_fill(~matching, -1e9), -1)).mean()


@torch.no_grad()
def evaluate(policy, original, teachers, anchors, batch_size, limit):
    policy.eval()
    result = {}
    for name, indices in teachers.groups.items():
        # Deterministic equally spaced samples, capped separately per group.
        indices = np.asarray(indices)[np.linspace(0, len(indices)-1, min(limit, len(indices)), dtype=int)]
        ce = exact = kind_ok = legal = probability = count = 0
        for start in range(0, len(indices), batch_size):
            batch, actions = teachers.batch(indices[start:start+batch_size], policy)
            logits = policy(batch)[0]
            best = logits.argmax(-1)
            ce += F.cross_entropy(logits, actions, reduction='sum').item()
            exact += best.eq(actions).sum().item()
            kind_ok += batch['cand_type'].gather(1, best[:, None]).eq(batch['cand_type'].gather(1, actions[:, None])).sum().item()
            legal += batch['cand'][torch.arange(len(best)), best, 0].gt(.5).sum().item()
            probability += logits.softmax(-1).gather(1, actions[:, None]).sum().item()
            count += len(actions)
        result[name] = dict(n=count, ce=ce/count, exact=exact/count, type_accuracy=kind_ok/count,
                            legal=legal/count, teacher_probability=probability/count)
    kl = count = 0
    ids = np.linspace(0, len(anchors.rows)-1, min(limit*2, len(anchors.rows)), dtype=int)
    for start in range(0, len(ids), batch_size):
        chosen = ids[start:start+batch_size]
        batch, _ = anchors.batch(chosen, policy)
        reference, _ = anchors.batch(chosen)
        kl += conditional_kl(policy(batch)[0], original(reference)[0], economic_mask(batch)).item()*len(chosen)
        count += len(chosen)
    result['economic_conditional_kl'] = kl/count
    return result


def train(args):
    sys.path.insert(0, str(args.python_root))
    from rl.model import Policy
    torch.set_num_threads(2)
    torch.manual_seed(args.seed+args.epoch)
    torch.set_float32_matmul_precision('highest')
    torch.backends.mha.set_fastpath_enabled(False)
    rng = np.random.default_rng(args.seed+args.epoch)
    device = torch.device(args.device)
    base = torch.load(args.base, map_location='cpu', weights_only=True)
    original = Policy(**base['cfg']).to(device).eval().requires_grad_(False)
    original.load_state_dict(base['model'])
    policy = Policy(**base['cfg']).to(device)
    previous = torch.load(args.initial, map_location='cpu', weights_only=True)
    policy.load_state_dict(previous['model'])
    # The task is imitation of actions. Preserve the GDP critic and its auxiliary heads.
    for module in (policy.value, policy.exec_head, policy.mem_aux):
        module.requires_grad_(False)
    slow, fast = [], []
    for name, parameter in policy.named_parameters():
        if parameter.requires_grad:
            (fast if name.startswith(('cross.', 'cnorm.', 'logit.', 'proj.c.')) else slow).append(parameter)
    optimizer = torch.optim.AdamW([dict(params=fast, lr=args.lr), dict(params=slow, lr=args.lr*.2)], weight_decay=0)
    if previous.get('expansion_v2', {}).get('optimizer'):
        optimizer.load_state_dict(previous['expansion_v2']['optimizer'])
        for group, rate in zip(optimizer.param_groups, (args.lr, args.lr*.2), strict=True):
            group['lr'] = rate
    teachers, anchors = load_episodes(args.data)
    if args.anchor_data:
        _,extra_anchors=load_episodes(args.anchor_data)
        anchors.extend(extra_anchors)
    if args.production_only:
        for episode in teachers:
            for obs,action,_,_ in episode['steps']:
                category(obs,action,True)
    training = [e for e in teachers if e['seed'] % 5 != 0]
    validation = [e for e in teachers if e['seed'] % 5 == 0]
    # Keep teacher validation maps out of economic gradient updates too.
    anchors_train = [e for e in anchors if e['seed'] % 5 != 0]
    anchors_valid = [e for e in anchors if e['seed'] % 5 == 0]
    if not all((training, validation, anchors_train, anchors_valid)):
        raise ValueError('Missing independent map partitions')
    train_set, valid_set = [Dataset(es,original,device,args.production_only) for es in (training,validation)]
    anchor_train,anchor_valid=[Dataset(es,original,device) for es in (anchors_train,anchors_valid)]
    print(json.dumps(dict(stage='dataset', epoch=args.epoch, teacher=len(train_set.rows),
                          groups={k:len(v) for k,v in train_set.groups.items()}, validation=len(valid_set.rows),
                          anchor=len(anchor_train.rows))), flush=True)
    policy.train()
    total = Counter()
    for step in range(args.steps or (len(train_set.rows)+args.batch-1)//args.batch):
        batch, actions = train_set.batch(train_set.draw(rng, args.batch), policy)
        logits = policy(batch)[0]
        exact = F.cross_entropy(logits, actions)
        grouped = type_loss(logits, batch, actions)
        ids = rng.choice(len(anchor_train.rows), args.batch, replace=False)
        anchor, _ = anchor_train.batch(ids, policy)
        with torch.no_grad():
            reference, _ = anchor_train.batch(ids)
            ref_logits = original(reference)[0]
        kl = conditional_kl(policy(anchor)[0], ref_logits, economic_mask(anchor))
        loss = exact + .5*grouped + args.anchor_weight*kl
        if not torch.isfinite(loss):
            raise ValueError('Non-finite balanced BC loss')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(fast+slow, 1.0)
        optimizer.step()
        total.update(exact=exact.item(), type=grouped.item(), kl=kl.item(), steps=1)
        if (step+1) % 200 == 0:
            print(json.dumps(dict(stage='training', epoch=args.epoch, step=step+1,
                                  **{k:total[k]/total['steps'] for k in ('exact','type','kl')})), flush=True)
    metrics = evaluate(policy, original, valid_set, anchor_valid, args.batch, args.validation_limit)
    output = dict(cfg=base['cfg'], model=policy.state_dict(), upd=base.get('upd',0),
                  expansion_v2=dict(epoch=args.epoch, validation=metrics, optimizer=optimizer.state_dict()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix('.tmp')
    torch.save(output, temp)
    temp.replace(args.output)
    args.output.with_suffix('.json').write_text(json.dumps(metrics, sort_keys=True)+'\n')
    print(json.dumps(dict(stage='saved', epoch=args.epoch, validation=metrics)), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--initial', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data', nargs='+', type=Path, required=True)
    parser.add_argument('--anchor-data', nargs='+', type=Path)
    parser.add_argument('--production-only',action='store_true')
    parser.add_argument('--python-root', type=Path, required=True)
    parser.add_argument('--epoch', type=int, required=True)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--lr', type=float, default=3e-4)
    parser.add_argument('--anchor-weight', type=float, default=.3)
    parser.add_argument('--batch', type=int, default=24)
    parser.add_argument('--seed', type=int, default=256001)
    parser.add_argument('--steps', type=int, default=0)
    parser.add_argument('--validation-limit', type=int, default=128)
    train(parser.parse_args())


if __name__ == '__main__':
    main()
