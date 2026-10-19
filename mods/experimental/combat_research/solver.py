"""Research probability DP adapted from rl-tactics, with current damage rules.

No World, player authority or old rules. Transition callback is supplied by the
current military domain; C reproduces its spread operation and is parity tested.
Unresolved probability mass is reported, never renormalized into certainty.
"""
from itertools import product

try:
    from . import _combat_fast as FAST
    FAST_ERROR=None
except Exception as exc:
    FAST=None
    FAST_ERROR=str(exc)


def solve(order,enemies,initial,power,spread,*,max_states=4000,max_rounds=64,use_fast=True):
    if not 1<=max_rounds<=128 or not 1<=max_states<=50000:raise ValueError('Invalid research DP limits')
    positions={owner:i for i,owner in enumerate(order)}
    enemy_idx=tuple(tuple(positions[o] for o in enemies[owner]) for owner in order)
    damage_cache={}

    def damage(sig):
        if sig in damage_cache:return damage_cache[sig]
        live=[bool(row) for row in sig]
        firing=[i for i in range(len(order)) if live[i] and any(live[j] for j in enemy_idx[i])]
        powers=[power(row) if row else (0,)*6 for row in sig]
        if accelerate:
            result=FAST.agg_damage(sig,enemy_idx,tuple(row[0][4] for row in initial),powers,(1,2,3,4,5,6))
            damage_cache[sig]=result
            return result
        result={};chance=1/(6**len(firing))
        for dice in product(range(6),repeat=len(firing)):
            hits=[0]*len(order)
            for i,face in zip(firing,dice):
                targets=[j for j in enemy_idx[i] if live[j]]
                for j in targets:hits[j]+=max(1,round(powers[i][face]/len(targets)*(100-initial[j][0][4])/100))
            key=tuple(hits);result[key]=result.get(key,0)+chance
        damage_cache[sig]=result
        return result

    # C signatures contain power tokens / retreat flags only. Defense is fixed
    # per faction for this static battle; hp and individual retreat cover vary.
    state0=tuple(tuple(unit[:4] for unit in row) for row in initial)
    backend='python';fallback=FAST_ERROR if use_fast else None
    numeric=all(type(u[1]) is int and 0<u[1]<=65535 for row in state0 for u in row)
    # The C state table stores integer HP and distributes remainder by index;
    # equal fractional shares can arise with multiple units or retreat cover.
    # Keep acceleration only where every hit remains an exact integer.
    supported=(len(order)<=8 and all(len(row)<=1 for row in state0) and numeric and
               all(unit[3] in (None,100) for row in state0 for unit in row))
    accelerate=use_fast and FAST is not None and supported
    if use_fast and FAST is not None and supported:
        kinds=[];indexes={};rows=[]
        for row in state0:
            units=[]
            for kind,hp,retreat,cover in row:
                if kind not in indexes:indexes[kind]=len(kinds);kinds.append(kind)
                units.append((indexes[kind],hp,retreat,cover))
            rows.append(units)
        try:
            dist,rounds,expected,losses,n_states=FAST.assess(len(order),[list(e) for e in enemy_idx],rows,kinds,damage,max_rounds,max_states)
            terminal={frozenset(order[i] for i in range(len(order)) if mask & (1<<i)):p for mask,p in dist}
            rounds=dict(rounds);backend='c'
        except Exception as exc:
            fallback=f'{type(exc).__name__}: {exc}'
            accelerate=False
    elif use_fast and FAST is not None:fallback='C requires integer-only one-unit factions; equal multi-unit shares use Python'
    if backend!='c':
        memo={};n_states=0
        def visit(state,depth):
            nonlocal n_states
            key=(state,depth)
            if key in memo:return memo[key]
            if not any(row and any(state[j] for j in enemy_idx[i]) for i,row in enumerate(state)):
                result=({frozenset(order[i] for i,row in enumerate(state) if row):1.0},{0:1.0},0.0,[0.0]*len(order))
                memo[key]=result;return result
            if depth>=max_rounds or n_states>=max_states:return {},{},0.0,[0.0]*len(order)
            n_states+=1;terminal={};hist={};expected=0.0;losses=[0.0]*len(order)
            sig=tuple(tuple((u[0],u[2]) for u in row) for row in state)
            for hits,chance in damage(sig).items():
                nxt=tuple(spread(row,hits[i]) for i,row in enumerate(state))
                lost=[sum(u[1] for u in row)-sum(u[1] for u in nxt[i]) for i,row in enumerate(state)]
                dist,rh,er,el=visit(nxt,depth+1)
                for survivors,p in dist.items():terminal[survivors]=terminal.get(survivors,0)+p*chance
                for turns,p in rh.items():hist[turns+1]=hist.get(turns+1,0)+p*chance
                expected+=chance*(1+er)
                for i in range(len(order)):losses[i]+=chance*(lost[i]+el[i])
            memo[key]=(terminal,hist,expected,losses);return memo[key]
        terminal,rounds,expected,losses=visit(state0,0)
    resolved=sum(terminal.values())
    return {'p_win':{o:sum(p for s,p in terminal.items() if o in s and not set(enemies[o])&s) for o in order},
            'p_lose':{o:sum(p for s,p in terminal.items() if o not in s and set(enemies[o])&s) for o in order},
            'p_draw':terminal.get(frozenset(),0.0),'rounds':rounds,
            'expected_rounds_partial':expected,'expected_losses_partial':dict(zip(order,losses)),
            'unresolved':max(0.0,1-resolved),'n_states':n_states,'backend':backend,'fallback':fallback}
