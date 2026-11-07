"""Refresh an owned Colab assignment without allocating or terminating a VM.

Run with the Colab CLI virtual environment. Never print proxy credentials.
"""
import argparse
import os
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session',required=True)
    p.add_argument('--endpoint-file',type=Path,required=True)
    p.add_argument('--reset-kernel',action='store_true')
    args=p.parse_args()
    if not (os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')):
        proxy=os.environ.get('COLAB_PROXY','http://192.168.1.1:20171')
        os.environ.update(HTTPS_PROXY=proxy,HTTP_PROXY=proxy,https_proxy=proxy,http_proxy=proxy)
    from colab_cli.common import state,_apply_proxy_info
    from colab_cli.state import SessionState
    assignments=state.client.list_assignments()
    saved=state.store.get(args.session)
    endpoint=(args.endpoint_file.read_text().strip() if args.endpoint_file.exists()
              else saved.endpoint if saved else None)
    matches=[a for a in assignments if a.endpoint==endpoint] if endpoint else [a for a in assignments if a.accelerator.value=='T4']
    if not matches:raise SystemExit(2)
    if len(matches)!=1:raise RuntimeError('Owned T4 assignment ambiguous')
    assignment=matches[0]
    if saved is None:
        saved=SessionState(name=args.session,endpoint=assignment.endpoint,token='',url='',
                           accelerator=assignment.accelerator.value,variant=assignment.variant.name)
    _apply_proxy_info(saved,assignment.runtime_proxy_info)
    if args.reset_kernel:saved.kernel_id=None;saved.session_id=None;saved.running=None
    state.store.add(saved)
    args.endpoint_file.write_text(assignment.endpoint+'\n')
    print('Owned T4 assignment refreshed; no allocation performed')


if __name__=='__main__':main()
