"""Private research capability. No player tools or model-facing schema."""
from copy import deepcopy
from engine.runtime import GameMod
from .solver import solve

# ★★ 记忆化（2026-10-24 加）：`assess` 是**纯函数** ——
#   它的**全部**输入都进了签名（参战各方的战力token/血量/撤退掩护/减伤、
#   战争关系、地形与城堡减伤、进攻方原型的**兵种**、骰子、DP 上限）
#   ⇒ **同一签名 ⇒ 同一结果** ⇒ 算一次就够。
#   ★ 为什么值得记：观测路径**每一步**都要问"这一格打起来我赢面多大"，
#     而一局里这些签名**高度重复**（实测 size8/t_max60：
#     **682 次调用只有 47 个唯一签名 ⇒ 93% 是重复**）
#     ⇒ 不记 ⇒ 同一个局面反算几百遍；实测 `assess` 占**全局 65%**
#     （每局 34.6 s / 53.4 s，每次调用 50.8 ms）
#     ⇒ 记下来：真路径 A/B（**同一个网、同一个种子、431 步**）⇒
#       **DP 真算 738 次 → 170 次（少 77%）**、**整局快 2.10×**（51.9 s → 24.7 s），
#       且 431 步的 (玩家, 动作, logp) **逐位相同** ✔（记忆化没改任何概率）。
#   ★ 记在**引擎这边**（不是 RL 侧缓存）：签名必须由**读输入的那一方**算 ——
#     RL 侧替引擎猜它读了什么 ⇒ 引擎哪天多读一个字段 ⇒ **概率静默过期**。
#   ★★ **必须是模块级**（不是实例属性）：`runtime.derived` 只在**读视图**里
#     缓存能力对象 ⇒ **每次调用都重造 `Forecast`** ⇒ 挂在实例上的缓存
#     **当场就死**（实测 738 次调用**零命中**）。而签名是**完备**的
#     （DP 只看 `order`/`enemies`/`initial`/骰子/上限）⇒ **跨局复用也安全**。
_MEMO = {}
_MEMO_CAP = 1024          # ★ 兜底：1024 份 × 几百字节 ⇒ 约 1 MB
_MEMO_HITS = 0
_MEMO_MISSES = 0


