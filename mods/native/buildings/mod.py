from engine.runtime import GameMod, Stage, Tool
from .content import building_types
from .objects import Buildings
from .types import BUILDING_TYPES


class BuildingsMod(GameMod):
    def mixins(self):return ()
    def content_definitions(self):return {cls.TYPE_ID:cls for cls in BUILDING_TYPES}
    def state_keys(self):return ('buildings',)
    def needs(self):return ('native:resource-exchange',)
    def provides(self):return ('native:buildings',)

    def initialize_world(self, world):
        world.buildings = Buildings(building_types(self.runtime.content),
            lambda method,*args:self.runtime.object_call(world,'native:resource-exchange',method,*args))

    def objects(self, world):return {'native:buildings':world.buildings}
    def save_state(self, world):return world.buildings.to_save()
    def load_state(self, world, state):world.buildings.restore(state)

    def tools(self):
        kinds = sorted(cls.TYPE_ID for cls in building_types(self.runtime.content))
        parameters = {'type':'object','properties':{
            'site':{'type':'string'},'kind':{'type':'string','enum':kinds}},
            'required':['site','kind'],'additionalProperties':False}
        return (Tool('build',self._build,parameters=parameters,
                     description='Construct a registered building on an owned site; completes at settlement'),)

    @staticmethod
    def _build(world, actor, site: str, kind: str):
        return world.buildings.build(actor,site,kind)

    def stages(self):
        handlers = {'produce':self._produce,'complete':self._complete}
        return tuple(Stage(row['Id'],handlers[row['Handler']],hook=row['Hook'],
                           needs=tuple(row.get('Needs',())),provides=tuple(row.get('Provides',())),
                           requires=tuple(row.get('Requires',())),after=tuple(row.get('After',())),
                           before=tuple(row.get('Before',())))
                     for row in self.declaration.get('Stages',()))

    @staticmethod
    def _produce(world, context):
        report = world.buildings.produce(external_energy=context.get('building_energy',{}))
        context['building_production'] = report
        # Accumulate into explicit market inputs, preserving other producers.
        data = context.setdefault('market',{})
        for field, entries in (('production',report.outputs),('consumption',report.inputs)):
            totals = data.setdefault(field,{})
            for key,amount in entries:totals[key] = totals.get(key,0)+amount

    @staticmethod
    def _complete(world, context):
        context['building_completions'] = world.buildings.complete_construction()
