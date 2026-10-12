from .objects import Units

def initialize(world):
    world.units = Units(world.size)
