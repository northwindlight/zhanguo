"""Human-facing application interface, shared by transports and future GUI.

Every game request uses the actor-bound public API. No observer projection,
private foreign accounts, hidden terrain, or rule mutation is used for views.
"""
from copy import deepcopy
from .army.candidates import candidates, command_states
from .tools.commands import COMMANDS

LABELS = {
    'queue_orders':'规划路线','cancel_orders':'取消路线',
    'queue_build':'加入建设队列','cancel_build_order':'取消待建','queue_recruit':'加入征兵队列','cancel_recruit_order':'取消待征',
    'build':'建设','demolish':'拆除建筑','recruit':'征兵','move':'移动','attack':'攻击','retreat':'撤退','disband':'遣散',
    'buy':'买入','sell':'卖出','auto_trade':'自动交易','remove_auto_trade':'删除自动交易','borrow':'贷款','buy_report':'购买经济报表','buy_map':'购买地图',
    'send_letter':'写信','gift':'赠礼','share_map':'共享地图','spy':'派谍',
    'guarantee':'保障','cancel_guarantee':'取消保障','propose_defense':'共同防御','break_defense':'解除共防',
    'accept_proposal':'接受提议','reject_proposal':'拒绝提议','declare_war':'宣战','offer_peace':'求和',
    'accept_peace':'接受议和','reject_peace':'拒绝议和','propose_vassalage':'提出附庸','release_vassal':'释放附庸',
    'independence_war':'独立战争','submit_vassalage':'称臣','vote':'投票',
    'bloc_found':'创建联盟','bloc_join':'申请入盟','bloc_leave':'退出联盟','bloc_dissolve':'解散联盟',
    'bloc_transfer':'移交盟主','bloc_rename':'联盟改名','end_turn':'结束回合',
}
GROUPS = {action: group for group, _, actions in COMMANDS for action in actions}


