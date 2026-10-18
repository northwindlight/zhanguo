"""Lightweight, stateless scenario Mods bound independently to each session.

Scenario effects use declared domain interfaces. No identity, victory policy,
or game-specific rule is known to this hook adapter.
"""
from .runtime import GameMod
from .state import unit_context


class ScenarioMod(GameMod):
    text = ''

    def mixins(self):return ()

    def on_start(self, world):pass

    def on_settlement(self, world, trigger_victory):pass

    def bind(self, world, trigger_victory):
        with unit_context(self.declaration['Mod']['Id']):self.on_start(world)
        return ScenarioHooks(self,world,trigger_victory)


class ScenarioHooks:
    def __init__(self, mod, world, trigger_victory):
        self.mod,self.world,self.trigger_victory=mod,world,trigger_victory
        self.text=mod.text

    def __call__(self):
        with unit_context(self.mod.declaration['Mod']['Id']):
            self.mod.on_settlement(self.world,self.trigger_victory)
