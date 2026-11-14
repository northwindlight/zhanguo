"""Read recorded valuations and boundary overviews; never replay game actions."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from engine.deltas import apply

NAMES = dict(zip(('qin','wei','han','zhao','yan','qi','chu','zhou'), '秦魏韩赵燕齐楚周'))
ACTORS = tuple('scenario:'+key for key in NAMES)
EXPENSES = ('recruit','military','administration')
FIELDS = ('gdp','land','consumption','cumulative_consumption','units')
LABELS = ('GDP（金/期）','领土（格）','当期消费（金）','累计消费（金）','军队（支）')


def read_bytes(path):
    return gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def money(row):
    assert not row.get('unpriced'), 'Unpriced amount cannot be treated as zero'
    return row['known']


def close(a, b):
    assert math.isclose(a, b, abs_tol=1e-7, rel_tol=1e-10), (a, b)


def overviews(paths):
    """Accept only the automatic boundary header, excluding manual mid-turn boards."""
    result = {}
    for path, low, high in paths:
        text = read_bytes(path).decode('utf-8')
        pattern = r'^—— 第 (\d+) 回合 ——\n\s*## 世界全景 · 第 \1 回合\n'
        for match in re.finditer(pattern, text, re.M):
            period = int(match[1])
            if not low <= period <= high:
                continue
            lines = text[match.end():match.end()+4000].splitlines()
            rows = {}
            for line in lines:
                m = re.match(r'^(秦|魏|韩|赵|燕|齐|楚|周)\s+(\d+)\s+(\d+)\s+', line)
                if m:
                    rows[m[1]] = {'land':int(m[2]), 'units':int(m[3])}
            assert rows, ('Missing country table', path.name, period)
            result[period] = {actor:rows.get(NAMES[actor.split(':')[1]], {'land':0,'units':0})
                              for actor in ACTORS}
    return result


def council_boundaries(data, limit):
    result = {}
    for actor in ACTORS:
        council = data['host']['services'][actor]['council']['state']
        state = None
        points = {}
        for row in council['archive']:
            full = row.get('state')
            if isinstance(full, dict) and 'facts' in full:
                facts = full['facts']
                state = {'facts':{k:deepcopy(facts.get(k, [])) for k in ('view','army')}}
            elif state is not None and row.get('changes'):
                changes = [p for p in row['changes'] if p['path'][:2] in (['facts','view'],['facts','army'])]
                if changes:
                    state = apply(state, changes)
            period = row.get('period', (row.get('state') or {}).get('period'))
            if state is not None and row['kind'] == 'settlement' and period is not None:
                if period > limit:
                    break
                facts = state['facts']
                points[period] = {'land':sum(t.get('owner')==actor for t in facts['view']),
                                 'units':sum(u.get('owner')==actor and u.get('hp',0)>0 for u in facts['army'])}
        result[actor] = points
    return result


def extract(save, audit, boards, label, end):
    data = json.loads(read_bytes(save))
    econ = data['modules']['native:economy']
    assert econ['next_period'] > end
    turns, facts = {}, {}

    def insert(mapping, key, row):
        if key in mapping:
            assert mapping[key] == row, ('Conflicting history', label, key)
        mapping[key] = row

    for row in econ['turns']:
        insert(turns, (row['period'],row['owner']), row)
    for row in econ['events']:
        insert(facts, row['fact']['id'], row)
    histories = sorted((audit/data['host']['audit_stream']/'native_economy').glob('*.json'))
    for path in histories:
        history = json.loads(path.read_text())
        assert history['format']=='zhanguo.domain-history/1' and history['module']=='native:economy'
        for entry in history['records']:
            row = entry['value']
            if entry['kind']=='turn':
                insert(turns, (row['period'],row['owner']), row)
            elif entry['kind']=='fact':
                insert(facts, row['fact']['id'], row)

    metrics = defaultdict(lambda: {'gdp':0, 'consumption':0, 'investment':0,
                                 'recruit':0,'military':0,'administration':0})
    lifetime = defaultdict(lambda: defaultdict(float))
    for row in facts.values():
        fact = row['fact'];actor=fact['owner'];period=fact['period'];category=fact['category']
        if actor not in ACTORS:
            continue
        if category in (*EXPENSES, 'construction'):
            lifetime[actor][category] += money(row['inputs'])
        if period > end:
            continue
        m = metrics[(period,actor)]
        if category=='production':
            m['gdp'] += money(row['outputs'])-money(row['inputs'])
        elif category in EXPENSES:
            value=money(row['inputs']);m['consumption']+=value;m[category]+=value
        elif category=='construction':
            m['investment'] += money(row['inputs'])

    # Verify that pruning/archiving has not dropped any historical expenses.
    for actor in ACTORS:
        expected = {key:money(econ['checkpoints'].get(actor,{}).get(key,{'known':0}))
                    for key in (*EXPENSES,'construction')}
        for row in econ['events']:
            fact=row['fact']
            if fact['owner']==actor and fact['category'] in expected:
                expected[fact['category']]+=money(row['inputs'])
        for category in expected:
            close(lifetime[actor][category],expected[category])

    unmatched_gdp = []
    for period in range(end+1):
        assert period+1 in boards, ('Missing boundary overview',label,period+1)
        for actor in ACTORS:
            m=metrics[(period,actor)]
            if (period,actor) in turns:
                turn=turns[(period,actor)]
                close(m['gdp'],money(turn['gdp']))
                close(m['consumption']+m['investment'],money(turn['spending']))
            elif m['gdp']:
                # A country can produce and then be eliminated before reports close.
                unmatched_gdp.append({'period':period,'actor':actor,'gdp':m['gdp']})

    if label=='dq':
        independent=council_boundaries(data,end+1)
        for period in range(1,end+2):
            for actor in ACTORS:
                assert independent[actor][period]==boards[period][actor], ('Council/overview mismatch',period,actor)
    else:
        assert econ['next_period']==end+1, 'Hydrogen source advanced: take a stable snapshot'
        sites=data['modules']['native:buildings']['sites']
        units=data['modules']['native:army']['units']['armies']
        for actor in ACTORS:
            expected={'land':sum(s['owner']==actor for s in sites),
                      'units':sum(u['owner']==actor and u['hp']>0 for u in units)}
            assert boards[end+1][actor]==expected, ('Final world/overview mismatch',actor,expected)

    aggregate=[];countries=[];cumulative=defaultdict(float)
    for period in range(end+1):
        total={key:0 for key in (*FIELDS,'investment','recruit','military','administration')}
        for actor in ACTORS:
            m=dict(metrics[(period,actor)]);cumulative[actor]+=m['consumption']
            m.update(boards[period+1][actor]);m['cumulative_consumption']=cumulative[actor]
            countries.append({'period':period,'actor':actor,**m})
            for key in total:total[key]+=m[key]
        aggregate.append({'period':period,**total})
    check={'save_sha256':digest(save),'save_boundary':econ['next_period'],
           'archived_economy_files':len(histories),'unique_facts':len(facts),
           'valuations_complete':True,'expense_checkpoints_match':True,'turn_reports_match_facts':True,
           'boundary_counts_independently_checked':True,'gdp_of_eliminated_before_report':unmatched_gdp}
    return aggregate,countries,check


def export(output, dq, hydrogen, countries, provenance):
    output.mkdir(parents=True,exist_ok=True)
    records=[]
    for a,b in zip(dq,hydrogen):
        row={'period':a['period'],'boundary':a['period']+1}
        for key in FIELDS:
            row['dq_'+key]=a[key];row['hydrogen_'+key]=b[key]
            row['difference_'+key]=b[key]-a[key]
        records.append(row)
    (output/'metrics.json').write_text(json.dumps({'provenance':provenance,'dq':dq,'hydrogen':hydrogen,
        'comparison':records,'countries':countries},ensure_ascii=False,indent=2)+'\n')
    headers=['回合','结算后边界']
    for label in LABELS:headers.extend(('旧局 '+label,'Hydrogen '+label,'差值（H−旧） '+label))
    with (output/'逐回合对比.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream,lineterminator='\n');writer.writerow(headers)
        for row in records:writer.writerow([row['period'],row['boundary'],*[row[prefix+key] for key in FIELDS for prefix in ('dq_','hydrogen_','difference_')]])
    lines=['| 回合 | GDP 旧局 / H | 领土 旧局 / H | 当期消费 旧局 / H | 累计消费 旧局 / H | 军队 旧局 / H |',
           '| ---: | ---: | ---: | ---: | ---: | ---: |']
    for a,b in zip(dq,hydrogen):
        pairs=[f'{a[k]:.2f} / {b[k]:.2f}' if k not in ('land','units') else f'{a[k]} / {b[k]}' for k in FIELDS]
        lines.append('| '+str(a['period'])+' | '+' | '.join(pairs)+' |')
    (output/'table.md').write_text('\n'.join(lines)+'\n')
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        wb=Workbook();ws=wb.active;ws.title='八国合计对比';ws.append(headers)
        for row in records:ws.append([row['period'],row['boundary'],*[row[prefix+key] for key in FIELDS for prefix in ('dq_','hydrogen_','difference_')]])
        for title,label in (('旧局逐国','dq'),('Hydrogen逐国','hydrogen')):
            sheet=wb.create_sheet(title);sheet.append(['回合','国家',*LABELS,'建造投资（金）','征兵消费（金）','军需消费（金）','行政消费（金）'])
            for row in countries[label]:sheet.append([row['period'],NAMES[row['actor'].split(':')[1]],*[row[k] for k in FIELDS],row['investment'],row['recruit'],row['military'],row['administration']])
        notes=wb.create_sheet('口径');notes.append(['项目','说明'])
        for key,text in provenance['definitions'].items():notes.append([key,text])
        notes.append(['版本','旧局experimental0.15.0；本局native4.22.0，种子不同；数值为两场实际对局状态。'])
        for sheet in wb:
            sheet.freeze_panes='C2' if sheet!=notes else 'A2';sheet.auto_filter.ref=sheet.dimensions
            sheet.row_dimensions[1].height=44
            for cell in sheet[1]:
                cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='24465C')
            for col in sheet.columns:
                letter=col[0].column_letter;sheet.column_dimensions[letter].width=24 if sheet!=notes else (18 if letter=='A' else 110)
                for cell in col[1:]:
                    if isinstance(cell.value,float):cell.number_format='0.00'
            for row in sheet.iter_rows():
                for cell in row:cell.alignment=Alignment(vertical='top',wrap_text=sheet==notes or cell.row==1)
            if sheet!=notes:
                sheet.column_dimensions['A'].width=10;sheet.column_dimensions['B'].width=14
        wb.save(output/'逐回合对比.xlsx')
    except ImportError:
        print('openpyxl unavailable; CSV/JSON/Markdown are complete')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('dq','hydrogen','dq_journal','dq_early_journal','hydrogen_journal','output'):
        parser.add_argument('--'+name.replace('_','-'),required=True,type=Path)
    args=parser.parse_args();end=33
    dq_boards=overviews([(args.dq_early_journal,0,19),(args.dq_journal,20,34)])
    h_boards=overviews([(args.hydrogen_journal,0,34)])
    dq,dc,dcheck=extract(args.dq,args.dq.with_suffix(args.dq.suffix+'.audit'),dq_boards,'dq',end)
    h,hc,hcheck=extract(args.hydrogen,args.hydrogen.with_suffix(args.hydrogen.suffix+'.audit'),h_boards,'hydrogen',end)
    provenance={'periods':[0,end],'definitions':{
        '时点':'回合t的生产及消费，配结算后边界t+1的领土和军队；仅使用完整结算。',
        '八国合计':'固定开局秦魏韩赵燕齐楚周；亡国之后领土军队为0，历史消费保留。',
        'GDP':'八国生产产值减中间投入与能源燃料；沿用事实发生时的已存估值，包含亡国当期已完成生产。',
        '当期消费':'recruit + military + administration；排除建造投资、交易购买及生产中间投入。',
        '累计消费':'开局第0回合起至本行回合的当期消费之和。',
        '军队':'正规军和民兵的存活支数，包含伤兵，不含野人；不是按HP加权军力。',
        '差值':'Hydrogen减旧局。'},
        'sources':{'dq':dcheck,'hydrogen':hcheck},
        'journal_sha256':{key:digest(getattr(args,key)) for key in ('dq_journal','dq_early_journal','hydrogen_journal')}}
    export(args.output,dq,h,{'dq':dc,'hydrogen':hc},provenance)
    print(json.dumps({'rows':len(dq),'last_dq':dq[-1],'last_hydrogen':h[-1],'checks':provenance['sources']},ensure_ascii=False))


if __name__=='__main__':main()
