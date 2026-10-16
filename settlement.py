"""Read-only finale for current session saves; ranking uses recorded consumption."""
import argparse
import json
from pathlib import Path

from engine.runtime import Runtime
from engine.session import SAVE_FORMAT, SaveFormatError
from engine.player import wire

ROOT=Path(__file__).resolve().parent


def load_save(path, mods=None):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('format')!=SAVE_FORMAT:
        raise SaveFormatError('存档格式不符：结算只接受当前 Session 存档，旧档请使用对应版本')
    try:
        distribution=data['dependencies']['distribution']['Id']
        source=mods or {'native':'mods.toml','experimental':'mods.experimental.toml'}.get(distribution)
        if source is None:raise SaveFormatError('自定义发行版请用 --mods 指定声明')
        runtime=Runtime.load(ROOT/source)
        runtime.check_save_dependencies(data['dependencies'])
        world=runtime.world_class()();runtime.load_mod_state(world,data['modules'])
        roster=data['host']['roster']
        if not isinstance(roster,dict) or set(roster)!=set(n.id for n in world.nations.snapshot()):
            raise SaveFormatError('国家名册与领域存档不一致')
        if any(not isinstance(r,dict) or not isinstance(r.get('name'),str) for r in roster.values()):
            raise SaveFormatError('非法结算名册')
    except (KeyError,TypeError,ValueError) as exc:
        raise SaveFormatError(str(exc)) from exc
    return data,world


def settle(data,world):
    """All participants rank, including fallen nations. No price recomputation."""
    alive=set(world.nation_actors);rows=[]
    weights={'native:infantry':1.0,'native:cavalry':1.5,'native:militia':.4}
    for actor,entry in data['host']['roster'].items():
        spending=world.economy.spending(actor)
        expenses={category:value.known for category,value in spending.items()}
        unpriced=sorted({key for value in spending.values() for key in value.unpriced})
        turns=world.economy.turns(actor);latest=turns[-1] if turns else None
        units=world.military.snapshot(actor) if actor in alive else ()
        rows.append({'actor':actor,'name':entry['name'],'alive':actor in alive,
                     'spending':expenses,'total':sum(expenses.values()),'unpriced':unpriced,
                     'gdp':wire(latest.gdp) if latest and actor in alive else {'known':0,'total':0,'unpriced':[]},
                     'land':len(world.territory.owned(actor)) if actor in alive else 0,
                     'units':len(units),'army':sum(u.hp/u.definition['hp']*weights.get(u.kind,u.definition['atk']/50) for u in units),
                     'building_assets':wire(latest.building_assets) if latest and actor in alive else {'known':0,'total':0,'unpriced':[]}})
    return sorted(rows,key=lambda r:(-r['total'],r['actor']))


def scoreboard_text(rows):
    lines=['|名次|国家|总消费（金）|GDP（金/期）|军力|领土|建筑资产（金）|状态|',
           '|---:|---|---:|---:|---:|---:|---:|---|']
    for rank,row in enumerate(rows,1):
        value=lambda key: f'{row[key]["known"]:g}'+('（部分未估值）' if row[key].get('unpriced') else '')
        total=f'{row["total"]:g}'+('（部分未估值）' if row['unpriced'] else '')
        lines.append(f'|{rank}|{row["name"]}|{total}|{value("gdp")}|{row["army"]:g}|{row["land"]}|{value("building_assets")}|'+('在世' if row['alive'] else '已亡国')+'|')
    return '\n'.join(lines)


def victory_text(data):
    event=data['host'].get('victory_event')
    if event is None:return ''
    message=event.get('message')
    return '剧本胜利：'+message if isinstance(message,str) and message else '胜利事件：'+json.dumps(event,ensure_ascii=False)


def run_chat(data,rows,cfg,rounds,log=print,remarks=None,backend_factory=None):
    """Discussion is opt-in at invocation; no tools or game mutations are granted.

    Provider owns retry policy. A missing/failed speaker leaves a transcript
    entry and never prevents the offline scoreboard from being delivered.
    """
    from clients.provider import make_backend
    from clients.config import normalize
    from clients.context import build
    from ui import load_ui
    backend_factory=backend_factory or make_backend
    config={n['id']:normalize(n,defaults=cfg) for n in cfg.get('nations',[])}
    board=scoreboard_text(rows);transcript=[];histories={};seen={}
    victory=victory_text(data)
    if victory:board=victory+'\n\n'+board
    for round_number in range(1,rounds+1):
        for row in rows:
            actor=row['actor'];item=config.get(actor)
            if item is None or item.get('client')!='llm':continue
            try:
                if actor not in histories:
                    state=data['clients'][actor].get('state',{})
                    ui=load_ui(item).llm(item)
                    history,_=build(cfg=item,mem=state.get('replay',[]),sums=state.get('summaries',[]),blocks=state.get('blocks',[]),
                        long_memory=state.get('long_memory',''),system_text=f'你是{row["name"]}的君主，游戏已结束。你在结算厅复盘，不能行动。',
                        tail_text='最终成绩单（按累计四类消费排名，GDP/军力/领土/建筑只供参考）：\n'+board,
                        archive_messages=ui.text)
                    histories[actor]=history;seen[actor]=0
                news='\n'.join(transcript[seen[actor]:])
                histories[actor].append({'role':'user','content':f'第{round_number}/{rounds}轮。看海人寄语：{(remarks or {}).get(actor,(remarks or {}).get(row["name"],""))}\n离席后的发言：\n{news}\n轮到你复盘。'})
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
    args=parser.parse_args(argv)
    try:
        data,world=load_save(args.save,args.mods);rows=settle(data,world)
    except (OSError,ValueError) as exc:parser.error(str(exc))
    board=scoreboard_text(rows);victory=victory_text(data)
    if victory:print(victory)
    print(board)
    if args.table:return 0
    transcript=[]
    if not args.no_chat:
        config=Path(args.config)
        if config.exists():
            cfg=json.loads(config.read_text(encoding='utf-8'))
            remarks=json.loads(Path(args.remarks).read_text(encoding='utf-8')) if args.remarks else {}
            transcript=run_chat(data,rows,cfg,args.rounds,remarks=remarks)
        else:print('未找到模型配置，跳过结算厅。')
    lines=['# 战国终局结算报告',*([victory] if victory else []),board,
           '排名按已记录的建造、征召、军费（含回血装备）、行政消费；沿用报表阶段估值，不按现价重算。GDP与资产显示最后已结算事实。']
    for row in rows:lines.append(row['name']+'：'+json.dumps(row['spending'],ensure_ascii=False))
    if transcript:lines.extend(['## 结算厅对话录',*transcript])
    Path(args.out).write_text('\n\n'.join(lines)+'\n',encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
