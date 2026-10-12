from .objects import Units
from .content import unit_definitions

def initialize(world):
    world.units = Units(world.size, definitions=unit_definitions(world.runtime.content))
