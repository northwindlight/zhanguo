"""Public compatibility entry points; the assembled UI mod owns rendering."""
from ui import load_ui

def player_map(observation):return load_ui().map_text(observation)
def player_svg(observation):return load_ui().map_svg(observation)
