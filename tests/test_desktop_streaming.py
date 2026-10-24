"""Offline transport-to-view checks: no API keys, network or real games."""
import json
from pathlib import Path
import tempfile
import unittest

from clients import provider
from host import GameHost
from ui.desktop import Controller
from tests.test_llm_provider import _FakeResp
from tests.test_llm_provider_anthropic import _Transport, _cfg, _msg_start, _thinking_block, _text_block, _stop


class TestLiveDialogue(unittest.TestCase):
    def test_openai_deltas_arrive_before_stream_exhaustion_and_exclude_tools(self):
        seen=[]
        def lines():
            def data(chunk):
                return 'data: '+json.dumps(chunk,ensure_ascii=False)
            yield data({'choices':[{'delta':{'reasoning_content':'先看粮仓。'}}]}); yield ''
            self.assertEqual(seen[-1],('thinking','先看粮仓。'))
            yield data({'choices':[{'delta':{'content':'诸位，'}}]}); yield ''
            self.assertEqual(seen[-1],('body','诸位，'))
            tool={'index':0,'id':'a','type':'function','function':{'name':'build','arguments':'{"x":2}'}}
            yield data({'choices':[{'delta':{'tool_calls':[tool]}}]}); yield ''
            yield data({'choices':[{'delta':{'content':'先修粮道。'}}]}); yield ''
            yield 'data: [DONE]'; yield ''
        orig=provider._post_json
        provider._post_json=lambda *a,**k:_FakeResp(lines=lines())
        try:
            backend=provider.OpenAICompat({'base_url':'http://offline','api_key':'unused'})
            msg,_=backend.chat_turn([],[],{'base_url':'http://offline','api_key':'unused','model':'offline','api_retries':1},on_delta=lambda *args:seen.append(args))
        finally:
            provider._post_json=orig
        self.assertEqual(seen,[('reset',''),('thinking','先看粮仓。'),('body','诸位，'),('body','先修粮道。')])
        self.assertEqual(msg['content'],'诸位，先修粮道。')
        self.assertEqual(msg['tool_calls'][0]['function']['arguments'],'{"x":2}')

    def test_retry_resets_the_aborted_attempt(self):
        seen=[];attempts=[]
        backend=provider.OpenAICompat({'base_url':'http://offline','api_key':'unused'})
        def once(*args,on_delta=None):
            attempts.append(1)
            provider._stream_notify(on_delta,'body','残片' if len(attempts)==1 else '最终正文')
            if len(attempts)==1:raise TimeoutError('offline interruption')
            return {'content':'最终正文'},{}
        backend._stream_once=once
        backend.chat_turn([],[],{'api_retries':2,'api_retry_wait':0},on_delta=lambda *args:seen.append(args))
        self.assertEqual(seen,[('reset',''),('body','残片'),('reset',''),('body','最终正文')])

    def test_anthropic_text_and_thought_exclude_signature_and_tool_input(self):
        seen=[]
        events=[_msg_start(),*_thinking_block(0,'实际思考','SECRET-SIGNATURE'),*_text_block(1,'实际发言'),*_stop('end_turn')]
        with _Transport(events):
            msg,_=provider.AnthropicCompat(_cfg()).chat_turn([],None,_cfg(),on_delta=lambda *args:seen.append(args))
        self.assertEqual(''.join(t for k,t in seen if k=='thinking'),msg['reasoning_content'])
        self.assertEqual(''.join(t for k,t in seen if k=='body'),msg['content'])
        self.assertNotIn('SECRET-SIGNATURE',str(seen))

    def test_display_callback_failure_does_not_break_the_request(self):
        def broken(*args):raise RuntimeError('display only')
        with _Transport([_msg_start(),*_text_block(0,'完整正文'),*_stop('end_turn')]):
            msg,_=provider.AnthropicCompat(_cfg()).chat_turn([],None,_cfg(),on_delta=broken)
        self.assertEqual(msg['content'],'完整正文')

    def test_session_controller_streams_real_model_text_once_and_summary_separately(self):
        cfg={'mods':'mods.toml','map_size':12,'seed':3,
             'nations':[{'id':'scenario:qin','name':'秦','client':'llm','model':'DeepSeek-Offline'}]}
        host=GameHost.new(cfg);rows=[]
        class Backend:
            def chat_turn(self,messages,tools,cfg,on_retry=None,on_delta=None):
                on_delta('reset','');on_delta('thinking','先核实');on_delta('thinking','库存。')
                on_delta('body','本期休整。')
                # Deltas must already have been sent while chat_turn is running.
                self_check=[r for r in rows if r['type']=='stream' and r.get('event')=='delta']
                if not self_check:raise AssertionError('buffered until whole response')
                return {'content':'本期休整。','reasoning_content':'先核实库存。','tool_calls':[
                    {'id':'plan','type':'function','function':{'name':'plan','arguments':json.dumps({'text':'本期核实粮食库存并休整，下期继续核对补给。'})}},
                    {'id':'end','type':'function','function':{'name':'end_turn','arguments':json.dumps({'summary':'完成休整。'})}}]}, {'out_tokens':12,'wall':.1}
        host.clients['scenario:qin']._backend=Backend()
        with tempfile.TemporaryDirectory() as d:
            control=Controller(host,Path(d)/'session.json',rows.append,playback=True)
            control.advance()
        streams=[r for r in rows if r['type']=='stream']
        self.assertEqual(sum(r.get('event')=='complete' for r in streams),1)
        self.assertFalse(any(r.get('event')=='message' for r in streams))
        complete=next(r for r in streams if r.get('event')=='complete')
        self.assertEqual(complete['thinking'],'先核实库存。')
        self.assertEqual(complete['body'],'本期休整。')
        self.assertEqual(next(r['text'] for r in streams if r.get('event')=='summary'),'完成休整。')
        self.assertEqual(rows[-2]['frame']['clients'][0]['model'],'DeepSeek-Offline')
        self.assertEqual(host.clock(),1)
        beats=next(r for r in rows if r['type']=='timeline')['beats']
        self.assertTrue(all(b['client_kind']=='llm' and b['playback_scope']=='actor' for b in beats if b['actor']))
        self.assertTrue(all(b['playback_scope']=='settlement' for b in beats if not b['actor']))
