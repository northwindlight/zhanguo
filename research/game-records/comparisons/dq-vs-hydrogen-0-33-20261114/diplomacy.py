"""Count canonical player actions, then reconcile diplomacy with settled commands."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from engine.deltas import apply

NAMES=dict(zip(('qin','wei','han','zhao','yan','qi','chu','zhou'),'秦魏韩赵燕齐楚周'))
DIPLOMACY=('guarantee','cancel_guarantee','propose_defense','break_defense','accept_proposal',
 'reject_proposal','bloc_found','bloc_join','bloc_leave','bloc_dissolve','bloc_transfer','bloc_rename',
 'vote','declare_war','offer_peace','accept_peace','reject_peace','propose_vassalage',
 'release_vassal','independence_war','submit_vassalage')
COMMUNICATION=('send_letter','gift','share_map','spy','buy_map','buy_report')
QUERIES=('foreign','war','intel','news','report','countries','map_offers','map_knowledge')
LABELS={'send_letter':'发信','gift':'赠礼','share_map':'共享地图','spy':'派遣间谍','buy_map':'购买地图',
 'buy_report':'购买经济报表','guarantee':'新增保障','cancel_guarantee':'取消保障',
 'propose_defense':'提出共同防御','break_defense':'解除共同防御','accept_proposal':'接受提议',
 'reject_proposal':'拒绝提议','bloc_found':'发起创盟','bloc_join':'申请入盟','bloc_leave':'退盟',
 'bloc_dissolve':'解散联盟','bloc_transfer':'移交盟主','bloc_rename':'联盟改名','vote':'联盟投票',
 'declare_war':'确认宣战','offer_peace':'提出和平','accept_peace':'接受和平','reject_peace':'拒绝和平',
 'propose_vassalage':'邀请附庸','release_vassal':'释放附庸','independence_war':'独立战争',
 'submit_vassalage':'主动称臣','declare_war_preview':'两步宣战自检'}


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def period(row):return (row.get('receipt') or {}).get('period',-1)


def canonical(data,path,label):
    if label=='dq':
        rows=[row for services in data['host']['services'].values()
              for row in services['council']['state']['archive'] if row['kind']=='action']
        method='council canonical archive; rolled-back suffix already removed'
    else:
        unique={}
        root=path.with_suffix(path.suffix+'.audit')/data['host']['audit_stream']
        for file in sorted(root.glob('*.json')):
            for row in json.loads(file.read_text())['records']:
                if row['id'] in unique:assert row==unique[row['id']],('Conflicting audit id',row['id'])
                unique[row['id']]=row
        for row in data['host']['audit']:
            if row['id'] in unique:assert row==unique[row['id']]
            unique[row['id']]=row
        rows=list(unique.values());method='deduplicated committed audit stream plus saved tail'
    rows=sorted((row for row in rows if 0<=period(row)<=33),key=lambda row:row['id'])
    assert len({row['id'] for row in rows})==len(rows)
    ends=[(period(row),row['actor']) for row in rows if row['action']=='end_turn' and row['receipt']['ok']]
    assert len(ends)==len(set(ends))
    return rows,{'method':method,'canonical_rows':len(rows),'completed_country_turns':len(ends)}


def commands(data,path):
    rows=list(data['modules']['native:diplomacy']['results'])
    root=path.with_suffix(path.suffix+'.audit')/data['host']['audit_stream']/'native_diplomacy'
    for file in sorted(root.glob('*.json')):
        rows.extend(row['value'] for row in json.loads(file.read_text())['records'] if row['kind']=='command')
    unique={}
    for row in rows:
        if row['id'] in unique:assert row==unique[row['id']]
        unique[row['id']]=row
    result=[]
    for row in unique.values():
        if 0<=row['period']<=33:
            row=dict(row);row['arguments']=dict(row['arguments']);result.append(row)
    return sorted(result,key=lambda row:(row['period'],row['id']))


def dispatch_results(data,path):
    rows=list(data['modules']['native:intelligence']['results'])
    root=path.with_suffix(path.suffix+'.audit')/data['host']['audit_stream']/'native_intelligence'
    for file in sorted(root.glob('*.json')):
        rows.extend(row['value'] for row in json.loads(file.read_text())['records'] if row['kind']=='dispatch')
    results=defaultdict(dict)
    for row in rows:
        key=(row['id'],row['status'])
        if row['status'] in results[row['id']]:assert results[row['id']][row['status']]==row,key
        results[row['id']][row['status']]=row
    return results


def boundary_diplomacy(data,label):
    if label=='hydrogen':
        assert data['modules']['native:economy']['next_period']==34
        return data['modules']['native:diplomacy']
    state=None
    for row in data['host']['services']['scenario:qin']['council']['state']['archive']:
        full=row.get('state')
        if isinstance(full,dict) and 'facts' in full:
            state={'facts':{'foreign':{'diplomacy':full['facts']['foreign']['diplomacy']}}}
        elif state is not None and row.get('changes'):
            patches=[p for p in row['changes'] if p['path'][:3]==['facts','foreign','diplomacy']]
            if patches:state=apply(state,patches)
        if row['kind']=='settlement' and row.get('period')==34:
            return state['facts']['foreign']['diplomacy']
    raise AssertionError('Missing period-34 diplomatic snapshot')


def analyze(path,label):
    data=json.loads(path.read_text());rows,check=canonical(data,path,label)
    stats=defaultdict(Counter);actors=defaultdict(Counter);periods=defaultdict(Counter)
    ledger=[];letters=[];reports=[];previews=[];dispatches=[];automatic=[]
    for row in rows:
        receipt=row['receipt'];args=row.get('arguments') or {};action=row['action']
        if receipt.get('automatic'):
            automatic.append({'id':row['id'],'period':period(row),'actor':row['actor'],'source':receipt['automatic']})
            continue
        if action in ('relations','intelligence','economy','military'):action=args.get('action',action)
        if action=='declare_war' and receipt['status']=='confirmation_required':action='declare_war_preview'
        if row['action']=='query':action='query.'+str(args.get('panel'))
        if action not in (*DIPLOMACY,*COMMUNICATION,'declare_war_preview') and not action.startswith('query.'):
            continue
        st=stats[action];st['attempts']+=1;st['accepted' if receipt['ok'] else 'rejected']+=1
        if receipt['ok']:
            actors[row['actor']][action]+=1;periods[period(row)][action]+=1
        entry={'id':row['id'],'period':period(row),'actor':row['actor'],'action':action,
               'accepted':receipt['ok'],'status':receipt['status']}
        if action in (*DIPLOMACY,*COMMUNICATION,'declare_war_preview'):
            entry['arguments']=args
            if not receipt['ok']:entry['error']=receipt.get('message')
        else:
            entry['arguments']={k:v for k,v in args.items() if k in ('panel','target','turn','kind','x','y','place')}
        ledger.append(entry)
        if action=='declare_war_preview':previews.append(entry)
        payload=receipt.get('data') or {}
        if action in ('send_letter','spy','share_map','gift') and receipt['ok']:
            dispatches.append({'action':action,'id':payload['id'],'submitted':period(row),'arrive':payload['arrive']})
        if action=='send_letter' and receipt['ok']:
            letters.append({**entry,'target':args['target'],'text':args['text'],'fee':payload['fee']})
        if action=='buy_report' and receipt['ok']:
            report=payload['report']
            reports.append({**entry,'target':report['owner'],'report_end':report['end'],'fee':payload['fee']})

    settled=commands(data,path)
    outcomes=defaultdict(Counter)
    for row in settled:outcomes[row['action']][row['status']]+=1
    for action in DIPLOMACY:
        if action=='vote':continue
        submitted=[]
        for row in rows:
            args=row.get('arguments') or {};name=args.get('action',row['action']) if row['action']=='relations' else row['action']
            rec=row['receipt']
            if name==action and rec['ok'] and rec['status']!='confirmation_required':
                submitted.append(rec['data']['id'])
        actual=[row['id'] for row in settled if row['action']==action and row['id'] in submitted]
        assert len(set(actual))==len(set(submitted)),('Missing final diplomatic result',label,action)

    message_index={row['id']:row for row in data['modules']['native:intelligence']['messages']}
    final_dispatch=dispatch_results(data,path)
    delivered=Counter();pending=Counter();cancelled=Counter()
    for dispatch in dispatches:
        if dispatch['arrive']>34:
            pending[dispatch['action']]+=1;continue
        statuses=final_dispatch.get(dispatch['id'],{})
        msg=message_index.get(dispatch['id'])
        if 'delivered' in statuses:
            delivered[dispatch['action']]+=1
            if msg:assert msg['sampled']<=34
        elif 'cancelled' in statuses:cancelled[dispatch['action']]+=1
        else:raise AssertionError(('Missing dispatch result',label,dispatch))
    assert delivered['send_letter']==len(letters)
    keys=[(r['actor'],r['target'],r['report_end']) for r in reports]
    assert len(keys)==len(set(keys)),'Repeated report purchase should not count as new information'
    first_preview=min((r['id'] for r in previews),default=None)
    before=sum(r['id']<first_preview for r in reports) if first_preview is not None else len(reports)
    actor_first={actor:min(r['id'] for r in previews if r['actor']==actor) for actor in {r['actor'] for r in previews}}
    boundary=boundary_diplomacy(data,label)
    pact_counts=Counter(p['kind'] for p in boundary['pacts'])
    summary={'letter_attempts':stats['send_letter']['attempts'],'letters_sent':len(letters),
             'letters_delivered_by_boundary34':delivered['send_letter'],
             'letter_characters':sum(len(r['text']) for r in letters),'letter_fees':sum(r['fee'] for r in letters),
             'directed_letter_pairs':len({(r['actor'],r['target']) for r in letters}),
             'paid_report_purchases':len(reports),'unique_buyer_target_report_versions':len(set(keys)),
             'report_fees':sum(r['fee'] for r in reports),'reports_before_first_war_preview':before,
             'reports_before_own_war_preview':sum(r['actor'] in actor_first and r['id']<actor_first[r['actor']] for r in reports),
             'reports_from_actors_without_war_preview':sum(r['actor'] not in actor_first for r in reports),
             'dispatch_delivered_by_boundary34':dict(delivered),'dispatch_pending_after_boundary34':dict(pending),
             'dispatch_cancelled_by_boundary34':dict(cancelled),
             'effective_declarations':outcomes['declare_war']['effective'],
             'defense_invitations':outcomes['propose_defense']['effective'],
             'effective_accept_proposal':outcomes['accept_proposal']['effective'],
             'alliance_invitations':outcomes['bloc_found']['effective'],
             'effective_peace_acceptances':outcomes['accept_peace']['effective'],
             'war_previews':len(previews),'boundary34_pacts':dict(pact_counts),
             'boundary34_alliances':len(boundary['alliances']),
             'letters_per_100_completed_country_turns':100*len(letters)/check['completed_country_turns']}
    check.update({'save_sha256':sha(path),'commands_reconciled':True,
                  'letter_dispatches_match_delivered_messages':True,'report_versions_unique':True})
    return {'summary':summary,'actions':{k:dict(v) for k,v in stats.items()},
            'by_actor':{k:dict(v) for k,v in actors.items()},'by_period':{k:dict(v) for k,v in periods.items()},
            'settled_outcomes':{k:dict(v) for k,v in outcomes.items()},'settled_commands':settled,
            'boundary34_pacts':boundary['pacts'],
            'letters':sorted(letters,key=lambda r:r['id']),'reports':reports,'ledger':ledger,
            'automatic_excluded':automatic,'checks':check}


def export(root,result):
    root.mkdir(parents=True,exist_ok=True)
    (root/'diplomacy-metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    overview=[]
    for action in (*COMMUNICATION,*DIPLOMACY,'declare_war_preview'):
        values=[]
        for label in ('dq','hydrogen'):
            stats=result[label]['actions'].get(action,{})
            values.extend((stats.get('attempts',0),stats.get('accepted',0),
                           result[label]['settled_outcomes'].get(action,{}).get('effective',0) if action in DIPLOMACY else None))
        overview.append([LABELS[action],*values])
    header=['动作','旧局调用','旧局通过提交校验','旧局期末命令生效','H调用','H通过提交校验','H期末命令生效']
    with (root/'外交动作对比.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream,lineterminator='\n');writer.writerow(header);writer.writerows(overview)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment,Font,PatternFill
    from openpyxl.utils import get_column_letter
    wb=Workbook();ws=wb.active;ws.title='动作汇总';ws.append(header)
    for row in overview:ws.append(row)
    selected=('send_letter','propose_defense','accept_proposal','bloc_found','declare_war','cancel_guarantee','spy','buy_report','buy_map')
    for title,key,indices in (('逐国','by_actor',['scenario:'+n for n in NAMES]),('逐回合','by_period',list(range(34)))):
        sheet=wb.create_sheet(title);sheet.append(['国家' if key=='by_actor' else '回合',*[LABELS[a]+'（旧/H）' for a in selected]])
        for index in indices:
            values=[]
            for action in selected:
                a=result['dq'][key].get(index,{}).get(action,0);b=result['hydrogen'][key].get(index,{}).get(action,0)
                values.append(f'{a} / {b}')
            sheet.append([NAMES[index.split(':')[1]] if key=='by_actor' else index,*values])
    for label,title in (('dq','旧局'),('hydrogen','Hydrogen')):
        sheet=wb.create_sheet(title+'命令结果');sheet.append(['回合','国家','动作','最终状态','结果说明','参数'])
        for row in result[label]['settled_commands']:sheet.append([row['period'],NAMES[row['actor'].split(':')[1]],LABELS.get(row['action'],row['action']),row['status'],row['message'],json.dumps(row['arguments'],ensure_ascii=False)])
        sheet=wb.create_sheet(title+'信件');sheet.append(['回合','发信国','收信国','费用','正文'])
        for row in result[label]['letters']:sheet.append([row['period'],NAMES[row['actor'].split(':')[1]],NAMES[row['target'].split(':')[1]],row['fee'],row['text']])
    sheet=wb.create_sheet('付费报表');sheet.append(['对局','回合','购买国','目标国','报告截止期','费用'])
    for label in ('dq','hydrogen'):
        for row in result[label]['reports']:sheet.append([label,row['period'],NAMES[row['actor'].split(':')[1]],NAMES[row['target'].split(':')[1]],row['report_end'],row['fee']])
    for sheet in wb:
        sheet.freeze_panes='B2';sheet.auto_filter.ref=sheet.dimensions;sheet.row_dimensions[1].height=32
        for cell in sheet[1]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='24465C')
        for col in range(1,sheet.max_column+1):sheet.column_dimensions[get_column_letter(col)].width=23
        for row in sheet:
            for cell in row:cell.alignment=Alignment(vertical='top',wrap_text=True)
        if sheet.title.endswith('信件'):sheet.column_dimensions['E'].width=100
        if sheet.title.endswith('命令结果'):
            sheet.column_dimensions['E'].width=50;sheet.column_dimensions['F'].width=65
    wb.save(root/'外交动作对比.xlsx')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dq',required=True,type=Path);parser.add_argument('--hydrogen',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent)
    args=parser.parse_args();result={'periods':[0,33]}
    for label,path in (('dq',args.dq),('hydrogen',args.hydrogen)):
        result[label]=analyze(path,label);print(label,json.dumps(result[label]['summary'],ensure_ascii=False),flush=True)
    export(args.output,result)


if __name__=='__main__':main()
