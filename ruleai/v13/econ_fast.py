"""Optional C fast path for the v13 economy solver.

The Python functions in :mod:`ruleai.v13.economy` and :mod:`ruleai.v13.market`
remain the reference implementation: this module only dispatches to the compiled
extension ``_econ_fast`` when it exists, and falls back on any error. Engine
calls (planning, quotes, trades) never live here; the extension is pure
arithmetic over data the solver already has.

The extension is optional: without it every call takes the Python path, so a
source checkout and the Windows portable package behave identically. Built
objects are not committed. See ``ruleai/v13/build.py``.
"""
from hashlib import sha256
from pathlib import Path
import os

try:
    from . import _econ_fast as FAST
    FAST_ERROR = None
except Exception as exc:  # 扩展缺失不是错误：Python 参考实现照常工作
    FAST = None
    FAST_ERROR = f'{type(exc).__name__}: {exc}'


def _stale():
    """改了 `_econ_fast.c` 却没重新编译：宁可走 Python，也不能跑旧二进制。"""
    source = Path(__file__).resolve().parent / '_econ_fast.c'
    try:
        return sha256(source.read_bytes()).hexdigest() != getattr(FAST, 'DIGEST', None)
    except OSError as exc:
        return f'{type(exc).__name__}: {exc}'


if FAST is not None:
    problem = _stale()
    if problem:
        FAST_ERROR = problem if isinstance(problem, str) else 'stale build: 请重新运行 python -m ruleai.v13.build'
        FAST = None

ENABLED = os.environ.get('ZHANGUO_ECON_FAST', '1') != '0' and FAST is not None
# 回退记录：测试要求并行对账跑完为空；生产里出现也不改变行为，只说明走了 Python。
FALLBACKS = []


def backend():
    return 'c' if ENABLED else 'python'


def reset_fallbacks():
    del FALLBACKS[:]


def _note(name, exc):
    if len(FALLBACKS) < 32:
        FALLBACKS.append(f'{name}: {type(exc).__name__}: {exc}')


def _call(name, reference, *args):
    if not ENABLED:
        return reference(*args)
    try:
        return getattr(FAST, name)(*args)
    except Exception as exc:
        _note(name, exc)
        return reference(*args)


def net_recipe(reference, catalog, buildings, military=None):
    return _call('net_recipe', reference, catalog, buildings, military)


def operating_targets(reference, catalog, buildings, military, prices, periods):
    return _call('operating_targets', reference, catalog, buildings, military, prices, periods)


def reserve_cash(reference, catalog, buildings, military, buy, profile):
    return _call('reserve_cash', reference, catalog, buildings, military, buy, profile)


def _pack(model):
    """市场模型快照。除 ``order_offset`` 外各字段在 Planner 生命周期内不变。"""
    cached = getattr(model, '_econ_pack', None)
    if cached is None:
        cached = {'inventory': model.inventory, 'reference': model.reference,
                  'buy': model.buy, 'sell': model.sell, 'base': model.base,
                  'elasticity': model.elasticity, 'external': model.external,
                  'flow': model.flow, 'prices': dict(model.market['prices'])}
        model._econ_pack = cached
    snapshot = dict(cached)
    snapshot['order_offset'] = model.order_offset
    return snapshot


def market_value(reference, model, net, own_net, extra, horizon, crowd):
    if not ENABLED:
        return reference(net, own_net, extra, horizon, crowd)
    try:
        return FAST.market_value(_pack(model), list(net.items()), own_net, extra,
                                 horizon, float(crowd))
    except Exception as exc:
        _note('market_value', exc)
        return reference(net, own_net, extra, horizon, crowd)
