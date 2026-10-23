"""Distribution-owned observer facts and administrative actions."""
from collections import defaultdict
from copy import deepcopy
import hashlib
from engine.player import wire
from engine.config import prepare
from engine.scenario import GridSession


class NativeObserver:
    def management(self):
        return (*super().management(),'observer_snapshot','set_rate','subsidize')

    def record(self,player,action,arguments,receipt,error=None):
        super().record(player,action,arguments,receipt,error)
        tool=self.runtime.tools.get(action) if isinstance(action,str) else None
        if not receipt or not receipt.get('ok') or tool is None or tool.read_only:return
        args=arguments if isinstance(arguments,dict) else {};position=None
        if type(args.get('x')) is int and type(args.get('y')) is int:position=(args['x'],args['y'])
        elif isinstance(args.get('site',args.get('place')),str):
            ref=args.get('site',args.get('place'))
            try:position=self.world.buildings.site(ref).position
            except KeyError:
                try:position=self.world.territory.resolve(ref)
                except ValueError:pass
        text=self.presentation.console.action(self.clock(),player.actor,action,arguments,receipt,{a:r['name'] for a,r in self.roster.items()})
        self.world.intelligence.emit('action',text,position=position,parties=(player.actor,),
                                     details={'source':'action_receipt'})

    def observer_snapshot(self):
        with self.lock, self.runtime.read_scope(self.world):
            names={a:r['name'] for a,r in self.roster.items()}
            nations=[self.player(a).observe() for a in self.actors()]
            flow_period=self.world.economy.next_period-1
            flows=defaultdict(lambda:defaultdict(float))
            if flow_period>=0:
                for valued in self.world.economy.facts(flow_period):
                    fact=valued.fact
                    if fact.period!=flow_period or fact.category not in ('production','military','tribute'):continue
                    for resource,amount in fact.outputs:
                        if fact.category in ('production','tribute'):flows[fact.owner][resource]+=amount
                    for resource,amount in fact.inputs:flows[fact.owner][resource]-=amount
            size,seed=self.grid();buildings=defaultdict(list)
            for instance in wire(self.world.buildings.snapshot())['instances']:
                buildings[instance['site']].append((instance['kind'],instance['level']))
            tiles=[]
            for y in range(size):
                for x in range(size):
                    cell=wire(self.world.territory.cell(x,y))
                    terrain=self.world.territory.terrain(x,y)
                    tiles.append({'position':[x,y],'name':cell['tile']['name'],'terrain':cell['tile']['terrain'],
                        'passable':terrain.passable,'claimable':terrain.claimable,
                        'owner':cell['owner'],'resources':cell['resources'],
                        'buildings':buildings[cell['tile']['id']]})
            diplomacy=wire(self.world.diplomacy.to_save())
            diplomacy['entity_members']=wire(tuple((entity,self.world.diplomacy.members(entity)) for entity in self.world.diplomacy.entities()))
            glyphs={key:(value.get('glyph') if isinstance(value,dict) else getattr(value,'glyph',None))
                    for key,value in self.runtime.content.definitions()}
            glyphs={key:glyph for key,glyph in glyphs.items() if glyph}
            roles={role:list(ids) for role,ids in self.runtime.content.roles().items()}
            return {'period':self.clock(),'names':names,'nations':nations,
                'resource_flows':{'period':flow_period,'owners':{owner:{resource:amount for resource,amount in values.items() if amount}
                    for owner,values in flows.items()}},
                'dead':[a for a in self.roster if a not in self.actors()],
                'market':wire(self.world.market.summary()),'bank':self.bank_status(),
                'records':self.records(),
                'map':{'actor':'observer','period':self.clock(),'seed':seed,'glyphs':glyphs,'role_kinds':roles,
                    'facts':{'view':tiles,'army':wire(self.world.military.snapshot()),'diplomacy':diplomacy}}}

    def bank_status(self):
        b=self.world.bank
        return {'rate':b.rate,'loan_rate':b.loan_rate,
            'minimum':b.policy.minimum_rate,'maximum':b.policy.maximum_rate}

    def set_rate(self,rate=None):
        with self.lock:
            if rate is not None:
                applied=self.world.bank.set_rate(rate)
                self.announce(f'央行储蓄利率调整为 {applied:+.1%}，贷款利率 {self.world.bank.loan_rate:+.1%}')
            return self.bank_status()

    def subsidize(self,actor,resources,cavalry=0):
        with self.lock:
            if actor not in self.actors():raise ValueError('Unknown active actor')
            if type(cavalry) is not int or cavalry<0:raise ValueError('Invalid cavalry count')
            allowed={s['id'] for s in self.player(actor).observe()['facts']['econ']['stocks']}
            if not isinstance(resources,dict) or any(k not in allowed or type(v) is not int or v<0 for k,v in resources.items()):
                raise ValueError('Invalid resource subsidy')
            land=self.world.territory.owned(actor)
            if cavalry and not land:raise ValueError('No site for cavalry')
            if resources:self.runtime.object_call(self.world,'native:resource-exchange','exchange',actor,{},resources)
            if cavalry:self.world.military.seed(actor,'native:cavalry',*land[0].tile.position,cavalry)
            self.world.intelligence.emit('subsidy',actor,public=True)
            return {'actor':actor,'resources':resources,'cavalry':cavalry}

    def _observer_entry(self,name,actor=None,kind=None,polity=None):
        template=next((n for n in self.entries() if n['client']==kind),self.entries()[0])
        excluded={'id','name','position','resources','polity','appearance','enabled',
            'enable_turn','enable_turn_max','start_gold','start_supply','start_cavalry',
            'extra_prompt','extra_prompt_file','extra_summary','extra_summary_file'}
        item={k:deepcopy(v) for k,v in template.items() if k not in excluded}
        item.update(id=actor or 'scenario:nation-'+hashlib.sha256(name.encode()).hexdigest()[:12],
            name=name,client=kind or template['client'],appearance='manual')
        if polity:item['polity']=polity
        cfg=deepcopy(self.cfg);cfg['nations'].append(item)
        return prepare(cfg,runtime=self.runtime).data['nations'][-1]

    def add(self,name,polity=None):
        with self.lock:
            if polity is None:
                # 政体名字面量只住在政体模块的 Aliases 里，观察者不认具体政体。
                matches=[key for key,mod in self.runtime.policies.items()
                         if name in mod.declaration.get('Mod',{}).get('Aliases',())]
                polity=matches[0] if len(matches)==1 else None
            existing=next((n for n in self.entries() if name in (n['id'],n['name'])),None)
            created=existing is None
            if created:
                if not name.strip():raise ValueError('Empty nation name')
                item=self._observer_entry(name,polity=polity)
                self.cfg['nations'].append(item)
            elif polity and existing.get('polity')!=polity:raise ValueError('Configured polity differs')
            try:return GridSession.add(self,name)
            except Exception:
                if created:self.cfg['nations'].remove(item)
                raise

    def restore(self,data):
        # Added nations reuse current provider settings; credentials never enter saves.
        from engine.session import SaveFormatError
        if not isinstance(data,dict) or not isinstance(data.get('host'),dict) or not isinstance(data['host'].get('roster'),dict) or not isinstance(data.get('clients'),dict):raise SaveFormatError('Invalid session envelope')
        known={n['id'] for n in self.entries()}
        for actor,row in data['host']['roster'].items():
            if actor not in known and isinstance(row,dict) and isinstance(row.get('name'),str):
                kind=data.get('clients',{}).get(actor,{}).get('kind')
                self.cfg['nations'].append(self._observer_entry(row['name'],actor,kind))
        return super().restore(data)
