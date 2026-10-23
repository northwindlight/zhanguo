#!/usr/bin/env python3
"""Record the real synthetic Godot window on Linux; no game or model requests."""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def stop(process, sig=signal.SIGTERM):
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, sig)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Xvfb 实际窗口录制与32秒地貌巡览')
    parser.add_argument('--output', type=Path, required=True, help='新建输出目录，已有目录拒绝覆盖')
    args = parser.parse_args(argv)
    for name in ('Xvfb', 'ffmpeg', 'ffprobe'):
        if not shutil.which(name):
            parser.error('需要安装 ' + name)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    image = output/'terrain.png'
    marker = Path(str(image)+'.tour-start.json')
    xvfb = renderer = recorder = None
    read_fd, write_fd = os.pipe()
    try:
        with (output/'xvfb.log').open('wb') as xlog, (output/'godot.log').open('wb') as glog, (output/'ffmpeg.log').open('wb') as flog:
            xvfb = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '1440x900x24', '-nolisten', 'tcp'],
                pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=xlog, start_new_session=True)
            os.close(write_fd); write_fd = -1
            if not select.select([read_fd], [], [], 15)[0]:
                raise RuntimeError('Xvfb 未能分配显示，见 xvfb.log')
            display = os.read(read_fd, 32).decode().strip()
            if not display.isdigit():
                raise RuntimeError('Xvfb 返回无效显示号')
            env = dict(os.environ, DISPLAY=':'+display)
            renderer = subprocess.Popen([sys.executable, str(ROOT/'desktop.py'), '--sample', 'terrain',
                '--sample-tour', '--smoke-output', str(image)], cwd=ROOT, env=env,
                stdout=glog, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic()+120
            while not marker.is_file():
                if renderer.poll() is not None:
                    raise RuntimeError('Godot 在巡览前退出，见 godot.log')
                if time.monotonic() > deadline:
                    raise TimeoutError('等待截图和预热超时，见 godot.log')
                time.sleep(.1)
            recorder = subprocess.Popen(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-draw_mouse', '0', '-probesize', '32', '-analyzeduration', '0',
                '-video_size', '1440x900', '-framerate', '15', '-f', 'x11grab', '-i', ':'+display,
                '-an', '-r', '15', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart', str(output/'tour.mp4')],
                stdout=flog, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            print('开始录制真实窗口：'+str(output/'tour.mp4'), flush=True)
            capture_deadline = time.monotonic()+8
            while not (output/'tour.mp4').is_file():
                if recorder.poll() is not None or time.monotonic() > capture_deadline:
                    raise RuntimeError('录制器未创建视频，见 ffmpeg.log')
                time.sleep(.1)
            if renderer.wait(timeout=65) != 0:
                raise RuntimeError('Godot 巡览未通过，见 godot.log')
            if recorder.poll() is not None:
                raise RuntimeError('录制进程提前退出，见 ffmpeg.log')
            stop(recorder, signal.SIGINT)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
            'format=duration:stream=width,height,nb_frames,r_frame_rate', '-of', 'json', str(output/'tour.mp4')],
            check=True, capture_output=True, text=True)
        info = json.loads(probe.stdout)
        if float(info['format']['duration']) < 30:
            raise RuntimeError('窗口录像短于30秒')
        report = json.loads(Path(str(image)+'.json').read_text())
        if not report.get('tour') or report['measured_seconds'] < 32 or not report['world_clipping_checked']:
            raise RuntimeError('缺少完整巡览或外缘检查证据')
        (output/'recording.json').write_text(json.dumps({'capture':'Xvfb real window, ffmpeg x11grab',
            'capture_fps':15, 'encoder':'libx264 ultrafast crf28', 'probe':info,
            'performance_measured_while_recording':True}, indent=2)+'\n')
        print('TERRAIN_RECORDING_OK '+str(output), flush=True)
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)
        stop(recorder, signal.SIGINT)
        stop(renderer)
        stop(xvfb)


if __name__ == '__main__':
    main()
