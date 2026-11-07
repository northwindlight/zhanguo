"""Native distribution scenario and administrative APIs. No host game rules."""
import random
from engine.player import wire, BUILTINS
from engine.session import SaveFormatError
from engine.scenario import GridSession
from .observer import NativeObserver

# 开局木料 60 → 200（用户 2026-10-17：前期木是硬瓶颈，开局先给够一批建材）。
START_RES={'native:gold':1500,'native:food':20,'native:wood':200,'native:ore':10,
           'native:oil':0,'native:equipment':5,'native:supply':10}

class NativeSession(NativeObserver,GridSession):
    def _client_cfg(self,item,joined_at=0):
        cfg=super()._client_cfg(item,joined_at)
        if cfg.get('client')=='rule' and cfg.get('rule_seed') is None:
            # Sample independently per nation, reproducibly across delayed
            # arrivals and save/load, using the world's persisted map seed.
            cfg['rule_seed']=random.Random(f'{self.grid()[1]}:{item["id"]}:rule').randrange(2**63)
        return cfg

    def _settle_end_turn(self,player):
        """回合末按行动轮换次序统一执行的收尾：自动交易与外交拒签。

        这些效果只与既有轮换次序有关，与谁先结束行动无关；因此不挂在
        end_turn 提交上，而是在 before_settlement 里按 turn_order 执行。"""
        automatic=self.runtime.object_call(self.world,'native:market','run_auto_trades',player.actor)
        if automatic['orders']:
            self._record_automatic(player,'auto_trade',{},automatic,'auto_trades')
        rejected=self.runtime.object_call(self.world,'native:diplomacy','finish',player.actor)
        if rejected['offers'] or rejected['votes']:
            self._record_automatic(player,'relations',{},rejected,'diplomacy_finish')
        return rejected

    def _record_automatic(self,player,action,arguments,data,source):
        self.record(player,action,arguments,{'api':1,'actor':player.actor,'action':action,'period':self.clock(),
            'ok':True,'status':'effective','code':None,'message':'','data':wire(data),'automatic':source})

    @property
    def arrival_distance(self):return 8 if self.grid()[0]>=32 else 3
    def entries(self):return self.cfg['nations']
    def identities(self):return tuple(n.id for n in self.world.nations.snapshot())
    def configure(self):
        seed=self.cfg.get('seed')
        if seed is None:seed=random.SystemRandom().randrange(2**31)
        self.world.territory.configure(self.cfg.get('map_size',60),seed)
    def grid(self):return self.world.territory.dimensions()
    def occupied(self):return [tile.tile.position for tile in self.world.territory.snapshot()]
    def valid_position(self, position):
        x,y=position;size,_=self.grid()
        if not (1<x<size-2 and 1<y<size-2):return False
        return all(self.world.territory.terrain(tx,ty).passable and not self.world.territory.cell(tx,ty).tile.id
                   for tx,ty in ((x,y),(x-1,y),(x+1,y),(x,y-1),(x,y+1)))
    def validate_boundary(self):
        expected={n['id'] for n in self.entries() if n['client']=='human' and n['id'] in self.roster}
        if self.world.government.human_actors()!=expected:
            raise SaveFormatError('Human seats differ from saved government control')
        if any(wire(self.world.government.snapshot(a))['ended'] for a in self.actors()):
            raise SaveFormatError('Cannot save a partially played cycle')

    def after_spawn(self):
        self.world.intelligence.capture_maps(self.world.government.human_actors())
        self.world.intelligence.publish_maps()

    def _record_production_start(self, player, action, arguments, result, source='production_queue'):
        data=wire(result);status=data.get('status','effective') if isinstance(data,dict) else 'effective'
        self.record(player,action,arguments,{'api':1,'actor':player.actor,'action':action,'period':self.clock(),
            'ok':status not in ('failed','cancelled'),'status':status,'code':'action_failed' if status in ('failed','cancelled') else None,'message':data.get('message','') if isinstance(data,dict) else '',
            'data':data,'automatic':source})

    def cycle(self,**callbacks):
        if self.victory_event is not None:raise ValueError('Game has ended with a victory event')
        self._boundary=False;self._batch_callback=callbacks.get('on_batch')
        try:receipts=super().cycle(**callbacks)
        finally:self._batch_callback=None
        # Knowledge is sampled after the unchanged domain stage sequence.
        self.world.intelligence.capture_maps();self.world.intelligence.publish_maps()
        return receipts

    def action_conflicts(self, actors):
        """行动期互斥对由军队域判定：可达/占据/军令目标可能重叠的两国顺序处理。"""
        return self.world.military.action_conflicts(actors)

    def before_settlement(self):
        """行动期结束、统一结算开始前：推进预付的建设与征兵队列。

        这两类都是"提交时预付、回合末开工"的操作；军令与手动移动/进攻仍是
        行动期即时执行（dynamic-turn 的行动期由玩家自行调度），不在此列。
        自动交易与外交拒签也只在这里按既有轮换次序执行，与谁先结束行动无关。"""
        for actor in self.turn_order():
            try:player=self.player(actor)
            except (ValueError,KeyError):continue
            if not player.action_state()['active']:continue
            if not player.action_state()['can_act']:self._settle_end_turn(player)
            for receipt in self.world.buildings.advance_orders(actor):
                place=self.world.buildings.site(receipt.site).position
                self._record_production_start(player,'build',{'kind':receipt.kind,'x':place[0],'y':place[1]},receipt)
            for row in self.world.military.advance_recruit_orders(actor):
                place=row['position']
                self._record_production_start(player,'military',{'action':'recruit','kind':row['kind'],'count':row['count'],'x':place[0],'y':place[1]},row['units'])
            if self._batch_callback:self._batch_callback(actor,None)

    def before_actor(self, player):
        actor=player.actor
        if not self.world.military.has_march_orders(actor):return False
        def observe():return self.player(actor).observe()
        def record_march(row):
            point=row['position']
            self._record_production_start(self.player(actor),'military',{'action':row['mode'],'ids':[row['aid']],'x':point[0],'y':point[1]},row['receipt'],source='march_orders')
        return bool(self.world.military.advance_march_orders(actor,observe(),refresh=observe,on_start=record_march))

    def record(self,player,action,arguments,receipt,error=None):
        tool=self.runtime.tools.get(action)
        if receipt and receipt.get('ok') and action not in BUILTINS and (tool is None or not tool.read_only):
            self.world.intelligence.capture_maps()
        return super().record(player,action,arguments,receipt,error)

    def finished(self):
        future=any(n.get('enabled',True) and n['id'] not in self.roster and n.get('appearance')=='scheduled' for n in self.entries())
        return super().finished() or (len(self.actors())<=1 and len(self.roster)>1 and not future)

    def board_rows(self):
        rows=[]
        for actor in self.world.nation_actors:
            obs=self.player(actor).observe();stocks={s['id']:s['amount'] for s in obs['facts']['econ']['stocks']}
            rows.append({'name':self.roster[actor]['name'],'land':len(obs['facts']['land']),
                         'army':sum(u['owner']==actor for u in obs['facts']['army']),'gold':stocks.get('native:gold',0)})
        return rows


    def spawn(self,item,position):
        actor=item['id'];size,_=self.world.territory.dimensions()
        if not isinstance(position,(tuple,list)) or len(position)!=2 or any(type(v) is not int for v in position):raise ValueError('Invalid start position')
        x,y=position;self.world.territory.check(x,y)
        points=[(x+dx,y+dy) for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1)) if 0<=x+dx<size and 0<=y+dy<size]
        if any(self.world.territory.cell(*point).tile.id for point in points):raise ValueError('Start overlaps existing territory')
        if not self.valid_position((x,y)):
            choices=sorted(((tx,ty) for tx in range(2,size-2) for ty in range(2,size-2)),
                           key=lambda p:(max(abs(p[0]-x),abs(p[1]-y)),(p[0]-x)**2+(p[1]-y)**2,p))
            safe=next((p for p in choices if self.valid_position(p)),None)
            if safe is None:raise ValueError('没有足够的连通陆地安置完整五格开局；请增大地图或更换种子')
            x,y=safe
            points=[(x+dx,y+dy) for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1))]
        polity=item.get('polity')
        if polity and self.runtime.find_policy(polity) is None:raise ValueError('Polity module missing')
        period=self.clock()
        self.world.nations.create(actor,item['name'],{**START_RES,**item.get('resources',{})})
        if item['client']=='human':self.world.government.set_human(actor)
        for tx,ty in points:
            site=f'{actor}/land-{tx}-{ty}'
            self.world.territory.create(site,actor,tx,ty,terrain='native:plain' if (tx,ty)==(x,y) else None,core=actor)
            if (tx,ty)==(x,y):
                self.world.buildings.seed(site,'native:town-hall')
                capital=site
        if polity:
            start={key:item[field] for key,field in (('Gold','start_gold'),('Supply','start_supply'),('Cavalry','start_cavalry')) if field in item}
            self.runtime.activate_actor(self.world,actor,polity,site=capital,start=start)
        self.world.succession.enroll(actor)
        self.world.intelligence.emit('appearance',item['name']+' 登场',public=True)
        return actor
