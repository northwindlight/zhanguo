#!/usr/bin/env python3
"""把 main 引擎录制的参照对局 JSON 转成 C++ replay 能吃的文本。

用法：ref2txt.py <ref.json> <seed> <size> <out.txt>
"""
import json
import re
import sys

RES = {'gold': 'GOLD', 'food': 'FOOD', 'wood': 'WOOD', 'ore': 'ORE', 'oil': 'OIL',
       'equipment': 'EQUIP', 'supply': 'SUPPLY'}
RES_IDX = {'native:gold': 0, 'native:food': 1, 'native:wood': 2, 'native:ore': 3,
           'native:oil': 4, 'native:equipment': 5, 'native:supply': 6}
MK_IDX = {'native:food': 0, 'native:wood': 1, 'native:ore': 2, 'native:oil': 3,
          'native:equipment': 4, 'native:supply': 5}
BLD = {'castle': 0, 'lumber-camp': 1, 'farm': 2, 'mine': 3, 'oil-well': 4, 'gold-mine': 5,
       'wood-power-plant': 6, 'oil-power-plant': 7, 'supply-factory': 8,
       'equipment-factory': 9, 'barracks': 10, 'town-hall': 11, 'watchtower': 12,
       'diplomatic-center': 13, 'academy': 14, 'garrison-farm': 15,
       'militia-camp': 15, 'military-farm': 15}
UNIT = {'infantry': 0, 'cavalry': 1, 'militia': 2}
PEACE_KIND = {'white': 0, 'pay': 1, 'demand': 2, 'submit': 3, 'subjugate': 4}


def pos(site):
    m = re.search(r'(\d+)-(\d+)$', site)
    return int(m.group(1)), int(m.group(2))


def S(x):
    return x.split(':')[-1]


