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
    # ★★ 2026-10-24：**"整数血量"这条前提已经过时** ——
    #   ① C 那边把**摊分**换成**逐位复刻** `Military.equal_allocation`（水位填充），
    #      血量改成 **double**，状态键按 **double 位型**编码 ⇒ 分数血量照样能走；
    #   ② 引擎的 `take_damage` 收的是 **float** ⇒ **打过一仗之后 HP 就是小数**
    #      ⇒ 以前那条 `numeric`（要求整数血量）把**绝大多数真实战斗**挡在 C 外面
    #      （实测：一局里 24 次走 Python、只有 4 次走 C ⇒ C 扩展等于没接上）。
    # ⇒ 现在只要求"血量是有限正数"（C 按 double 收）。
    numeric=all(type(u[1]) in (int,float) and 0<u[1]<=1e9 for row in state0 for u in row)
    # The C state table stores integer HP and distributes remainder by index;
    # equal fractional shares can arise with multiple units or retreat cover.
    # Keep acceleration only where every hit remains an exact integer.
    # ★★ 2026-10-24：那条限制**解开了** —— C 那边的摊分已改成**逐位复刻**
    #   `Military.equal_allocation`（水位填充 + 撤退掩护比例）且血量改 double，
    #   ⇒ **多支军 / 分数掩护**也能走 C 了（以前这两类只能退回 Python，
    #   而 RL 的真实战斗**恰恰是多支军** ⇒ C 扩展对训练根本没用上）。
    #   ⇒ 现在只保留**结构性**上限：≤8 方、每方 ≤8 支、掩护是 0..100 的整数。
    supported=(len(order)<=8 and all(len(row)<=8 for row in state0) and numeric and
               all(unit[3] is None or (type(unit[3]) is int and 0<=unit[3]<=100)
                   for row in state0 for unit in row))
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
    elif use_fast and FAST is not None:fallback='C requires <=8 factions, <=8 units per faction, positive numeric HP and integer retreat cover'
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
