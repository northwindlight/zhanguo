"""Public service/host entry. Clients receive runtime.player(world, actor)."""
from pathlib import Path
from engine.runtime import Runtime
from engine.player import API_VERSION,PlayerSession
from host import GameHost,SaveFormatError

runtime=Runtime.load(Path(__file__).resolve().parent/'mods.toml')
World=runtime.world_class()
__all__=['Runtime','runtime','World','GameHost','SaveFormatError','PlayerSession','API_VERSION']
