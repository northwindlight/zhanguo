"""Spectator prose over public JSON, without engine or domain object access."""
from collections import Counter
from copy import deepcopy
import json


def num(value):
    if value is None:return '—'
    if isinstance(value,(int,float)):return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value,float) else str(value)
    return str(value)


class ReceiptPresentation:
    def remember(self,observation):
        facts=observation.get('facts',observation)
        actor=observation.get('actor','')
        tiles=self._tiles.setdefault(actor,{})
        for row in facts.get('view',[]):
            tile=row.get('tile',row)
            position=tile.get('position')
            if isinstance(position,(list,tuple)) and len(position)==2 and tile.get('name'):
                tiles[tuple(position)]=tile['name']
        for row in facts.get('army',[]):
            if 'owner' in row and 'id' in row and 'kind' in row:
                self._army_kinds[(row['owner'],row['id'])]=row['kind']
        sites=facts.get('building_sites',{})
        for row in sites.get('sites',[]):self._sites[row['id']]=deepcopy(row)
        for row in facts.get('land',[]):
            tile=row.get('tile',row)
            position=tile.get('position')
            if isinstance(position,(list,tuple)) and len(position)==2 and tile.get('name'):
                tiles[tuple(position)]=tile['name']
            if 'id' in tile:self._terrain[tile['id']]=tile.get('terrain')

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
            for position,name in self._tiles.get(actor,{}).items():
                if name==value:return f'{name}({position[0]}, {position[1]})'
        # Native site identity is a fallback only; observations provide coordinates.
        if isinstance(value,str):
            import re
            m=re.search(r'/land-(\d+)-(\d+)$',value)
            if m:return self.pos([int(m[1]),int(m[2])],actor=actor)
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
                if definition['effects'].get('build_discount'):lines.append('  工程院节省取决于后续建设计划，不预测固定现金收益。')
        return '\n'.join(lines)

    def land(self,rows,instances=None):
        lines=[f'国土 {len(rows)} 块：']
        for row in rows:
            tile=row.get('tile',row);site=self._sites.get(tile.get('id'),{})
            place=f'「{tile["name"]}」' if tile.get('name') else ''
            completed=Counter(b['kind'] for b in instances or [] if b['site']==tile.get('id') and not b['remaining'])
            pending=Counter(b['kind'] for b in instances or [] if b['site']==tile.get('id') and b['remaining'])
            buildings='、'.join(f'{self.label("content",k)}×{n}' for k,n in completed.items()) or '无'
            if pending:buildings+='；在建 '+ '、'.join(f'{self.label("content",k)}×{n}' for k,n in pending.items())
            resources=self.resources(row.get('resources',site.get('resources',[])),gold_short=False)
            cap=f'｜建筑位{sum(completed.values())+sum(pending.values())}/{site["capacity"]}' if 'capacity' in site and instances is not None else ''
            terrain=self.label('content',tile.get('terrain') or self._terrain.get(tile.get('id')) or '')
            lines.append(f'{self.pos(tile)}{place}{terrain}{cap}｜资源 {resources}'+('｜建筑 '+buildings if instances is not None else ''))
        return '\n'.join(lines)

    def call_caption(self,action,args,actor=None,receipt=None):
        title=self.label('action',action)
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
            if action=='plan' and isinstance(data,dict) and 'reason' in data:return '❌ 议会未批准国策：'+data['reason']
            return '❌ '+(message or str(receipt.get('code') or '未完成'))
        if action=='build' and isinstance(data,dict) and 'costs' in data:
            name=self.label('content',data['kind']);pos=self.pos(data,args,actor)
            return f'动工 {name}（{pos}，本回合在建、结算后落成），'+self.resources(data['costs'],sign='−')
        if action=='building_catalog' and isinstance(data,list):return self.catalog(data)
        if action=='building_economics' and (isinstance(data,list) or isinstance(data,dict) and 'net_per_period' in data):return self.economics(data)
        if action=='building_sites' and isinstance(data,dict):
            self.remember({'building_sites':data})
            return self.land([{'tile':s,'resources':s['resources']} for s in data['sites']],data['instances'])
        if action=='tile' and isinstance(data,dict):
            title=f'{self.pos(data)}'+(f'「{data["name"]}」' if data.get('name') else '')+f' {self.label("content",data["terrain"])} · '+self.name(data.get('owner') or '无主',self._names)
            details=[]
            if 'resources' in data:details.append('资源容量：'+self.resources(data['resources'])+f'；建筑位{data["capacity"]}')
            counts=Counter(b['kind'] for b in data['buildings'] if not b['remaining'])
            details.append('建筑：'+('、'.join(self.label('content',k)+'×'+str(n) for k,n in counts.items()) or '无'))
            if data.get('construction'):details.append('在建：'+'、'.join(self.label('content',b['kind']) for b in data['construction']))
            if data.get('army'):details.append('军队：'+'、'.join(self.name(u['owner'],self._names)+' #'+str(u['id'])+' '+self.label('content',u['kind'])+' 兵力'+num(u['hp']) for u in data['army']))
            return title+'\n'+'\n'.join(details)
        if action=='land' and isinstance(data,list):return self.land(data)
        if action in ('buy','sell') and isinstance(data,dict) and 'exact_total' in data:
            quantity=data['quantity'];label=self.label('content',data['resource'])
            verb=self.label('action',action);cash='实付' if data['side']=='buy' else '实收'
            return f'{verb} {label}×{quantity}，{cash}{data["gold"]}金（含价差均价{num(data["exact_total"]/quantity)}；本期市价{num(data["price_before"])}）'
        if action=='plan' and isinstance(data,dict):
            if 'approved' in data:
                return '✅ 国策已通过：'+data.get('reason',message)+'\n'+str(args.get('text',''))
            return '✅ 国策已制定'+(f'（有效至第{data["valid_until"]}回合）' if 'valid_until' in data else '')+'\n'+data.get('text',args.get('text',''))
        if action=='end_turn' and isinstance(data,dict):return '回合小结：'+data.get('text',args.get('summary',''))
        if action=='player_rules' and isinstance(data,list):
            if not data:return '未找到该主题；省略主题可查看全部规则。'
            return '\n\n'.join('【'+r['title']+'】\n'+r['body'] for r in data)
        if action=='unit_catalog' and isinstance(data,dict):
            return '【兵种一览】\n'+'\n'.join(f'{self.label("content",k)}：征召{self.resources(v["recruit"])}｜满编兵力{v["hp"]}，攻击{v["atk"]}，移动{v["speed"]}，每回合补给{v["supply"]}' for k,v in data.items())
        if action in ('army','recruit') and isinstance(data,list):
            if not data:return '暂无可见军队。'
            return ('征召完成；新兵下回合可以行动。\n' if action=='recruit' else f'可见军队 {len(data)} 支：\n')+'\n'.join(self.army_line(u) for u in data)
        if action=='disband' and isinstance(data,list):
            return '\n'.join(self.receipt_body(action,args,{**receipt,'data':row},actor) for row in data)
        if action=='threats' and isinstance(data,list):
            return '【视野内他国军队】\n'+('\n'.join(self.relation(row['relation'])+'｜'+self.army_line(row['unit']) for row in data) or '暂无。')
        if action=='countries' and isinstance(data,list):
            return '【外交对象】\n'+('\n'.join(f'{row["name"]}（{row["id"]}）｜{self.relation(row["relation"])}'+('｜接壤' if row['border'] else '')+('｜有来信' if row['has_letter'] else '')+('｜求和代表 '+self.name(row['war_representative'],self._names) if row['war_representative'] else '') for row in data) or '暂无别国。')
        if action=='battle' and isinstance(data,list):return self.battles(data)
        if action=='diplomacy' and isinstance(data,dict):return self.diplomacy(data)
        if action=='report' and isinstance(data,list):return self.reports(data)
        if action=='handbooks' and isinstance(data,list):return '【指南目录】\n'+'\n'.join(row['title']+'：player_rules(topic="'+row['title']+'")' for row in data)
        if action=='memory_search' and isinstance(data,list):return '【历史记忆】\n'+('\n\n'.join('第'+str(row['period'])+'期：\n'+row['context'] for row in data) or '没有匹配记录。')
        if action in ('move','retreat','attack','disband') and isinstance(data,dict):
            if data.get('status')=='failed':return '❌ '+self.label('receipt',data.get('message','行动失败'))
            units='、'.join(self.unit_ref(i,actor) for i in data.get('units',[]))
            if action=='disband':return f'已遣散 {units}，不退还资源。'
            if action=='retreat':return f'{units} 已申请撤至{self.pos(args=args,actor=actor)}；本期仍参加战斗。'
            if action=='attack':return f'{units} 已进攻{self.pos(args=args,actor=actor)}；'+('已占领该地块。' if data.get('occupied') else '进入交战，伤亡在结算时计算。' if data.get('engaged') else '未发生交战。')
            return f'{units} 已移动至{self.pos(args=args,actor=actor)}。'
        if action in ('send_letter','spy','gift','share_map') and isinstance(data,dict):
            target=self.name(data['target'],self._names);fee=data['fee']
            what={'send_letter':'信件','spy':'间谍','gift':'赠送物资','share_map':'共享地图'}[action]
            result=f'已提交向{target}派送{what}，−{fee}金；等待结算派发。'
            if data.get('text'):result+='\n'+data['text']
            if data.get('resource'):result+=' '+self.resources({data['resource']:data['quantity']})
            return result
        if action=='borrow' and isinstance(data,dict):return f'借入{data["principal"]}金，期限{data["remaining"]}回合，当前应还{data["due"]}金；借款利率{data["initial_rate"]:.1%}。'
        if action=='bank' and isinstance(data,dict):
            loan=data.get('loan');result=f'现金{num(data["cash"])}金｜信用额度{num(data["credit_limit"])}金｜负债{num(data["liabilities"])}金'
            if loan:result+=f'｜贷款剩余{loan["remaining"]}回合，应还{loan["due"]}金'
            return result
        if action=='econ' and isinstance(data,dict):return '【经济概况】\n库存：'+self.resources({r['id']:r['amount'] for r in data['stocks']},gold_short=False)+'\n库存折'+self.money(data['inventory'])+'，建筑重置值'+self.money(data['buildings'])+'，总资产'+self.money(data['gross_assets'])
        if action=='market' and isinstance(data,dict):
            lines=['【世界市场】本期公开报价，同期内先后手同价；价格由库存与基准库存决定，大单均价随扫过的深度上升、大额抛压价。']
            inventory=dict(data.get('inventory',[]));reference=dict(data.get('reference',[]));buy=dict(data.get('buy_prices',[]));sell=dict(data.get('sell_prices',[]))
            for k,p in data['prices']:lines.append(f'{self.label("content",k)}｜本期价{num(p)}金｜买{num(buy.get(k))}金／卖{num(sell.get(k))}金'+(f'｜库存{num(inventory[k])}/基准{num(reference.get(k))}' if k in inventory else ''))
            return '\n'.join(lines)
        if action=='script_write' and isinstance(data,dict):return f'脚本已保存（第{data["revision"]}版）；尚未运行。\n'+args.get('source','')
        if action=='script_patch' and isinstance(data,dict):return f'脚本已修订（第{data["revision"]}版）；尚未运行。'
        if isinstance(data,dict) and receipt.get('status')=='submitted':
            return '已提交，等待外交结算；预留'+num(data.get('fee',0))+'金。'+('\n'+self.label('receipt',data['message']) if data.get('message') else '')
        # Unknown mods retain all their public data in readable compact form.
        return message+('\n' if message and data is not None else '')+(self.format(data,self._names) if data is not None else '') or '已完成。'

    @staticmethod
    def relation(value):return {'war':'交战','ally':'同盟','neutral':'中立'}.get(value,value)

    def army_line(self,u):
        status='｜交战中' if u.get('engaged') else ''
        if u.get('retreat_to') is not None:status+='｜待撤至'+self.pos(u['retreat_to'])+'｜撤退承伤比例'+num(u.get('retreat_cover'))+'%'
        return f'{self.name(u["owner"],self._names)} {self.label("content",u["kind"])}#{u["id"]} {self.pos([u["x"],u["y"]],actor=u["owner"])}｜兵力{num(u["hp"])}'+status

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
                    elif key=='votes':head+=f'｜截止第{row["deadline"]}期｜有票{names(row["voters"])}｜'+('、'.join(name(n)+('赞成' if choice else '反对') for n,choice in row['ballots']) or '尚未投票')
                    else:head+='｜'+self.label('status',row['status'])+f'｜费用{row["fee"]}金'
                    lines.append(head)
                    if args:lines.append('  '+self.format(args,self._names).replace('\n','；'))
                    if row.get('message'):lines.append('  '+self.label('receipt',row['message']))
        return '\n'.join(lines)
