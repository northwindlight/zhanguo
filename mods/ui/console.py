"""Observer presentation over detached facts; no domain or host access."""
import json
import unicodedata
from .i18n import Translator
from .receipts import ReceiptPresentation
from .facts import flatten


class ConsoleUI(ReceiptPresentation):
    def __init__(self,cfg,tool_layer=None):
        self.tool_layer=tool_layer
        self.text=Translator.from_config(cfg,tool_layer=tool_layer)
        self._sites={};self._terrain={};self._catalog={};self._names={};self._tiles={};self._army_kinds={}
        self._facts={};self._period=0;self._actor=None
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
    KEEP_ACTIONS=('plan','end_turn','explain_plan')
    """终端整段照打的回执：国策、回合小结与议会解释——都是模型自己写的正文，
    剪掉尾巴就没法整段读完；其余回执（规则、面板、战绩流水）在屏幕上早截断，
    日志里一直是全的。"""

    def preview(self,text,*,action=None,chars=600,lines=10):
        """终端显示按行数/字数封顶，**但国策与规则不封顶**；其余回执更早截断。

        只在屏幕上截；`action()` 与日志保留完整正文和原始数据。
        """
        if action in self.KEEP_ACTIONS:return text
        rows=text.splitlines();shown='\n'.join(rows[:lines])
        if len(rows)<=lines and len(shown)<=chars:return text
        return shown[:chars].rstrip()+'\n'+self.text('console.receipt_truncated')
    def action(self,period,actor,action,arguments,receipt,names):
        if isinstance(arguments,str):
            try:arguments=json.loads(arguments)
            except (ValueError,TypeError):pass
        self._names=names
        self._period=period;self._actor=actor
        args=arguments if isinstance(arguments,dict) else {}
        caption=self.call_caption(action,args,actor,receipt)
        result=self.receipt_body(action,args,receipt,actor)
        actor_name=names.get(actor,actor)
        # 回执正文已经以同样的措辞开头时不再重复一遍（原先会出现「◇ 卖出 石油×1 → 卖出 石油×1，实收…」）。
        if caption and result.startswith(caption):
            return self.text('console.action_line_bare',period=period,actor=actor_name,result=result)
        return self.text('console.action_line',period=period,actor=actor_name,
            caption=caption,result=result)
    def event(self,row,names):
        # 行动回执的正文自带「[回合·行动] 国 ◇ …」抬头，再套一层事件前缀会写成
        # 「[26·action] [26·行动] 燕 ◇ …」——前缀重复，kind 还漏回英文。
        detail=row.get('details')
        if isinstance(detail,dict) and detail.get('source')=='action_receipt':return row['text']
        text=self.name(row['text'],names)
        if isinstance(detail,dict) and detail.get('type')=='alliance_vote':
            choices='、'.join(names.get(actor,actor)+'：'+self.text('console.ballot_'+('abstain' if choice is None else 'yes' if choice else 'no'))
                             for actor,choice in detail['ballots'].items())
            text=self.text('console.vote_tally',id=detail['id'],ballots=choices,yes=detail['yes'],no=detail['no'],
                           abstain=detail['abstain'],status=self.text('console.vote_'+detail['status']))
        if row.get('kind')=='tribute' and isinstance(detail,dict):
            text=self.text('console.tribute',source=names.get(detail['source'],detail['source']),
                target=names.get(detail['target'],detail['target']),resources=self.resources(detail['resources'],gold_short=False))
        if row.get('kind')=='occupation' and isinstance(detail,dict) and 'previous_owner' in detail:
            if detail['previous_owner'] is not None:
                previous=self.text('query.territory_lost',name=names.get(detail['previous_owner'],detail['previous_owner']))
            elif 'previous_core' in detail:
                previous=self.text('query.vacated_land' if detail['previous_core'] is not None else 'query.wild_land')
            else:previous=self.label('relation','unowned')
            owner=names.get(detail['owner'],detail['owner'])
            place=self.pos({'position':row['position'],'name':detail.get('name')})
            text=self.text('query.territory_change',place=place,previous=previous,owner=owner)
            if detail.get('core'):text+='｜'+self.text('query.core',name=names.get(detail['core'],detail['core']))
            if detail.get('town_halls'):text+='｜'+self.text('query.town_halls',count=detail['town_halls'])
        if isinstance(detail,dict) and {'actor','action','arguments','status','message'}<=detail.keys():
            actor=detail['actor'];self._names=names
            text=self.name(actor,names)+' '+self.call_caption(detail['action'],detail['arguments'],actor)+'｜'+self.label('status',detail['status'])+'｜'+self.label('receipt',detail['message'])
        return self.text('console.event',period=row['period'],
            kind=self.label('event',row['kind']),text=self.label('receipt',text))
    def message(self,row,names):
        payload=row['payload']
        if row.get('kind')=='spy' and isinstance(payload,dict):
            title=self.text('console.spy_message',period=row['sampled'],id=row['id'],
                            source=names.get(row['source'],row['source']),target=names.get(row['target'],row['target']))
            counts='、'.join(self.label('content',kind)+'×'+str(count) for kind,count in payload.get('unit_counts',{}).items()) or self.text('console.no_units')
            lines=[title,'  '+self.text('console.intel_inventory',values=self.resources(payload.get('inventory',{}),gold_short=False)),
                   '  '+self.text('console.intel_armies',values=counts)]
            if 'buildings_total' in payload:lines.append('  '+self.text('console.intel_buildings',count=payload['buildings_total']))
            economy=payload.get('economy')
            if isinstance(economy,dict):
                lines.append('  '+self.text('console.intel_economy',period=economy['period'],
                    gdp=self.money(economy['gdp']),assets=self.money(economy['total_assets']),liabilities=economy['liabilities']))
            lines.extend(self.intelligence_tiles(payload.get('tiles',[]),names))
            for key in ('alliances','wars'):
                if payload.get(key):lines.append('  '+self.label('field',key)+'：'+self.format(payload[key],names))
            return '\n'.join(lines)
        if row.get('kind')=='map' and isinstance(payload,dict):
            title=self.text('console.map_message',period=row['sampled'],id=row['id'],
                            source=names.get(row['source'],row['source']),target=names.get(row['target'],row['target']))
            return '\n'.join([title,*self.intelligence_tiles(payload.get('tiles',[]),names)])
        if row.get('kind')=='gift' and isinstance(payload,dict):
            body=self.text('console.gift_received',values=self.resources({payload['resource']:payload['quantity']},gold_short=False))
        else:body=payload.get('text') if isinstance(payload,dict) else payload
        if body is None:body=self.format(payload,names)
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
        flows=state.get('resource_flows',{}).get('owners',{})
        def number(v):return str(v) if isinstance(v,int) else f'{v:g}'
        import re
        ansi=re.compile(r'\033\[[0-9;]*m')
        width=lambda t:sum(2 if unicodedata.east_asian_width(c) in 'WF' else 0 if unicodedata.combining(c) else 1 for c in ansi.sub('',t))
        resources=('gold','food','wood','ore','oil','equipment','supply')
        source=[]
        for obs in state['nations']:
            f=obs['facts'];actor=obs['actor'];stocks={r['id']:r['amount'] for r in f['econ']['stocks']}
            source.append((names[actor],len(f['land']),sum(u['owner']==actor for u in f['army']),actor,
                [stocks.get('native:'+resource,0) for resource in resources]))
        amount_widths=[max([0,*[width(number(row[4][i])) for row in source]]) for i in range(len(resources))]
        table=[]
        for name,land,army,actor,amounts in source:
            row=[name,number(land),number(army)]
            for index,(resource,amount) in enumerate(zip(resources,amounts)):
                value=number(amount);value+=' '*(amount_widths[index]-width(value))
                delta=flows.get(actor,{}).get('native:'+resource,0)
                if delta:
                    color='32' if delta>0 else '31'
                    value+=' '+f'\033[{color}m{delta:+g}\033[0m'
                row.append(value)
            table.append(row)
        sizes=[max([width(headers[i]),*[width(row[i]) for row in table]]) for i in range(len(headers))]
        def line(row):return '  '.join(v+' '*(sizes[i]-width(v)) for i,v in enumerate(row))
        lines+=['',self.text('console.flow_legend'),line(headers),'  '.join('-'*n for n in sizes),*[line(r) for r in table],'',self.text('console.country_details')]
        for obs in state['nations']:
            actor=obs['actor'];facts=flatten(obs['facts']);econ=facts['econ'];units=[u for u in facts['army'] if u['owner']==actor]
            counts={}
            for unit in units:counts[unit['kind']]=counts.get(unit['kind'],0)+1
            military='、'.join(f'{self.label("content",kind)} {count}' for kind,count in sorted(counts.items())) or self.text('console.no_units')
            lines.append(self.text('console.country_head',name=names[actor],military=military))
            lines.append(self.text('console.assets',assets=self.money(econ['gross_assets']),
                                   liabilities=facts.get('bank',{}).get('liabilities',0)))
            diplomacy=facts.get('diplomacy',{})
            for relation in diplomacy.get('vassals',[]):
                if actor in (relation['subject'],relation['overlord']):
                    lines.append(self.text('console.vassal',subject=names.get(relation['subject'],relation['subject']),
                        overlord=names.get(relation['overlord'],relation['overlord']),
                        kind=self.text('console.vassal_'+relation['kind']),percent=relation['tribute_percent']))
            for alliance in diplomacy.get('alliances',[]):
                if actor in alliance.get('members',[]):lines.append(self.text('console.alliance',name=alliance.get('name',alliance.get('id','')),members=', '.join(names.get(a,a) for a in alliance['members'])))
            for war in diplomacy.get('wars',[]):
                attackers=war.get('attackers',[]);defenders=war.get('defenders',[])
                if actor in attackers+defenders:lines.append(self.text('console.war',attackers=', '.join(names.get(a,a) for a in attackers),defenders=', '.join(names.get(a,a) for a in defenders)))
            for pact in diplomacy.get('pacts',[]):
                if 'nation/'+actor in (pact.get('a'),pact.get('b')):
                    lines.append(self.text('console.pact',details=self.label('pact',pact['kind'])+' '+str(self.name(pact['a'],names))+' → '+str(self.name(pact['b'],names))))
        if state['dead']:lines.append(self.text('console.dead',names=', '.join(names[a] for a in state['dead'])))
        return '\n'.join(lines)
