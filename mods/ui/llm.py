"""Compose model-facing prose and image snapshots without owning client memory."""
import json
from copy import deepcopy
from .i18n import Translator
from .console import ConsoleUI
from .maps import snapshot,owner_color
from .views import player_map
from .briefing import sections
from .facts import flatten
from .receipts import num
from clients.context import est_tokens

# 简报分节越界时按此序从后往前丢弃；同键给出「去哪查全文」的工具名。
FALLBACK={'capital':'frontier','threats':'threats','battle':'battle','territory':'news','power':'econ','plan':'government',
          'map':'view','army':'army','report':'report','diplomacy':'foreign','market':'market','bank':'bank',
          'intel':'intel','dispatches':'dispatches'}


class LLMUI:
    def __init__(self,cfg,tool_layer=None):
        self.cfg=cfg;self.text=Translator.from_config(cfg,tool_layer=tool_layer);self.sites={};self.has_scripts=False
        self.catalogs={};self.rulebook=[];self.guide_catalog=[];self.period=0;self.at_war=False;self.polity=None
        # 模型与观察者共用同一套中文渲染器；差别只在成文后的排序与裁剪。
        self.receipts=ConsoleUI(cfg,tool_layer=tool_layer);self.actor=None;self.names={};self.facts={}

    def values(self):
        """短键 → 本地化键值（金/木/粮/农场/步兵…）。语言一换，模型看到的键值就换。"""
        if getattr(self,'_values',None) is None:
            # 英文就是短键本身（键值即英文），其他语言用本地化名字。
            english=self.text.language.split('-')[0]=='en'
            self._values={key:(key if english else self.text.resolve('content.'+ident,key))
                          for ident,key in self.cfg.get('_content_keys',{}).items()}
        return self._values

    def localized_values(self):
        """本地化键值 → 短键：入口把模型给的键值翻回引擎认的短键。"""
        return {value:key for key,value in self.values().items()}

    def _key(self,ident):
        """模型面向的键值：注册内容用本地化键值，未注册的命名空间 ID 剥掉命名空间再查。

        国家 ID（scenario:*）不动：那是模型必须原样传回工具的句柄，不是内容键。
        """
        keys=self.cfg.get('_content_keys',{})
        if ident in keys:return self.values()[keys[ident]]
        if ident.startswith('native:'):
            key=ident.split(':',1)[1]
            return self.values().get(key,self.text.resolve('content.'+ident,key))
        return ident

    def _remember(self,observation):
        """把这次观察交给共用渲染器：地点索引、兵种、当前回合都从这里来。

        观察者侧由 mp_run 每回合喂；模型侧原先从来不喂，于是专用渲染器永远走不到。
        """
        self.receipts.remember(observation)
        facts=flatten(observation.get('facts',{}))
        self.actor=observation.get('actor');self.facts=facts
        self.names={n['id']:n['name'] for n in facts.get('nations',[]) if isinstance(n,dict)}
        self.receipts._names=self.names;self.receipts._actor=self.actor
        return facts

    def receipt(self,receipt,arguments=None):
        """模型侧回执走观察者同一套中文渲染。

        工具名与参数已经写在 tool_calls 里，这里只给结果正文，不重复抬头。
        """
        presented=deepcopy(receipt)
        # 国策历史小结归客户端记忆与日志保管，模型回执不重播（既有口径，勿改）。
        if presented.get('action')=='government' and isinstance(presented.get('data'),dict):
            presented['data'].pop('summaries',None)
        args=arguments if isinstance(arguments,dict) else {}
        return self.receipts.receipt_body(presented.get('action'),args,presented,self.actor)

    def system(self):
        text=self.text('llm.system')+'\n'+str(self.cfg.get('system_prompt') or '')+'\n'+str(self.cfg.get('extra_summary') or '')
        text+='\n国策是跨回合战略：经济、军事、情报、外交的目标、优先级、资源约束和调整条件应充分说明。end_turn 的 summary 只用一两句写实际结果，详细流水已有回执。先阅读当前状态和行动简报；已有资料无需重复查询。互不依赖的工具可在同一批调用，依赖前一步结果的行动等待回执。坐标只用于实际动作与 query(panel=tile) 的单格详情，其余查询按全国或整类返回。'
        # The model can fetch every rule/catalog by tool. Only explicitly
        # registered role guidance belongs in the stable prompt.
        active=[section for section in self.rulebook if section['title'] in ('匈奴政体',)]
        if active:text+='\n'+json.dumps(active,ensure_ascii=False,separators=(',',':'))
        # 旧版把开局指南直接挂在前 20 回合的 system 前缀里（过期后仍可 player_rules 取回）：
        # 开局最贵的几个坑，模型不一定会主动去查手册。
        guide=next((section for section in self.rulebook if section['title']=='基础指南'),None)
        if guide and self.cfg.get('opening_guide',True) and self.period<20:text+='\n'+str(guide['body'])
        text+='\n只读查询统一用 query(panel=…)：land/tile/map、army/reachable/frontier/threats/battle/logistics、market/econ/bank/report、government/intel/news/dispatches、foreign（本国与全部对外关系）、unit_catalog/building_catalog/terrain_catalog/building_economics、handbooks。动作类工具按命令分：military（征召/移动/进攻/撤退/遣散）、economy（买卖/借款/购报表）、relations（外交与投票）、intelligence（写信/赠礼/共享地图/间谍），各自用 action 参数选动作。规则用 player_rules(topic)，按游标取事件用 player_events(cursor)。'
        if self.has_scripts:text+='\n'+self.text('llm.scripting')
        return text

    def rules(self,sections):
        self.rulebook=deepcopy(sections)

    def actions(self,schemas):
        # 工具名、参数名是 API；enum 与描述里提到的内容键值随语言走（zh=金/农场，en=gold/farm）。
        schemas=deepcopy([schema for schema in schemas if schema['function']['name']!='player_state'])
        self.has_scripts=any(s['function']['name']=='script' for s in schemas)
        import re
        values=self.values()
        def localize(node):
            if isinstance(node,dict):
                for key,value in list(node.items()):
                    if key=='enum' and isinstance(value,list):
                        node[key]=[values.get(item,item) for item in value]
                    else:localize(value)
            elif isinstance(node,list):
                for value in node:localize(value)
        pattern=re.compile(r'(?<![\w:-])(?:'+'|'.join(re.escape(k) for k in sorted(values,key=len,reverse=True))+r')(?![\w:-])') if values else None
        def rewrite(text):return pattern.sub(lambda m:values[m.group()],text) if pattern else text
        def annotations(node,path):
            if isinstance(node,dict):
                for key,value in list(node.items()):
                    if key in ('description','title') and isinstance(value,str):
                        node[key]=self.text.resolve(path+'.'+key,value)
                    elif isinstance(value,(dict,list)):annotations(value,path+'.'+key)
            elif isinstance(node,list):
                for index,value in enumerate(node):annotations(value,path+'.'+str(index))
        for schema in schemas:
            fn=schema['function'];annotations(fn,'tools.'+fn['name'])
            for ident,key in self.cfg.get('_content_keys',{}).items():
                fn['description']=fn.get('description','').replace(ident,key)
            fn['description']=rewrite(fn.get('description',''))
            localize(fn.get('parameters') or {})
        return schemas

    def dashboard(self,observation):
        """Decision cues derived exclusively from detached public facts."""
        facts=flatten(observation['facts']);actor=observation['actor'];period=observation['period']
        stocks={s['id']:s['amount'] for s in facts.get('econ',{}).get('stocks',[])}
        catalog={b['id']:b for b in facts.get('building_catalog',[])}
        units=facts.get('unit_catalog',{});sites=facts.get('building_sites',{})
        active=[b for b in sites.get('instances',[]) if b['remaining']==0]
        inputs={};outputs={};generation=0;demand=0
        for b in active:
            kind=catalog.get(b['kind'],{})
            generation+=kind.get('energy_output',0);demand+=kind.get('energy_use',0)
            for resource,amount in kind.get('inputs',{}).items():inputs[resource]=inputs.get(resource,0)+amount
            for resource,amount in kind.get('outputs',{}).items():outputs[resource]=outputs.get(resource,0)+amount
        own=[u for u in facts.get('army',[]) if u['owner']==actor and u['hp']>0]
        camp_capacity={}
        positions={s['id']:tuple(s['position']) for s in sites.get('sites',[])}
        for b in active:
            cap=catalog.get(b['kind'],{}).get('effects',{}).get('militia_cap',0)
            if cap and b['site'] in positions:
                pos=positions[b['site']];camp_capacity[pos]=camp_capacity.get(pos,0)+cap
        supply=0
        for u in own:
            pos=(u['x'],u['y'])
            if u['kind']=='native:militia' and camp_capacity.get(pos,0):camp_capacity[pos]-=1
            else:supply+=units.get(u['kind'],{}).get('supply',0)
        inputs['native:supply']=inputs.get('native:supply',0)+supply
        for u in own:
            if not u['engaged'] and u['kind']!='native:militia' and u['hp']<units.get(u['kind'],{}).get('hp',u['hp']):inputs['native:equipment']=inputs.get('native:equipment',0)+1
        # Capacity is an upper bound, not a claim that every plant can run.
        energy={'capacity':generation,'demand':demand,'capacity_gap':max(0,demand-generation)}
        shortages={r:max(0,n-stocks.get(r,0)) for r,n in inputs.items() if n>stocks.get(r,0)}
        armies=[{'id':u['id'],'kind':u['kind'],'position':[u['x'],u['y']],
                 'status':'新兵' if u['born_turn']==period else '交战中' if u['engaged'] else '已行动' if u['moved_turn']==period else '可行动'} for u in own]
        allies={actor};enemies={'野人'}
        diplomacy=facts.get('diplomacy',{})
        for bloc in diplomacy.get('alliances',[]):
            if actor in bloc['members']:allies.update(bloc['members'])
        for war in diplomacy.get('wars',[]):
            if actor in war['attackers']:enemies.update(war['defenders'])
            if actor in war['defenders']:enemies.update(war['attackers'])
        own_positions=[s['position'] for s in sites.get('sites',[])]
        threats=[{'owner':u['owner'],'kind':u['kind'],'position':[u['x'],u['y']]} for u in facts.get('army',[])
                 if u['hp']>0 and u['owner'] in enemies and u['owner'] not in allies and u['owner']!='野人'
                 and any(max(abs(u['x']-p[0]),abs(u['y']-p[1]))<=1 for p in own_positions)]
        plan=facts.get('government',{}).get('plan')
        return {'plan_status':'须提交国策' if not plan or plan['valid_until']<period else '国策有效',
                'energy_capacity':energy,'planned_regular_inputs':inputs,'recipe_outputs_before_dynamic_effects':outputs,
                'stock_shortfalls_before_production':shortages,'army_supply_per_period':supply,
                'army_actions':armies,'border_threats':threats,
                'construction':sites.get('construction',[]),
                'frontier':{key:value for key,value in facts.get('frontier',{}).items() if key!='capital_warning'},
                'capital_warning':deepcopy(facts.get('frontier',{}).get('capital_warning',[])),
                'diplomacy_todo':{key:diplomacy[key] for key in ('offers','votes','pending') if diplomacy.get(key)}}

    def event_lines(self,facts,events):
        """近讯：重大事件全留，其余按最近十条取；正文过长时指路 news。"""
        major={row['id'] for row in events if row.get('kind') in ('extinction','victory') or
               isinstance(row.get('details'),dict) and row['details'].get('importance')=='major'}
        selected=major|{row['id'] for row in events[-10:]}
        rows=[]
        for row in events:
            if row['id'] not in selected:continue
            kept={k:v for k,v in row.items() if k!='recipients'}
            if kept.get('details'):
                kept['text']=self.receipts.event(row,self.names);kept.pop('details',None)
            text=kept.get('text','')
            if len(text)>320:text=text[:320]+'…（完整事件用 news 查询）'
            rows.append((row['id'],text))
        return rows,major

    def briefing_text(self,observation,events,cues,fit):
        """分节战情总纲；越界时从最不紧迫的节开始整节丢弃，并写明去哪查。"""
        items=sections(self.receipts,observation,events,cues,self._key,bool(self.cfg.get('vision',False)))
        rows,major=self.event_lines(self.facts,events)
        head=self.text('brief.head',period=observation['period'],
                       status=self.text('query.acting' if observation['can_act'] else 'query.ended'))
        def assemble():
            parts=[head]+[text for _,text in items]
            if rows:parts.append(self.text('brief.news_head')+'\n'+'\n'.join(text for _,text in rows))
            return '\n\n'.join(parts)
        dropped=[];trimmed=False
        while not fit(assemble()) and len(items)>1:
            dropped.append(items.pop()[0])
        while not fit(assemble()) and rows:
            # 先裁普通事件；灭国/胜利/领土变更这些大事留到最后。
            index=next((i for i,(ident,_) in enumerate(rows) if ident not in major),0)
            rows.pop(index);trimmed=True
        text=assemble()
        if trimmed:text+='\n'+self.text('brief.news_trimmed')
        if dropped:
            text+='\n\n'+'；'.join(self.text('brief.omitted',tool=FALLBACK.get(key,key)) for key in dropped)
        return text

    def state(self,observation,events):
        facts=self._remember(observation)
        for site in facts.get('building_sites',{}).get('sites',[]):self.sites[site['id']]=list(site['position'])
        self.period=observation['period'];self.guide_catalog=facts.get('handbooks',[])
        self.polity=self.cfg.get('polity')
        actor=observation['actor']
        self.at_war=any(actor in w['attackers']+w['defenders'] for w in facts.get('diplomacy',{}).get('wars',[]))
        cues=self.dashboard(observation)
        cap=self.cfg.get('model_state_tokens',12000)
        tail=self.briefing_text(observation,events,cues,lambda text:est_tokens(text)<=cap)
        if self.cfg.get('opening_guide',True) and observation['period']<20 and self.cfg.get('extra_prompt'):
            tail+='\n'+self.text('llm.background',text=self.cfg['extra_prompt'])
        legend,image=self.map_context(observation)
        return tail+legend,image

    def map_context(self,observation):
        legend='';image=None
        if self.cfg.get('vision',False) and observation['facts'].get('view'):
            facts=snapshot(observation)
            owners=sorted({t['owner'] for t in facts['tiles'] if t['owner']})
            names={n['id']:n['name'] for n in flatten(observation['facts']).get('nations',[])}
            legend+='\n'+self.text('map.legend',owners=', '.join(f'{chr(65+i)}={names.get(owner,owner)}' for i,owner in enumerate(owners)))
            image={'facts':facts,'max_px':self.cfg.get('map_max_px',960),'estimated_tokens':self.cfg.get('map_estimated_tokens',2048),
                   'fallback':self.text('map.fallback')+'\n'+player_map(observation,self.text)}
        return legend,image

    def context_plan(self,p):
        k=lambda n:f'{int(n or 0)/1000:.1f}k' if int(n or 0)<1000000 else f'{int(n)/1000000:.2f}M'
        t=self.text
        head=t('context.fixed',turns=p.fixed_turns) if p.mode=='fixed' else t('context.budget',window=k(p.window),budget=k(p.budget),ratio=(p.budget/p.window*100 if p.window else 0))
        if p.roll=='period':head+=t('context.period',turns=p.period) if p.period else t('context.auto',turns=p.effective_period or '?')
        total=p.total_tokens or 1;hit=t('context.hit',ratio=p.stable_tokens/total*100) if p.stable_tokens else ''
        return t('context.detail',head=head,turns=p.replay_turns,replay=k(p.replay_tokens),archive=k(p.archive_tokens),state=k(p.tail_tokens),system=k(p.sys_tokens),total=k(total),hit=hit)
