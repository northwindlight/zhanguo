from ..base import DomainMod

class ArmyMod(DomainMod):
    package = "mods.native.army"
    logic_class = "ArmyLogic"

    def content_definitions(self):
        from .content import native_unit_definitions
        return native_unit_definitions()

    def objects(self, world):
        return {'native:army': world.units}

    def schema_for_actor(self, world, actor, schema):
        if schema['function']['name']=='recruit':
            from .views import _recruit_desc
            schema['function']['description']=_recruit_desc({k:world.recruit_cost(actor,k) for k in world.units.type_ids}, world.units.definitions)
            schema['function']['parameters']['properties']['kind']['enum']=list(world.units.type_ids)
        return schema
