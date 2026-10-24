"""Record an actual offline v13 window, including its first natural battles.

Uses a fresh normal game advanced four complete cycles, then the ordinary
desktop resume entry. No subsidies, real saves or model services are used.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def inside(out):
    env={**os.environ,'ZHANGUO_DESKTOP_SMOKE_TOUR':'1'}
    with (out/'launcher.log').open('w') as log:
        proc=subprocess.Popen([sys.executable,str(ROOT/'desktop.py'),'--resume',str(out/'input/session.json'),
            '--smoke-output',str(out/'world.png'),'--log-file',str(out/'godot.log')],cwd=ROOT,env=env,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        recorder=None
        try:
            deadline=time.monotonic()+180
            while 'DESKTOP_BATTLE_DEMO_START' not in (out/'launcher.log').read_text():
                if proc.poll() is not None:raise RuntimeError('窗口在进入演出前退出；检查launcher.log')
                if time.monotonic()>deadline:raise TimeoutError('等待首次演出超时')
                time.sleep(.1)
            with (out/'ffmpeg.log').open('w') as capture_log:
                recorder=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','warning','-y','-f','x11grab',
                    '-video_size','1440x900','-framerate','20','-draw_mouse','0','-probesize','32','-analyzeduration','0',
                    '-i',os.environ['DISPLAY'],'-t','32','-c:v','libx264','-preset','ultrafast','-crf','26',
                    '-pix_fmt','yuv420p',str(out/'tour.mp4')],stdout=capture_log,stderr=subprocess.STDOUT,start_new_session=True)
                recorder.wait(timeout=55)
            code=proc.wait(timeout=360)
            if code:raise RuntimeError(f'窗口验收失败：{code}')
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
                'format=duration:stream=width,height,nb_frames','-of','json',str(out/'tour.mp4')],text=True))
            if float(probe['format']['duration'])<15:raise RuntimeError('录制不足15秒')
            probe['capture_trigger']='first_natural_battle'
            (out/'recording.json').write_text(json.dumps(probe,indent=2)+'\n')
            print('DESKTOP_RECORD_OK',probe['format']['duration'],out)
        finally:
            for child in (recorder,proc):
                if child is not None and child.poll() is None:
                    os.killpg(child.pid,signal.SIGTERM);child.wait(timeout=10)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--inside',action='store_true')
    p.add_argument('--seed',type=int,default=20261023);args=p.parse_args()
    out=args.output.resolve()
    if args.inside:return inside(out)
    out.mkdir(parents=True,exist_ok=False);(out/'input').mkdir()
    sys.path.insert(0,str(ROOT))
    from scenarios.eight_nations import build
    from host import write_json
    host=build(seed=args.seed)
    for _ in range(4):host.cycle()
    write_json(out/'input/config.json',host.cfg);host.save(out/'input/session.json')
    subprocess.run(['xvfb-run','-a','-s','-screen 0 1440x900x24',sys.executable,str(Path(__file__).resolve()),
                    '--inside','--output',str(out)],check=True,cwd=ROOT)


if __name__=='__main__':main()
