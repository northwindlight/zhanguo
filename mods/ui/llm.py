"""Compose model-facing text and image snapshots without owning client memory."""
import json
from copy import deepcopy
from .i18n import Translator
from .maps import snapshot,owner_color
from .views import player_atlas, player_map


class LLMUI:
    def __init__(self,cfg):self.cfg=cfg;self.text=Translator.from_config(cfg);self.sites={};self.has_scripts=False;self.catalogs={};self.current=None;self.rulebook=[];self.guide_catalog=[];self.period=0;self.at_war=False

    def _key(self,ident):
        """模型面向的键值：注册内容用它的短键，未注册的命名空间 ID 至少剥掉命名空间。

        国家 ID（scenario:*）不动：那是模型必须原样传回工具的句柄，不是内容键。
        """
        keys=self.cfg.get('_content_keys',{})
        if ident in keys:return keys[ident]
        return ident.split(':',1)[1] if ident.startswith('native:') else ident

    def compact(self,value):
        """Short coordinate references for model presentation only; preserve prose."""
        if isinstance(value,list):return [self.compact(v) for v in value]
        if not isinstance(value,dict):
            return self._key(value) if isinstance(value,str) else value
        if isinstance(value.get('id'),str) and isinstance(value.get('position'),(list,tuple)):
            self.sites[value['id']]=list(value['position'])
        prose={'text','summary','message','reason','content','reasoning_content','source','old','new','output'}
        result={self._key(k):v if k in prose else self.compact(v) for k,v in value.items()}
        if isinstance(value.get('id'),str) and value['id'] in self.sites:
            result.pop('id');result.setdefault('position',self.sites[value['id']])
        if isinstance(value.get('site'),str) and value['site'] in self.sites:
            result.pop('site');result['position']=self.sites[value['site']]
        if isinstance(value.get('tile'),dict):
            tile=result.pop('tile');result.update(tile)
        stocks=value.get('stocks')
        if isinstance(stocks,list) and all(isinstance(s,dict) and 'id' in s and 'amount' in s for s in stocks):
            result['stocks']={self._key(s['id']):s['amount'] for s in stocks}
        return result

    def receipt(self,receipt):
        return json.dumps(self.compact(receipt),ensure_ascii=False,separators=(',',':'))

    def system(self):
        text=self.text('llm.system')+'\n'+str(self.cfg.get('system_prompt') or '')+'\n'+str(self.cfg.get('extra_summary') or '')
        text+='\n国策是跨回合战略：经济、军事、情报、外交的目标、优先级、资源约束和调整条件应充分说明。end_turn 的 summary 只用一两句写实际结果，详细流水已有回执。先阅读当前状态和行动简报；已有资料无需重复查询。互不依赖的工具可在同一批调用，依赖前一步结果的行动等待回执。坐标只用于实际动作与 tile 单格详情，其余查询按全国或整类返回。'
        guides={g['title']:g for g in self.guide_catalog}
        def include(section):
            guide=guides.get(section['title'])
            if not guide:return True
            when=guide['prompt_when']
            return when=='always' or when=='opening' and self.period<self.cfg.get('joined_at',0)+guide['opening_periods'] or when=='war' and self.at_war
        active=[section for section in self.rulebook if include(section)]
        if active:text+='\n公开规则与当前阶段指南（已提供内容无需重复查询；其他指南可随时按标题查）：'+json.dumps(active,ensure_ascii=False,separators=(',',':'))
        if self.catalogs:text+='\n常备资料（内容优先使用短键值；发生冲突时才用命名空间）：'+json.dumps(self.compact(self.catalogs),ensure_ascii=False,separators=(',',':'),sort_keys=True)
        if self.has_scripts:text+='\n'+self.text('llm.scripting')
        return text

    def rules(self,sections):
        self.rulebook=deepcopy(sections)

    def actions(self,schemas):
        # Translate only annotations; names, argument keys, enums and IDs are API.
        schemas=deepcopy(schemas)
        self.has_scripts=any(s['function']['name']=='script_run' for s in schemas)
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
        return schemas

    def dashboard(self,observation):
        """Decision cues derived exclusively from detached public facts."""
        facts=observation['facts'];actor=observation['actor'];period=observation['period']
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
                 if u['hp']>0 and u['owner'] in enemies and u['owner'] not in allies
                 and any(max(abs(u['x']-p[0]),abs(u['y']-p[1]))<=1 for p in own_positions)]
        plan=facts.get('government',{}).get('plan')
        return {'plan_status':'须提交国策' if not plan or plan['valid_until']<period else '国策有效',
                'energy_capacity':energy,'planned_regular_inputs':inputs,'recipe_outputs_before_dynamic_effects':outputs,
                'stock_shortfalls_before_production':shortages,'army_supply_per_period':supply,
                'army_actions':armies,'border_threats':threats,
                'construction':sites.get('construction',[]),
                'diplomacy_todo':{key:diplomacy[key] for key in ('offers','votes','pending') if diplomacy.get(key)}}

    def atlas_text(self,observation):
        """模型看到的地图：老版坐标地图格式（按势力分段、逐格一行）。"""
        return player_atlas(observation,self.text,self._key).split('\n')

    def current_state(self,observation):
        facts={key:value for key,value in observation['facts'].items() if key not in ('building_catalog','unit_catalog','terrain_catalog','handbooks')}
        facts=deepcopy(facts)
        if 'government' in facts:facts['government'].pop('summaries',None)
        if isinstance(facts.get('view'),list):facts['view']=self.atlas_text(observation)
        return {'period':observation['period'],'can_act':observation['can_act'],'facts':facts,'decision':self.dashboard(observation)}

    def refresh(self,observation):
        """Send only changed public facts after a batch, with no stale values."""
        current=self.current_state(observation);previous=self.current or {}
        changed={key:value for key,value in current.items() if key!='facts' and value!=previous.get(key)}
        changed_facts={key:value for key,value in current['facts'].items() if value!=previous.get('facts',{}).get(key)}
        if changed_facts:changed['facts']=changed_facts
        self.current=current
        if not changed:return None
        return '当前状态更新（以此覆盖同名旧值；未列出的事实不变；电力 capacity 是装机上限，燃料不足时实际发电更低）：'+json.dumps(self.compact(changed),ensure_ascii=False,separators=(',',':'))

    def state(self,observation,events):
        # Index all owned sites before rendering references, independent of key order.
        for site in observation['facts'].get('building_sites',{}).get('sites',[]):
            self.sites[site['id']]=list(site['position'])
        self.period=observation['period'];self.guide_catalog=observation['facts'].get('handbooks',[])
        actor=observation['actor']
        self.at_war=any(actor in w['attackers']+w['defenders'] for w in observation['facts'].get('diplomacy',{}).get('wars',[]))
        self.current=self.current_state(observation)
        self.catalogs={key:observation['facts'][key] for key in ('building_catalog','unit_catalog','terrain_catalog','handbooks') if key in observation['facts']}
        presented=deepcopy(observation)
        for key in self.catalogs:presented['facts'].pop(key)
        if isinstance(presented['facts'].get('view'),list):
            presented['facts']['view']=self.atlas_text(observation)
        government=presented['facts'].get('government',{})
        if 'summaries' in government:government['summaries']=government['summaries'][-2:]
        tail=json.dumps(self.compact({'state':presented,'events':events,'decision':self.dashboard(observation)}),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if observation['period']<self.cfg.get('joined_at',0)+20 and self.cfg.get('extra_prompt'):
            tail+='\n'+self.text('llm.background',text=self.cfg['extra_prompt'])
        legend,image=self.map_context(observation)
        return tail+legend,image

    def map_context(self,observation):
        legend='';image=None
        if self.cfg.get('vision',False) and observation['facts'].get('view'):
            facts=snapshot(observation)
            owners=sorted({t['owner'] for t in facts['tiles'] if t['owner']})
            legend+='\n'+self.text('map.legend',owners=', '.join(f'{owner}={owner_color(owner)}' for owner in owners))
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
