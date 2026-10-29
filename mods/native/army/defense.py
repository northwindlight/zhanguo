"""Shared combat cover for resolution, public queries and authorized views."""


def defense_pct(terrain, castle=0):
    return 100-((100-terrain)*(100-castle))//100


def cover(owner, land_owner, active, terrain, castle=0):
    defending=owner==land_owner or owner=='野人' or owner not in active
    fort=castle if owner==land_owner else 0
    return {'defending':defending,'terrain':terrain if defending else 0,
            'castle':fort if defending else 0,
            'total':defense_pct(terrain,fort) if defending else 0}
