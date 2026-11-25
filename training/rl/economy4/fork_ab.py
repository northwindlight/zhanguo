"""Replace both continuation inputs with a verified A checkpoint and its Adam state."""
import argparse
import json
from pathlib import Path
import shutil
import time

from mixed_bc_run import digest, publish


def fork_a(work, destination, history):
    parent = json.loads((work / 'state.json').read_text())
    current = json.loads((destination / 'resume-state.json').read_text())
    source = work / parent['models']['A']['latest']
    source_sha = parent['models']['A']['sha256']
    if digest(source) != source_sha:
        raise ValueError('A checkpoint checksum mismatch')
    if parent['games'] != current['games'] or parent['contract'] != current['contract']:
        raise ValueError('Fork requires the current verified continuation boundary')
    # Retain the previous pair before changing either input. The stopped watcher
    # must not publish this dataset until the new pair manifest has been written.
    history.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(destination / 'resume-state.json', history / 'previous-state.json')
    for identity, row in current['models'].items():
        checkpoint = destination / row['latest']
        if digest(checkpoint) != row['sha256']:
            raise ValueError('Previous input checkpoint checksum mismatch')
        shutil.copyfile(checkpoint, history / (identity + '-previous.pt'))
    models = {}
    for identity in ('A', 'B'):
        target = destination / (identity + '-fork-A.pt')
        temporary = target.with_suffix('.partial')
        shutil.copyfile(source, temporary)
        if digest(temporary) != source_sha:
            raise ValueError('Fork checkpoint copy corrupted')
        temporary.replace(target)
        models[identity] = dict(latest=target.name, sha256=source_sha)
    event = dict(kind='replace_B_with_A', games=parent['games'], batches=parent['batches'],
                 parent_policy='A', parent_sha256=source_sha, optimizer='preserved', time=time.time())
    replacement = dict(parent, models=models,
                       branch_events=current.get('branch_events', []) + [event])
    publish(destination / 'resume-state.json', replacement)
    publish(history / 'fork.json', event)
    return replacement


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('work', 'destination', 'history'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fork_a(args.work, args.destination, args.history), indent=2))
