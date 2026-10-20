"""Player-facing territory API; tool schemas live in the distribution tool layer."""
from dataclasses import replace

from engine.resource import Resource
from .objects import Terrain


class TerritoryAPI:
    def __init__(self, world):
        self.world = world

    def land(self, actor, terrain=None, resource=None, offset=0, limit=None):
        if offset < 0 or limit is not None and limit < 1:
            raise ValueError('offset 须非负，limit 须为正整数')
        if terrain is not None and terrain not in {t.id for _, t in self.world.runtime.content.definitions() if isinstance(t, Terrain)}:
            raise ValueError('未知地形')
        if resource is not None:
            definition = self.world.runtime.content.definition(resource)
            if not isinstance(definition, type) or not issubclass(definition, Resource):
                raise ValueError('未知资源')
        rows = tuple(t for t in self.world.territory.owned(actor)
                     if (terrain is None or t.tile.terrain == terrain) and
                     (resource is None or dict(t.resources).get(resource, 0) > 0))
        return rows[offset:] if limit is None else rows[offset:offset + limit]

    def terrain_catalog(self, actor):
        try:
            costs = self.world.runtime.content.definition('native:cavalry')['move_cost']
        except KeyError:
            costs = {}
        return tuple(replace(t, cavalry_cost=costs.get(t.id, t.cavalry_cost))
                     for _, t in self.world.runtime.content.definitions() if isinstance(t, Terrain))
