"""议和赔款预扣：付款方在提交时扣下赔款，回合末外交结算直接划转；拒绝、作废或失败原数退回。"""
import unittest

from tests import test_world_domains as fixtures
Q,C,Z=fixtures.Q,fixtures.C,fixtures.Z
GOLD='native:gold'


class TestDiplomacyEscrow(unittest.TestCase):
    def setUp(self):self.f=fixtures.TestWorldDomains()
    def cash(self,w,actor):return w.resources.balance(actor,GOLD)

    def offer(self,w,kind,gold):
        before=self.cash(w,Q)
        command=self.f.call(w,Q,'offer_peace',target=C,kind=kind,gold=gold)
        return before,command

    def test_pay_offer_reserves_indemnity_at_submission(self):
        w=self.f.world();self.f.declare(w)
        before,command=self.offer(w,'pay',100)
        self.assertEqual(command.escrow,100)
        self.assertEqual(self.cash(w,Q),before-command.fee-100)
        self.f.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        self.assertEqual(offer.escrow,100);self.assertEqual(self.cash(w,Q),before-command.fee-100)

    def test_accepted_pay_offer_settles_from_escrow_even_if_payer_is_broke(self):
        w=self.f.world();self.f.declare(w)
        before,command=self.offer(w,'pay',100);self.f.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        w.resources.exchange(Q,{GOLD:self.cash(w,Q)},{})          # 求和方之后花光现金：赔款已预扣，不受影响
        cash_c=self.cash(w,C)
        self.f.call(w,C,'accept_peace',offer=offer.id);result=self.f.cycle(w)['diplomacy_results'][0]
        self.assertEqual(result.status,'effective')
        self.assertEqual(self.cash(w,C),cash_c+100);self.assertEqual(self.cash(w,Q),0)
        self.assertFalse(w.diplomacy.war_between(Q,C))

    def test_rejected_pay_offer_refunds_indemnity_but_not_fee(self):
        w=self.f.world();self.f.declare(w)
        before,command=self.offer(w,'pay',100);self.f.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        self.f.call(w,C,'reject_peace',offer=offer.id);self.f.cycle(w)
        self.assertEqual(self.cash(w,Q),before-command.fee)
        self.assertTrue(w.diplomacy.war_between(Q,C));self.assertFalse(w.diplomacy.snapshot(C).offers)

    def test_unanswered_pay_offer_is_auto_rejected_and_refunded(self):
        w=self.f.world();self.f.declare(w)
        before,command=self.offer(w,'pay',100);self.f.cycle(w)
        w.runtime.object_call(w,'native:diplomacy','finish',C)     # 收件国行动结束仍未回应 → 自动拒绝（回合末收尾）
        self.f.cycle(w)
        self.assertFalse(w.diplomacy.snapshot(C).offers)
        self.assertEqual(self.cash(w,Q),before-command.fee)

    def test_unaffordable_pay_offer_is_rejected_at_submission(self):
        w=self.f.world();self.f.declare(w);before=self.f.save(w)
        with self.assertRaises(ValueError):self.f.call(w,Q,'offer_peace',target=C,kind='pay',gold=10**7)
        self.assertEqual(self.f.save(w),before)

    def test_demand_acceptance_reserves_at_submission_and_settles(self):
        w=self.f.world();self.f.declare(w)
        self.f.call(w,Q,'offer_peace',target=C,kind='demand',gold=50);self.f.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0];cash_c,cash_q=self.cash(w,C),self.cash(w,Q)
        command=self.f.call(w,C,'accept_peace',offer=offer.id)
        self.assertEqual((command.fee,command.escrow),(0,50));self.assertEqual(self.cash(w,C),cash_c-50)
        w.resources.exchange(C,{GOLD:self.cash(w,C)},{})          # 接受方之后花光现金：赔款已预扣
        result=self.f.cycle(w)['diplomacy_results'][0]
        self.assertEqual(result.status,'effective');self.assertEqual(self.cash(w,Q),cash_q+50)
        self.assertFalse(w.diplomacy.war_between(Q,C))

    def test_failed_demand_acceptance_refunds_escrow(self):
        # 楚向秦索赔 50，秦接受（预扣 50）；同期秦也向楚求白和、楚先接受（同优先级按国家键排序，楚在前）→ 战争已结束，秦的接受失败并退回
        w=self.f.world();self.f.declare(w)
        self.f.call(w,C,'offer_peace',target=Q,kind='demand',gold=50)
        self.f.call(w,Q,'offer_peace',target=C,kind='white');self.f.cycle(w)
        demand=next(o for o in w.diplomacy.snapshot(Q).offers if o.actor==C)
        white=next(o for o in w.diplomacy.snapshot(C).offers if o.actor==Q)
        cash_q=self.cash(w,Q)
        command=self.f.call(w,Q,'accept_peace',offer=demand.id);self.assertEqual(command.escrow,50)
        self.assertEqual(self.cash(w,Q),cash_q-50)
        self.f.call(w,C,'accept_peace',offer=white.id)
        results={r.actor:r.status for r in self.f.cycle(w)['diplomacy_results'] if r.action=='accept_peace'}
        self.assertEqual(results,{C:'effective',Q:'failed'})
        self.assertEqual(self.cash(w,Q),cash_q);self.assertFalse(w.diplomacy.war_between(Q,C))

    def test_other_escrowed_offer_in_same_war_is_refunded_when_war_ends(self):
        w=self.f.world();self.f.declare(w)
        before_q,cq=self.offer(w,'pay',100)
        before_c=self.cash(w,C);cc=self.f.call(w,C,'offer_peace',target=Q,kind='pay',gold=60)
        self.f.cycle(w)
        mine=next(o for o in w.diplomacy.snapshot(C).offers if o.actor==Q)
        self.f.call(w,C,'accept_peace',offer=mine.id);self.f.cycle(w)
        self.assertFalse(w.diplomacy.war_between(Q,C))
        self.assertEqual(self.cash(w,Q),before_q-cq.fee-100)
        self.assertEqual(self.cash(w,C),before_c-cc.fee+100)      # C 自己挂的 60 原数退回，再收到 Q 的 100

    def test_escrow_survives_save_and_restore(self):
        w=self.f.world();self.f.declare(w)
        self.offer(w,'pay',100)
        data=self.f.save(w)
        self.assertEqual(data['diplomacy']['pending'][0]['escrow'],100) if 'diplomacy' in data else None
        w2=self.f.world();w2.diplomacy.restore(w.diplomacy.to_save())
        self.assertEqual(w2.diplomacy._pending[0].escrow,100)
        self.f.cycle(w)
        w3=self.f.world();w3.diplomacy.restore(w.diplomacy.to_save())
        self.assertEqual(w3.diplomacy._offers[0].escrow,100)
        plain=self.f.world();self.assertNotIn('escrow',str(plain.diplomacy.to_save()))


if __name__=='__main__':unittest.main()
