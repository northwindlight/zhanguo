"""Presentation uses actual purchases and delivered letters, never inferred prose."""
from copy import deepcopy
import unittest

from scenarios.eight_nations import build
from mods.ui.desktop_activity import TimelineCapture,difference
from tests.test_desktop_activity import replay


class TestDesktopExchanges(unittest.TestCase):
    def prepared(self):
        host=build(seed=73);actor=host.actors()[0]
        self.assertTrue(host.player(actor).submit('plan',{'text':'本期核对库存、按实际成本交易并开展通信。'})['ok'])
        return host,actor

    def test_market_stage_uses_real_goods_and_cash_changes(self):
        host,actor=self.prepared();capture=TimelineCapture(host)
        for side,quantity in [('buy',2),('sell',1)]:
            before={r['id']:r['amount'] for a in capture.scene()['accounts'] if a['id']==actor for r in a['stocks']}
            args={'action':side,'resource':'native:wood','quantity':quantity}
            receipt=host.player(actor).submit('economy',args);self.assertTrue(receipt['ok'],receipt)
            capture.capture(actor,'economy',args,receipt)
            beat=next(b for b in reversed(capture.beats) if b['phase']=='trade')
            after={r['id']:r['amount'] for a in capture.scene()['accounts'] if a['id']==actor for r in a['stocks']}
            self.assertEqual(beat['market_actor'],actor)
            self.assertEqual(beat['trade']['goods'],[{'resource':'native:wood','amount':quantity*(1 if side=='buy' else -1)}])
            self.assertEqual(beat['trade']['gold'],after['native:gold']-before['native:gold'])
        before=len(capture.beats);args={'action':'buy','resource':'unknown:missing','quantity':1}
        rejected=host.player(actor).submit('economy',args);self.assertFalse(rejected['ok'])
        capture.capture(actor,'economy',args,rejected)
        self.assertEqual(capture.beats[before]['phase'],'rejected');self.assertEqual(capture.beats[before]['market_actor'],actor)
        self.assertNotIn('trade',capture.beats[before])

    def test_letter_plays_only_after_actual_delivery_and_never_twice(self):
        host,actor=self.prepared();target=host.actors()[1];capture=TimelineCapture(host);initial=deepcopy(capture.previous)
        args={'action':'send_letter','target':target,'text':'实际送达以后才展示这封信。'}
        receipt=host.player(actor).submit('intelligence',args);self.assertTrue(receipt['ok'],receipt)
        capture.capture(actor,'intelligence',args,receipt)
        actions=capture.drain();self.assertFalse(any(b['phase']=='letter' for b in actions));self.assertFalse(host.records()['messages'])
        host.world.resolve_turn();settlement=capture.finish();self.assertIsNone(capture.warning)
        letters=[b for b in settlement if b['phase']=='letter'];self.assertEqual(len(letters),1)
        delivered=next(m for m in host.records()['messages'] if m['kind']=='letter')
        self.assertEqual(letters[0]['letter']['id'],delivered['id']);self.assertEqual(letters[0]['text'],delivered['payload']['text'])
        self.assertEqual(letters[0]['letter']['source'],actor);self.assertEqual(letters[0]['letter']['target'],target)
        self.assertEqual(letters[0]['playback_scope'],'settlement')
        self.assertFalse(difference(replay(initial,actions+settlement),capture.scene()))
        again=TimelineCapture(host);host.world.resolve_turn();self.assertFalse(any(b['phase']=='letter' for b in again.finish()))


if __name__=='__main__':unittest.main()
