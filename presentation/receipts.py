"""Spectator prose over public JSON, without engine or domain object access."""
from collections import Counter
from copy import deepcopy
from engine.coordinates import coordinates
import json
from .queries import QueryPresentation
from .facts import flatten, army_state


def num(value):
    if value is None:return '—'
    if isinstance(value,(int,float)):return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value,float) else str(value)
    return str(value)


# 外交动作原先共用一句「已提交，等待外交结算；预留X金」——分不出宣战、共防还是保障。
DIPLOMACY_ACTIONS=frozenset(('guarantee','cancel_guarantee','propose_defense','break_defense',
    'declare_war','offer_peace','accept_peace','reject_peace','accept_proposal','reject_proposal',
    'bloc_found','bloc_join','bloc_leave','bloc_dissolve','bloc_rename','bloc_transfer','vote'))


def step_notice(receipt):
    """调用进度提醒：模型往返超过 20 次、或只剩 5 次时，催玩家收尾。

    单位是模型往返（clients/llm.py 的 step），不是工具调用条数——
    一次往返可以并行发多个工具调用。回执上没有 steps 字段时什么也不做。
    """
    steps=receipt.get('steps') if isinstance(receipt,dict) else None
    if not isinstance(steps,dict):return None
    used,limit=steps.get('used'),steps.get('limit')
    if type(used) is not int or type(limit) is not int or limit<1:return None
    remaining=limit-used
    if used<=20 and remaining>5:return None
    return (f'⚠️ 本回合已用 {used}/{limit} 次模型调用（还剩 {remaining} 次）：'
            '先停手查资料，把计划写进策略脚本并保存，尽快 end_turn；没查完的留到下回合。')


