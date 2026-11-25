# -*- coding: utf-8 -*-
"""离线测试用的假端点：把 `fake(url, headers, body, timeout)` 接到官方 SDK 的 httpx2 传输层。

提供方测试照旧只写"端点收到什么、回什么"：`fake` 返回一个可逐行迭代（流式 SSE 行）
或可 `read()`（非流式 JSON）的对象，抛 `EndpointError(status_code=…)` 表示 HTTP 错误，
抛 `TimeoutError` 表示读超时。请求经过真实的 SDK：URL 拼接、鉴权头、SSE 分帧与 JSON
解析都是 SDK 自己做的，测的是生产路径而不是另一套假实现。
"""
from __future__ import annotations

import contextlib
import json

import httpx2

from clients import provider


def _lines(resp):
    for line in resp:
        raw = line.encode("utf-8") if isinstance(line, str) else line
        yield raw.rstrip(b"\r\n") + b"\n"


@contextlib.contextmanager
def fake_endpoint(fake):
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content or b"{}")
        headers = {k.lower(): v for k, v in request.headers.items()}
        timeout = (request.extensions.get("timeout") or {}).get("read")
        try:
            resp = fake(str(request.url), headers, body, timeout)
        except provider.EndpointError as exc:
            if exc.status_code:
                return httpx2.Response(exc.status_code, text=str(exc), request=request)
            raise httpx2.ReadError(str(exc), request=request) from exc
        except TimeoutError as exc:
            raise httpx2.ReadTimeout(str(exc), request=request) from exc
        if body.get("stream"):
            return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                   content=_lines(resp), request=request)
        return httpx2.Response(200, headers={"content-type": "application/json"},
                               content=resp.read(), request=request)

    previous = provider._http_transport
    provider._http_transport = httpx2.MockTransport(handler)
    try:
        yield
    finally:
        provider._http_transport = previous
