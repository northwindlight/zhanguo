"""Decision-local memoization of formal read-only quotes and unit catalogs.

The caller must finish this scope before executing a project. Every other API
request conservatively invalidates the cache; returned values remain detached.
"""
from contextlib import contextmanager
from copy import deepcopy
import os


def query_key(capability, method, arguments):
    if arguments is None:
        arguments = {}
    if type(arguments) is not dict:
        return None
    if (capability, method) == ('native:army', 'unit_catalog') and not arguments:
        return capability, method
    if (capability, method) == ('economy:queries', 'trade_quote'):
        if set(arguments) != {'resource', 'quantity', 'side'}:
            return None
        resource, quantity, side = (arguments[k] for k in ('resource', 'quantity', 'side'))
        if type(resource) is str and type(quantity) is int and quantity >= 0 and type(side) is str and side in ('buy', 'sell'):
            return capability, method, resource, quantity, side
    return None


@contextmanager
def memo_read_queries(player):
    if os.environ.get('ZHANGUO_ECON_QUERY_CACHE', '1') == '0':
        yield
        return
    original = player.call_api
    had_override = 'call_api' in vars(player)
    previous_override = vars(player).get('call_api')
    cache = {}

    def call_api(capability=None, method=None, arguments=None, *args, **kwargs):
        key = None if args or kwargs else query_key(capability, method, arguments)
        if key is None:
            cache.clear()
            return original(capability, method, arguments, *args, **kwargs)
        if key not in cache:
            # Store a detached value so even the first caller cannot mutate it.
            value = original(capability, method, arguments)
            cache[key] = deepcopy(value)
            return value
        return deepcopy(cache[key])

    player.call_api = call_api
    try:
        yield
    finally:
        if had_override:
            player.call_api = previous_override
        else:
            del player.call_api