class ReceiptPresentation(QueryPresentation):
    def remember(self,observation):
        facts=flatten(observation.get('facts',observation))
        actor=observation.get('actor','')
        if actor:
            self._facts[actor]={key:deepcopy(facts[key]) for key in
                ('land','building_sites','building_catalog','army','unit_catalog','nations','diplomacy','view','frontier') if key in facts}
            self._period=observation.get('period',self._period)
        tiles=self._tiles.setdefault(actor,{})
        for row in facts.get('view',[]):
            tile=row.get('tile',row)
            position=tile.get('position')
            if isinstance(position,(list,tuple)) and len(position)==2 and tile.get('name') and row.get('owner',actor) is not None:
                tiles[tuple(position)]=tile['name']
        for row in facts.get('army',[]):
            if 'owner' in row and 'id' in row and 'kind' in row:
                self._army_kinds[(row['owner'],row['id'])]=row['kind']
        sites=facts.get('building_sites',{})
        for row in sites.get('sites',[]):self._sites[row['id']]=deepcopy(row)
        for row in facts.get('land',[]):
            tile=row.get('tile',row)
            position=tile.get('position')
            if isinstance(position,(list,tuple)) and len(position)==2 and tile.get('name') and row.get('owner',actor) is not None:
                tiles[tuple(position)]=tile['name']
            if 'id' in tile:self._terrain[tile['id']]=tile.get('terrain')

    @staticmethod
    def _coordinates(text):
        """坐标串（`4 12` / `4,12` / `(4,12)`）→ (x, y)；不是坐标串就是 None。

        玩家可以拿坐标串填 `place` / `site`（与引擎 `native:territory.resolve` 同一套写法），
        所以回执这条也得认——否则模型按说明填坐标，日志里那块地只剩「所选地块」四个字。
        """
        return coordinates(text)

    def pos(self,value=None,args=None,actor=None):
        args=args or {}
        if 'x' in args and 'y' in args:value=[args['x'],args['y']]
        elif value is None:value=args.get('place',args.get('site',args.get('position')))
        tile_name=None
        if isinstance(value,dict):
            tile_name=value.get('name')
            value=value.get('position',value.get('site'))
        if isinstance(value,(list,tuple)) and len(value)==2:
            x,y=value
            tile_name=tile_name or self._tiles.get(actor,{}).get((x,y))
            return f'{tile_name}({x}, {y})' if tile_name else f'({x}, {y})'
        if isinstance(value,str) and value in self._sites:return self.pos(self._sites[value]['position'],actor=actor)
        if isinstance(value,str):
            coordinates=self._coordinates(value)
            if coordinates is not None:return self.pos(list(coordinates),actor=actor)
        if isinstance(value,str):
            for position,name in self._tiles.get(actor,{}).items():
                if name==value:return f'{name}({position[0]}, {position[1]})'
        # Native site identity is a fallback only; observations provide coordinates.
        if isinstance(value,str):
            import re
            m=re.search(r'/land-(\d+)-(\d+)$',value)
            if m:return self.pos([int(m[1]),int(m[2])],actor=actor)
        # 认不出来时别拿「所选地块」冒充地块：玩家写的是什么就照原样给回去，日志至少看得见。
        if isinstance(value,str) and value.strip():return value.strip()
        return '所选地块'

    def unit_ref(self,unit_id,actor):
        kind=self._army_kinds.get((actor,unit_id))
        return (self.label('content',kind)+'#' if kind else '#')+str(unit_id)

    def resources(self,value,sign='',gold_short=True):
        pairs=value.items() if isinstance(value,dict) else value or []
        labels={'native:gold':'金','native:wood':'木','native:food':'粮','native:ore':'矿','native:oil':'油'}
        return ' '.join(f'{sign}{num(n)}{labels.get(k,self.label("content",k)) if gold_short else self.label("content",k)}' for k,n in pairs if n) or '无'

    def money(self,value):
        if value is None:return '无法估值'
        if isinstance(value,dict):
            if value.get('total') is None:return '未估值（'+','.join(self.label('content',k) for k in value.get('unpriced',[]))+'）'
            return num(value['total'])+'金'
        return num(value)+'金'

    def intelligence_tiles(self,tiles,names):
        lines=['  '+self.text('console.intel_tiles',count=len(tiles))]
        for tile in tiles:
            owner=names.get(tile.get('owner'),tile.get('owner')) or self.label('relation','unowned')
            buildings='、'.join(self.label('content',kind)+'×'+str(count) for kind,count in tile.get('buildings',{}).items()) or '—'
            construction='、'.join(self.label('content',kind)+'×'+str(count) for kind,count in tile.get('construction',{}).items()) or '—'
            lines.append('  '+self.pos(tile.get('position'))+' '+self.label('content',tile['terrain'])+' · '+owner+
                '｜'+self.label('field','buildings')+' '+buildings+'｜'+self.label('field','construction')+' '+construction+
                '｜'+self.label('field','resources')+' '+self.resources(tile.get('resources',{}),gold_short=False))
        return lines

    def call_caption(self,action,args,actor=None,receipt=None):
        """抬头：工具层成文优先；直接构造 ConsoleUI（无 tool layer）时退回通用格式。"""
        layer=getattr(self,'tool_layer',None)
        if layer is not None:
            rendered=layer.caption(action,args,actor,receipt,self)
            if rendered is not None:return rendered
        title=self.label('action',action)
        parts=[]
        for key,value in args.items():
            if key in ('text','summary','source','old','new','content'):continue
            if key in ('site','tile'):value=self.pos(value)
            elif isinstance(value,str):value=self.name(self.label('content',value),self._names)
            else:value=num(value)
            parts.append(f'{self.label("field",key)}={value}')
        return title+(' '+' '.join(parts) if parts else '')

    def receipt_body(self,action,args,receipt,actor):
        """所有工具回执的统一出口：正文之外附一条标准的调用进度提醒。

        进度由 engine/player.py 的 note_step 盖在回执上，单位是模型往返次数，
        与发行版无关；没有该字段（规则客户端、非模型调用）时行为不变。
        """
        text=self._receipt_body(action,args,receipt,actor) or ''
        notice=step_notice(receipt)
        return text+('\n'+notice if notice else '')

    def _receipt_body(self,action,args,receipt,actor):
        """回执：工具层持有全部动作成文；这里只保留协议与兜底。

        工具层拿不到上下文时返回 None，才落到通用失败/数据倾倒格式——
        未知 Mod 的工具也必须能讲清楚发生了什么。
        """
        # 只读面板统一走 query：正文交给面板渲染器。
        if action=='query' and isinstance((args or {}).get('panel'),str):
            target='view' if args['panel']=='map' else args['panel']
            if target!='query':return self._receipt_body(target,args,receipt,actor)
        data=receipt.get('data');message=self.label('receipt',receipt.get('message',''))
        layer=getattr(self,'tool_layer',None)
        if not receipt['ok']:
            if isinstance(data,dict) and data.get('reason')=='insufficient_resource':
                required=data.get('required') or 0;available=data.get('available') or 0
                resource=self.label('content',data.get('resource',''))
                hint=('先卖库存换金' if data.get('resource')=='native:gold'
                      else '可在市场买入，或等本期产出')
                return (f'❌ {resource}不足：需要 {num(required)}，现有 {num(available)}，'
                        f'还差 {num(max(0,required-available))}；{hint}，或缩小数量。')
            if isinstance(data,dict) and data.get('reason')=='market_inventory':
                resource=self.label('content',data.get('resource',''))
                requested=data.get('requested') or 0;available=data.get('available') or 0
                return (f'❌ 市场 {resource} 库存不足：想买 {num(requested)}，可卖 {num(available)}，'
                        f'还差 {num(max(0,requested-available))}；减少数量或等补货。')
            if isinstance(data,dict) and data.get('reason')=='building_slots':
                return self.building_slots(data)
            if action=='plan' and isinstance(data,dict) and 'reason' in data:
                return '❌ 议会未批准国策：'+data['reason']+'\n'+args.get('text','')
            if layer is not None:
                rendered=layer.receipt(action,args,receipt,actor,self)
                if rendered is not None:return rendered
            return '❌ '+(message or str(receipt.get('code') or '未完成'))
        if layer is not None:
            rendered=layer.receipt(action,args,receipt,actor,self)
            if rendered is not None:return rendered
        query=self.query_body(action,args,data,actor)
        if query is not None:return query
        if action=='player_rules' and isinstance(data,list):
            if not data:return '未找到该主题；省略主题可查看全部规则。'
            return '\n\n'.join('【'+r['title']+'】\n'+r['body'] for r in data)
        if action=='player_events' and isinstance(data,dict) and 'items' in data:
            rows=data.get('items') or []
            return (f'【事件】共 {len(rows)} 条，游标 {num(data.get("cursor",0))}：\n'+
                    ('\n'.join(self.event(row,self._names) for row in rows) or '无'))
        if action=='memory_search' and isinstance(data,list):
            return '【历史记忆】\n'+('\n\n'.join('第'+str(row['period'])+'期：\n'+row['context'] for row in data) or '没有匹配记录。')
        if isinstance(data,dict) and receipt.get('status')=='submitted':
            return '已提交，等待外交结算；预留'+num(data.get('fee',0))+'金。'+('\n'+self.label('receipt',data['message']) if data.get('message') else '')
        # Unknown mods retain all their public data in readable compact form.
        return message+('\n' if message and data is not None else '')+(self.format(data,self._names) if data is not None else '') or '已完成。'
    def building_slots(self,data):
        """地块建造失败：把建筑位、同类上限、本地资源容量和已有建筑一次说清。"""
        position=data.get('position') or (0,0);pos=self.pos(position)
        kind=self.label('content',data.get('kind',''))
        occupied=data.get('occupied') or 0;capacity=data.get('capacity')
        block=data.get('block')
        if block=='slots':
            reason=f'本格建筑位已满：{num(occupied)}/{num(capacity)}'
        elif block=='per_site':
            reason=f'同类建筑已达上限：{kind} 已有 {num(data.get("same"))}，上限 {num(data.get("max_per_site"))}'
        elif block=='resource':
            need=(data.get('same') or 0)+(data.get('count') or 1)
            reason=(f'本格 {self.label("content",data.get("site_resource") or "")} 容量不足：'
                    f'需要 {num(need)}，本格容量 {num(data.get("resource_capacity"))}')
        elif block=='min_existing':
            reason=f'现有建筑数量不足：本格已有 {num(occupied)}，至少需 {num(data.get("min_slots"))}'
        else:
            reason='本格无法建造'
        built='、'.join(self.label('content',item) for item in (data.get('existing') or ())) or '无'
        resources=data.get('resources') or {}
        capacity_text='、'.join(f'{self.label("content",key)} {num(value)}'
                                for key,value in sorted(resources.items())) or '无'
        x,y=position[0],position[1]
        return (f'❌ 无法在 {pos} 建造{kind}：{reason}。\n'
                f'本格已有建筑：{built}（{num(occupied)}/{num(capacity)} 位）。\n'
                f'本格资源容量：{capacity_text}。\n'
                f'地块详情：query(panel=tile, place="{x} {y}")。')

    @staticmethod
    def relation(value):return {'war':'交战','ally':'同盟','neutral':'中立'}.get(value,value)

    def army_line(self,u):
        state=army_state(u,self._period)
        status='｜'+self.text('query.army_'+state)
        catalog=self._facts.get(self._actor,{}).get('unit_catalog',{})
        if u['owner']==self._actor and u['kind'] in catalog:status+='｜'+self.text('query.speed',speed=catalog[u['kind']]['speed'])
        # 旧版逐军给「本回合可及 N 格」：同一兵种在不同位置可及数不同，聚合数帮不上忙。
        reach={row['id']:row.get('reach',0) for row in (self._facts.get(self._actor,{}).get('frontier') or {}).get('units',[])}
        if u['owner']==self._actor and state=='ready' and u['id'] in reach:status+='｜'+self.text('query.army_reach',count=reach[u['id']])
        if u.get('retreat_to') is not None:status+='｜待撤至'+self.pos(u['retreat_to'])+'｜撤退承伤比例'+num(u.get('retreat_cover'))+'%'
        return f'{self.name(u["owner"],self._names)} {self.label("content",u["kind"])}#{u["id"]} {self.pos([u["x"],u["y"]],actor=u["owner"])}｜HP{num(u["hp"])}'+status
