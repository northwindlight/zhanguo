"""Offline native/experimental continuation checks, with disposable saves only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from host import GameHost


class Council:
    def chat_turn(self,messages,tools,cfg,on_retry=None):
        names={t['function']['name'] for t in tools}
        name='council_approve' if 'council_approve' in names else 'council_next_stage'
        return {'content':'已核对合成事实','tool_calls':[{'id':'verdict','type':'function',
            'function':{'name':name,'arguments':'{}'}}]},{}


def attach(host):
    for services in host.services.values():
        if 'council' in services:services['council']._review_backend=Council()


def run(mods,cycles):
    cfg={'mods':mods,'map_size':24,'seed':73,'nations':[
        {'id':f'scenario:n{i}','name':name,'client':'rule'} for i,name in enumerate(('秦','楚','齐'))]}
    host=GameHost.new(cfg);attach(host)
    for _ in range(cycles//2):host.cycle()
    with TemporaryDirectory(prefix='zhanguo-audit-') as directory:
        path=Path(directory)/'synthetic.json';host.save(path)
        other=GameHost.load(path,deepcopy(cfg));attach(other)
        for _ in range(cycles-cycles//2):host.cycle();other.cycle()
        assert host.to_save()==other.to_save(),'Continuation changed observable game or client state'
        host.save(path)
        from settlement import load_save,settle
        before=path.read_bytes();data,world=load_save(path)
        rows=settle(data,world);assert before==path.read_bytes()
        return {'distribution':mods,'cycles':host.clock(),'nations':len(host.roster),
                'alive':len(host.actors()),'continuation_equal':True,
                'scoreboard_rows':len(rows),'save_bytes':path.stat().st_size,
                'archive_segments':sum(1 for _ in path.with_suffix('.json.audit').rglob('*.json'))}


if __name__=='__main__':
    print(json.dumps([run('mods.toml',40),run('mods.experimental.toml',6)],ensure_ascii=False,indent=2))
