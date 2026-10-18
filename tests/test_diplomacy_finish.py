"""Turn completion handles diplomatic silence through both distributions' APIs."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
Q,C,Z='scenario:qin','scenario:chu','scenario:zhao'


class TestDiplomacyFinish(unittest.TestCase):
    def host(self,experimental=False):
        cfg={'map_size':12,'seed':73,'mods':'mods.experimental.toml' if experimental else 'mods.toml',
             'nations':[{'id':actor,'name':name,'client':'rule','position':position}
                for actor,name,position in ((Q,'秦',[2,2]),(C,'楚',[8,2]),(Z,'赵',[2,8]))]}
        h=GameHost.new(cfg)
        for actor,client in h.clients.items():
            client.play=lambda player,**kwargs:[]
            self.assertTrue(h.player(actor).submit('plan',{'text':'依据实际生产经营国家'})['ok'])
        return h

    def submit(self,h,actor,action,**args):
        umbrella={'guarantee':'relations','cancel_guarantee':'relations','propose_defense':'relations',
                  'break_defense':'relations','accept_proposal':'relations','reject_proposal':'relations',
                  'bloc_found':'relations','bloc_join':'relations','bloc_leave':'relations',
                  'bloc_dissolve':'relations','bloc_transfer':'relations','bloc_rename':'relations',
                  'declare_war':'relations','offer_peace':'relations','accept_peace':'relations',
                  'reject_peace':'relations','vote':'relations'}.get(action)
        result=(h.player(actor).submit(umbrella,{'action':action,**args}) if umbrella
                else h.player(actor).submit(action,args))
        self.assertTrue(result['ok'],result)
        return result

    def end(self,h,actor):return self.submit(h,actor,'end_turn',summary='完成本期实际经营')

    def offer(self,h,kind='defense',tos=None):
        if kind=='found':self.submit(h,Q,'bloc_found',bloc='scenario:union',name='合纵',tos=tos or [C,Z])
        elif kind=='peace':
            self.submit(h,Q,'declare_war',target=C);h.cycle()
            self.submit(h,Q,'offer_peace',target=C,kind='white')
        else:self.submit(h,Q,'propose_defense',target=C)
        h.cycle()
        return h.player(C).call_api('native:diplomacy','snapshot')['offers'][0]['id']

    def test_unanswered_received_offers_reject_at_successful_end_in_both_distributions(self):
        for experimental in (False,True):
            for kind in ('defense','found','peace'):
                with self.subTest(experimental=experimental,kind=kind):
                    h=self.host(experimental);offer=self.offer(h,kind)
                    before=h.world.resources.balance(C,'native:gold')
                    result=self.end(h,C)
                    self.assertEqual(result['data']['auto_rejected'],{'offers':[offer],'votes':[]})
                    self.assertFalse(h.player(C).call_api('native:diplomacy','snapshot')['offers'])
                    self.assertEqual(h.world.resources.balance(C,'native:gold'),before)
                    rendered=h.ui.console.action(h.clock(),C,'end_turn',{},result,{C:'楚'})
                    self.assertIn('免费自动拒绝',rendered)
                    h.cycle()
                    self.assertTrue(any('自动拒绝' in e['text'] for e in h.player(Q).events()['items']))
                    if kind=='peace':self.assertTrue(h.world.diplomacy.war_between(Q,C))
                    if kind=='found':
                        for actor in (Q,C,Z):
                            self.assertTrue(any('创建失败' in e['text'] and e.get('details',{}).get('importance')=='major'
                                for e in h.player(actor).events()['items']))

    def test_queued_explicit_replies_are_preserved(self):
        for kind in ('defense','found','peace'):
            for accept in (False,True):
                with self.subTest(kind=kind,accept=accept):
                    h=self.host();offer=self.offer(h,kind,tos=[C])
                    action=('accept_' if accept else 'reject_')+('peace' if kind=='peace' else 'proposal')
                    self.submit(h,C,action,offer=offer)
                    self.assertNotIn('auto_rejected',self.end(h,C)['data'])
                    h.cycle()
                    if kind=='peace':self.assertEqual(h.world.diplomacy.war_between(Q,C),not accept)
                    elif kind=='found':self.assertEqual(h.world.diplomacy.alliance(Q) is not None,accept)
                    else:self.assertEqual(bool(h.player(C).call_api('native:diplomacy','snapshot')['pacts']),accept)

    def test_previous_founding_acceptance_needs_no_repeat_reply(self):
        h=self.host();offer=self.offer(h,'found')
        self.submit(h,C,'accept_proposal',offer=offer);self.end(h,C);h.cycle()
        self.assertEqual(h.player(C).call_api('native:diplomacy','snapshot')['offers'][0]['accepted'],[C])
        self.assertNotIn('auto_rejected',self.end(h,C)['data'])
        self.assertEqual(h.player(C).call_api('native:diplomacy','snapshot')['offers'][0]['id'],offer)
        self.assertEqual(self.end(h,Z)['data']['auto_rejected']['offers'],[offer])

    def test_outgoing_and_newly_published_offers_wait_for_recipient_next_turn(self):
        h=self.host();self.end(h,C)
        self.submit(h,Q,'propose_defense',target=C)
        self.assertNotIn('auto_rejected',self.end(h,Q)['data'])
        h.cycle();offer=h.player(C).call_api('native:diplomacy','snapshot')['offers'][0]['id']
        self.assertNotIn('auto_rejected',self.end(h,Q)['data'])
        self.assertEqual(self.end(h,C)['data']['auto_rejected']['offers'],[offer])

    def test_failed_end_turn_does_not_reject_and_second_end_is_idempotent(self):
        h=self.host();offer=self.offer(h)
        self.assertFalse(h.player(C).submit('end_turn',{'summary':'短'})['ok'])
        self.assertEqual(h.player(C).call_api('native:diplomacy','snapshot')['offers'][0]['id'],offer)
        self.end(h,C)
        results=len(h.player(C).call_api('native:diplomacy','snapshot')['results'])
        self.assertFalse(h.player(C).submit('end_turn',{'summary':'重复结束不得再拒绝'})['ok'])
        self.assertEqual(len(h.player(C).call_api('native:diplomacy','snapshot')['results']),results)

    def bloc(self,h):
        self.submit(h,Q,'bloc_found',bloc='scenario:union',name='合纵',tos=[Z]);h.cycle()
        offer=h.player(Z).call_api('native:diplomacy','snapshot')['offers'][0]['id']
        self.submit(h,Z,'accept_proposal',offer=offer);h.cycle()

    def test_unanswered_votes_default_no_but_explicit_abstention_is_preserved(self):
        for abstain in (False,True):
            with self.subTest(abstain=abstain):
                h=self.host();self.bloc(h)
                self.submit(h,Q,'guarantee',target=C);self.end(h,Q);h.cycle()
                vote=h.player(Q).call_api('native:diplomacy','snapshot')['votes'][0]['id']
                self.submit(h,Q,'vote',vote=vote,accept=True);self.end(h,Q)
                if abstain:self.submit(h,Z,'vote',vote=vote,accept=None)
                result=self.end(h,Z)
                if abstain:self.assertNotIn('auto_rejected',result['data'])
                else:self.assertEqual(result['data']['auto_rejected']['votes'],[vote])
                h.cycle()
                self.assertEqual(bool(h.player(Q).call_api('native:diplomacy','snapshot')['pacts']),abstain)

    def test_alliance_external_offer_is_responsibility_of_representative(self):
        h=self.host();self.bloc(h)
        self.submit(h,C,'propose_defense',target=Q);h.cycle()
        offer=h.player(Q).call_api('native:diplomacy','snapshot')['offers'][0]['id']
        self.assertNotIn('auto_rejected',self.end(h,Z)['data'])
        self.assertEqual(self.end(h,Q)['data']['auto_rejected']['offers'],[offer])

    def test_alliance_response_waiting_for_internal_vote_is_not_auto_rejected(self):
        h=self.host();self.bloc(h)
        self.submit(h,C,'propose_defense',target=Q);h.cycle()
        offer=h.player(Q).call_api('native:diplomacy','snapshot')['offers'][0]['id']
        self.submit(h,Z,'accept_proposal',offer=offer)
        self.assertNotIn('auto_rejected',self.end(h,Q)['data']);self.end(h,Z);h.cycle()
        vote=h.player(Q).call_api('native:diplomacy','snapshot')['votes'][0]['id']
        for actor in (Q,Z):self.submit(h,actor,'vote',vote=vote,accept=True)
        self.assertNotIn('auto_rejected',self.end(h,Q)['data']);self.end(h,Z);h.cycle()
        self.assertFalse(h.player(Q).call_api('native:diplomacy','snapshot')['offers'])
        self.assertTrue(h.player(Q).call_api('native:diplomacy','snapshot')['pacts'])

    def test_automatic_reply_results_and_notices_survive_save_reload(self):
        h=self.host();self.offer(h);self.end(h,C);h.cycle()
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.json';h.save(path)
            restored=GameHost.load(path,h.cfg)
            self.assertEqual(restored.to_save(),h.to_save())
            self.assertTrue(any('自动拒绝' in e['text'] for e in restored.player(Q).events()['items']))


if __name__=='__main__':unittest.main()