class Forecast:
    def __init__(self,call,settings,die):self.call=call;self.settings=settings;self.die=die
    def assess(self,actor,ids,x,y):
        self.call('native:territory','check',x,y)
        if not isinstance(ids,(list,tuple)) or not ids or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('Unique own unit IDs required')
        visible=self.call('native:army','visible_units',actor)
        owned={u.id:u for u in visible if u.owner==actor and u.alive}
        if any(i not in owned for i in ids):raise ValueError('Own living units required')
        if not self.call('native:vision','visible',actor,x,y):
            return {'research':True,'known':False,'winnable':None,'probabilities':None}
        selected=[deepcopy(owned[i]) for i in ids]
        troops=[u for u in visible if (u.x,u.y)==(x,y) and u.alive and not (u.owner==actor and u.id in ids)]
        for u in selected:u.engaged=True
        troops+=selected;groups={}
        for u in troops:groups.setdefault(u.owner,[]).append(u)
        active={u.owner for u in troops if u.engaged and u.owner!='野人'}
        enemies={owner:tuple(other for other in groups if other!=owner and
                    ((owner=='野人' and other in active) or (other=='野人' and owner in active) or
                     owner!='野人' and other!='野人' and self.call('native:diplomacy','war_between',owner,other))) for owner in groups}
        tile=self.call('native:territory','cell',x,y);terrain=self.call('native:territory','terrain',x,y)
        castle=0
        if tile.tile.id:
            castle=self.call('native:buildings','effects',tile.tile.id,'native:castle').get('defense_per_level',0)
            castle*=self.call('native:buildings','count',tile.tile.id,'native:castle')
        order=tuple(sorted(groups));initial=[]
        for owner in order:
            defense=self.call('native:army','defense_pct',terrain.defense,castle if owner==tile.owner else 0)
            cover=defense if owner==tile.owner or owner=='野人' or owner not in active else 0
            initial.append(tuple((self.call('native:army','faction_power',[u]),u.hp,False,
                                  u.retreat_cover if u.retreat_cover is not None else 100,cover) for u in groups[owner]))
        prototype=selected[0]
        # ★★★ **签名**（2026-10-24）：把 DP 的**全部**输入收进一个可哈希的键 ——
        #   · `order`/`enemies` ⇒ 谁跟谁在打（战争关系）；
        #   · `initial` ⇒ 每一方每一支的 (战力token, 血量, 撤退掩护, 减伤)
        #     —— token 已含兵种的攻击力与"是否要撤"（`faction_power`）；
        #   · `prototype.kind` ⇒ ★ **原型兵的兵种**：`spread` 是拿原型**复制**出来的
        #     （`spread_damage` 只看掩护/血量/承伤，**不看兵种**）
        #     ⇒ 兵种只经由"原型是谁"影响摊分 ⇒ 必须进签名；
        #   · `self.die` ⇒ 骰子修正（世界常量，但**进键更保险**）；
        #   · DP 上限与胜负阈值 ⇒ 常量，但同样进键（换配置不该吃旧缓存）。
        #   ★ 少进一项 ⇒ 概率**静默过期**（不报错）⇒ 这是本条最该盯的地方。
        key=(order,tuple((owner,enemies[owner]) for owner in order),tuple(initial),
             prototype.kind,tuple(sorted(self.die.items())),
             self.settings['MaxStates'],self.settings['MaxRounds'],
             self.settings['WinThreshold'])
        cached=_MEMO.get(key)
        if cached is not None:
            global _MEMO_HITS
            _MEMO_HITS+=1
            return deepcopy(cached)        # ★ 返回**副本** ⇒ 调用方改不坏缓存
        def spread(row,damage):
            if not row:return ()
            units=[]
            for token,hp,retreat,cover in row:
                u=deepcopy(prototype);u.hp=hp;u.retreat_cover=cover;units.append(u)
            self.call('native:army','spread_damage',damage,units)
            return tuple((row[i][0],u.hp,row[i][2],row[i][3]) for i,u in enumerate(units) if u.alive)
        def power(sig):
            total=sum(token for token,retreat in sig)
            return tuple(max(1,total*(100+self.die[face])//100) for face in sorted(self.die))
        odds=solve(order,enemies,tuple(initial),power,spread,
                   max_states=self.settings['MaxStates'],max_rounds=self.settings['MaxRounds'])
        result={'research':True,'known':True,'winnable':odds['p_win'][actor]>=self.settings['WinThreshold'],
                'probabilities':odds,'win_threshold':self.settings['WinThreshold']}
        global _MEMO_MISSES
        _MEMO_MISSES+=1
        if len(_MEMO)>=_MEMO_CAP:_MEMO.clear()   # ★ 兜底：不许无限长
        _MEMO[key]=deepcopy(result)
        return result


class CombatResearchMod(GameMod):
    def configure(self,declaration,path):
        super().configure(declaration,path)
        from engine.config import Field,Schema
        fields={'MaxStates':Field((int,),minimum=1,maximum=50000),
                'MaxRounds':Field((int,),minimum=1,maximum=128),
                'WinThreshold':Field((int,float),minimum=0,maximum=1)}
        settings=declaration.get('Research',{})
        if set(settings)!=set(fields):raise ValueError('Research must declare all DP limits')
        Schema(fields).normalize(settings,path='Research')

    def mixins(self):return ()
    def objects(self,world):
        return {'research:combat-probabilities':Forecast(
            lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),self.declaration['Research'],
            self.runtime.number('army.combat-die'))}
