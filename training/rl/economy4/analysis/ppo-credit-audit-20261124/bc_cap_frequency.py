"""Count old 30-attempt turns, including the final turn recovered from totals."""
from collections import Counter
import json
from pathlib import Path
import pickle
import re
from zipfile import ZipFile

import numpy as np

ROOT=Path(__file__).resolve().parents[2]


def run():
    totals=Counter();per_episode=[];turn_hist=Counter();phase={k:Counter() for k in ('before_turn30','turn30_onward','clean_teacher_record_turns')}
    for archive in sorted((ROOT/'industry-bc-results/chunks').glob('chunk-*.zip')):
        with ZipFile(archive) as bundle:
            footers={}
            for line in bundle.read('rollout.log').decode().splitlines():
                if not line.startswith('game '):continue
                row=dict(re.findall(r'(\w+)=(\S+)',line))
                key=(int(row['seed']),int(row['seat']))
                if key in footers:raise ValueError('Duplicate footer')
                footers[key]=row
            for name in bundle.namelist():
                if not name.endswith('.pkl'):continue
                item=pickle.loads(bundle.read(name));seed=item['seed'];row=footers[seed,seed%4]
                features=np.asarray(item['features']);turns=len(features)
                if turns!=int(row['turns']):raise ValueError('Turn count mismatch')
                previous_float=features[1:,73]*30
                previous=np.rint(previous_float).astype(int)
                if np.max(np.abs(previous-previous_float),initial=0)>1e-4:raise ValueError('Unrecognized step feature')
                # Every closed country turn contributes one EndTurn decision;
                # all other decisions increment its attempt counter, including
                # failed or suppressed actions. Recover the final missing row.
                final=int(row['decisions'])-turns-int(previous.sum())
                steps=np.append(previous,final)
                if np.any(steps<0) or np.any(steps>30):raise ValueError(f'Invalid reconstructed steps for {seed}: {steps}')
                caps=steps==30;hits=int(caps.sum());teacher_turns={int(r[2]) for r in item['teacher']}
                clean=bool(item['outcome']['reached'] and not item['outcome']['teacher_short_turns'] and not item['outcome']['teacher_deaths'])
                totals.update(episodes=1,country_turns=turns,capped_turns=hits,episodes_with_cap=int(hits>0),
                              final_turn_caps=int(final==30),actual_attempts=int(steps.sum()),end_decisions=turns)
                if clean:totals.update(clean_episodes=1,clean_turns=turns,clean_caps=hits,clean_episodes_with_cap=int(hits>0))
                for ti,n in enumerate(steps):
                    turn_hist[int(n)]+=1;key='before_turn30' if ti+1<30 else 'turn30_onward'
                    phase[key].update(turns=1,caps=int(n==30))
                    if ti in teacher_turns:phase['clean_teacher_record_turns'].update(turns=1,caps=int(n==30))
                per_episode.append(dict(seed=seed,turns=turns,caps=hits,clean=clean,final_steps=int(final)))
    result=dict(totals=dict(totals),cap_turn_fraction=totals['capped_turns']/totals['country_turns'],
        cap_episode_fraction=totals['episodes_with_cap']/totals['episodes'],phases={k:dict(v) for k,v in phase.items()},
        attempts_histogram=dict(sorted(turn_hist.items())),episodes=per_episode,
        definition='The demonstration country (seat=seed%4) reached 30 attempted game actions in a completed turn, including model-led and scripted/hybrid phases.',
        final_turn_recovery='footer.decisions - completed_turns - sum(previous_turn_steps); exactly one end decision per closed turn.',
        limitation='Reaching the cap is not proof that an additional useful action was rejected. Processed samples do not retain every boundary decision or rejected tutor request; exact forced-truncation count cannot be reconstructed.')
    Path(__file__).with_name('bc-cap-frequency.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('episodes','attempts_histogram')},indent=2))


if __name__=='__main__':run()
