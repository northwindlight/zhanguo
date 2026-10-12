from ..base import DomainMod

class ArmyMod(DomainMod):
    package = "mods.native.army"
    logic_class = "ArmyLogic"

    def objects(self, world):
        return {'army.units': world.units}

    def schema_for_actor(self, world, actor, schema):
        if schema['function']['name']=='recruit':
            from .views import _recruit_desc
            from .parameters import UNIT_TYPES
            schema['function']['description']=_recruit_desc({k:world.recruit_cost(actor,k) for k in UNIT_TYPES})
        return schema