def main():
    ref, seed, size, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    D = json.load(open(ref))
    actors = list(D['init']['actors'].keys())
    idx = {a: i for i, a in enumerate(actors)}
    lines = [f"# seed={seed} size={size} nations={len(actors)} turns={len(D['log'])}"]
    for a in actors:
        v = D['init']['actors'][a]
        th = next((b for b in v['bld'] if b[0] == 'town-hall'), None)
        if th is None:
            raise SystemExit(f"no town-hall for {a}")
        cx, cy = int(th[1][0]), int(th[1][1])
        st = {S(x['id']): x['amount'] for x in v['stocks']}
        order = ['gold', 'food', 'wood', 'ore', 'oil', 'equipment', 'supply']
        lines.append("i %d %d %d %s" % (idx[a], cx, cy,
                                        " ".join(str(st.get(k, 0)) for k in order)))
    prop_id = 1
    bloc_ids = {}
    proposal_kind = {}
    buildings = {}
    for r in D['log']:
        t = r['turn']
        for x in r['actions']:
            act = x['action']
            if act in ('plan', 'end_turn'):
                continue
            a = idx.get(x['actor'])
            if a is None:
                continue
            start = len(lines)
            args = x['args']
            data = x['res'].get('data')
            if act in ('buy', 'sell'):
                g = MK_IDX.get(args.get('resource'))
                if g is None:
                    continue
                lines.append(f"{t} {a} {act} {g} {args['quantity']}")
            elif act == 'build':
                kid = BLD.get(S(args.get('kind', '')))
                site = args.get('site')
                if kid is None or not site:
                    continue
                px, py = pos(site)
                lines.append(f"{t} {a} build {px} {py} {kid}")
                if x['ok'] and isinstance(data, dict) and 'id' in data:
                    buildings[data['id']] = (px, py, kid)
            elif act == 'recruit':
                u = UNIT.get(S(args.get('kind', '')))
                site = args.get('site')
                if u is None or not site:
                    continue
                px, py = pos(site)
                lines.append(f"{t} {a} recruit {px} {py} {int(args.get('count', 1))} {u}")
            elif act == 'move':
                lines.append(f"{t} {a} move {int(args['aid'])} {int(args['x'])} {int(args['y'])}")
            elif act == 'attack':
                ids = ' '.join(str(int(i)) for i in args.get('ids', []))
                lines.append(f"{t} {a} attack {ids} {int(args['x'])} {int(args['y'])}")
            elif act == 'retreat':
                lines.append(f"{t} {a} retreat {int(args['aid'])} {int(args['x'])} {int(args['y'])}")
            elif act == 'disband':
                for i in args.get('ids', []):
                    lines.append(f"{t} {a} disband {int(i)}")
            elif act == 'declare_war':
                lines.append(f"{t} {a} declare_war {idx[args['target']]}")
            elif act == 'guarantee':
                lines.append(f"{t} {a} declare_guarantee {idx[args['target']]}")
            elif act == 'cancel_guarantee':
                lines.append(f"{t} {a} cancel_guarantee {idx[args['target']]}")
            elif act == 'propose_defense':
                proposal_kind[prop_id] = 'pact'
                lines.append(f"{t} {a} propose_pact {idx[args['target']]}")
                prop_id += 1
            elif act == 'propose_vassalage':
                proposal_kind[prop_id] = 'vassal_propose'
                lines.append(f"{t} {a} propose_vassalage {idx[args['target']]}")
                prop_id += 1
            elif act == 'submit_vassalage':
                proposal_kind[prop_id] = 'vassal_submit'
                lines.append(f"{t} {a} submit_vassalage {idx[args['target']]}")
                prop_id += 1
            elif act == 'accept_proposal':
                k = proposal_kind.get(args.get('offer'))
                if k == 'found':
                    lines.append(f"{t} {a} accept_founding")
                elif k == 'pact':
                    lines.append(f"{t} {a} accept_pact")
                elif k in ('vassal_propose', 'vassal_submit'):
                    lines.append(f"{t} {a} accept_vassalage")
            elif act == 'reject_proposal':
                k = proposal_kind.get(args.get('offer'))
                if k == 'pact':
                    lines.append(f"{t} {a} reject_pact")
                elif k in ('vassal_propose', 'vassal_submit'):
                    lines.append(f"{t} {a} reject_vassalage")
            elif act == 'offer_peace':
                k = PEACE_KIND.get(args.get('kind', 'white'), 0)
                if x.get('ok'):
                    prop_id += 1          # 和平提议与邀约共用 main 的提议编号
                lines.append(f"{t} {a} offer_peace {idx[args['target']]} {k} "
                             f"{int(args.get('gold', 0))} {int(args.get('truce', 0))}")
            elif act == 'accept_peace':
                lines.append(f"{t} {a} accept_peace")
            elif act == 'reject_peace':
                lines.append(f"{t} {a} reject_peace")
            elif act == 'gift':
                g = RES_IDX.get(args.get('resource'))
                if g is None:
                    continue
                lines.append(f"{t} {a} gift {idx[args['target']]} {g} {int(args['quantity'])}")
            elif act == 'send_letter':
                lines.append(f"{t} {a} send_letter {idx[args['target']]} {len(args.get('text') or '')}")
            elif act == 'share_map':
                lines.append(f"{t} {a} share_map {idx[args['target']]}")
            elif act == 'spy':
                lines.append(f"{t} {a} spy {idx[args['target']]}")
            elif act == 'borrow':
                lines.append(f"{t} {a} bank_loan")
            elif act == 'bloc_found':
                proposal_kind[prop_id] = 'found'
                prop_id += 1
                bloc_ids.setdefault(args.get('bloc'), len(bloc_ids) + 1)
                name = args.get('name', 'b')
                inv = ' '.join(str(idx[v]) for v in args.get('tos', []))
                lines.append(f"{t} {a} propose_bloc {name} {inv}".rstrip())
            elif act == 'bloc_join':
                lines.append(f"{t} {a} bloc_join {bloc_ids.get(args.get('bloc'), 1)}")
            elif act == 'vote':
                ac = args.get('accept')
                lines.append(f"{t} {a} vote {int(args['vote'])} {2 if ac is None else (1 if ac else 0)}")
            elif act == 'demolish':
                if data and x['ok']:
                    bx, by = pos(data['site'])
                    bid = BLD[S(data['kind'])]
                else:
                    bx, by, bid = buildings.get(args['id'], (-1, -1, -1))
                lines.append(f"{t} {a} demolish {bx} {by} {bid}")
            elif act == 'bloc_leave':
                lines.append(f"{t} {a} bloc_leave")
            elif act == 'bloc_transfer':
                lines.append(f"{t} {a} bloc_transfer {idx[args['target']]}")
            elif act == 'bloc_rename':
                lines.append(f"{t} {a} bloc_rename {args.get('name', 'b')}")
            elif act == 'release_vassal':
                lines.append(f"{t} {a} release_vassal {idx[args['target']]}")
            elif act == 'independence_war':
                lines.append(f"{t} {a} independence_war")
            for i in range(start, len(lines)):
                lines[i] += f" | {int(x['ok'])}"
    open(out, 'w', encoding='utf-8').write("\n".join(lines) + "\n")
    print(f"wrote {out}: {len(lines)} lines, actors={len(actors)}")


if __name__ == '__main__':
    main()
