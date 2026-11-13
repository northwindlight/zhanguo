"""Read-only finale for current session saves; ranking uses recorded consumption.

Consumption is final use: recruit, military and administration. Construction is
capital formation, not consumption; it is reported separately and never ranked.
"""
import argparse
import json
from pathlib import Path

from engine.runtime import Runtime
from engine.session import SAVE_FORMAT, SaveFormatError
from engine.player import wire

ROOT=Path(__file__).resolve().parent
CONSUMPTION_CATEGORIES=('recruit','military','administration')
INVESTMENT_CATEGORIES=('construction',)


def load_save(path, mods=None):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('format')!=SAVE_FORMAT:
        raise SaveFormatError('存档格式不符：结算只接受当前 Session 存档，旧档请使用对应版本')
    try:
        distribution=data['dependencies']['distribution']['Id']
        source=mods or {'native':'mods.toml','experimental':'mods.experimental.toml','stock:exchange':'mods.stock.toml'}.get(distribution)
        if source is None:raise SaveFormatError('自定义发行版请用 --mods 指定声明')
        runtime=Runtime.load(ROOT/source)
        runtime.check_save_dependencies(data['dependencies'])
        world=runtime.world_class()();runtime.load_mod_state(world,data['modules'])
        roster=data['host']['roster']
        if not isinstance(roster,dict):raise SaveFormatError('非法结算名册')
        if distribution == 'stock:exchange':
            if not set(roster).issubset(world.portfolio.accounts):raise SaveFormatError('股市名册与领域存档不一致')
        elif set(roster)!=set(n.id for n in world.nations.snapshot()):
            raise SaveFormatError('国家名册与领域存档不一致')
        if any(not isinstance(r,dict) or not isinstance(r.get('name'),str) for r in roster.values()):
            raise SaveFormatError('非法结算名册')
    except (KeyError,TypeError,ValueError) as exc:
        raise SaveFormatError(str(exc)) from exc
    return data,world


def settle(data,world):
    """All participants rank by consumption; construction is reported apart. No price recomputation."""
    if data['dependencies']['distribution']['Id']=='stock:exchange':
        from mods.stock.settlement import settle as stock_settle
        return stock_settle(data, world)
    alive=set(world.nation_actors);rows=[]
    event=data['host'].get('victory_event') or {}
    weights={'native:infantry':1.0,'native:cavalry':1.5,'native:militia':.4}
    for actor,entry in data['host']['roster'].items():
        spending=world.economy.spending(actor)
        expenses={category:value.known for category,value in spending.items()}
        consumption=sum(expenses.get(category,0.0) for category in CONSUMPTION_CATEGORIES)
        investment=sum(expenses.get(category,0.0) for category in INVESTMENT_CATEGORIES)
        unpriced=sorted({key for value in spending.values() for key in value.unpriced})
        turns=world.economy.turns(actor);latest=turns[-1] if turns else None
        units=world.military.snapshot(actor) if actor in alive else ()
        outcome=('winner' if actor in event.get('winners',()) else
                 'secondary_winner' if actor in event.get('secondary_winners',()) else
                 'preserved' if actor in event.get('preserved',()) else None)
        rows.append({'actor':actor,'name':entry['name'],'alive':actor in alive,'outcome':outcome,
                     'spending':expenses,'consumption':consumption,'investment':investment,'unpriced':unpriced,
                     'gdp':wire(latest.gdp) if latest and actor in alive else {'known':0,'total':0,'unpriced':[]},
                     'land':len(world.territory.owned(actor)) if actor in alive else 0,
                     'units':len(units),'army':sum(u.hp/u.definition['hp']*weights.get(u.kind,u.definition['atk']/50) for u in units),
                     'building_assets':wire(latest.building_assets) if latest and actor in alive else {'known':0,'total':0,'unpriced':[]}})
    return sorted(rows,key=lambda r:(-r['consumption'],r['actor']))


def outcome_text(row):
    return {'winner':'胜利','secondary_winner':'次要胜利','preserved':'臣属保全（未胜未败）'}.get(
        row.get('outcome'),'在世' if row['alive'] else '已亡国')