class HumanAPI:
    def __init__(self, host):
        self.host = host

    def player(self, actor):
        if actor not in self.host.clients or self.host.clients[actor].kind != 'human':
            raise ValueError('不是本机真人席位')
        return self.host.player(actor)

    def operations(self, actor):
        player = self.player(actor)
        available = {s['function']['name'] for s in player.actions()}
        result = []
        for name, label in LABELS.items():
            parent = GROUPS.get(name, name)
            if parent not in available:continue
            spec = self.host.runtime.actions.get(name) or self.host.runtime.tools.get(name)
            if spec is None or not self.host.runtime.tool_available(self.host.world, actor, parent):continue
            if name in self.host.runtime.actions and not self.host.runtime.action_available(self.host.world,actor,name):continue
            result.append({'operation':name,'label':label,'group':parent,
                           'parameters':spec.schema()['function']['parameters']})
        return result

    def submit(self, actor, operation, arguments):
        player = self.player(actor)
        if operation not in {row['operation'] for row in self.operations(actor)}:
            raise ValueError('此真人操作不可用')
        if not isinstance(arguments, dict):raise ValueError('操作参数须为对象')
        args = deepcopy(arguments)
        if 'action' in args:raise ValueError('不能覆盖操作种类')
        if operation in ('move','attack','retreat'):
            # The UI keeps the original checked set; only this target's legal
            # known subset is submitted, once, to the existing batch validator.
            selected = args.pop('ids', None)
            point = [args.get('x'), args.get('y')]
            if any(type(v) is not int for v in point):raise ValueError('请选择地图上的整数坐标')
            row = next((row for row in self.targets(actor, selected, operation)['targets']
                        if row['position'] == point), None)
            if row is None:raise ValueError('当前已知状态下没有军队可执行此目标')
            args['ids'] = row['ids']
        parent = GROUPS.get(operation)
        return player.submit(parent, {'action':operation, **args}) if parent else player.submit(operation, args)

    def targets(self, actor, ids, mode):
        return candidates(self.player(actor).observe(), ids, mode, self.host.grid()[0])

    def tile(self, actor, x, y):
        size = self.host.grid()[0]
        if type(x) is not int or type(y) is not int or not (0 <= x < size and 0 <= y < size):
            raise ValueError('地块坐标超出地图')
        state = self.state(actor)
        return {'tile':next(t for t in state['map']['tiles'] if t['position']==[x,y]),
                'armies':[u for u in state['facts']['army'] if u['x']==x and u['y']==y]}

    def panel(self,actor,arguments):
        if arguments.get('panel')=='news':
            return self.state(actor)['facts']['news']
        return self.player(actor).query('query',arguments)

    def site_options(self, actor, x, y, count=1, queued=False):
        player=self.player(actor)
        if type(x) is not int or type(y) is not int:raise ValueError('请选择地块')
        planning=player.call_api('native:buildings','planning',x=x,y=y,queued=queued)
        planning['units']=player.call_api('native:army','planning',x=x,y=y,count=count,queued=queued)
        planning['recruitment_orders']=[r for r in player.call_api('native:army','recruit_orders') if r['site']==planning['site']]
        planning['catalog']=player.call_api('native:buildings','catalog')
        planning['unit_catalog']=player.call_api('native:army','unit_catalog')
        planning['estimates']=player.query('query',{'panel':'building_economics','other_slots':planning['completed']})
        return planning

    def market_options(self, actor, resource):
        player=self.player(actor)
        market=player.query('query',{'panel':'market'})
        inventory=dict(market['inventory'])
        if resource not in inventory:raise ValueError('请选择已上市的资源')
        stocks={s['id']:s['amount'] for s in player.query('query',{'panel':'econ'})['stocks']}
        quotes=[]
        for side in ('buy','sell'):
            for quantity in (1,10,50,100,500):
                row={'side':side,'quantity':quantity,'available':False}
                try:
                    quote=player.query('query',{'panel':'trade_quote','resource':resource,'quantity':quantity,'side':side})
                    row['quote']=quote
                    row['reason']=('本国金币不足' if stocks[market['currency']]<quote['gold'] else None) if side=='buy' else ('本国库存不足' if stocks[resource]<quantity else None)
                    row['available']=row['reason'] is None
                except ValueError as exc:
                    row['reason']=str(exc)
                quotes.append(row)
        return {'period':market['period'],'resource':resource,'market':market,'stocks':stocks,'quotes':quotes}

    def state(self, actor, *, controls=True):
        player = self.player(actor)
        observed = player.observe();facts = observed['facts']
        commands=command_states(observed,self.host.grid()[0])
        for unit in facts['army']:
            if unit['owner']==actor:unit['commands']=commands.get(unit['id'],{})
        facts['news']=[e for e in facts['news'] if e['kind']!='action' or
                       (e.get('details') or {}).get('actor')==actor]
        memory = {tuple(c['position']):c for c in player.call_api('native:intelligence','knowledge')}
        live = {tuple(t['position']):t for t in facts['view']}
        size = self.host.grid()[0];tiles = []
        for y in range(size):
            for x in range(size):
                p = (x,y);row = {'position':[x,y], 'visibility':'unknown'}
                if p in memory:
                    fields = memory[p]['fields']
                    row.update(visibility='memory',fields=deepcopy(fields))
                if p in live:
                    row.update(visibility='live',current=deepcopy(live[p]))
                tiles.append(row)
        relation = facts['foreign']['diplomacy']
        offers = [p for p in relation.get('offers',[]) if p.get('can_accept') or p.get('can_reject')]
        votes = [v for v in relation.get('votes',[]) if v['deadline']==observed['period'] and actor in v.get('voters',[]) and
                 actor not in dict(v.get('ballots',[]))]
        result={**observed, 'nickname':self.host.clients[actor].cfg.get('nickname','玩家'),
                'colors':{n['id']:n['color'] for n in self.host.cfg.get('nations',[]) if n.get('color') and n['id'] in {v['id'] for v in facts['foreign']['nations']}},
                'map':{'size':size,'tiles':tiles}}
        # Choreography uses these same facts and knowledge, but does not need
        # to rebuild command schemas, route planning or management panels.
        if not controls:return result
        result.update({
                'army_orders':player.call_api('native:army','orders'),
                'production_queue':{'construction':player.call_api('native:buildings','orders'),
                                    'recruitment':player.call_api('native:army','recruit_orders')},
                'operations':self.operations(actor),
                'map_offers':player.call_api('native:intelligence','map_offers'),
                'reports':player.query('query',{'panel':'report','all':True}),
                'pending_end':{'offers':offers,'votes':votes,
                               'consequence':'未处理外交提议将自动拒绝；未表态联盟投票将自动反对'}})
        return result
