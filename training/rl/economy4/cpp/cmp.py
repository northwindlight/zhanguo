#!/usr/bin/env python3
"""比对 C++ 回放 dump 与参照对局 ref JSON，逐回合逐字段。

用法：cmp.py <ref.json> <dump.txt>
"""
import json
import sys
import shlex

RES_ORDER = ['gold', 'food', 'wood', 'ore', 'oil', 'equipment', 'supply']
RES_IDX = {'gold': 0, 'food': 1, 'wood': 2, 'ore': 3, 'oil': 4, 'equipment': 5, 'supply': 6}
MK = ['food', 'wood', 'ore', 'oil', 'equipment', 'supply']
BLD_NAME = {0: 'castle', 1: 'lumber-camp', 2: 'farm', 3: 'mine', 4: 'oil-well',
            5: 'gold-mine', 6: 'wood-power-plant', 7: 'oil-power-plant',
            8: 'supply-factory', 9: 'equipment-factory', 10: 'barracks',
            11: 'town-hall', 12: 'watchtower', 13: 'diplomatic-center', 14: 'academy',
            15: 'militia-camp'}
UNIT_NAME = {0: 'infantry', 1: 'cavalry', 2: 'militia'}


def S(x):
    return x.split(':')[-1]


def pos(v):
    return int(v[0]), int(v[1])


def load_dump(path):
    turns = {}
    cur = {'S': {}, 'N': {}, 'B': {}, 'A': {}, 'L': {}, 'M': None, 'R': [], 'relations': {}}
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        if line == '---':
            turns.setdefault(cur['turn'], cur)
            cur = {'S': {}, 'N': {}, 'B': {}, 'A': {}, 'L': {}, 'M': None, 'R': [], 'relations': {}}
            continue
        p = line.split()
        tag = p[0]
        if tag == 'S':
            t, a = int(p[1]), int(p[2])
            cur['turn'] = t
            cur['S'][a] = [float(v) for v in p[3:10]]
        elif tag == 'N':
            cur['N'][int(p[2])] = bool(int(p[3]))
        elif tag == 'B':
            t, a, b, x, y = int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5])
            cur['turn'] = t
            cur['B'].setdefault(a, []).append((b, x, y))
        elif tag == 'A':
            t, a, _aid, k, x, y, hp = int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]), int(p[6]), float(p[7])
            cur['turn'] = t
            cur['A'].setdefault(a, []).append((k, x, y, hp))
        elif tag == 'L':
            t, a, land = int(p[1]), int(p[2]), int(p[3])
            cur['turn'] = t
            cur['L'][a] = land
        elif tag == 'M':
            cur['turn'] = int(p[1])
            vals = [float(v) for v in p[2:20]]
            cur['M'] = {'price': vals[0:6], 'inv': [int(v) for v in vals[6:12]],
                        'ref': vals[12:18]}
        elif tag in 'KVCPTW':
            cur['relations'].setdefault(tag, []).append(shlex.split(line)[1:])
        elif tag == 'R':
            if len(p) > 8 and p[8] in ('0', '1'):
                cur['R'].append((int(p[1]), int(p[2]), p[3], int(p[4]), int(p[8])))
    return turns


def compare_relations(after, relations, seats, turn):
    d = after.get('diplo') or {}
    bl = {int(i): (int(chief), name, tuple(sorted(map(int, members))))
          for i, chief, name, *members in relations.get('C', [])}
    actual_blocs = sorted(bl.values())
    expected_blocs = sorted((seats[b['chief']], b['name'], tuple(sorted(seats[n] for n in b['members'])))
                           for b in d.get('alliances', []))
    cols = relations.get('W', [])
    actual_wars = sorted((tuple(sorted(map(int, xs[1:int(xs[0]) + 1]))), tuple(sorted(map(int, xs[int(xs[0]) + 2:]))))
                         for xs in cols)
    expected_wars = sorted((tuple(sorted(seats[n] for n in w['attackers'])),
                            tuple(sorted(seats[n] for n in w['defenders']))) for w in d.get('wars', []))
    actual_vassals = sorted(tuple(map(int, row)) for row in relations.get('V', []))
    expected_vassals = sorted((seats[v['subject']], seats[v['overlord']], v['tribute_percent'])
                              for v in d.get('vassals', []))
    actual_loans = sorted((int(row[0]), float(row[1])) for row in relations.get('K', []))
    expected_loans = sorted((seats[n], a['bank']['loan']['principal']) for n, a in after['actors'].items()
                            if (a.get('bank') or {}).get('loan'))

    def cpp_entity(i):
        return bl[i - 1000][2] if i >= 1000 and i - 1000 in bl else (i,)

    entities = {key: tuple(sorted(seats[n] for n in values)) for key, values in d.get('entity_members', [])}

    def ref_entity(id):
        if id in entities:
            return entities[id]
        if id.startswith('nation/'):
            return (seats[id.removeprefix('nation/')],)
        return ()

    def pact(kind, a, b):
        if kind == 1 and b < a:
            a, b = b, a
        return kind, a, b

    actual_pacts = sorted(pact(k, cpp_entity(a), cpp_entity(b)) for k, a, b in
                          (map(int, p) for p in relations.get('P', [])))
    expected_pacts = sorted(pact(int(p['kind'] == 'defense'), ref_entity(p['a']), ref_entity(p['b']))
                            for p in d.get('pacts', []))
    actual_truces = sorted((tuple(sorted((cpp_entity(int(a)), cpp_entity(int(b))))), int(until))
                           for a, b, until in relations.get('T', []))
    expected_truces = sorted((tuple(sorted((ref_entity(a), ref_entity(b)))), until)
                             for a, b, until in d.get('truces', []) if until > turn)

    return [f"{key}: ref {expected} cpp {actual}"
            for key, actual, expected in [('wars', actual_wars, expected_wars),
                ('alliances', actual_blocs, expected_blocs), ('vassals', actual_vassals, expected_vassals),
                ('pacts', actual_pacts, expected_pacts), ('truces', actual_truces, expected_truces),
                ('loans', actual_loans, expected_loans)] if actual != expected]


