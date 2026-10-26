"""Exercise the real NDJSON human host in a fresh offline eight-nation game."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
from threading import Thread
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scenarios.eight_nations import build_config


def review(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    cfg=build_config({'rule_ai':'v13','nations':[{'client':'rule','rule_ai':'v13'}]},20261026)
    cfg['nations'][0].update(client='human',nickname='协议验收玩家')
    cfg['max_turns']=3
    config=output/'config.json';config.write_text(json.dumps(cfg,ensure_ascii=False),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if k not in ('ZHANGUO_RESUME','ZHANGUO_UI_MODS','ZHANGUO_DESKTOP_CONFIG')}
    env['ZHANGUO_DESKTOP_CONFIG']=str(config)
    queue=Queue();rows=[];turns=[];results=[];timelines=[];active=None
    with (output/'stderr.log').open('w',encoding='utf-8') as stderr:
        process=subprocess.Popen([sys.executable,'-m','ui.desktop','--output',str(output/'game'),'--delay','.05'],
            cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,encoding='utf-8',bufsize=1)
        def read():
            for line in process.stdout:queue.put(line)
            queue.put(None)
        Thread(target=read,daemon=True).start()
        def send(**row):process.stdin.write(json.dumps(row,ensure_ascii=False)+'\n');process.stdin.flush()
        def request(turn,request_id,operation,arguments):
            return dict(command='human_submit',actor=turn['actor'],period=turn['period'],
                control_generation=turn['control_generation'],seat_token=turn['seat_token'],
                request_id=request_id,operation=operation,arguments=arguments)
        started=False;deadline=time.monotonic()+90
        try:
            while time.monotonic()<deadline:
                try:line=queue.get(timeout=5)
                except Empty:
                    if process.poll() is not None:break
                    continue
                if line is None:break
                row=json.loads(line);rows.append(row)
                if row['type']=='error':raise AssertionError(row)
                if row['type']=='human_turn':
                    active=row;turns.append(row['period'])
                    send(**request(row,f'{row["period"]}/buy','buy',{'resource':'food','quantity':2}))
                elif row['type']=='human_result':
                    results.append(row)
                    if not row['ok'] or not row['data']['ok']:raise AssertionError(row)
                    if str(row['request_id']).endswith('/buy'):
                        if row.get('duplicate'):send(**request(active,f'{active["period"]}/end','end_turn',{}))
                        else:send(**request(active,row['request_id'],'buy',{'resource':'food','quantity':2}))
                elif row['type']=='timeline':
                    timelines.append(row)
                    send(command='playback_ack',generation=row['generation'],sequence=row['sequence'])
                elif row['type']=='status':
                    if row['state']=='paused' and not started:
                        started=True;send(command='resume')
                    elif row['state']=='paused' and row['period']==3:send(command='stop')
                    elif row['state']=='stopped':break
            else:raise TimeoutError('human host review timed out')
            process.stdin.close();code=process.wait(timeout=10)
            if code:raise AssertionError(f'host exit {code}')
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    saved=json.loads((output/'game/session.json').read_text(encoding='utf-8'))
    gov=saved['modules']['native:government']
    actor=cfg['nations'][0]['id']
    assert turns==[0,1,2],turns
    assert len([r for r in results if r.get('duplicate')])==3
    assert sum(r['stage']=='settlement' for r in timelines)==3
    assert len([r for r in saved['host']['audit'] if r['actor']==actor])==6
    assert not any(p['owner']==actor for p in gov['plans'])
    assert not any(p['owner']==actor for p in gov['summaries'])
    assert all(r['frame'].get('scope')=='player' for r in rows if r['type']=='snapshot')
    assert all(r['actor']==actor for r in rows if r['type'] in ('activity','stream'))
    assert not (output/'stderr.log').read_text(encoding='utf-8').strip()
    from host import GameHost
    restored=GameHost.load(output/'game/session.json',cfg)
    assert restored.to_save()==saved
    report={'period':restored.clock(),'human_turns':turns,'actual_human_writes':6,'deduplicated_requests':3,
        'action_batches':sum(r['stage']=='action' for r in timelines),
        'settlement_batches':sum(r['stage']=='settlement' for r in timelines),
        'player_scoped_snapshots':sum(r['type']=='snapshot' for r in rows),
        'ai_versions':sorted({c.version for c in restored.clients.values() if c.kind=='rule'}),
        'human_plan_or_summary_created':False,'exact_save_reload':True,'stderr_empty':True,
        'save_sha256':hashlib.sha256((output/'game/session.json').read_bytes()).hexdigest()}
    (output/'protocol.ndjson').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    review(parser.parse_args().output)
