"""Produce an isolated, offline v13 visual fixture; never reads a live save."""
import argparse
import json
from pathlib import Path

from scenarios.eight_nations import build
from .desktop_activity import TimelineCapture


def build_extinction_fixture():
    """A owns an old B map, captures its last hall, then reloads the save."""
    from tempfile import TemporaryDirectory
    from host import GameHost
    from mods.native.human import HumanAPI
    from .desktop_player import frame as player_frame
    a,b,c='scenario:human','scenario:other','scenario:third'
    cfg={'mods':'mods.toml','map_size':12,'seed':73,'nations':[
        {'id':a,'name':'秦','client':'human','color':'66b6a7'},
        {'id':b,'name':'楚','client':'rule','color':'ce7765'},
        {'id':c,'name':'赵','client':'rule'}]}
    host=GameHost.new(cfg);api=HumanAPI(host);world=host.world
    live={tuple(t['position']) for t in api.state(a)['map']['tiles'] if t['visibility']=='live'}
    sites={s.id:s for s in world.buildings.snapshot().sites}
    hall=next(i for i in world.buildings.snapshot().instances if i.kind=='native:town-hall' and sites[i.site].owner==b)
    capital=sites[hall.site].position
    point=next((x,y) for x in range(1,11) for y in range(1,11) if (x,y) not in live and
        world.territory.owner(x,y) is None and world.territory.terrain(x,y).claimable and
        max(abs(x-capital[0]),abs(y-capital[1]))>3)
    site='scenario:remembered-b';world.territory.create(site,b,*point,terrain='native:plain')
    world.buildings.seed(site,'native:castle');world.intelligence.publish_maps()
    def submit(operation,args):
        receipt=api.submit(a,operation,args)
        if not receipt['ok']:raise ValueError(receipt)
        return receipt
    def snapshot(api,host):
        state=api.state(a)
        return {'scope':'player',**state,'visual':player_frame(host.ui,state)}
    submit('buy_map',{'target':b});submit('declare_war',{'target':b,'confirm':True})
    host.cycle(on_human=lambda p:[submit('end_turn',{})])
    unit=world.military.seed(a,'native:infantry',*capital)[0]
    world.territory.transfer(site,c)  # Hidden from A; extinction must not reveal C.
    before=snapshot(api,host)
    host.cycle(on_human=lambda p:[submit('attack',{'ids':[unit.id],'x':capital[0],'y':capital[1]}),submit('end_turn',{})])
    if b in host.actors():raise ValueError('B survived the fixture')
    after=snapshot(api,host)
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'session.json';host.save(path);loaded=GameHost.load(path,cfg)
        restored=snapshot(HumanAPI(loaded),loaded)
    return {'position':list(point),'old_owner':b,'offline':True,'frames':[
        {'name':'before','frame':before},{'name':'after','frame':after},{'name':'restored','frame':restored}]}


def build_fixture():
    host=build(seed=20261023)
    for _ in range(4):host.cycle()
    frame=host.ui.desktop_frame(host.observer_snapshot())
    capture=TimelineCapture(host);actor='scenario:qin'
    for action,args in [('plan',{'text':'本期核实生产与军需，向楚寄信，并依据实际局势经营。'}),
                        ('intelligence',{'action':'send_letter','target':'scenario:chu','text':'这是一封实际离线寄出并在回合末送达的演出验收信件。'})]:
        receipt=host.player(actor).submit(action,args)
        if not receipt.get('ok'):raise ValueError(receipt)
        capture.capture(actor,action,args,receipt)
    host.cycle(on_result=capture.capture)
    beats=capture.finish()
    if capture.warning:raise ValueError(capture.warning)
    if not any(b['phase']=='letter' for b in beats):raise ValueError('Fixture letter was not delivered')
    return {'frame':frame,'beats':beats,'period':4,'normal_initial_armies':0,'subsidies':False,'offline':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--extinction',action='store_true')
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(build_extinction_fixture() if args.extinction else build_fixture(),stream,ensure_ascii=False)
    print('OFFLINE_VISUAL_FIXTURE_OK',args.output)


if __name__=='__main__':main()