def main():
    ref, dump = sys.argv[1], sys.argv[2]
    D = json.load(open(ref))
    actors = list(D['init']['actors'].keys())
    idx = {a: i for i, a in enumerate(actors)}
    turns = load_dump(dump)
    bad = 0
    first = None
    for r in D['log']:
        t = r['turn']
        after = r['after']
        cl = turns.get(t)
        if cl is None:
            print(f"turn {t}: C++ dump missing")
            bad += 1
            break
        msgs = [f"action {actor}.{name}: ref {expected} cpp {actual}"
                for _, actor, name, actual, expected in cl['R'] if actual != expected]
        msgs += compare_relations(after, cl['relations'], idx, t)
        for a in actors:
            i = idx[a]
            live = a in after['actors']
            if i in cl['N'] and cl['N'][i] != live:
                msgs.append(f"{a}: alive differs")
            if not live:
                continue
            ns = cl['S'].get(i, [0] * 7)
            ref_st = {S(x['id']): x['amount'] for x in after['actors'][a]['stocks']}
            for k, j in RES_IDX.items():
                if ref_st.get(k, 0) != ns[j]:
                    msgs.append(f"{a}.{k}: ref {ref_st.get(k, 0)} cpp {ns[j]}")
            ref_b = sorted((BLD_NAME.get(S(k), S(k)), pos(v)) for k, v in after['actors'][a]['bld'])
            cpp_b = sorted((BLD_NAME.get(b, str(b)), (x, y)) for b, x, y in cl['B'].get(i, []))
            if ref_b != cpp_b:
                msgs.append(f"{a}.bld: ref-only {[x for x in ref_b if x not in cpp_b][:4]} "
                            f"cpp-only {[x for x in cpp_b if x not in ref_b][:4]}")
            ref_army = after['actors'][a]['army']
            cpp_army = cl['A'].get(i, [])
            ra = sorted((UNIT_NAME.get(S(k), S(k)), int(x), int(y), float(hp)) for k, x, y, hp in ref_army)
            ca = sorted((UNIT_NAME.get(k, str(k)), x, y, hp) for k, x, y, hp in cpp_army)
            if len(ra) != len(ca) or any(a[:3] != b[:3] or abs(a[3] - b[3]) > 1e-6 for a, b in zip(ra, ca)):
                msgs.append(f"{a}.army: ref {ra[:6]} cpp {ca[:6]}")
            if after['actors'][a]['land'] != cl['L'].get(i, -1):
                msgs.append(f"{a}.land: ref {after['actors'][a]['land']} cpp {cl['L'].get(i, -1)}")
        if cl['M'] is not None:
            for j, g in enumerate(MK):
                if abs(after['market']['prices'][g] - cl['M']['price'][j]) > 1e-6:
                    msgs.append(f"price.{g}: ref {after['market']['prices'][g]:.4f} cpp {cl['M']['price'][j]:.4f}")
                if after['market']['inventory'][g] != cl['M']['inv'][j]:
                    msgs.append(f"inv.{g}: ref {after['market']['inventory'][g]} cpp {cl['M']['inv'][j]}")
                if abs(after['market']['reference'][g] - cl['M']['ref'][j]) > 1e-6:
                    msgs.append(f"ref.{g}: ref {after['market']['reference'][g]:.4f} cpp {cl['M']['ref'][j]:.4f}")
        if msgs:
            bad += 1
            if first is None:
                first = t
                print(f"turn {t}: {len(msgs)} diffs")
                for m in msgs[:15]:
                    print("  ", m)
    print(f"checked {len(D['log'])} turns; bad turns={bad}; first_bad={first}")


if __name__ == '__main__':
    main()
