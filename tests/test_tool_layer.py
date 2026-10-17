"""Tool layer contract: explicit wrappers must cover every declared player API."""
import unittest

from engine.runtime import GameMod, ModError, Runtime, Tool, Unit


class API:
    def a(self, actor): return 'a'
    def b(self, actor): return 'b'


class PartialMod(GameMod):
    declaration = {'Mod': {'Id': 'test:partial'}, 'PlayerAPI': {'Methods': ['a', 'b']}}

    def mixins(self): return ()
    def provides(self): return ('test:cap',)
    def player_api(self, world): return API()


class Layer:
    def __init__(self, runtime, tools):
        self.runtime = runtime
        self._tools = tuple(tools)

    def tools(self): return self._tools
    def catalogs(self): return {}


def _tool(api=('a',), *, read_only=False, fact=False, panel=False, owner='test:cap'):
    def handler(world, actor): return 'ok'
    return Tool('test_tool', handler, owner=owner, api=api, read_only=read_only, fact=fact, panel=panel)


def build_missing(runtime):
    return Layer(runtime, (_tool(api=('a',)),))


def build_complete(runtime):
    return Layer(runtime, (_tool(api=('a', 'b')),))


def build_fact_write(runtime):
    return Layer(runtime, (_tool(api=('a', 'b'), read_only=False, fact=True),))


def build_unknown_owner(runtime):
    return Layer(runtime, (_tool(api=('a',), owner='test:missing'),))


class TestToolLayerContract(unittest.TestCase):
    def runtime(self, entry):
        return Runtime([Unit('test:partial', PartialMod(), version='1')],
                       {'Tools': {'Entry': f'{__name__}:{entry}'}}, enforce_player_apis=True)

    def test_every_declared_player_api_method_must_be_wrapped(self):
        with self.assertRaisesRegex(ModError, '未封装：b'):
            self.runtime('build_missing')

    def test_complete_coverage_loads(self):
        runtime = self.runtime('build_complete')
        self.assertEqual(tuple(tool.name for tool in runtime.tool_layer.tools()), ('test_tool',))
        self.assertIs(runtime.tool_owners['test_tool'], runtime.units['test:partial'].mod)

    def test_fact_and_panel_tools_must_be_read_only(self):
        with self.assertRaisesRegex(ModError, 'fact/panel 必须只读'):
            self.runtime('build_fact_write')

    def test_tool_owner_capability_must_exist(self):
        with self.assertRaisesRegex(ModError, 'owner 能力无提供者'):
            self.runtime('build_unknown_owner')


if __name__ == '__main__':
    unittest.main()
