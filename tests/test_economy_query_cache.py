"""Only read-only requests are cached, only within one encoding scope."""
import os
import unittest
from unittest.mock import patch

from rl.economy.query_cache import memo_read_queries, query_key
from tests.test_economy_bc import cfg, torch


QUOTE = dict(resource='native:wood', quantity=3, side='buy')


class Player:
    def __init__(self):
        self.calls=[]

    def call_api(self, capability=None, method=None, arguments=None, *args, **kwargs):
        self.calls.append((capability, method, arguments, args, kwargs))
        return {'gold':len(self.calls), 'nested':{'items':[1]}}


class QueryCacheChecks(unittest.TestCase):
    def test_repeated_quotes_and_catalogs_use_one_read_and_are_detached(self):
        p=Player()
        with memo_read_queries(p):
            first=p.call_api('economy:queries','trade_quote',QUOTE)
            first['nested']['items'].append(2)
            second=p.call_api('economy:queries','trade_quote',dict(QUOTE))
            self.assertEqual(second['nested']['items'],[1])
            second['nested']['items'].append(3)
            self.assertEqual(p.call_api('economy:queries','trade_quote',QUOTE)['nested']['items'],[1])
            p.call_api('native:army','unit_catalog',{})
            p.call_api('native:army','unit_catalog')
        self.assertEqual(len(p.calls),2)
        self.assertNotIn('call_api',vars(p))

    def test_other_requests_invalidate_and_are_never_deduplicated(self):
        p=Player()
        with memo_read_queries(p):
            p.call_api('economy:queries','trade_quote',QUOTE)
            for _ in range(2):p.call_api('economy:queries','buy',QUOTE)
            self.assertEqual(p.call_api('economy:queries','trade_quote',QUOTE)['gold'],4)
        self.assertEqual(len(p.calls),4)

    def test_distinct_quotes_and_invalid_arguments_cannot_collide(self):
        self.assertIsNone(query_key('economy:queries','trade_quote',dict(QUOTE,quantity=True)))
        self.assertIsNone(query_key('economy:queries','trade_quote',dict(QUOTE,extra=1)))
        self.assertIsNone(query_key('native:army','unit_catalog',{'other':1}))
        p=Player()
        with memo_read_queries(p):
            for args in (QUOTE,dict(QUOTE,quantity=4),dict(QUOTE,side='sell'),dict(QUOTE,resource='native:food')):
                p.call_api('economy:queries','trade_quote',args)
        self.assertEqual(len(p.calls),4)

    def test_nested_scope_exception_restores_existing_override(self):
        p=Player();original=p.call_api;p.call_api=original
        with self.assertRaisesRegex(RuntimeError,'stop'):
            with memo_read_queries(p):
                outer=p.call_api
                with memo_read_queries(p):pass
                self.assertIs(p.call_api,outer)
                raise RuntimeError('stop')
        self.assertIs(p.call_api,original)

    def test_disable_and_new_scopes_do_not_reuse_old_quotes(self):
        p=Player()
        with patch.dict(os.environ,{'ZHANGUO_ECON_QUERY_CACHE':'0'}),memo_read_queries(p):
            for _ in range(2):p.call_api('economy:queries','trade_quote',QUOTE)
        for _ in range(2):
            with memo_read_queries(p):p.call_api('economy:queries','trade_quote',QUOTE)
        self.assertEqual(len(p.calls),4)

    def test_keyword_overrides_and_nonstandard_args_bypass_cache(self):
        p=Player()
        with memo_read_queries(p):
            for _ in range(2):p.call_api('economy:queries','trade_quote',QUOTE,action_label='probe')
            for _ in range(2):p.call_api('economy:queries','trade_quote',dict(QUOTE,quantity=True))
        self.assertEqual(len(p.calls),4)


@unittest.skipIf(torch is None,'Optional training dependencies')
class TeacherCacheChecks(unittest.TestCase):
    def test_teacher_labels_and_world_are_unchanged(self):
        import numpy as np
        from host import GameHost
        from rl.economy.projects import RecordingProjects
        records=[];worlds=[]
        for enabled in ('0','1'):
            collected={}
            with patch.dict(os.environ,{'ZHANGUO_ECON_QUERY_CACHE':enabled}):
                host=GameHost.new(cfg())
                for actor,client in list(host.clients.items()):
                    collected[actor]=[]
                    host.clients[actor]=RecordingProjects(state=client.state,
                        sink=lambda row,a=actor:collected[a].append(row))
                for _ in range(4):host.cycle()
                worlds.append(host.runtime.save_mod_state(host.world))
                records.append(collected)
        self.assertEqual(worlds[0],worlds[1])
        self.assertEqual(records[0].keys(),records[1].keys())
        for actor in records[0]:
            self.assertEqual(len(records[0][actor]),len(records[1][actor]))
            for before,after in zip(records[0][actor],records[1][actor]):
                self.assertEqual(before.keys(),after.keys())
                for key in before:
                    if isinstance(before[key],np.ndarray):np.testing.assert_array_equal(before[key],after[key])
                    else:self.assertEqual(before[key],after[key])


if __name__=='__main__':unittest.main()
