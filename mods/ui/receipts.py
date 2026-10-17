"""Spectator prose over public JSON, without engine or domain object access."""
from collections import Counter
from copy import deepcopy
from engine.coordinates import coordinates
import json
from .queries import QueryPresentation
from .facts import flatten


def num(value):
    if value is None:return '—'
    if isinstance(value,(int,float)):return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value,float) else str(value)
    return str(value)


# 外交动作原先共用一句「已提交，等待外交结算；预留X金」——分不出宣战、共防还是保障。
DIPLOMACY_ACTIONS=frozenset(('guarantee','cancel_guarantee','propose_defense','break_defense',
    'declare_war','offer_peace','accept_peace','reject_peace','accept_proposal','reject_proposal',
    'bloc_found','bloc_join','bloc_leave','bloc_dissolve','bloc_rename','bloc_transfer','vote'))


class ReceiptPresentation(QueryPresentation):
    def remember(self,observation):
        facts=flatten(observation.get('facts',observation))
        actor=observation.get('actor','')
        if actor:
            self._facts[actor]={key:deepcopy(facts[key]) for key in
                ('land','building_sites','army','unit_catalog','nations','diplomacy','view','frontier') if key in facts}
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

    def constraints(self,row):
        pieces=[]
        if row.get('min_slots'):pieces.append(f'需已有{row["min_slots"]}座建筑')
        if row.get('site_resource'):pieces.append('受本地'+self.label('content',row['site_resource'])+'容量限制')
        if row.get('max_per_site') is not None:pieces.append(f'每格限{row["max_per_site"]}座/级')
        if row.get('self_build_limit') is not None:pieces.append(f'全国自建限{row["self_build_limit"]}座')
        return '；'.join(pieces)

    def behavior(self,row):
        effects=row.get('effects',{});pieces=[]
        if effects.get('gold_base') is not None:pieces.append(f'产{effects["gold_base"]}金+本格每座其他建筑×{effects.get("gold_per_slot",0)}金')
        elif row.get('outputs'):pieces.append('产'+self.resources(row['outputs']))
        if row.get('inputs'):pieces.insert(0,'耗'+self.resources(row['inputs']))
        if row.get('energy_output'):pieces.append(f'发{row["energy_output"]}电')
        if row.get('energy_use'):pieces.append(f'耗{row["energy_use"]}电')
        if effects.get('defense_per_level'):pieces.append(f'每级防御+{effects["defense_per_level"]}%')
        if effects.get('build_discount'):pieces.append(f'本格后续建造金价−{effects["build_discount"]}%')
        if effects.get('vision_radius'):pieces.append(f'视野半径{effects["vision_radius"]}')
        if effects.get('recruit_cap'):pieces.append(f'每回合可征{effects["recruit_cap"]}军')
        if effects.get('militia_cap'):pieces.append(f'可驻{effects["militia_cap"]}支民兵，每回合可征{effects.get("militia_per_turn",1)}支')
        return '，'.join(pieces) or '辅助建筑，不直接产出'

    def catalog(self,rows):
        lines=[f'【建筑一览 · {len(rows)}种】造价为基础金价；地形、工程院和本国政体系数另计。']
        for row in rows:
            key=row.get('id',row.get('kind'));self._catalog[key]=deepcopy(row)
            costs=row.get('costs_by_level',[])
            if costs and len(costs)>1:
                cost='→'.join(num(c.get('native:gold',0)) for c in costs)+'金'
                rest={k:v for k,v in costs[0].items() if k!='native:gold'}
                if rest:cost+=' '+self.resources(rest)
            else:cost=self.resources(costs[0] if costs else row['costs']) if costs or 'costs' in row else '造价未提供'
            rule=self.constraints(row)
            lines.append(f'{self.label("content",key)}：{cost}｜{self.behavior(row)}'+('｜'+rule if rule else ''))
        return '\n'.join(lines)

    def economics(self,data):
        rows=data if isinstance(data,list) else [data]
        lines=['【完整建筑报告 · 当前市价】基础造价含本国政体系数（普通国家 100%，匈奴 +30%），不含地形和工程院优惠；满负荷估算。',
               '采集按卖价，工厂按买价自用替代，耗电按木电燃料成本；收益不是本回合实收。']
        for row in rows:
            name=self.label('content',row['kind']);net=self.money(row['net_per_period']);pb=row['payback_periods']
            details=f'造价折{self.money(row["construction"])}'
            if row['energy_produced']:
                details+=f'｜每回合燃料{self.money(row["inputs"])}→{row["energy_produced"]}电（电不出售）'
            else:
                details+=f'｜每回合净收益{net}'
                if row['energy_used']:details+=f'（已扣{row["energy_used"]}电成本）'
                if row.get('output_basis')=='buy':details+=f'｜纯外销产值{self.money(row["outputs_sell"])}'
                details+='｜回本'+(num(pb)+'回合' if pb is not None else '—')
            lines.append(name+'：'+details)
            if row.get('costs_by_level'):
                definition={**row,'inputs':dict(row.get('recipe_inputs',[])),'outputs':dict(row.get('recipe_outputs',[])),
                            'energy_use':row['energy_used'],'energy_output':row['energy_produced'],'effects':dict(row.get('effects',[]))}
                costs=row['costs_by_level']
                cost='各级基础造价 '+('→'.join(num(c.get('native:gold',0)) for c in costs)+'金 '+self.resources({k:v for k,v in costs[0].items() if k!='native:gold'}) if len(costs)>1 else self.resources(costs[0]))
                lines.append('  '+cost+'；'+self.behavior(definition)+('；'+self.constraints(row) if self.constraints(row) else ''))
                for assumption in row.get('assumptions',[]):lines.append('  '+assumption)
        return '\n'.join(lines)

    def land(self,rows,instances=None,actor=None,args=None):
        facts=self._facts.get(actor,{})
        sites=facts.get('building_sites',{})
        if instances is None and 'instances' in sites:instances=sites['instances']
        args=args or {}
        if args:
            lines=[self.text('query.land_page',count=len(rows),offset=args.get('offset',0))]
            if 'land' in facts:lines[0]+='｜'+self.text('query.land_total',count=len(facts['land']))
            if args.get('terrain'):lines[0]+='｜'+self.label('content',args['terrain'])
            if args.get('resource'):lines[0]+='｜'+self.label('content',args['resource'])
            more=any(args.get(key) for key in ('terrain','resource')) or 'land' not in facts or args.get('offset',0)+len(rows)<len(facts['land'])
            if args.get('limit') and len(rows)==args['limit'] and more:
                lines.append(self.text('query.land_next',offset=args.get('offset',0)+len(rows)))
        else:lines=[f'国土 {len(rows)} 块：']
        cores={tuple(r['tile']['position']):r['tile'].get('core') for r in facts.get('land',[])}
        for row in rows:
            tile=row.get('tile',row);site=self._sites.get(tile.get('id'),{})
            completed=[b for b in instances or [] if b['site']==tile.get('id') and not b['remaining']]
            pending=[b for b in instances or [] if b['site']==tile.get('id') and b['remaining']]
            buildings=self.building_names(completed)
            if pending:buildings+='；在建 '+self.building_names(pending)
            resources=self.resources(row.get('resources',site.get('resources',[])),gold_short=False)
            cap=f'｜建筑位{len(completed)+len(pending)}/{site["capacity"]}' if 'capacity' in site and instances is not None else ''
            terrain=self.label('content',tile.get('terrain') or self._terrain.get(tile.get('id')) or '')
            position=tuple(tile.get('position',()))
            core=tile.get('core',cores.get(position))
            details=[]
            if core:details.append(self.text('query.core',name=self.name(core,self._names)))
            if 'construction' in sites:
                ordered=any(r['site']==tile.get('id') and r['period']==self._period for r in sites['construction'])
                details.append(self.text('query.ordered' if ordered else 'query.can_build'))
            guards=[u for u in facts.get('army',[]) if u['owner']==actor and (u['x'],u['y'])==position and u['hp']>0]
            if guards:details.append(self.text('query.garrison',units='、'.join(self.unit_ref(u['id'],actor)+' '+num(u['hp'])+'HP' for u in guards)))
            lines.append(f'{self.pos(tile,actor=actor)}{terrain}{cap}｜资源 {resources}'+('｜建筑 '+buildings if instances is not None else '')+
                         ('｜'+'｜'.join(details) if details else ''))
        return '\n'.join(lines)

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
        """回执：工具层持有全部动作成文；这里只保留协议与兜底。

        工具层拿不到上下文时返回 None，才落到通用失败/数据倾倒格式——
        未知 Mod 的工具也必须能讲清楚发生了什么。
        """
        # 只读面板统一走 query：正文交给面板渲染器。
        if action=='query' and isinstance((args or {}).get('panel'),str):
            target='view' if args['panel']=='map' else args['panel']
            if target!='query':return self.receipt_body(target,args,receipt,actor)
        data=receipt.get('data');message=self.label('receipt',receipt.get('message',''))
        layer=getattr(self,'tool_layer',None)
        if not receipt['ok']:
            if isinstance(data,dict) and data.get('reason')=='insufficient_resource':
                return f'❌ {self.label("content",data["resource"])}不足：需要{num(data["required"])}，现有{num(data["available"])}；请先补足资源或缩小数量。'
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
    @staticmethod
    def relation(value):return {'war':'交战','ally':'同盟','neutral':'中立'}.get(value,value)

    def army_line(self,u):
        state='newborn' if u.get('born_turn')==self._period else 'engaged' if u.get('engaged') else 'moved' if u.get('moved_turn')==self._period else 'ready'
        status='｜'+self.text('query.army_'+state)
        catalog=self._facts.get(self._actor,{}).get('unit_catalog',{})
        if u['owner']==self._actor and u['kind'] in catalog:status+='｜'+self.text('query.speed',speed=catalog[u['kind']]['speed'])
        # 旧版逐军给「本回合可及 N 格」：同一兵种在不同位置可及数不同，聚合数帮不上忙。
        reach={row['id']:row.get('reach',0) for row in (self._facts.get(self._actor,{}).get('frontier') or {}).get('units',[])}
        if u['owner']==self._actor and state=='ready' and u['id'] in reach:status+='｜'+self.text('query.army_reach',count=reach[u['id']])
        if u.get('retreat_to') is not None:status+='｜待撤至'+self.pos(u['retreat_to'])+'｜撤退承伤比例'+num(u.get('retreat_cover'))+'%'
        return f'{self.name(u["owner"],self._names)} {self.label("content",u["kind"])}#{u["id"]} {self.pos([u["x"],u["y"]],actor=u["owner"])}｜HP{num(u["hp"])}'+status
