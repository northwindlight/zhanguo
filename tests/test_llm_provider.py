# -*- coding: utf-8 -*-
"""LLM 提供方的**失败语义**：哪些该重试、哪些必须立刻抛（⇒ 上层终止本局）。

★ 2026-09-20：aliyun token-plan 的「Your token-plan 1-week quota has been exhausted」
是个 429（RateLimitError），被当成普通限流**重试**了——每次调用叠 3 次退避、每回合叠
max_steps 次、五国 × 14 个回合，白烧一整周额度（用户：「100 块钱额度白烧了」）。
配额耗尽/欠费这类错误重试一万次也不会好，只该立刻抛给上层去终止。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_provider  # noqa: E402

QUOTA_MSG = ("Error code: 429 - {'error': {'message': 'Your token-plan 1-week quota has been "
             "exhausted. The quota will reset at 09-25 18:11:00 UTC.', "
             "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}")
TRANSIENT_MSG = "Error code: 429 - Rate limit reached for requests. Please retry after 1s."


class _Fake429(Exception):
    """冒充 `openai.RateLimitError`：`chat_turn` 里是运行时 `from openai import RateLimitError`，
    所以把这个名字指过来，`except` 就能接住。"""


class TestPermanentErrors(unittest.TestCase):
    def test_fingerprints(self):
        self.assertTrue(llm_provider._is_permanent(_Fake429(QUOTA_MSG)))
        self.assertTrue(llm_provider._is_permanent(Exception("账户欠费，请充值")))
        self.assertFalse(llm_provider._is_permanent(_Fake429(TRANSIENT_MSG)),
                         "普通限流该重试，别误判成永久错误")

    def setUp(self):
        self.calls: list[int] = []
        import openai
        self._saved = getattr(openai, "RateLimitError", None)
        openai.RateLimitError = _Fake429
        self._orig_openai = openai.OpenAI
        openai.OpenAI = lambda **kw: types.SimpleNamespace()
        self.addCleanup(self._restore)

    def _restore(self):
        import openai
        openai.OpenAI = self._orig_openai
        if self._saved is not None:
            openai.RateLimitError = self._saved

    def _backend(self, msg: str, **cfg_extra):
        b = llm_provider.make_backend({"provider": "openai", "base_url": "http://stub",
                                       "api_key": "k", "model": "m",
                                       "api_retries": 3, "api_retry_wait": 0})
        def boom(*a, **k):
            self.calls.append(1)
            raise _Fake429(msg)
        b._stream_once = boom
        return b, {**{"api_retries": 3, "api_retry_wait": 0}, **cfg_extra}

    def test_quota_exhausted_is_not_retried(self):
        """★ 配额耗尽：**一次都不重试**，直接抛（省掉 3×退避 × max_steps 的无效烧钱）。"""
        b, cfg = self._backend(QUOTA_MSG)
        with self.assertRaises(_Fake429):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(self.calls), 1, "配额耗尽不该重试")

    def test_transient_rate_limit_is_retried(self):
        """普通限流照旧重试到上限——别为了修配额墙把瞬时抖动也一刀切了。"""
        b, cfg = self._backend(TRANSIENT_MSG)
        with self.assertRaises(_Fake429):
            b.chat_turn([], [], cfg)
        self.assertEqual(len(self.calls), 3, "瞬时错误该重试满 api_retries 次")


if __name__ == "__main__":
    unittest.main()
