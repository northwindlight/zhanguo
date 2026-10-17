"""Player-facing scripting API; tool schemas live in the experimental tool layer."""


class ScriptingAPI:
    def __init__(self, world):
        self.world = world

    def write(self, actor, source):
        return self.world.scripts.write(actor, source)

    def patch(self, actor, old, new):
        return self.world.scripts.patch(actor, old, new)

    def check(self, actor, source=None):
        return self.world.scripts.check(actor, source)

    def status(self, actor, include_source=False):
        return self.world.scripts.status(actor, include_source)

    def claim(self, actor, inputs=None):
        if inputs is not None and not isinstance(inputs, dict):
            raise ValueError('inputs must be a JSON object')
        return self.world.scripts.claim(actor)