def scoreboard_text(rows):
    if rows and rows[0].get('kind')=='stock':
        from mods.stock.settlement import scoreboard_text as stock_scoreboard
        return stock_scoreboard(rows)
    lines=['|名次|国家|消费（金）|投资（金）|GDP（金/期）|军力|领土|建筑资产（金）|状态|',
           '|---:|---|---:|---:|---:|---:|---:|---:|---|']
    for rank,row in enumerate(rows,1):
        value=lambda key: f'{row[key]["known"]:g}'+('（部分未估值）' if row[key].get('unpriced') else '')
        consumption=f'{row["consumption"]:g}'+('（部分未估值）' if row['unpriced'] else '')
        investment=f'{row["investment"]:g}'
        lines.append(f'|{rank}|{row["name"]}|{consumption}|{investment}|{value("gdp")}|{row["army"]:g}|{row["land"]}|{value("building_assets")}|'+outcome_text(row)+'|')
    return '\n'.join(lines)


def victory_text(data):
    event=data['host'].get('victory_event')
    if event is None:return ''
    message=event.get('message')
    return '剧本胜利：'+message if isinstance(message,str) and message else '胜利事件：'+json.dumps(event,ensure_ascii=False)


def run_chat(data,rows,cfg,rounds,log=print,remarks=None,backend_factory=None,
             prior_transcript=None,start_round=1,prior_rounds=1):
    """Discussion is opt-in at invocation; no tools or game mutations are granted.

    Provider owns retry policy. A missing/failed speaker leaves a transcript
    entry and never prevents the offline scoreboard from being delivered.
    """
    from clients.provider import make_backend
    from clients.config import normalize
    from clients.context import build
    from clients.model_view import ModelView
    from ui import load_ui
    backend_factory=backend_factory or make_backend
    config={n['id']:normalize(n,defaults=cfg) for n in cfg.get('nations',[])}
    board=scoreboard_text(rows);transcript=list(prior_transcript or []);histories={};seen={}
    is_stock=bool(rows and rows[0].get('kind')=='stock')
    victory=victory_text(data)
    if victory:board=victory+'\n\n'+board
    for round_number in range(start_round,rounds+1):
        for row in rows:
            actor=row['actor'];item=config.get(actor)
            if item is None or item.get('client')!='llm':continue
            try:
                if actor not in histories:
                    state=data['clients'][actor].get('state',{})
                    presentation=load_ui(item)
                    ui=ModelView(item,presentation=presentation)
                    if is_stock:
                        from mods.stock.settlement import private_brief
                        system=f'你是模拟股市比赛玩家{row["name"]}。比赛已结束，在结算厅复盘，不能行动。结合自己的记录与策略，诚实分享经过、经验和教训；记不清就说不清，不编造事实。'
                        tail='最终成绩按结算现金排名：\n'+board+'\n'+private_brief(data,row)
                    else:
                        system=f'你是{row["name"]}的君主，游戏已结束。你在结算厅复盘，不能行动。'
                        tail='本国结局：'+outcome_text(row)+'。最终成绩单按累计消费（征召、军需、行政，不含建造）排名，消费名次不等同剧本胜负；GDP/军力/领土/建筑只供参考：\n'+board
                    history,_=build(cfg=item,mem=state.get('replay',[]),sums=state.get('summaries',[]),blocks=state.get('blocks',[]),
                        long_memory=state.get('long_memory',''),system_text=system,
                        tail_text=tail,
                        archive_messages=ui.text)
                    histories[actor]=history;seen[actor]=0
                    if prior_transcript and is_stock:
                        # Reconstruct what this speaker heard and said during
                        # previous rounds, preserving the conversation order.
                        import re
                        mark=re.compile(r'^【第(\d+)轮·'+re.escape(row['name'])+r'】')
                        for index,previous in enumerate(prior_transcript):
                            match=mark.match(previous)
                            if not match:continue
                            earlier='\n'.join(prior_transcript[seen[actor]:index])
                            previous_round=int(match.group(1))
                            histories[actor].append({'role':'user','content':
                                f'第{previous_round}/{prior_rounds if previous_round == 1 else rounds}轮。看海人寄语：{(remarks or {}).get(actor,(remarks or {}).get(row["name"],""))}\n离席后的发言：\n{earlier}\n轮到你分享本局的策略、关键转折、风险和经验教训。'})
                            speech=previous[match.end():]
                            if not speech.startswith('发言失败（'):
                                histories[actor].append({'role':'assistant','content':speech})
                            seen[actor]=index+1
                news='\n'.join(transcript[seen[actor]:])
                histories[actor].append({'role':'user','content':f'第{round_number}/{rounds}轮。看海人寄语：{(remarks or {}).get(actor,(remarks or {}).get(row["name"],""))}\n离席后的发言：\n{news}\n轮到你分享本局的策略、关键转折、风险和经验教训。' if is_stock else f'第{round_number}/{rounds}轮。看海人寄语：{(remarks or {}).get(actor,(remarks or {}).get(row["name"],""))}\n离席后的发言：\n{news}\n轮到你复盘。'})
                text,_=backend_factory(item).complete_text(histories[actor],item,tools=[])
                if not isinstance(text,str) or not text.strip():raise ValueError('空发言')
                histories[actor].append({'role':'assistant','content':text})
                line=f'【第{round_number}轮·{row["name"]}】{text}'
            except Exception as exc:line=f'【第{round_number}轮·{row["name"]}】发言失败（{type(exc).__name__}），本轮缺席。'
            transcript.append(line);seen[actor]=len(transcript);log(line)
    return transcript


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--save',default='mp_save.json');parser.add_argument('--config',default='mp_config.json')
    parser.add_argument('--mods');parser.add_argument('--table',action='store_true');parser.add_argument('--no-chat',action='store_true')
    parser.add_argument('--rounds',type=int,default=5);parser.add_argument('--out',default='结算报告.md');parser.add_argument('--remarks')
    parser.add_argument('--resume',action='store_true',help='继续已完成整轮的股市结算厅报告')
    args=parser.parse_args(argv)
    try:
        data,world=load_save(args.save,args.mods);rows=settle(data,world)
    except (OSError,ValueError) as exc:parser.error(str(exc))
    board=scoreboard_text(rows);victory=victory_text(data)
    if victory:print(victory)
    print(board)
    if args.table:return 0
    transcript=[]
    is_stock=bool(rows and rows[0].get('kind')=='stock')
    start_round=1
    if args.resume:
        if not is_stock:parser.error('续聊目前只支持股市结算厅')
        import re
        report=Path(args.out).read_text(encoding='utf-8')
        matches=list(re.finditer(r'^【第(\d+)轮·([^】]+)】',report,re.MULTILINE))
        if not matches:parser.error('报告没有可继续的结算厅发言')
        transcript=[report[m.start():matches[i+1].start() if i+1<len(matches) else len(report)].strip()
                    for i,m in enumerate(matches)]
        cfg_for_resume=json.loads(Path(args.config).read_text(encoding='utf-8'))
        speaker_ids={n['id'] for n in cfg_for_resume.get('nations',[]) if n.get('client')=='llm'}
        expected=[row['name'] for row in rows if row['actor'] in speaker_ids]
        groups={}
        for match in matches:groups.setdefault(int(match.group(1)),[]).append(match.group(2))
        if (not expected or sorted(groups)!=list(range(1,max(groups)+1)) or
                any(names!=expected for names in groups.values())):
            parser.error('结算厅报告存在不完整轮次，不能自动续聊')
        start_round=max(groups)+1
        if start_round>args.rounds:parser.error('报告已达到指定轮数')
    elif is_stock:
        # Save each contribution immediately, so a later timeout cannot erase
        # earlier speakers' completed remarks.
        Path(args.out).write_text('# 模拟股市终局结算报告\n\n'+board+
            '\n\n排名采用最终清算后已记录的结算现金；未参与最终清算的玩家不补造排名。\n\n## 结算厅对话录\n', encoding='utf-8')
    def log_remark(line):
        print(line, flush=True)
        if is_stock:
            with Path(args.out).open('a', encoding='utf-8') as stream:
                stream.write('\n'+line+'\n')
    if not args.no_chat:
        config=Path(args.config)
        if config.exists():
            cfg=json.loads(config.read_text(encoding='utf-8'))
            remarks=json.loads(Path(args.remarks).read_text(encoding='utf-8')) if args.remarks else {}
            transcript=run_chat(data,rows,cfg,args.rounds,remarks=remarks,log=log_remark,
                                prior_transcript=transcript if args.resume else None,
                                start_round=start_round)
        else:print('未找到模型配置，跳过结算厅。')
    if rows and rows[0].get('kind')=='stock':
        lines=['# 模拟股市终局结算报告',board,'排名采用最终清算后已记录的结算现金；未参与最终清算的玩家不补造排名。']
    else:
        lines=['# 战国终局结算报告',*([victory] if victory else []),board,
               '排名按已记录的征召、军需（含回血装备）和行政消费；建造是投资，单列不计入排名。沿用报表阶段估值，不按现价重算。GDP与资产显示最后已结算事实。']
        for row in rows:lines.append(row['name']+'：'+json.dumps(row['spending'],ensure_ascii=False))
    if transcript:lines.extend(['## 结算厅对话录',*transcript])
    Path(args.out).write_text('\n\n'.join(lines)+'\n',encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
