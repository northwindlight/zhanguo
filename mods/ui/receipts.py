"""Spectator prose over public JSON, without engine or domain object access."""
from collections import Counter
from copy import deepcopy
from engine.coordinates import coordinates
import json
from .queries import QueryPresentation


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
        facts=observation.get('facts',observation)
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
        title=self.label('action',action)
        if action=='reachable':
            ids=args.get('ids',[args.get('aid','?')])
            return self.text('console.reachable_caption',title=title,unit='、'.join(self.unit_ref(aid,actor) for aid in ids),
                             mode=self.text('console.reachable_attack' if args.get('attack',False) else 'console.reachable_move'))
        if action in ('build','recruit'):
            parts=[title,self.label('content',args.get('kind',''))]
            if args.get('count',1)!=1:parts.append('×'+num(args['count']))
            if 'site' in args or 'x' in args or 'place' in args:
                location=self.pos(args=args,actor=actor)
                if location=='所选地块':
                    location=self.pos((receipt or {}).get('data'),actor=actor)
                    if location!='所选地块':location='所选地块 '+location
                parts.append(location)
            return ' '.join(p for p in parts if p)
        if action=='building_economics':return title+(' '+self.label('content',args['kind']) if args.get('kind') else ' 全部建筑')
        if action in ('buy','sell'):return title+' '+self.label('content',args.get('resource',''))+'×'+num(args.get('quantity',''))
        if action in ('plan','end_turn','script_write','script_patch','script_run','explain_plan'):return title
        if action in ('move','retreat','attack'):return title+' '+('、'.join(self.unit_ref(n,actor) for n in args['ids']) if 'ids' in args else self.unit_ref(args.get('aid','?'),actor))+' → '+self.pos(args=args,actor=actor)
        if action=='disband':return title+' '+('、'.join(self.unit_ref(n,actor) for n in args['ids']) if 'ids' in args else self.unit_ref(args.get('aid','?'),actor))
        # 外交正文已经把对象、金额和下一步讲全；抬头只留动作名，免得把
        # native:he / scenario:chu 这类引擎键和坐标数组漏到屏幕上。
        if action in DIPLOMACY_ACTIONS:return title
        parts=[]
        for key,value in args.items():
            if key in ('text','summary','source','old','new','content'):continue
            if key in ('site','tile'):value=self.pos(value)
            elif isinstance(value,str):value=self.name(self.label('content',value),self._names)
            else:value=num(value)
            parts.append(f'{self.label("field",key)}={value}')
        return title+(' '+' '.join(parts) if parts else '')

    def receipt_body(self,action,args,receipt,actor):
        data=receipt.get('data');message=self.label('receipt',receipt.get('message',''))
        if action=='script_run' and isinstance(data,dict) and 'steps' in data:
            result=f'脚本{self.label("status",data["status"])}；计算{data["steps"]}，API调用{data["calls"]}，已成功动作{data["actions"]}。'
            if data.get('error'):
                error=data['error'];result+='\n❌ '+(f'第{error["line"]}行：' if error.get('line') else '')+error['message']
            if data.get('output'):result+='\n'+data['output']
            return result
        if not receipt['ok']:
            if isinstance(data,dict) and data.get('reason')=='insufficient_resource':return f'❌ {self.label("content",data["resource"])}不足：需要{num(data["required"])}，现有{num(data["available"])}；请先补足资源或缩小数量。'
            if action=='plan' and isinstance(data,dict) and 'reason' in data:return '❌ 议会未批准国策：'+data['reason']+'\n'+args.get('text','')
            return '❌ '+(message or str(receipt.get('code') or '未完成'))
        query=self.query_body(action,args,data,actor)
        if query is not None:return query
        if action=='build' and isinstance(data,dict) and 'costs' in data:
            name=self.label('content',data['kind']);pos=self.pos(data,args,actor)
            return f'动工 {name}（{pos}，本回合在建、结算后落成），'+self.resources(data['costs'],sign='−')
        if action=='building_catalog' and isinstance(data,list):return self.catalog(data)
        if action=='building_economics' and (isinstance(data,list) or isinstance(data,dict) and 'net_per_period' in data):return self.economics(data)
        if action=='building_sites' and isinstance(data,dict):
            self.remember({'building_sites':data})
            return self.land([{'tile':s,'resources':s['resources']} for s in data['sites']],data['instances'],actor=actor)
        if action=='tile' and isinstance(data,dict):
            title=f'{self.pos(data,actor=actor)} {self.label("content",data["terrain"])} · '+self.name(data.get('owner') or '无主',self._names)
            details=[]
            if 'resources' in data:details.append('资源容量：'+self.resources(data['resources'])+f'；建筑位{data["capacity"]}')
            details.append('建筑：'+self.building_names([b for b in data['buildings'] if not b['remaining']]))
            pending=[b for b in data['buildings'] if b['remaining']]
            if pending:details.append('在建：'+self.building_names(pending))
            if data.get('army'):details.append('军队：'+'、'.join(self.name(u['owner'],self._names)+' #'+str(u['id'])+' '+self.label('content',u['kind'])+' HP'+num(u['hp']) for u in data['army']))
            return title+'\n'+'\n'.join(details)
        if action=='land' and isinstance(data,list):return self.land(data,actor=actor,args=args)
        if action in ('buy','sell') and isinstance(data,dict) and 'exact_total' in data:
            quantity=data['quantity'];label=self.label('content',data['resource'])
            verb=self.label('action',action);cash='实付' if data['side']=='buy' else '实收'
            # 沿用旧版口径：中间价走了多少、含价差均价多少、实付/实收多少，末尾报成交后的余量。
            line=(f'{verb} {label}×{quantity}（中间价 {num(data["price_before"])}→{num(data["price_after"])}，'
                  f'含价差均价 {num(data["exact_total"]/quantity)}，{cash} {num(data["gold"])}）')
            if 'remaining' in data:line+=f'，余{num(data["remaining"])}'
            return line+self.churn_warning(data)
        if action=='plan' and isinstance(data,dict):
            if 'approved' in data:
                body=data.get('text',args.get('text',''))
                if data['approved']:return '✅ 国策已通过。\n'+body
                return '❌ 议会未批准国策：'+data.get('reason',message)+'\n'+body
            return '✅ 国策已制定'+(f'（有效至第{data["valid_until"]}回合）' if 'valid_until' in data else '')+'\n'+data.get('text',args.get('text',''))
        if action=='end_turn' and isinstance(data,dict):
            text='回合小结：'+data.get('text',args.get('summary',''))
            rejected=data.get('auto_rejected',{})
            for kind in ('offers','votes'):
                if rejected.get(kind):text+='\n'+self.text('console.auto_rejected_'+kind,ids='、'.join('#'+str(i) for i in rejected[kind]))
            return text
        if action=='player_rules' and isinstance(data,list):
            if not data:return '未找到该主题；省略主题可查看全部规则。'
            return '\n\n'.join('【'+r['title']+'】\n'+r['body'] for r in data)
        if action=='unit_catalog' and isinstance(data,dict):
            return '【兵种一览】\n'+'\n'.join(f'{self.label("content",k)}：征召{self.resources(v["recruit"])}｜满编HP{v["hp"]}，攻击{v["atk"]}，移动{v["speed"]}，每回合补给{v["supply"]}'+
                ('｜'+self.text('query.move_costs',values='、'.join(self.label('content',terrain)+' '+num(cost) for terrain,cost in v['move_cost'].items())) if v.get('move_cost') else '') for k,v in data.items())
        if action in ('army','recruit') and isinstance(data,list):
            if not data:return '暂无可见军队。'
            if action=='army':return self.army_list(data,actor)
            return '征召完成；新兵下回合可以行动。\n'+'\n'.join(self.army_line(u) for u in data)
        if action=='disband' and isinstance(data,list):
            return '\n'.join(self.receipt_body(action,args,{**receipt,'data':row},actor) for row in data)
        if action=='threats' and isinstance(data,list):
            return '【视野内他国军队】\n'+('\n'.join(self.relation(row['relation'])+'｜'+self.army_line(row['unit']) for row in data) or '暂无。')
        if action=='countries' and isinstance(data,list):
            return self.country_list(data)
        if action=='battle' and isinstance(data,list):return self.battles(data)
        if action=='reachable' and isinstance(data,list):
            if not data:return self.text('console.reachable_empty')
            if isinstance(data[0],dict) and 'destinations' in data[0]:
                return '\n\n'.join(self.unit_ref(row['aid'],actor)+'\n'+self.receipt_body(action,args,
                    {**receipt,'data':row['destinations']},actor) for row in data)
            lines=[self.text('console.reachable_header',count=len(data))]
            visible={tuple(tile['position']):tile for tile in self._facts.get(actor,{}).get('view',[])}
            for position,cost in data:
                tile=visible.get(tuple(position),{})
                line=self.text('console.reachable_destination',place=self.pos(position,actor=actor),cost=num(cost),
                               terrain=self.label('content',tile.get('terrain','—')))
                buildings=dict(tile.get('buildings',[]))
                for kind in ('native:town-hall','native:castle'):
                    if buildings.get(kind):
                        line+='｜'+self.label('content',kind)+(' L' if kind=='native:castle' else '×')+str(buildings[kind])
                if cost==0:line+='｜'+self.text('console.reachable_current')
                lines.append(line)
            lines.append(self.text('console.reachable_assumptions'))
            return '\n'.join(lines)
        if action=='logistics' and isinstance(data,dict):
            lines=[self.text('console.logistics_supply',**{key:num(data[key]) for key in ('period','need','available','used','shortfall')}),
                   self.text('console.logistics_damage',damage=num(data['starvation_damage_per_unit']),exempt=num(data['exempt_count']))]
            if data['starving_units']:
                lines.append(self.text('console.logistics_units',units='、'.join(self.unit_ref(uid,actor) for uid in data['starving_units'])))
            lines.append(self.text('console.logistics_assumptions'))
            return '\n'.join(lines)
        if action=='diplomacy' and isinstance(data,dict):return self.diplomacy(data)
        if action=='intel' and isinstance(data,list):return self.intel_history(data)
        if action=='news' and isinstance(data,list):return '\n'.join(self.event(row,self._names) for row in reversed(data)) or self.text('console.no_news')
        if action=='report' and isinstance(data,list):return self.report_trend(data) if args.get('all') else self.reports(data)
        if action=='handbooks' and isinstance(data,list):return '【指南目录】\n'+'\n'.join(row['title']+'：player_rules(topic="'+row['title']+'")' for row in data)
        if action=='memory_search' and isinstance(data,list):return '【历史记忆】\n'+('\n\n'.join('第'+str(row['period'])+'期：\n'+row['context'] for row in data) or '没有匹配记录。')
        if action in ('move','retreat','attack','disband') and isinstance(data,dict):
            if data.get('status')=='failed':return '❌ '+self.label('receipt',data.get('message','行动失败'))
            units='、'.join(self.unit_ref(i,actor) for i in data.get('units',[]))
            if action=='disband':
                # 旧版逐条讲清代价：不退料、当回合起不再吃补给与军费、民兵编制当场释放。
                return (f'已遣散 {units}：不返还任何粮/装备/黄金；本回合起即不再吃补给、不计军费'
                        f'（民兵的编制名额当场释放）。遣散不亡国，但军队没了就是没了，番号不回收。')
            if action=='retreat':
                return f'{units} 已申请撤至{self.pos(args=args,actor=actor)}；本期仍参加战斗，结算后才脱离。'
            if action=='attack':
                where=self.pos(args=args,actor=actor)
                if data.get('occupied'):return f'{units} 已进攻{where}：无守军，已占领该地块。'
                if data.get('engaged'):return f'{units} 已进攻{where}，进入交战；之后每回合结算一轮，可用 retreat 撤出。'
                return f'{units} 已进攻{where}，未发生交战。'
            # 旧版明确提醒「走进去不等于占下来」——这是最容易踩的一条。
            return (f'{units} 已移动至{self.pos(args=args,actor=actor)}（未占地：移动只挪位置，'
                    f'无主地走完还是无主地；要占地必须用 attack）。')
        if action in ('send_letter','spy','gift','share_map') and isinstance(data,dict):
            target=self.name(data['target'],self._names);fee=data['fee']
            cost=f'−{fee}金' if fee else '免费'
            if action=='spy':
                # 旧版讲清回报内容（经济底细+外交+粗略军情+地图），否则花 100 金不知道买什么。
                return (f'已派间谍前往{target}（{cost}），预计第{data["arrive"]}期拿回其情报：'
                        f'国库与储备、上期收入、各格建筑、外交关系、粗略军情与对方已知地图（不含军队位置与编号）。')
            if action=='send_letter':
                chars=len(str(data.get('text') or args.get('text') or ''))
                return f'信已发出，{target}将于第{data["arrive"]}期收到（{chars} 字，{cost}）。'
            what={'gift':'赠送物资','share_map':'共享地图'}[action]
            result=f'已提交向{target}派送{what}（{cost}）；预计第{data["arrive"]}期送达。'
            if data.get('text'):result+='\n'+data['text']
            if data.get('resource'):result+=' '+self.resources({data['resource']:data['quantity']})
            return result
        if action=='borrow' and isinstance(data,dict):return f'借入{data["principal"]}金，期限{data["remaining"]}回合，当前应还{data["due"]}金；借款利率{data["initial_rate"]:.1%}。'
        if action=='bank' and isinstance(data,dict):
            loan=data.get('loan');result=f'现金{num(data["cash"])}金｜信用额度{num(data["credit_limit"])}金｜负债{num(data["liabilities"])}金'
            if loan:result+=f'｜贷款剩余{loan["remaining"]}回合，应还{loan["due"]}金'
            for purchase in reversed(data.get('purchases',[])):
                report=purchase['report']
                result+='\n'+self.text('query.purchased_report',name=self.name(report['owner'],self._names),period=purchase['period'],fee=purchase['fee'])+'\n'+self.reports([report])
            return result
        if action=='buy_report' and isinstance(data,dict) and 'report' in data:
            return self.text('query.purchased_report',name=self.name(data['report']['owner'],self._names),period=data['period'],fee=data['fee'])+'\n'+self.reports([data['report']])
        if action=='econ' and isinstance(data,dict):return '【经济概况】\n库存：'+self.resources({r['id']:r['amount'] for r in data['stocks']},gold_short=False)+'\n库存折'+self.money(data['inventory'])+'，建筑重置值'+self.money(data['buildings'])+'，总资产'+self.money(data['gross_assets'])
        if action=='market' and isinstance(data,dict):
            lines=['【世界市场】本期公开报价，同期内先后手同价；价格由库存与基准库存决定，大单均价随扫过的深度上升、大额抛压价。']
            inventory=dict(data.get('inventory',[]));reference=dict(data.get('reference',[]));buy=dict(data.get('buy_prices',[]));sell=dict(data.get('sell_prices',[]))
            for k,p in data['prices']:lines.append(f'{self.label("content",k)}｜本期价{num(p)}金｜买{num(buy.get(k))}金／卖{num(sell.get(k))}金'+(f'｜库存{num(inventory[k])}/基准{num(reference.get(k))}' if k in inventory else ''))
            return '\n'.join(lines)
        if action=='script_write' and isinstance(data,dict):return f'脚本已保存（第{data["revision"]}版）；尚未运行。\n'+args.get('source','')
        if action=='script_patch' and isinstance(data,dict):return f'脚本已修订（第{data["revision"]}版）；尚未运行。'
        if action in DIPLOMACY_ACTIONS and isinstance(data,dict):
            rendered=self.diplomacy_receipt(action,data,args)
            if rendered:return rendered
        if action=='player_events' and isinstance(data,dict) and 'items' in data:
            rows=data.get('items') or []
            return (f'【事件】共 {len(rows)} 条，游标 {num(data.get("cursor",0))}：\n'+
                    ('\n'.join(self.event(row,self._names) for row in rows) or '无'))
        if isinstance(data,dict) and receipt.get('status')=='submitted':
            return '已提交，等待外交结算；预留'+num(data.get('fee',0))+'金。'+('\n'+self.label('receipt',data['message']) if data.get('message') else '')
        # Unknown mods retain all their public data in readable compact form.
        return message+('\n' if message and data is not None else '')+(self.format(data,self._names) if data is not None else '') or '已完成。'

    @staticmethod
    def relation(value):return {'war':'交战','ally':'同盟','neutral':'中立'}.get(value,value)

    def alliance_name(self,key):
        facts=(self._facts.get(self._actor) or {})
        for row in (facts.get('diplomacy') or {}).get('alliances',[]):
            if key in ('bloc/'+row['id'],row['id']):return row['name']
        return str(key)

    def diplomacy_receipt(self,action,data,args=None):
        """外交提交回执：说清提交了什么、预留多少金、下一步等谁。

        外交是「提交 → 结算」两步，提交这一刻拿不到最终结果（是否转投票、守侧谁传导参战
        都由结算决定），所以这里不编造结果，只把提交内容讲明白；结果用 diplomacy 查。
        参数以回执里的规范化 arguments 为准，缺失时回落到调用参数。
        """
        args={**(args or {}),**dict(data.get('arguments') or ())}
        fee=num(data.get('fee',0))
        target=self.name(args.get('target',''),self._names)
        if action=='vote':
            choice=args.get('accept')
            word='弃权' if choice is None else '赞成' if choice else '反对'
            return f'已对投票 #{data.get("id",args.get("vote","?"))} 表态：{word}。'
        if action=='guarantee':return f'已提交：保障 {target} 独立，预留 {fee} 金。'
        if action=='cancel_guarantee':return f'已提交：撤销对 {target} 的保障，预留 {fee} 金。'
        if action=='propose_defense':return f'已提交：向 {target} 提议共同防御，预留 {fee} 金；等对方接受。'
        if action=='break_defense':return f'已提交：解除与 {target} 的共同防御，预留 {fee} 金。'
        if action=='declare_war':return f'已提交：对 {target} 宣战，预留 {fee} 金；结算后开战，别把提交当已开战。'
        if action=='bloc_found':
            tos=[self.name(t,self._names) for t in (args.get('tos') or [])]
            return (f'已发起结盟「{args.get("name","")}」：等 {"、".join(tos) or "受邀方"} 全部接受后成立，'
                    f'任一拒绝即流产；预留 {fee} 金。')
        if action=='bloc_join':return f'已向「{self.alliance_name(args.get("bloc",""))}」申请入盟，预留 {fee} 金；等成员表决。'
        if action=='bloc_leave':return f'已提交退出联盟，预留 {fee} 金。'
        if action=='bloc_dissolve':return f'已提交解散联盟，预留 {fee} 金。'
        if action=='bloc_rename':return f'已提交把联盟改名为「{args.get("name","")}」，预留 {fee} 金。'
        if action=='bloc_transfer':return f'已提交把盟主转给 {target}，预留 {fee} 金。'
        if action=='accept_proposal':return f'已回应提议 #{args.get("offer","?")}：接受，免费；约定内容按条款执行。'
        if action=='reject_proposal':return f'已回应提议 #{args.get("offer","?")}：拒绝，免费。'
        if action=='accept_peace':return f'已回应议和 #{args.get("offer","?")}：接受，免费；赔款按条款执行。'
        if action=='reject_peace':return f'已回应议和 #{args.get("offer","?")}：拒绝，免费。'
        if action=='offer_peace':
            kind=args.get('kind','white')
            gold=num(args.get('gold',0))
            terms={'white':'白和（不赔不索）','pay':f'我方赔款 {gold} 金','demand':f'索要赔款 {gold} 金'}.get(kind,str(kind))
            line=f'已向 {target} 提出：{terms}'
            if args.get('truce') is not None:line+=f'，约定休战 {num(args["truce"])} 回合'
            line+=f'，等对方下一回合回应；预留 {fee} 金。'
            if args.get('note'):line+='\n'+args['note']
            return line
        return None

    def churn_warning(self,data):
        """空转当场点名：同商品又买又卖＝白付两趟价差，还得算自己推价。

        只在真有重叠时出现；赚了也照报（照付的价差是真的，别把它当生意）。
        """
        churn=data.get('churn') or {}
        if not churn.get('quantity'):return ''
        return '\n'+self.text('query.market_churn_gain' if churn['loss']<0 else 'query.market_churn',
            resource=self.label('content',data['resource']),
            buy=num(churn['buy_quantity']),buy_price=num(churn['buy_average']),
            sell=num(churn['sell_quantity']),sell_price=num(churn['sell_average']),
            overlap=num(churn['quantity']),loss=num(abs(churn['loss'])))

    def army_line(self,u):
        state='newborn' if u.get('born_turn')==self._period else 'engaged' if u.get('engaged') else 'moved' if u.get('moved_turn')==self._period else 'ready'
        status='｜'+self.text('query.army_'+state)
        catalog=self._facts.get(self._actor,{}).get('unit_catalog',{})
        if u['owner']==self._actor and u['kind'] in catalog:status+='｜'+self.text('query.speed',speed=catalog[u['kind']]['speed'])
        if u.get('retreat_to') is not None:status+='｜待撤至'+self.pos(u['retreat_to'])+'｜撤退承伤比例'+num(u.get('retreat_cover'))+'%'
        return f'{self.name(u["owner"],self._names)} {self.label("content",u["kind"])}#{u["id"]} {self.pos([u["x"],u["y"]],actor=u["owner"])}｜HP{num(u["hp"])}'+status

    def report_trend(self,rows):
        lines=[self.text('query.trend_head',count=len(rows))]
        for row in sorted(rows,key=lambda r:r['end'],reverse=True):
            percent='—' if row['gdp_growth'] is None else num(row['gdp_growth']*100)+'%'
            lines.append(self.text('query.trend_row',start=row['start'],end=row['end'],gdp=self.money(row['gdp']),growth=percent,
                military=self.money(row['military']),assets=self.money(row['total_assets']),investment=self.money(row['investment'])))
        return '\n'.join(lines)

    def battles(self,rows):
        lines=['【当前交战】构成与基础输出，实际伤亡由结算决定。']
        for row in rows:
            lines.append(self.pos(row['position'])+('｜盲战：仅显示本国部队，敌方与防御未知' if row['blind'] else '｜'+self.label('content',row['terrain'])))
            for side in row['sides']:
                role={'defending':'防守','attacking':'进攻','bystander':'未参战','unknown':'位置关系未知'}[side['role']]
                lines.append(f'  {self.name(side["owner"],self._names)}｜{role}｜{side["count"]}支｜基础输出{num(side["output_base"])}｜地形{num(side["terrain_reduction"])}%／城堡{num(side["castle_reduction"])}%／合计减伤{num(side["combined_reduction"])}%')
                lines.extend('    '+self.army_line(u) for u in side['units'])
        if not rows:lines.append('暂无交战。')
        return '\n'.join(lines)

    def reports(self,rows):
        lines=['【经济报表】生产与投资按报表估值，GDP不等于现金收入。']
        for row in rows:
            lines.append(f'第{row["start"]}—{row["end"]}期（{row["span"]}期）｜GDP {self.money(row["gdp"])}｜建设{self.money(row["investment"])}｜征兵{self.money(row["recruitment"])}｜军事{self.money(row["military"])}')
            lines.append(f'  库存{self.money(row["inventory_assets"])}｜建筑{self.money(row["building_assets"])}｜总资产{self.money(row["total_assets"])}｜负债{num(row["liabilities"])}金｜净资产{self.money(row["net_assets"])}｜进口{num(row["imports"])}金／出口{num(row["exports"])}金')
            percent=lambda value:'—' if value is None else num(value*100)+'%'
            lines.append('  GDP增长'+percent(row['gdp_growth'])+'｜投资增长'+percent(row['investment_growth'])+'｜资产增长'+percent(row['assets_growth'])+'｜军费比'+percent(row['military_ratio'])+'｜贸易比'+percent(row['trade_ratio']))
        if not rows:lines.append('尚无已发布报表。')
        return '\n'.join(lines)

    def diplomacy(self,data):
        lines=['【外交】提议、投票和待办尚未生效；以当前条约与战争为准。']
        blocs={'bloc/'+b['id']:b['name'] for b in data.get('alliances',[])}
        name=lambda value:blocs.get(value,self.name(value,self._names))
        names=lambda values:'、'.join(name(v) for v in values)
        for key,title in (('alliances','联盟'),('pacts','条约'),('wars','战争'),('truces','休战'),('offers','待回应提议'),('votes','待投票'),('pending','待结算'),('results','处理结果')):
            rows=data.get(key,[])
            lines.append('【'+title+'】'+('无' if not rows else ''))
            for row in rows:
                if key=='alliances':lines.append(f'{row["name"]}（{row["id"]}）｜盟主{name(row["chief"])}｜成员{names(row["members"])}')
                elif key=='pacts':lines.append({'guarantee':'保障','defense':'共同防御'}.get(row['kind'],row['kind'])+f'｜{name(row["a"])} → {name(row["b"])}')
                elif key=='wars':lines.append(f'战争#{row["id"]}｜主导{name(row["a"])} 对 {name(row["b"])}｜攻方{names(row["attackers"])}｜守方{names(row["defenders"])}')
                elif key=='truces':lines.append(f'{name(row[0])} 与 {name(row[1])}｜休战至第{row[2]}期')
                else:
                    args=dict(row['arguments'])
                    action={'found':'联盟创始邀请','defense':'共同防御提议','peace':'和平提议'}.get(row['action'],self.label('action',row['action']))
                    head=f'#{row["id"]} {name(row["actor"])}｜{action}'
                    if key=='offers':head+=' → '+name(row['target'])+'｜已接受 '+(names(row['accepted']) or '无')
                    elif key=='votes':head+=f'｜截止第{row["deadline"]}期｜有票{names(row["voters"])}｜'+('、'.join(name(n)+('弃权' if choice is None else '赞成' if choice else '反对') for n,choice in row['ballots']) or '尚未投票')
                    else:head+='｜'+self.label('status',row['status'])+f'｜费用{row["fee"]}金'
                    lines.append(head)
                    if args:lines.append('  '+self.format(args,self._names).replace('\n','；'))
                    if row.get('message'):lines.append('  '+self.label('receipt',row['message']))
        return '\n'.join(lines)
