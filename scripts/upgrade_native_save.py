#!/usr/bin/env python3
"""Upgrade a known v0.6.3 native/experimental save into a new file."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mods.native.save_upgrade import upgrade_copy
from engine.runtime import ModError


def main():
    parser=argparse.ArgumentParser(description='校验并复制升级已知v0.6.3存档，原文件和历史保留')
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--config',required=True,help='原局配置；只读校验，不调用模型')
    args=parser.parse_args()
    try:
        path=upgrade_copy(args.input,args.output,json.loads(Path(args.config).read_text(encoding='utf-8')))
    except (OSError,ValueError,ModError) as exc:parser.exit(2,str(exc)+'\n')
    print('已校验升级副本：'+str(path))


if __name__=='__main__':main()
