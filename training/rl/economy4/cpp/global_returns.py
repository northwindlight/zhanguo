"""Four-country global-event returns. Reward features never enter observations."""
import json
from pathlib import Path
import numpy as np
from rewarding import territory_gdp


def global_targets(directory,episodes,scale=3000.):
    wanted={(e[2]['seed'],e[2]['seat']):e for e in episodes};result={};stats=dict(real_gdp_sum=0.,territory_bonus_sum=0.,training_reward_sum=0.,turns=0)
    for path in sorted(Path(directory).glob('*.jsonl')):
        seed=int(path.stem);events=[json.loads(line) for line in path.read_text().splitlines()]
        if [r['event'] for r in events]!=list(range(len(events))):raise ValueError('Broken global event order')
        own={seat:e for (game,seat),e in wanted.items() if game==seed}
        if not own:continue
        rewards=np.zeros(4,np.float64);local={seat:np.full(len(e[0]),np.nan,np.float64) for seat,e in own.items()}
        seen={seat:set() for seat in own};settlements=[]
        for event in reversed(events):
            if event['kind']=='settlement':
                real=np.asarray(event['nominal_gdp'],np.float64)
                bonus=np.array([territory_gdp(max(0,int(n)-int(initial))) for n,initial in zip(event['land'],event['initial_land'])],np.float64)
                rewards+=real+bonus;settlements.append(event)
                for seat in own:
                    stats['real_gdp_sum']+=real[seat];stats['territory_bonus_sum']+=bonus[seat];stats['training_reward_sum']+=real[seat]+bonus[seat];stats['turns']+=1
            elif event['kind']=='decision':
                if not 0<=event['snapshot']<=event['event']:raise ValueError('Future observation in global trace')
                seat=event['seat']
                if seat not in own:continue
                index=event['index'];rows=own[seat][0]
                if not 0<=index<len(rows) or index in seen[seat]:raise ValueError('Duplicate/missing global decision')
                if rows[index]['ti']!=event['turn'] or int(rows[index]['obs']['cand_type'][rows[index]['a']])!=event['type']:
                    raise ValueError('Global event does not match actor trajectory')
                local[seat][index]=rewards[seat]/scale;seen[seat].add(index)
            else:raise ValueError('Unknown global event kind')
        settlements.reverse()
        for seat,episode in own.items():
            if not np.isfinite(local[seat]).all():raise ValueError('Incomplete global reward alignment')
            actual=[r['nominal_gdp'][seat] for r in settlements]
            if not np.allclose(actual,episode[2]['nominal_gdp'],rtol=1e-6,atol=.001):raise ValueError('Global GDP differs from real trajectory GDP')
            result[seed,seat]=local[seat]
    if result.keys()!=wanted.keys():raise ValueError('Missing global event file')
    return result,stats


def sequence_indices(length,start,count):
    """Consecutive decisions of one country; foreign events remain in the global return."""
    if not 0<=start<length-1 or not 2<=count<=4:raise ValueError('Invalid action sequence')
    return list(range(start,min(length,start+count)))
