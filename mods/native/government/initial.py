from ..core import *

def initialize(world):
    world.turn = 0
    world.state_version = 0
    world.summaries: dict[str, list[dict]] = {}
    world.summary_blocks: dict[str, list[dict]] = {}
    world.long_memory: dict[str, str] = {}
    world.turn_memory: dict[str, list[dict]] = {}
    world.plans: dict[str, dict] = {}
    world.extra_prompt: dict[str, dict] = {}
    world.opening_guide: bool = True
    world.history: list[dict] = []
    world.history_seen = 0
    world.token_usage: dict[str, dict] = {}
    world.token_usage = {nm: {k: 0 for k in USAGE_FIELDS} for nm in world.nations}
