"""Compose model-facing text and image snapshots without owning client memory."""
import json
from copy import deepcopy
from .i18n import Translator
from .console import ConsoleUI
from .maps import snapshot,owner_color
from .views import player_atlas, player_map
from .deltas import difference
from clients.context import est_tokens


class LLMUI:
    def __init__(self,cfg):self.cfg=cfg;self.text=Translator.from_config(cfg);self.sites={};self.has_scripts=False;self.catalogs={};self.current=None;self.rulebook=[];self.guide_catalog=[];self.period=0;self.at_war=False;self.polity=None;self.receipts=ConsoleUI(cfg)

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
        presented=deepcopy(receipt)
        # History belongs to client memory, not a full replay embedded in a
        # current-government query. Keep the underlying player API detached.
        if receipt.get('action')=='government' and isinstance(presented.get('data'),dict):
            presented['data'].pop('summaries',None)
        # Same rejection translator as the observer, retaining structured data
        # for scripts and subsequent decisions.
        if not receipt.get('ok'):presented['message']=self.receipts.label('receipt',receipt.get('message',''))
        return json.dumps(self.compact(presented),ensure_ascii=False,separators=(',',':'))

    def system(self):
        text=self.text('llm.system')+'\n'+str(self.cfg.get('system_prompt') or '')+'\n'+str(self.cfg.get('extra_summary') or '')
        text+='\n国策是跨回合战略：经济、军事、情报、外交的目标、优先级、资源约束和调整条件应充分说明。end_turn 的 summary 只用一两句写实际结果，详细流水已有回执。先阅读当前状态和行动简报；已有资料无需重复查询。互不依赖的工具可在同一批调用，依赖前一步结果的行动等待回执。坐标只用于实际动作与 tile 单格详情，其余查询按全国或整类返回。'
        # The model can fetch every rule/catalog by tool. Only explicitly
        # registered role guidance belongs in the stable prompt.
        active=[section for section in self.rulebook if section['title'] in ('匈奴政体',)]
        if active:text+='\n'+json.dumps(active,ensure_ascii=False,separators=(',',':'))
        text+='\n规则按需用 player_rules(topic)，兵种/建筑/地形用 unit_catalog/building_catalog/terrain_catalog，地块用 land/tile，完整地图用 view，历史近讯用 news，情报用 intel，国策正文用 government，完整经济与资产用 econ，已购报表用 bank。'
        if self.has_scripts:text+='\n'+self.text('llm.scripting')
        return text

    def rules(self,sections):
        self.rulebook=deepcopy(sections)

    def actions(self,schemas):
        # 工具名、参数名是 API；enum 与描述里提到的内容键值随语言走（zh=金/农场，en=gold/farm）。
        schemas=deepcopy([schema for schema in schemas if schema['function']['name']!='player_state'])
        self.has_scripts=any(s['function']['name']=='script_run' for s in schemas)
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

    def atlas_text(self,observation):
        """模型看到的地图：老版坐标地图格式（按势力分段、逐格一行）。"""
        return player_atlas(observation,self.text,self._key).split('\n')

    # Each standing fact has a decision purpose; new module panels never
    # become permanent model input by accident.
    STANDING=('government','nation','econ','bank','market','army','diplomacy','dispatches')

    def current_state(self,observation):
        facts={key:deepcopy(observation['facts'][key]) for key in self.STANDING if key in observation['facts']}
        government=facts.get('government',{})
        government.pop('summaries',None)
        if 'econ' in facts:facts['econ']={'stocks':facts['econ'].get('stocks',[])}
        if 'nation' in facts:
            row=facts['nation'];facts['nation']={**row.get('nation',{}),'land_count':len(row.get('sites',[]))}
        if government.get('plan'):
            government['plan']={key:value for key,value in government['plan'].items() if key!='text'}
        if facts.get('bank',{}).get('purchases'):
            facts['bank']['purchases']=[{'owner':p['report']['owner'],'end':p['report']['end']} for p in facts['bank']['purchases']]
        if 'army' in facts:facts['army']=[u for u in facts['army'] if u['owner']!='野人']
        # Keep structured rows so per-row changes remain addressable.
        if not self.cfg.get('vision',False):
            facts['view']=[{key:row[key] for key in ('position','name','terrain','owner','buildings') if key in row} for row in observation['facts'].get('view',[])]
        return {'period':observation['period'],'can_act':observation['can_act'],'facts':facts,'decision':self.dashboard(observation)}

    def _bounded(self,current):
        # Reserve room for the JSON envelope, indexes and omission notices.
        cap=self.cfg.get('model_state_tokens',12000)-512;omitted=[]
        def size():return est_tokens(json.dumps(self.compact(current),ensure_ascii=False,separators=(',',':')))
        for key,tool in (('view','view'),('army','army'),('diplomacy','diplomacy'),('government','government'),('nation','nation'),('dispatches','dispatches'),('market','market'),('bank','bank')):
            if size()<=cap:break
            value=current['facts'].pop(key,None)
            if value:omitted.append(f'{key} 超出本期载荷预算，完整内容用 {tool} 查询')
        if size()>cap:
            current.pop('decision',None)
            omitted.append('行动简报超出载荷预算；请按需查询当前事实')
        if size()>cap:
            current['facts'].pop('econ',None)
            omitted.append('库存超出载荷预算，完整库存用 econ 查询')
        if omitted:current['omitted']=omitted
        return current

    def refresh(self,observation):
        current=self.compact(self._bounded(self.current_state(observation)))
        changes=difference(self.current or {},current)
        self.current=current
        if not changes:return None
        return '状态差分（path 指向字段；set 覆盖、remove 删除；rows 按 position 或 (owner,id) 合并 upsert/remove，order 非空时重排。未列项保持原值）：'+json.dumps(changes,ensure_ascii=False,separators=(',',':'))

    def state(self,observation,events):
        for site in observation['facts'].get('building_sites',{}).get('sites',[]):self.sites[site['id']]=list(site['position'])
        self.period=observation['period'];self.guide_catalog=observation['facts'].get('handbooks',[])
        self.polity=self.cfg.get('polity')
        actor=observation['actor']
        self.at_war=any(actor in w['attackers']+w['defenders'] for w in observation['facts'].get('diplomacy',{}).get('wars',[]))
        self.current=self.compact(self._bounded(self.current_state(observation)))
        major_ids={row['id'] for row in events if row.get('kind') in ('extinction','victory') or
                   isinstance(row.get('details'),dict) and row['details'].get('importance')=='major'}
        selected=major_ids|{row['id'] for row in events[-10:]}
        recent=[{k:v for k,v in row.items() if k!='recipients'} for row in events if row['id'] in selected]
        names={n['id']:n['name'] for n in observation['facts'].get('nations',[])}
        for row in recent:
            if row.get('details'):row['text']=self.receipts.event(row,names);row.pop('details',None)
            if len(row.get('text',''))>320:row['text']=row['text'][:320]+'…（完整事件用 news 查询）'
        messages=observation['facts'].get('intel',[])
        intel=[{k:row[k] for k in ('id','source','kind','sampled') if k in row} for row in messages[-10:]]
        payload={'state':self.current,'events':recent,'intel_index':intel}
        encode=lambda:json.dumps(self.compact(payload),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        cap=self.cfg.get('model_state_tokens',12000)
        while est_tokens(encode())>cap and recent:
            ordinary=next((i for i,row in enumerate(recent) if row['id'] not in major_ids),0)
            recent.pop(ordinary)
        if len(recent)<len(events):payload['event_notice']='近讯已按载荷预算裁剪，完整近讯用 news 查询。'
        while est_tokens(encode())>cap and intel:intel.pop(0)
        tail=encode()
        if self.cfg.get('opening_guide',True) and observation['period']<20 and self.cfg.get('extra_prompt'):
            tail+='\n'+self.text('llm.background',text=self.cfg['extra_prompt'])
        legend,image=self.map_context(observation)
        return tail+legend,image

    def map_context(self,observation):
        legend='';image=None
        if self.cfg.get('vision',False) and observation['facts'].get('view'):
            facts=snapshot(observation)
            owners=sorted({t['owner'] for t in facts['tiles'] if t['owner']})
            names={n['id']:n['name'] for n in observation['facts'].get('nations',[])}
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
