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
        if action == 'declare_war':args.setdefault('confirm',True)
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
                    self.assertTrue(h.player(C).call_api('native:diplomacy','snapshot')['offers'])
                    self.assertTrue(any(c['action'].startswith('reject_') for c in h.player(C).call_api('native:diplomacy','snapshot')['pending']))
                    self.assertEqual(h.world.resources.balance(C,'native:gold'),before)
                    rendered=h.ui.console.action(h.clock(),C,'end_turn',{},result,{C:'楚'})
                    self.assertIn('免费自动拒绝',rendered)
                    h.cycle()
                    self.assertFalse(h.player(C).call_api('native:diplomacy','snapshot')['offers'])
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

    def test_secret_ballots_hide_other_votes_in_queries_and_api_receipts_until_tally(self):
        for experimental in (False,True):
            with self.subTest(experimental=experimental):
                h=self.host(experimental);self.bloc(h)
                self.submit(h,Q,'guarantee',target=C);h.cycle()
                snapshot=lambda actor:h.player(actor).call_api('native:diplomacy','snapshot')
                vote=snapshot(Q)['votes'][0]['id']
                before=snapshot(Z)['votes']
                receipt=self.submit(h,Q,'vote',vote=vote,accept=True)
                self.assertEqual(dict(receipt['data']['ballots']),{Q:True})
                self.assertEqual(snapshot(Z)['votes'],before)
                receipt=h.player(Z).call_api('native:diplomacy','vote',vote=vote,accept=None)
                self.assertEqual(dict(receipt['data']['ballots']),{Z:None})
                self.assertEqual(dict(snapshot(Q)['votes'][0]['ballots']),{Q:True})
                self.assertEqual(dict(snapshot(Z)['votes'][0]['ballots']),{Z:None})
                # Changing a sealed ballot must not alter a peer's view either.
                peer=snapshot(Z)['votes']
                self.submit(h,Q,'vote',vote=vote,accept=False)
                self.assertEqual(snapshot(Z)['votes'],peer)
                self.assertFalse(snapshot(C)['votes'])
                for actor in (Q,Z,C):
                    self.assertFalse([e for e in h.player(actor).query('news') if (e.get('details') or {}).get('type')=='alliance_vote'])
                # Domain serialization retains real ballots while snapshots remain private.
                saved=h.world.diplomacy.to_save()
                h.world.diplomacy.restore(saved)
                self.assertEqual(dict(snapshot(Z)['votes'][0]['ballots']),{Z:None})
                self.assertEqual(dict(snapshot(Q)['votes'][0]['ballots']),{Q:False})
                self.end(h,Q);self.end(h,Z);h.cycle()
                for actor in (Q,Z):
                    events=[e for e in h.player(actor).query('news') if (e.get('details') or {}).get('type')=='alliance_vote']
                    self.assertEqual(len(events),1)
                    result=events[0]['details']
                    self.assertEqual(result['ballots'],{Q:False,Z:None})
                    self.assertEqual((result['yes'],result['no'],result['abstain'],result['status']),(0,1,1,'rejected'))
                    rendered=h.ui.console.event(events[0],{Q:'秦',Z:'赵'})
                    self.assertIn('秦：反对',rendered);self.assertIn('赵：弃权',rendered)
                    self.assertFalse(snapshot(actor)['votes'])
                self.assertFalse([e for e in h.player(C).query('news') if (e.get('details') or {}).get('type')=='alliance_vote'])
                with TemporaryDirectory() as folder:
                    path=Path(folder)/'opened.json';h.save(path)
                    restored=GameHost.load(path,h.cfg)
                    for actor in (Q,Z,C):
                        self.assertEqual(restored.player(actor).query('news'),h.player(actor).query('news'))

    def test_internal_declaration_does_not_warn_target_until_war_is_effective(self):
        for passed in (False,True):
            with self.subTest(passed=passed):
                h=self.host();self.bloc(h)
                self.submit(h,Q,'declare_war',target=C);h.cycle()
                snapshot=lambda actor:h.player(actor).call_api('native:diplomacy','snapshot')
                vote=snapshot(Q)['votes'][0]['id']
                self.assertTrue(any(r['message']=='Vote opened; treaty pending' for r in snapshot(Z)['results']))
                def target_not_warned():
                    self.assertFalse(snapshot(C)['votes']);self.assertFalse(snapshot(C)['wars'])
                    self.assertFalse([r for r in snapshot(C)['results'] if r['action'] in ('declare_war','vote:declare_war')])
                    self.assertFalse([e for e in h.player(C).query('news')
                        if (e.get('details') or {}).get('action') in ('declare_war','vote:declare_war')])
                target_not_warned()
                self.submit(h,Q,'vote',vote=vote,accept=True)
                self.submit(h,Z,'vote',vote=vote,accept=passed)
                target_not_warned();h.cycle()
                if passed:
                    self.assertTrue(snapshot(C)['wars'])
                    events=[e for e in h.player(C).query('news') if (e.get('details') or {}).get('action')=='declare_war']
                    self.assertEqual(len(events),1)
                    self.assertEqual(events[0]['details']['message'],'Diplomatic action applied')
                else:target_not_warned()

    def test_rejected_external_admission_gets_a_reply_without_internal_ballots(self):
        h=self.host();self.bloc(h)
        self.submit(h,C,'bloc_join',bloc='scenario:union');h.cycle()
        vote=h.player(Q).call_api('native:diplomacy','snapshot')['votes'][0]['id']
        for actor in (Q,Z):self.submit(h,actor,'vote',vote=vote,accept=False)
        h.cycle()
        results=h.player(C).call_api('native:diplomacy','snapshot')['results']
        self.assertTrue(any(r['action']=='bloc_join' and '拒绝' in r['message'] for r in results))
        news=h.player(C).query('news')
        self.assertTrue(any('拒绝' in e['text'] for e in news))
        self.assertFalse([e for e in news if (e.get('details') or {}).get('type')=='alliance_vote'])
        self.assertIsNone(h.world.diplomacy.alliance(C))

    def test_rejected_received_treaty_closes_offer_and_replies_to_foreign_entity(self):
        for peace in (False,True):
            with self.subTest(peace=peace):
                h=self.host();self.bloc(h)
                if peace:
                    self.submit(h,C,'declare_war',target=Q);h.cycle()
                    self.submit(h,C,'offer_peace',target=Q,kind='white')
                else:self.submit(h,C,'propose_defense',target=Q)
                h.cycle()
                offer=h.player(Q).call_api('native:diplomacy','snapshot')['offers'][0]['id']
                self.submit(h,Q,'accept_peace' if peace else 'accept_proposal',offer=offer);h.cycle()
                vote=h.player(Q).call_api('native:diplomacy','snapshot')['votes'][0]['id']
                for actor in (Q,Z):self.submit(h,actor,'vote',vote=vote,accept=False)
                h.cycle()
                self.assertFalse(h.player(Q).call_api('native:diplomacy','snapshot')['offers'])
                results=h.player(C).call_api('native:diplomacy','snapshot')['results']
                self.assertTrue(any(r['action']==('reject_peace' if peace else 'reject_proposal')
                                    and r['message']=='Offer rejected' for r in results))
                news=h.player(C).query('news')
                self.assertTrue(any((e.get('details') or {}).get('message')=='Offer rejected' for e in news))
                self.assertFalse([e for e in news if (e.get('details') or {}).get('type')=='alliance_vote'])
                self.assertEqual(bool(h.world.diplomacy.snapshot(Q).wars),peace)

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
                events=[e for e in h.player(Q).query('news') if (e.get('details') or {}).get('type')=='alliance_vote']
                self.assertEqual(len(events),1)
                result=events[0]['details']
                self.assertEqual(result['ballots'],{Q:True,Z:None if abstain else False})
                self.assertEqual(result['status'],'passed' if abstain else 'rejected')
                self.assertEqual((result['yes'],result['no'],result['abstain']),(1,0,1) if abstain else (1,1,0))

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
