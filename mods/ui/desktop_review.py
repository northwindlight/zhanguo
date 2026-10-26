"""Produce an isolated, offline v13 visual fixture; never reads a live save."""
import argparse
import json
from pathlib import Path

from scenarios.eight_nations import build
from .desktop_activity import TimelineCapture


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
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(build_fixture(),stream,ensure_ascii=False)
    print('OFFLINE_VISUAL_FIXTURE_OK',args.output)


if __name__=='__main__':main()
