"""兼容公共入口。标准玩法从 nativemod 装载；引擎仅调度 Mod。"""
from pathlib import Path
from engine.runtime import Runtime

runtime = Runtime.load(Path(__file__).resolve().parent / 'mods.toml')
from mods.native.core import *
from mods.native import presentation
presentation.assemble(runtime)
World = runtime.world_class()
World.__module__ = __name__
