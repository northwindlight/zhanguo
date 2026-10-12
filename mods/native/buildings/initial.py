from ..core import *

def initialize(world):
    world.diplo_built: dict[str, int] = {}
    world.grid_short: dict[str, bool] = {}
    world.energy_report: dict[str, tuple[int, int, bool]] = {}
