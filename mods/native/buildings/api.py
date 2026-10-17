"""Player-facing building API; tool schemas live in the distribution tool layer."""
from .content import building_types


class BuildingsAPI:
    def __init__(self, world):
        self.world = world

    def build(self, actor, kind, site=None, x=None, y=None):
        site = self.world.buildings.resolve_site(actor, site, x, y)
        return self.world.buildings.build(actor, site, kind,
                                          policy_pct=self.world.runtime.parameter(self.world, actor, 'build_cost_pct', 100))

    def sites(self, actor):
        snap = self.world.buildings.snapshot()
        sites = [s for s in snap.sites if s.owner == actor]
        ids = {s.id for s in sites}
        return {'sites': sites, 'instances': [b for b in snap.instances if b.site in ids],
                'construction': [r for r in snap.construction if r.owner == actor],
                'self_built': {kind: count for owner, kind, count in snap.self_built if owner == actor}}

    def catalog(self, actor):
        world = self.world
        return [{'id': cls.TYPE_ID, 'roles': list(cls.ROLES), 'min_slots': cls.MIN_SLOTS, 'max_per_site': cls.MAX_PER_SITE,
                 'self_build_limit': cls.SELF_BUILD_LIMIT, 'site_resource': cls.SITE_RESOURCE,
                 'energy_use': cls.ENERGY_USE, 'energy_output': cls.ENERGY_OUTPUT,
                 'inputs': dict(cls.INPUTS), 'outputs': dict(cls.OUTPUTS),
                 'effects': dict(cls.EFFECTS), 'build_discount': cls.BUILD_DISCOUNT,
                 'policy_pct': world.runtime.parameter(world, actor, 'build_cost_pct', 100),
                 'costs_by_level': [cls.construction_cost(level) for level in range(1, len(cls.GOLD_COSTS) + 1)]}
                for cls in building_types(world.runtime.content)]
