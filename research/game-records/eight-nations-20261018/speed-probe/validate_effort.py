"""Offline exhaustive oracle and answer checks for the six-project effort probe."""
import json
from pathlib import Path

projects = [
    ('A',38,12,1,82,[]), ('B',33,10,1,73,[]), ('C',27,9,1,64,[]),
    ('F',29,11,0,60,[]), ('G',21,7,0,44,[]), ('L',19,4,0,39,[]),
]

# Exhaustively solve this finite instance independently of model answers.
best = None
optimal = []
for mask in range(1 << len(projects)):
    chosen = [p for i,p in enumerate(projects) if mask >> i & 1]
    ids = {p[0] for p in chosen}
    if any(not set(p[5]) <= ids for p in chosen):
        continue
    load = sum(p[3] for p in chosen)
    for gen in range(3):
        if len(chosen) + gen > 5 or 2 * gen < load:
            continue
        wood_cost = sum(p[2] for p in chosen) + 8 * gen
        buy = max(0, wood_cost - 40)
        sell = max(0, 40 - wood_cost)
        gold = 180 + 60 + 3 * sell - 5 * buy - 8 - sum(p[1] for p in chosen) - 15 * gen
        if gold < 95:
            continue
        score = (sum(p[4] for p in chosen), gold, 0)
        row = {'projects':sorted(ids), 'generators':gen, 'buy_timber':buy, 'sell_timber':sell,
               'final_gold':gold, 'total_value':score[0]}
        if best is None or score > best:
            best = score; optimal = [row]
        elif score == best:
            optimal.append(row)

def validate(row):
    text = row.get('answer','').strip()
    if text.startswith('```'):
        text = text.split('\n',1)[1].rsplit('```',1)[0].strip()
    try:
        answer = json.loads(text)
    except ValueError:
        return {'json_valid': False, 'feasible':False, 'optimal':False}
    try:
        ints = ['generators','sell_equipment','buy_timber','sell_timber','buy_supplies']
        if any(type(answer[k]) is not int or answer[k] < 0 for k in ints):
            raise ValueError('nonnegative integer')
        g,se,bw,sw,bs = [answer[k] for k in ints]
        ids = answer['selected_projects']
        if len(set(ids)) != len(ids) or not set(ids) <= {p[0] for p in projects}:
            raise ValueError('project ids')
        if g > 2 or se > 10 or sw > 40+bw or len(ids)+g > 5:
            raise ValueError('quantity bound')
        gold, wood, supply, equipment = 180+6*se-5*bw+3*sw-8*bs, 40+bw-sw, 2+bs, 10-se
        capacity, load, built, ng, value = 4,4,set(),0,0
        by_id={p[0]:p for p in projects}
        for item in answer['construction_order']:
            if item == 'GENERATOR':
                gold-=15;wood-=8;capacity+=2;ng+=1
            else:
                project=by_id[item]
                if item in built or item not in ids or not set(project[5]) <= built:
                    raise ValueError('construction order')
                gold-=project[1];wood-=project[2];load+=project[3];value+=project[4];built.add(item)
            if min(gold,wood) < 0 or load > capacity:
                raise ValueError('intermediate feasibility')
        if ng != g or built != set(ids) or gold < 95 or supply < 3:
            raise ValueError('final feasibility')
        expected = {'final_gold':gold,'final_timber':wood,'final_supplies':supply,
                    'final_equipment':equipment,'final_capacity':capacity,'final_load':load,'total_value':value}
        balances = all(answer.get(k) == v for k,v in expected.items())
        return {'json_valid': True, 'feasible':True, 'balances_correct':balances,
                'optimal':(value,gold,wood) == best, 'computed':expected}
    except (KeyError,ValueError,TypeError) as exc:
        return {'json_valid':True,'feasible':False,'optimal':False,'error_type':type(exc).__name__}


def evaluate_response(row):
    original = row.get('answer', '').strip()
    try:
        strict = isinstance(json.loads(original), dict)
    except ValueError:
        strict = False
    decoded = None
    decoder = json.JSONDecoder()
    for i, char in enumerate(original):
        if char != '{':
            continue
        try:
            candidate, _ = decoder.raw_decode(original[i:])
        except ValueError:
            continue
        if isinstance(candidate, dict) and 'selected_projects' in candidate:
            decoded = candidate
            break
    if decoded is None:
        return {'strict_json_only': strict, 'plan_extractable': False, 'feasible': False, 'optimal': False}
    check = validate({'answer': json.dumps(decoded)})
    check.pop('json_valid', None)
    return {'strict_json_only': strict, 'plan_extractable': True, **check}

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('result_file')
    args = parser.parse_args()
    path = Path(args.result_file)
    result = json.loads(path.read_text())
    result['oracle'] = {'score': best, 'optimal_plans': optimal}
    for row in result['requests']:
        row['validation'] = evaluate_response(row)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + chr(10))
    print(json.dumps({'oracle': result['oracle'], 'checks': [r['validation'] for r in result['requests']]}, ensure_ascii=False))
