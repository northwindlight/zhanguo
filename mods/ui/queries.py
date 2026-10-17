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
        layer=getattr(self,'tool_layer',None)
        if layer is not None:
            rendered=layer.panel(action,data,args,actor,self)
            if rendered is not None:return rendered
        if action=='view' and isinstance(data,list):
            return player_atlas(self.observation(actor,view=data),self.text)
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
