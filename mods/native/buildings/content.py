"""Building-domain validation over the engine's opaque content registry."""
from engine.building import Building, BuildingCycle, integer, recipe
from engine.resource import Resource
from engine.identifiers import content_id
from engine.runtime import ModError
from .types import NativeBuilding


def building_types(content):
    result = []
    resources = {key for key,value in content.definitions()
                 if isinstance(value,type) and issubclass(value,Resource)}
    for key, cls in content.definitions():
        if not isinstance(cls,type) or not issubclass(cls,Building):continue
        if not issubclass(cls,NativeBuilding) or key != cls.TYPE_ID:
            raise ModError(f'Building does not implement the native domain contract: {key}')
        if not isinstance(cls.NAME_KEY,str) or not cls.NAME_KEY:
            raise ModError(f'Missing building name key: {key}')
        if cls.PHASE not in ('passive','gather','power','industry','income'):
            raise ModError(f'Invalid building phase: {key}')
        for value in (cls.WOOD_COST,cls.MIN_SLOTS,cls.ENERGY_USE,cls.ENERGY_OUTPUT,cls.BUILD_DISCOUNT):integer(value)
        if cls.BUILD_DISCOUNT>100:raise ModError('Invalid building discount')
        if not isinstance(cls.GOLD_COSTS,tuple) or not cls.GOLD_COSTS:raise ModError('Invalid building costs')
        for cost in cls.GOLD_COSTS:integer(cost)
        for limit in (cls.MAX_PER_SITE,cls.SELF_BUILD_LIMIT):
            if limit is not None:integer(limit,1)
        if len(cls.GOLD_COSTS)>1 and cls.MAX_PER_SITE != len(cls.GOLD_COSTS):
            raise ModError('Level costs must match per-site limit')
        if cls.SITE_RESOURCE is not None:content_id(cls.SITE_RESOURCE)
        for leg in (cls.INPUTS,cls.OUTPUTS):
            for resource in recipe(leg):
                if resource not in resources:raise ModError(f'Unknown building recipe resource: {resource}')
        if not {'native:gold','native:wood'}<=resources:raise ModError('Missing native construction resources')
        result.append(cls)
    return tuple(result)
