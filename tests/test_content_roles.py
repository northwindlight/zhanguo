"""Content roles, declared numbers and the facts-driven map presentation."""
import unittest

from presentation.facts import map_presentation
from engine.content import ContentRegistry
from engine.runtime import Runtime
from tests import test_world_domains as fixtures

Q, C = fixtures.Q, fixtures.C


class TestContentRoles(unittest.TestCase):
    def runtime(self):
        return Runtime.load('mods.toml')

    def test_native_roles_are_declared_and_indexed(self):
        content = self.runtime().content
        self.assertEqual(content.role('capital'), ('native:town-hall',))
        self.assertEqual(content.role('currency'), ('native:gold',))
        self.assertIn('native:supply', content.role('supply'))
        self.assertIn('native:wood', content.role('construction'))
        self.assertIn('native:barracks', content.role('recruiter'))
        self.assertIn('native:militia-camp', content.role('militia'))
        self.assertIn('native:castle', content.role('castle'))
        self.assertEqual(content.roles_of('native:plain'), ())

    def test_bad_role_declarations_fail_loudly(self):
        registry = ContentRegistry()
        with self.assertRaises(ValueError):
            registry.register('x', {'x:a': type('A', (), {'ROLES': ('Bad',)})})
        with self.assertRaises(ValueError):
            registry.register('x', {'x:a': type('A', (), {'ROLES': ('ok', 'ok')})})
        with self.assertRaises(ValueError):
            registry.register('x', {'x:a': type('A', (), {'ROLES': 'ok'})})

    def test_buildings_answer_by_role_without_content_ids(self):
        harness = fixtures.TestWorldDomains()
        world = harness.world()
        harness.site(world, 'scenario:qc', Q, 1, 1)
        self.assertTrue(world.buildings.has_role('scenario:qc', 'capital'))
        self.assertEqual(world.buildings.count_role('scenario:qc', 'capital'), 1)
        self.assertIn('native:town-hall', world.buildings.kinds_with_role('capital'))
        world.buildings.seed('scenario:qc', 'native:castle')
        self.assertEqual(world.buildings.effect_role('scenario:qc', 'castle', 'defense_per_level'), 10)
        with self.assertRaises(ValueError):
            world.buildings.kinds_with_role('no-such-role')

    def test_declared_numbers_resolve_in_rule_text(self):
        runtime = self.runtime()
        self.assertEqual(runtime.number('army.starvation'), 35)
        self.assertEqual(runtime.number('army.heal'), 30)
        self.assertEqual(runtime.number('army.heal-equipment'), 1)
        with self.assertRaises(Exception):
            runtime.number('army.no-such-number')
        text = '\n'.join(section.body for section in runtime.rule_sections())
        self.assertIn('完全断供每支损失35', text)
        self.assertNotIn('{{number:', text)

    def test_map_glyphs_come_from_public_catalogs(self):
        content = self.runtime().content
        self.assertEqual(content.definition('native:plain').glyph, 'P')
        self.assertEqual(content.definition('native:infantry')['glyph'], 'I')
        facts = {
            'terrain_catalog': [{'id': 'native:plain', 'glyph': 'P'}],
            'unit_catalog': {'native:infantry': {'glyph': 'I'}},
            'building_catalog': [{'id': 'native:town-hall', 'roles': ['capital']}],
        }
        glyphs, roles = map_presentation(facts)
        self.assertEqual(glyphs, {'native:plain': 'P', 'native:infantry': 'I'})
        self.assertEqual(roles, {'capital': ['native:town-hall']})


if __name__ == '__main__':
    unittest.main()
