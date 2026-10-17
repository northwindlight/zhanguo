"""Dedicated query prose over the latest detached player observation."""
from .views import player_atlas


class QueryPresentation:
    def observation(self,actor,**facts):
        return {'actor':actor,'period':self._period,'facts':{**self._facts.get(actor,{}),**facts}}

    def building_names(self,rows):
        groups={}
        for row in rows:groups.setdefault(row['kind'],[]).append(row)
        parts=[]
        for kind,items in groups.items():
            name=self.label('content',kind)
            if kind=='native:castle' or any(row.get('level',1)>1 for row in items):
                name+=' L'+str(max(row.get('level',1) for row in items))
            else:name+='×'+str(len(items))
            parts.append(name)
        return '、'.join(parts) or self.text('query.none')

    def query_body(self,action,args,data,actor):
        if action=='view' and isinstance(data,list):
            return player_atlas(self.observation(actor,view=data),self.text)
        if action=='nations' and isinstance(data,list):
            return '\n'.join(self.text('query.nation_row',name=row['name'],identity=row['id'],
                status=self.text('query.alive' if row['alive'] else 'query.extinct'))+
                ('｜'+row['extinction_reason'] if row.get('extinction_reason') else '') for row in data) or self.text('query.none')
        if action=='nation' and isinstance(data,dict):
            nation=data['nation'];sites=data.get('sites',[])
            return self.text('query.nation_head',name=nation['name'],identity=nation['id'],count=len(sites))+(
                '\n'+'、'.join(self.pos(s,actor=actor) for s in sites) if sites else '')
        if action=='government' and isinstance(data,dict):
            plan=data.get('plan');period=data['period']
            lines=[self.text('query.government_head',period=period,status=self.text('query.ended' if data['ended'] else 'query.acting'))]
            if plan:
                lines.append(self.text('query.plan_head',start=plan['period'],end=plan['valid_until'],
                    status=self.text('query.expired' if plan['valid_until']<period else 'query.valid')))
                lines.append(plan['text'])
            else:lines.append(self.text('query.no_plan'))
            # 国策是当前战略，不在这里重播所有历史小结；历史仍由 memory_search 检索。
            return '\n'.join(lines)
        if action=='terrain_catalog' and isinstance(data,list):
            return '\n'.join(self.text('query.terrain',name=self.label('content',row['id']),
                defense=row['defense'],penalty=row['build_penalty'],cost=row['cavalry_cost']) for row in data)
        if action=='frontier' and isinstance(data,dict):
            lines=[self.text('query.frontier_head',seen=data['visible_unowned'],reachable=len(data['reachable']),frontier=len(data['frontier']))]
            # 紧急警报必须在坐标长表前，否则终端截断会再次藏掉用户要的国祚提示。
            for warning in data.get('capital_warning',[]):
                enemies='、'.join(self.name(u['owner'],self._names)+' '+self.label('content',u['kind'])+'#'+str(u['id']) for u in warning['enemies'])
                lines.append(self.text('query.capital_warning',place=self.pos(warning,actor=actor),enemies=enemies))
            for key in ('frontier','reachable'):
                lines.append(self.text('query.'+key,places='、'.join(self.pos(p,actor=actor) for p in data[key]) or self.text('query.none')))
            return '\n'.join(lines)
        if action=='dispatches' and isinstance(data,list):
            if not data:return self.text('query.no_dispatches')
            lines=[self.text('query.dispatches_head',count=len(data))]
            for row in sorted(data,key=lambda r:(r['arrive'] if r['arrive'] is not None else float('inf'),r['id'])):
                kind=self.text.resolve('query.dispatch.'+row['kind'],row['kind'])
                lines.append(self.text('query.dispatch_row',identity=row['id'],kind=kind,target=self.name(row['target'],self._names),
                    status=self.label('status',row['status']),submitted=row['submitted'],arrive=row['arrive'],fee=row['fee']))
                if row.get('resource'):lines.append('  '+self.resources({row['resource']:row['quantity']},gold_short=False))
                if row.get('text'):lines.append('  '+row['text'])
            return '\n'.join(lines)
        return None

    def army_list(self,rows,actor):
        # 本国军、他国军、静态野人分开；海量野人不应挤掉本国行动状态。
        own=sorted((u for u in rows if u['owner']==actor),key=lambda u:u['id'])
        foreign=sorted((u for u in rows if u['owner'] not in (actor,'野人')),key=lambda u:(u['owner'],u['id']))
        guards=[u for u in rows if u['owner']=='野人']
        lines=[self.text('query.army_head',own=len(own),foreign=len(foreign),guards=len(guards))]
        lines.extend(self.army_line(u) for u in own)
        lines.extend(self.army_line(u) for u in foreign)
        if guards:lines.append(self.text('query.guard_hint',count=len(guards)))
        return '\n'.join(lines)

    def country_list(self,rows):
        lines=[self.text('query.countries_head')]
        for row in rows:
            tags=[self.relation(row['relation'])]
            for pact in row.get('treaties',[]):
                tags.append(self.text('query.defense' if pact['kind']=='defense' else 'query.guarantee_'+pact['direction']))
            if row['border']:tags.append(self.text('query.border'))
            if row.get('letter_count'):tags.append(self.text('query.letters',count=row['letter_count']))
            elif row['has_letter']:tags.append(self.text('query.has_letter'))
            if row.get('entity','').startswith('bloc/'):
                bloc=next((b['name'] for b in self._facts.get(self._actor,{}).get('diplomacy',{}).get('alliances',[]) if 'bloc/'+b['id']==row['entity']),row['entity'])
                tags.append(self.text('query.bloc',name=bloc,chief=self.name(row['chief'],self._names)))
            if row.get('war_representative'):tags.append(self.text('query.peace_rep',name=self.name(row['war_representative'],self._names)))
            lines.append(row['name']+'（'+row['id']+'）｜'+'｜'.join(tags))
        return '\n'.join(lines) if rows else self.text('query.no_countries')

    def intel_history(self,rows):
        if not rows:return self.text('console.no_intel')
        # 以到达顺序倒排，而非派遣 ID；迟到的低编号报告也应排在新近讯。
        ordered=list(reversed(rows))
        lines=[self.text('query.intel_head',count=len(rows)),self.message(ordered[0],self._names)]
        for row in ordered[1:]:
            kind=self.text.resolve('query.dispatch.'+row['kind'],row['kind'])
            lines.append(self.text('query.intel_index',identity=row['id'],period=row['sampled'],kind=kind,
                                   source=self.name(row['source'],self._names)))
        lines.extend(self.message(row,self._names) for row in ordered[1:])
        return '\n'.join(lines)
