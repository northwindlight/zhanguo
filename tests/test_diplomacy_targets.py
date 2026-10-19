"""Diplomatic entity addressing and actionable offer views; synthetic games only."""
from pathlib import Path
from clients.model_view import model_view_for
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
from tests import test_diplomacy_finish as fixtures
Q,C,Z=fixtures.Q,fixtures.C,fixtures.Z

B='scenario:union'


class DiplomaticTargets(unittest.TestCase):
    def test_war_and_peace_accept_country_alliance_and_entity_addresses(self):
        for target in (Q,'nation/'+Q,B,'bloc/'+B):
            with self.subTest(target=target):
                f=fixtures.TestDiplomacyFinish();h=f.host();f.bloc(h)
                f.submit(h,C,'declare_war',target=target);h.cycle()
                self.assertTrue(h.world.diplomacy.war_between(C,Q))
                self.assertTrue(h.world.diplomacy.war_between(C,Z))
                f.submit(h,C,'offer_peace',target=target,kind='white',truce=5);h.cycle()
                q=h.player(Q).call_api('native:diplomacy','snapshot')['offers'][0]
                z=h.player(Z).call_api('native:diplomacy','snapshot')['offers'][0]
                self.assertEqual(q['target'],'bloc/'+B)
                self.assertEqual(q['response_actor'],Q)
                self.assertTrue(q['can_accept']);self.assertTrue(q['can_reject'])
                self.assertFalse(z['can_accept']);self.assertFalse(z['can_reject'])
                self.assertEqual(z['response_actor'],Q)
                observation=h.player(Z).observe()
                dashboard=model_view_for(h.ui, {}).dashboard(observation)
                self.assertFalse(dashboard['diplomacy_todo'].get('offers'))
                self.assertEqual(dashboard['diplomacy_waiting_offers'][0]['id'],q['id'])
                h.ui.console.remember(observation)
                foreign=h.player(Z).query('foreign')
                rendered=h.ui.console.action(h.clock(),Z,'foreign',{}, {'ok':True,'data':foreign},{Q:'秦',C:'楚',Z:'赵'})
                self.assertIn('回应代表：秦',rendered)
                self.assertIn('不能直接接受或拒绝',rendered)
                denied=h.player(Z).submit('relations',{'action':'reject_peace','offer':q['id']})
                self.assertFalse(denied['ok']);self.assertIn('战争代表',denied['message'])
                f.submit(h,Q,'accept_peace',offer=q['id']);h.cycle()
                z=h.player(Z).call_api('native:diplomacy','snapshot')['offers'][0]
                self.assertFalse(z['can_accept']);self.assertFalse(z['can_reject'])
                self.assertEqual(len(z['vote_ids']),1)
                vote=z['vote_ids'][0]
                for actor in (Q,Z):f.submit(h,actor,'vote',vote=vote,accept=True)
                h.cycle();self.assertFalse(h.world.diplomacy.snapshot(C).wars)

    def test_entity_treaty_follows_new_chief_and_survives_save(self):
        f=fixtures.TestDiplomacyFinish();h=f.host();f.bloc(h)
        f.submit(h,C,'propose_defense',target='bloc/'+B)
        f.submit(h,Q,'bloc_transfer',target=Z);h.cycle()
        offer=h.player(Z).call_api('native:diplomacy','snapshot')['offers'][0]
        self.assertEqual(offer['response_actor'],Z)
        self.assertEqual(h.world.diplomacy.representative('bloc/'+B),Z)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'game.json';h.save(path)
            restored=GameHost.load(path,h.cfg)
            self.assertEqual(restored.player(Z).call_api('native:diplomacy','snapshot'),h.player(Z).call_api('native:diplomacy','snapshot'))

    def test_dissolved_target_fails_clearly_and_refunds_reserved_fee(self):
        f=fixtures.TestDiplomacyFinish();h=f.host();f.bloc(h)
        before=h.world.resources.balance(C,'native:gold')
        f.submit(h,C,'propose_defense',target='bloc/'+B)
        f.submit(h,Q,'bloc_dissolve');h.cycle()
        result=h.player(C).call_api('native:diplomacy','snapshot')['results'][-1]
        self.assertEqual(result['status'],'failed')
        self.assertIn('联盟不存在或已解散',result['message'])
        self.assertEqual(h.world.resources.balance(C,'native:gold'),before)
        for target in ('bloc/'+B,'does:not-exist'):
            r=h.player(C).submit('relations',{'action':'offer_peace','target':target,'kind':'white'})
            self.assertFalse(r['ok']);self.assertNotIn('该国已亡国',r['message'])

    def test_country_specific_actions_do_not_redirect_alliance_to_chief(self):
        f=fixtures.TestDiplomacyFinish();h=f.host();f.bloc(h)
        for action in ('bloc_transfer','submit_vassalage','propose_vassalage','release_vassal'):
            for target in (B,'bloc/'+B):
                r=h.player(Q).submit('relations',{'action':action,'target':target})
                self.assertFalse(r['ok']);self.assertIn('具体国家键',r['message'])

    def test_target_representative_building_controls_peace_fee(self):
        f=fixtures.TestDiplomacyFinish();h=f.host();f.bloc(h)
        f.submit(h,C,'declare_war',target=B);h.cycle()
        site=next(s for s in h.world.buildings.snapshot().sites if s.owner==Q)
        h.world.buildings.seed(site.id,'native:diplomacy-center')
        receipt=h.player(C).call_api('native:diplomacy','submit',action='offer_peace',target='bloc/'+B,kind='white')
        self.assertTrue(receipt['ok'],receipt)
        self.assertEqual(receipt['data']['fee'],0)
