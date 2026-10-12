from ..core import *

def initialize(world):
    world._mapgen = None
    world.tiles: dict[tuple[int, int], dict] = {}
    world._terr_grid = None
