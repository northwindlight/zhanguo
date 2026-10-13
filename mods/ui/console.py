"""Observer presentation over detached facts; no domain or host access."""
import json
import unicodedata
from .i18n import Translator
from .receipts import ReceiptPresentation


class ConsoleUI(ReceiptPresentation):
    def __init__(self,cfg):
        self.text=Translator.from_config(cfg)
        self._sites={};self._terrain={};self._catalog={};self._names={}
    def label(self,category,value):
        fallback=self.text.resolve('content.native:'+str(value),str(value)) if category=='content' else str(value)
        return self.text.resolve(category+'.'+str(value),fallback)
    def name(self,value,names):
        return names.get(value,names.get(str(value).removeprefix('nation/'),value))
    def value(self,value,names=None):
        if isinstance(value,dict):return {self.label('field',k):v if k in ('text','summary','source','old','new','output','message','reason','content') else self.value(v,names) for k,v in value.items()}
        if isinstance(value,list):return [self.value(v,names) for v in value]
        if isinstance(value,str):return self.name(self.label('content',value),names or {})
        return value
    def format(self,value,names=None,depth=0):
        """Readable detached facts; source text and scripts remain verbatim."""
        indent='  '*depth
        if isinstance(value,dict):
            if 'net_per_period' in value and 'payback_periods' in value:
                money=lambda v:self.format(v,names)
                rows=[f'{self.label("content",value["kind"])} · '+self.text('console.estimate_level',level=value['level']),
                      self.text('console.estimate_cost',cost=money(value['construction'])),
                      self.text('console.estimate_flows',inputs=money(value['inputs']),outputs=money(value['outputs']),basis=self.label('basis',value.get('output_basis','mid')),sell=money(value.get('outputs_sell',value['outputs']))),
                      self.text('console.estimate_energy',used=value['energy_used'],produced=value['energy_produced'],cost=self.format(value['energy_unit_cost']),reference=self.label('content',value.get('energy_reference') or '—')),
                      self.text('console.estimate_return',net=money(value['net_per_period']),payback=self.format(value['payback_periods']))]
                if value.get('projected_slots'):rows.append(self.text('console.estimate_density',slots=value['other_slots'],bonus=value['projected_slots']))
                if value['kind']=='native:academy' or value.get('academy_saved_per_period') or value.get('academy_density_per_period'):
                    rows.append(self.text('console.estimate_academy',saved=value.get('academy_saved_per_period',0),density=value.get('academy_density_per_period',0)))
                return '\n'.join(indent+row for row in rows)
            if {'known','unpriced','total','complete'}<=value.keys():
                return f'{value["total"]:g} 金' if value['complete'] else self.text('console.unpriced')+' ('+', '.join(map(str,value['unpriced']))+')'
            rows=[]
            for key,item in value.items():
                label=self.label('field',key)
                if label==key:label=self.label('content',key)
                body=str(item) if key in ('text','summary','source','old','new','output','message','reason','content') and isinstance(item,str) else self.format(item,names,depth+1)
                rows.append(f'{indent}{label}：'+ ('\n'+body if isinstance(item,(dict,list)) and '\n' in body else body))
            return '\n'.join(rows) or '—'
        if isinstance(value,list):
            if not value:return '—'
            if all(isinstance(row,list) and len(row)==2 and isinstance(row[0],str) and isinstance(row[1],(int,float)) for row in value):
                return '、'.join(f'{self.label("content",k)} {v:g}' for k,v in value)
            if all(not isinstance(row,(dict,list)) for row in value):return ', '.join(self.format(v,names) for v in value)
            return '\n'.join(indent+'· '+self.format(row,names,depth+1).lstrip() for row in value)
        if value is None:return '—'
        if isinstance(value,bool):return self.text('console.yes' if value else 'console.no')
        if isinstance(value,(int,float)):return f'{value:g}'
        return str(self.name(self.label('content',value),names or {}))
    def preview(self,text,*,chars=900,lines=14):
        """Only terminal output is capped; action() and journals retain all facts."""
        rows=text.splitlines();shown='\n'.join(rows[:lines])
        if len(rows)<=lines and len(shown)<=chars:return text
        return shown[:chars].rstrip()+'\n'+self.text('console.receipt_truncated')
    def action(self,period,actor,action,arguments,receipt,names):
        if isinstance(arguments,str):
            try:arguments=json.loads(arguments)
            except (ValueError,TypeError):pass
        self._names=names
        args=arguments if isinstance(arguments,dict) else {}
        caption=self.call_caption(action,args)
        result=self.receipt_body(action,args,receipt,actor)
        return self.text('console.action_line',period=period,actor=names.get(actor,actor),
            caption=caption,result=result)
    def event(self,row,names):
        text=self.name(row['text'],names)
        if row['kind']=='occupation':
            import re
            match=re.fullmatch(r'(.+) occupied \((\d+),(\d+)\); previous owner: (.+)',row['text'])
            if match:text=self.text('console.occupation',actor=self.name(match[1],names),x=match[2],y=match[3],previous=self.name(match[4],names) if match[4]!='None' else self.text('map.unowned'))
        elif row['kind']=='battle':
            import ast
            try:
                damage=ast.literal_eval(row['text'])
                text=self.text('console.battle_damage',details=', '.join(f'{self.name(a,names)} {v:g}' for a,v in damage))
            except (ValueError,TypeError,SyntaxError):pass
        return self.text('console.event',period=row['period'],
            kind=self.label('event',row['kind']),text=self.label('receipt',text))
    def message(self,row,names):
        payload=row['payload']
        body=payload.get('text') if isinstance(payload,dict) else payload
        if body is None:body=json.dumps(self.value(payload,names),ensure_ascii=False)
        return self.text('console.message',period=row['sampled'],source=names.get(row['source'],row['source']),
            target=names.get(row['target'],row['target']),payload=body)
    def board(self,rows):return '\n'.join(self.text('console.board',**row) for row in rows)
    def overview(self,state):
        names=state['names'];lines=['',self.text('console.overview',period=state['period'])]
        for observation in state['nations']:self.remember(observation)
        market='  '.join(f'{self.label("content",k)} {v:g}' for k,v in state['market']['prices'])
        lines.append(self.text('console.market',values=market));lines.append(self.text('console.bank',**state['bank']))
        zh=self.text.language=='zh-CN'
        headers=['国家','国土','军队','金币','粮食','木材','矿石','石油','装备','补给'] if zh else ['Nation','Land','Units','Gold','Food','Wood','Ore','Oil','Equip','Supply']
        rows=[]
        def number(v):
            if abs(v)>=100000000:return f'{v/100000000:.1f}亿' if zh else f'{v/1000000000:.1f}G'
            if abs(v)>=10000:return f'{v/10000:.1f}万' if zh else f'{v/1000:.1f}k'
            return f'{v:g}'
        for obs in state['nations']:
            f=obs['facts'];actor=obs['actor'];stocks={r['id']:r['amount'] for r in f['econ']['stocks']}
            rows.append([names[actor],len(f['land']),sum(u['owner']==actor for u in f['army']),*[stocks.get('native:'+r,0) for r in ('gold','food','wood','ore','oil','equipment','supply')]])
        table=[[str(v) if i==0 else number(v) for i,v in enumerate(row)] for row in rows]
        width=lambda t:sum(2 if unicodedata.east_asian_width(c) in 'WF' else 0 if unicodedata.combining(c) else 1 for c in t)
        sizes=[max([width(headers[i]),*[width(row[i]) for row in table]]) for i in range(len(headers))]
        def line(row):return '  '.join(v+' '*(sizes[i]-width(v)) if i==0 else ' '*(sizes[i]-width(v))+v for i,v in enumerate(row))
        lines+=['',line(headers),'  '.join('-'*n for n in sizes),*[line(r) for r in table],'',self.text('console.country_details')]
        for obs in state['nations']:
            actor=obs['actor'];facts=obs['facts'];econ=facts['econ'];units=[u for u in facts['army'] if u['owner']==actor]
            counts={}
            for unit in units:counts[unit['kind']]=counts.get(unit['kind'],0)+1
            military='、'.join(f'{self.label("content",kind)} {count}' for kind,count in sorted(counts.items())) or self.text('console.no_units')
            lines.append(self.text('console.country_head',name=names[actor],military=military))
            turn=econ.get('latest_turn')
            if turn:
                def money(v):return self.text('console.unpriced') if v.get('total') is None else f'{v["total"]:g}'
                lines.append(self.text('console.economy',gdp=money(turn['gdp']),
                    investment=money(turn['investment']),military=money(turn['military']),
                    assets=money(turn['total_assets']),liabilities=turn['liabilities']))
            gov=facts.get('government',{});plan=gov.get('plan')
            if plan:lines.append(self.text('console.policy',text=plan['text']))
            diplomacy=facts.get('diplomacy',{})
            for alliance in diplomacy.get('alliances',[]):
                if actor in alliance.get('members',[]):lines.append(self.text('console.alliance',name=alliance.get('name',alliance.get('id','')),members=', '.join(names.get(a,a) for a in alliance['members'])))
            for war in diplomacy.get('wars',[]):
                attackers=war.get('attackers',[]);defenders=war.get('defenders',[])
                if actor in attackers+defenders:lines.append(self.text('console.war',attackers=', '.join(names.get(a,a) for a in attackers),defenders=', '.join(names.get(a,a) for a in defenders)))
            for pact in diplomacy.get('pacts',[]):
                if 'nation/'+actor in (pact.get('a'),pact.get('b')):
                    lines.append(self.text('console.pact',details=self.label('pact',pact['kind'])+' '+str(self.name(pact['a'],names))+' → '+str(self.name(pact['b'],names))))
            for msg in state['records']['messages']:
                if msg['sampled']>=state['period']-1 and actor in (msg['source'],msg['target']):lines.append('  '+self.message(msg,names))
        if state['dead']:lines.append(self.text('console.dead',names=', '.join(names[a] for a in state['dead'])))
        return '\n'.join(lines)
