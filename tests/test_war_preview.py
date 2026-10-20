"""宣战推演面板：状态不变，名单与真实结算同源；第二跳不参战。"""
import unittest

from tests import test_world_domains as domains
Q, C, Z, Y = domains.Q, domains.C, domains.Z, domains.Y


class WarPreview(unittest.TestCase):
    def harness(self):
        f = domains.TestWorldDomains()
        w = f.world()
        f.site(w, 'scenario:capital', Q, 1, 1)
        return f, w

    def test_camps_match_the_real_declaration_and_nothing_mutates(self):
        f, w = self.harness()
        w.diplomacy.seed_pact('defense', Q, C)      # 楚守秦：第一跳
        w.diplomacy.seed_pact('guarantee', Y, Q)    # 燕保障秦：也是第一跳
        before = f.save(w)
        preview = w.diplomacy.war_preview(Z, Q)
        self.assertEqual(f.save(w), before)
        self.assertTrue(preview['legal'], preview)
        self.assertEqual(set(preview['defenders']), {Q, C, Y})
        self.assertEqual({row['entity'] for row in preview['joined']},
                         {'nation/' + C, 'nation/' + Y})
        f.declare(w, Z, Q)
        war = next(war for war in w.diplomacy.snapshot(Z).wars if Z in war.attackers)
        self.assertEqual(set(preview['attackers']), set(war.attackers))
        self.assertEqual(set(preview['defenders']), set(war.defenders))

    def test_second_hop_guarantor_is_explained_but_never_joins(self):
        f, w = self.harness()
        w.diplomacy.seed_pact('defense', Q, Z)      # 赵被卷入：第一跳
        w.diplomacy.seed_pact('guarantee', Y, Z)    # 燕保障赵：若二跳就会参战
        preview = w.diplomacy.war_preview(C, Q)
        self.assertTrue(preview['legal'], preview)
        self.assertEqual(set(preview['defenders']), {Q, Z})
        self.assertEqual({row['entity'] for row in preview['not_pulled']}, {'nation/' + Y})
        row = preview['not_pulled'][0]
        self.assertEqual(row['via'], ['nation/' + Z])
        self.assertIn('guarantee', row['kinds'])

    def test_truce_and_existing_war_are_answers_not_exceptions(self):
        f, w = self.harness()
        w.diplomacy._truce('nation/' + Q, 'nation/' + C, 20)
        preview = w.diplomacy.war_preview(Q, C)
        self.assertFalse(preview['legal'])
        self.assertEqual(preview['reason'], 'truce')
        self.assertEqual(preview['truce']['until'], 20)
        w.diplomacy._truces = {}
        f.declare(w, Q, C)
        preview = w.diplomacy.war_preview(Q, C)
        self.assertFalse(preview['legal'])
        self.assertEqual(preview['reason'], 'already_at_war')

    def test_declaration_joins_an_existing_front_instead_of_propagating(self):
        f, w = self.harness()
        w.diplomacy.seed_pact('defense', Q, Z)      # 秦赵同阵营
        f.declare(w, Z, C)
        preview = w.diplomacy.war_preview(Q, C)
        self.assertTrue(preview['legal'], preview)
        self.assertEqual(preview['front']['war'], 1)
        self.assertEqual(preview['front']['side'], 'attackers')
        self.assertEqual(set(preview['attackers']), {Z, Q})
        self.assertEqual(set(preview['defenders']), {C})
        self.assertFalse(preview['joined'])

    def test_alliance_declaration_needs_a_vote_and_uses_the_whole_entity(self):
        f, w = self.harness()
        f.found(w, (Q, Z))
        preview = w.diplomacy.war_preview(Q, C)
        self.assertTrue(preview['requires_vote'])
        self.assertEqual(set(preview['attackers']), {Q, Z})
        # 攻击联盟实体同样定位到当前代表并展开全盟。
        entity = w.diplomacy.war_preview(C, 'bloc/' + domains.B)
        self.assertEqual(set(entity['defenders']), {Q, Z})

    def test_panel_is_exposed_through_the_query_umbrella(self):
        f, w = self.harness()
        w.diplomacy.seed_pact('defense', Q, C)
        schema = next(s for s in w.runtime.schemas() if s['function']['name'] == 'query')
        self.assertIn('war', schema['function']['parameters']['properties']['panel']['enum'])
        self.assertEqual(w.call_tool(Q, 'query', {'panel': 'war', 'target': C})['defenders'], [C])
        # 模型照样能填显示名，normalizer 在面板分发前归一化。
        self.assertEqual(w.call_tool(Q, 'query', {'panel': 'war', 'target': '楚'})['defenders'], [C])


class WarPreviewRendering(unittest.TestCase):
    def host(self):
        from host import GameHost
        cfg = {'map_size': 12, 'seed': 73, 'mods': 'mods.toml',
               'nations': [{'id': actor, 'name': name, 'client': 'rule', 'position': position}
                           for actor, name, position in
                           ((Q, '秦', [2, 2]), (C, '楚', [8, 2]), (Z, '赵', [2, 8]), (Y, '燕', [8, 8]))]}
        h = GameHost.new(cfg)
        for client in h.clients.values():
            client.play = lambda player, **kwargs: []
        return h

    def test_rendered_panel_states_the_frozen_front_and_one_hop(self):
        h = self.host()
        h.world.diplomacy.seed_pact('defense', C, Z)    # 赵被卷入
        h.world.diplomacy.seed_pact('guarantee', Y, Z)  # 燕只保障赵：二跳
        data = h.player(Q).query('query', {'panel': 'war', 'target': C})
        text = h.ui.console.action(h.clock(), Q, 'query', {'panel': 'war', 'target': C},
                                   {'ok': True, 'data': data},
                                   {Q: '秦', C: '楚', Z: '赵', Y: '燕'})
        self.assertIn('【宣战推演】秦 → 楚', text)
        self.assertIn('赵 参战', text)
        self.assertIn('燕 不参战', text)
        self.assertIn('义务不二跳', text)
        self.assertIn('战线冻结', text)


if __name__ == '__main__':
    unittest.main()
