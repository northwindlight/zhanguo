"""Capacity diagnostic: fit existing teacher labels with the unchanged network.

Uses checksum-verified synthetic PPO snapshots, independent train/validation
worlds, and classification-only selection. This never promotes a game policy.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import pickle
import random
import time

import numpy as np
import torch
from torch.utils.data import DataLoader

from rl.economy.model import collate, load_policy
from rl.economy.provenance import provenance, require_compatible
from rl.economy.train import metrics


def rows(path):
    metadata = json.loads(path.with_suffix('.json').read_text())
    require_compatible(metadata)
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['sha256']:
        raise ValueError('Trajectory checksum mismatch')
    with gzip.open(path, 'rb') as source:
        streams = pickle.load(source)
    result = [step['row'] for stream in streams for step in stream]
    if not all(row['legal'][row['target']] for row in result):
        raise ValueError('Recorded teacher label is not executable')
    return result, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train', type=Path, required=True)
    parser.add_argument('--valid', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--lr', type=float, default=1e-5)
    parser.add_argument('--batch', type=int, default=4)
    parser.add_argument('--seed', type=int, default=20261113)
    args = parser.parse_args()
    if min(args.epochs, args.batch) < 1 or args.lr <= 0:
        parser.error('Positive epochs, batch size and learning rate required')
    if (args.out/'metrics.json').exists():
        parser.error('Output already exists; use a new diagnostic directory')
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    train, train_meta = rows(args.train)
    valid, valid_meta = rows(args.valid)
    if train_meta['seed'] == valid_meta['seed']:
        parser.error('Train and validation worlds must be independent')
    if {train_meta['seed'], valid_meta['seed']} & {8101, 8102}:
        parser.error('Screening worlds must not enter this diagnostic training')
    model, base = load_policy(args.checkpoint)
    model.to(args.device)
    training = DataLoader(train, batch_size=args.batch, shuffle=True, collate_fn=collate)
    validation = DataLoader(valid, batch_size=args.batch, shuffle=False, collate_fn=collate)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    args.out.mkdir(parents=True, exist_ok=True)
    start = time.time(); records = []; best = float('inf')
    record = dict(epoch=0, valid=metrics(model, validation), seconds=time.time()-start)
    records.append(record); print(json.dumps(record), flush=True)
    for epoch in range(1, args.epochs+1):
        record = dict(epoch=epoch, train=metrics(model, training, optimizer),
                      valid=metrics(model, validation), seconds=time.time()-start)
        records.append(record)
        if record['valid']['loss'] < best:
            best = record['valid']['loss']
            saved = {k: v for k, v in base.items() if k not in ('model', 'optimizer', 'rng', 'metrics')}
            saved.update(**provenance(), model={k: v.detach().cpu() for k, v in model.state_dict().items()},
                config=model.config, fingerprint=model.fingerprint, experimental=True, accepted=False,
                stage='diagnostic_teacher_correction',
                seen_seeds=sorted(set(base.get('seen_seeds', [])) | {train_meta['seed'], valid_meta['seed']}),
                training_objective=dict(type='diagnostic_teacher_correction',
                    train_seeds=[train_meta['seed']], valid_seeds=[valid_meta['seed']], epochs=epoch,
                    lr=args.lr, opening_weight=1., mobilization_weight=1.,
                    selection='validation classification loss; not S promotion'))
            torch.save(saved, args.out/'best.pt')
        (args.out/'metrics.json').write_text(json.dumps(dict(train_rows=len(train), valid_rows=len(valid),
            train_sha256=train_meta['sha256'], valid_sha256=valid_meta['sha256'], records=records), indent=2)+'\n')
        print(json.dumps(record), flush=True)
    (args.out/'complete.json').write_text(json.dumps(dict(epochs=args.epochs, best_loss=best,
                                                       seconds=time.time()-start))+'\n')


if __name__ == '__main__':
    main()
