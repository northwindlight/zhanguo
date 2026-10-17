"""Proposal fees use synthetic worlds and real submission/settlement paths."""
import unittest

from tests import test_world_domains as fixtures
Q,C,Z=fixtures.Q,fixtures.C,fixtures.Z


class TestDiplomacyFees(unittest.TestCase):
    def zero_cash(self,w,actor):
        w.resources.exchange(actor,{'native:gold':w.resources.balance(actor,'native:gold')},{})

    def test_found_fee_is_100_even_with_either_diplomacy_center(self):
        fixture=fixtures.TestWorldDomains()
        for centers in ((),(Q,),(C,),(Q,C)):
            with self.subTest(centers=centers):
                w=fixture.world()
                for i,actor in enumerate(centers):
                    site=fixture.site(w,'scenario:center-'+str(i),actor,i+1,1)
                    w.buildings.seed(site,'native:diplomacy-center')
                before=w.resources.balance(Q,'native:gold')
                command=fixture.call(w,Q,'bloc_found',bloc=fixtures.B,name='合纵',tos=[C])
                self.assertEqual(command.fee,100)
                self.assertEqual(w.resources.balance(Q,'native:gold'),before-100)
                fixture.cycle(w)
                self.assertEqual(w.economy.turns(Q)[0].spending.total,100)

    def test_zero_cash_recipient_can_accept_or_reject_defense(self):
        fixture=fixtures.TestWorldDomains()
        for action in ('accept_proposal','reject_proposal'):
            with self.subTest(action=action):
                w=fixture.world();fixture.call(w,Q,'propose_defense',target=C);fixture.cycle(w)
                offer=w.diplomacy.snapshot(C).offers[0];self.zero_cash(w,C)
                response=fixture.call(w,C,action,offer=offer.id)
                self.assertEqual(response.fee,0);self.assertEqual(w.resources.balance(C,'native:gold'),0)
                result=fixture.cycle(w)['diplomacy_results'][0]
                self.assertEqual(result.status,'effective')
                self.assertFalse(w.diplomacy.snapshot(C).offers)
                self.assertEqual(w.economy.turns(C)[-1].spending.total,0)

    def test_found_replies_free_and_rejection_does_not_refund_initiator(self):
        fixture=fixtures.TestWorldDomains()
        for action in ('accept_proposal','reject_proposal'):
            with self.subTest(action=action):
                w=fixture.world();before=w.resources.balance(Q,'native:gold')
                fixture.call(w,Q,'bloc_found',bloc=fixtures.B,name='合纵',tos=[C]);fixture.cycle(w)
                offer=w.diplomacy.snapshot(C).offers[0];self.zero_cash(w,C)
                self.assertEqual(fixture.call(w,C,action,offer=offer.id).fee,0)
                fixture.cycle(w)
                self.assertEqual(w.resources.balance(Q,'native:gold'),before-100)
                self.assertEqual(w.resources.balance(C,'native:gold'),0)
                self.assertEqual(w.diplomacy.alliance(Q) is not None,action=='accept_proposal')

    def test_zero_cash_representative_can_respond_to_white_peace(self):
        fixture=fixtures.TestWorldDomains()
        for action in ('accept_peace','reject_peace'):
            with self.subTest(action=action):
                w=fixture.world();fixture.declare(w)
                fixture.call(w,Q,'offer_peace',target=C,kind='white');fixture.cycle(w)
                offer=w.diplomacy.snapshot(C).offers[0];self.zero_cash(w,C)
                self.assertEqual(fixture.call(w,C,action,offer=offer.id).fee,0)
                fixture.cycle(w)
                self.assertEqual(w.resources.balance(C,'native:gold'),0)
                self.assertEqual(w.diplomacy.war_between(Q,C),action=='reject_peace')

    def test_unaffordable_founding_does_not_reserve_cash_or_publish(self):
        fixture=fixtures.TestWorldDomains();w=fixture.world();self.zero_cash(w,Q)
        w.resources.exchange(Q,{}, {'native:gold':99})
        before=fixture.save(w)
        with self.assertRaises(ValueError):fixture.call(w,Q,'bloc_found',bloc=fixtures.B,name='合纵',tos=[C])
        self.assertEqual(fixture.save(w),before)


if __name__=='__main__':unittest.main()
