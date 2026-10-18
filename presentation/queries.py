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
