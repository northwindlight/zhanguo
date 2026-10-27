from engine.runtime import GameMod, Stage, RuleSection
from .api import IntelligenceAPI
from .objects import Intelligence


class IntelligenceMod(GameMod):
    def mixins(self):return ()
    def numbers(self):return {'intelligence.'+key:value for key,value in self.declaration['Intelligence'].items()}
    def initialize_world(self, world):
        world.intelligence=Intelligence(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,cap:self.runtime.permits(world,actor,cap),self.declaration['Intelligence'])
    def objects(self, world):return {'native:intelligence':world.intelligence}
    def save_state(self, world):return world.intelligence.to_save()
    def load_state(self, world, state):world.intelligence.restore(state)
    def pending_archive(self,world):return world.intelligence.pending_archive()
    def archive_committed(self,world):world.intelligence.archive_committed()
    def restore_archive(self,world,rows):world.intelligence.restore_archive(rows)
    def player_api(self, world):return IntelligenceAPI(world)
    def rule_sections(self,world=None):
        rules=self.declaration['Intelligence']
        return (RuleSection('历史地图与购图',
            f'query(panel=map_offers) 查询各国地图商在开局及最近完整回合边界发布的全国测绘：本国土地及邻接无主地，含采样时地形、资源、归属、建筑与在建数量，不含军队分布。'
            f'intelligence(action=buy_map,target=国家) 购买指定版本，费用{rules["MapBase"]}金加每格{rules["MapPerTile"]}金，记行政消费；同一买方同一版本只买一次。'
            '成功后立即取得历史地图，不获得实时视野。共享图和间谍图只在真正送达时合入知识。query(panel=map_knowledge) 保留逐字段日期与来源；旧报告不覆盖新观察，未提供的字段不表示拆除。',
            keywords=('map','地图','knowledge','情报','buy_map')),)
    def stages(self):return (Stage('native:intelligence.deliver',lambda w,c:c.update(deliveries=w.intelligence.deliver()),hook='cycle.end',before=('native:government.advance',)),
        Stage('native:intelligence.dispatch',self._dispatch,hook='relations',needs=('relations.applied',),provides=('dispatches.started',)),
        Stage('native:intelligence.events',self._events,hook='cycle.end',needs=('actors.eliminated',),before=('native:government.advance',)))
    @staticmethod
    def _dispatch(world, context):
        results,facts=world.intelligence.resolve();context['dispatch_results']=results;context.setdefault('economic_records',[]).extend(facts)
    @staticmethod
    def _events(world, context):
        # Keep last-turn provenance with existing private events, so demolition
        # or a capture next turn cannot rewrite the buildings that produced it.
        production=context.get('building_production')
        sources={r.owner:{} for r in production.energy} if production else {}
        for record in getattr(production,'records',()):
            if record.owner is None or not (record.inputs or record.outputs):continue
            group=sources.setdefault(record.owner,{}).setdefault(record.kind,{'kind':record.kind,'count':0,'inputs':{},'outputs':{}})
            group['count']+=1
            for name in ('inputs','outputs'):
                for resource,amount in getattr(record,name):group[name][resource]=group[name].get(resource,0)+amount
        for owner,groups in sources.items():
            rows=[{**row,'inputs':[[k,v] for k,v in row['inputs'].items()],'outputs':[[k,v] for k,v in row['outputs'].items()]} for row in groups.values()]
            world.intelligence.emit('production-detail','本期建筑实际产出与用料已结算。',parties=(owner,),
                details={'owner':owner,'period':production.period,'sources':rows})
        tribute={}
        for source,target,resource,amount in getattr(context.get('building_production'),'transfers',()):
            tribute.setdefault((source,target),{})[resource]=amount
        for (source,target),resources in tribute.items():
            world.intelligence.emit('tribute',f'{source} 向 {target} 上贡，已强制结算。',
                parties=(source,target),details={'source':source,'target':target,'resources':resources})
        for c in context.get('vote_results',()):world.intelligence.emit('diplomacy',c.message,parties=tuple(n for n,_ in c.entities)+(c.actor,))
        for c in context.get('diplomacy_results',()):
            public=c.status=='effective' and c.message=='Diplomatic action applied' and c.action in ('declare_war','accept_peace','accept_proposal','guarantee','cancel_guarantee','break_defense','bloc_leave','bloc_dissolve','bloc_rename','bloc_transfer')
            details={'actor':c.actor,'action':c.action,'arguments':dict(c.arguments),'status':c.status,'message':c.message}
            if public:details['importance']='major'
            world.intelligence.emit('diplomacy',c.message,parties=tuple(n for n,_ in c.entities)+(c.actor,),
                public=public,details=details)
        for effect in context.get('diplomacy_effects',()):
            if effect[0]=='vote_result':
                details=effect[1]
                choices='、'.join(f'{actor}：'+('弃权' if choice is None else '赞成' if choice else '反对')
                                 for actor,choice in details['ballots'].items())
                outcome={'passed':'通过','rejected':'否决','cancelled':'作废'}[details['status']]
                world.intelligence.emit('diplomacy',
                    f'联盟表决#{details["id"]}开票：{choices}；赞成{details["yes"]}、反对{details["no"]}、弃权{details["abstain"]}，{outcome}。',
                    parties=effect[2],details=details)
            if effect[0]=='notice':world.intelligence.emit('diplomacy',effect[1],parties=effect[2],public=effect[3],
                details={'importance':'major'} if effect[3] else None)
        for owner,resource,quantity,loss in context.get('market_churn',()):world.intelligence.emit('market',f'{resource} 本期买卖重叠空转{quantity}单位，按成交均价折算净亏{loss:g}金（负值为获利）——同商品又买又卖＝白付两趟价差并自己推价，要买就直接买、要卖就直接卖。',parties=(owner,))
        for report in context.get('economy_publications',()):world.intelligence.emit('economy',f'第{report.start}—{report.end}期经济报表已公布，可用 report 查询本国、buy_report 购买别国。',parties=(report.owner,))
        for event in context.get('domain_events',()):world.intelligence.emit(**event)
        for owner in context.get('extinct_nations',()):world.intelligence.emit('extinction',owner,public=True)
