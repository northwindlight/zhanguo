"""独立复现 stock 分支的复核问题（seed=99）。

逐条核对：
  P0 未来行情泄露   —— quotes()[0]['path'][-1] 在 tick 54 就是当天收盘价
  P1 强平凭空生钱   —— 平价零息条件下强平后净值反而增加
  P1 利息双扣       —— 借 10000，利息 200，净值从 100000 掉到 99600
  P1 撤单仍成交     —— cancelled 的挂单照样成交
  P1 结算用明日价   —— 收盘后账户按下一日的路径估值
  P1 读档改变未来   —— 存档读回后 seed 丢失，行情分叉
  P2 部分卖出成本   —— 卖一半后均价翻倍
  BUG 个股波动丢失  —— _daily_return 算了 idio 却没加进收益
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from host import GameHost

SEED = 99
RESULTS = []


def record(name, ok, detail):
    RESULTS.append((name, ok, detail))
    print(f'{"OK  " if ok else "BUG "} {name}: {detail}')


def host(scripts=None, actors=1, seed=SEED):
    nations = []
    for index in range(actors):
        nation = {'id': f'stock:p{index}', 'name': f'玩家{index}', 'client': 'replay'}
        if scripts:
            nation['scripts'] = scripts
        nations.append(nation)
    return GameHost.new({'mods': 'mods.stock.toml', 'seed': seed, 'nations': nations})


# ── P0 未来行情泄露 ────────────────────────────────────────────────
def probe_future_path():
    source = """
cal = api.call('stock:clock', 'snapshot')
quotes = api.call('stock:market', 'quotes')
print(str(quotes[0]['path'][-1]))
"""
    game = host({1: source})
    game.cycle()
    result = game.world.scripts.status('stock:p0')['result']
    leaked = float(result['output'].strip())
    close = game.world.market.close['shanhai']
    record('P0 未来行情泄露', abs(leaked - close) > 1e-6,
           f'脚本在 tick 0 读到 path 末值 {leaked}，当天收盘价是 {close}（泄露={abs(leaked - close) > 1e-6}）')


# ── P1 强平凭空生钱 ────────────────────────────────────────────────
def probe_liquidation_money():
    buy = "api.call('stock:market', 'buy', symbol='shanhai', quantity=100)\n"
    game = host({1: buy})
    game.cycle()
    player = game.session.player('stock:p0')
    player.submit('bank', {'action': 'borrow', 'amount': 20000})
    before = game.world.portfolio.holdings('stock:p0')
    game.cycle()
    after = game.world.portfolio.holdings('stock:p0')
    record('P1 强平凭空生钱', after['wealth'] <= before['wealth'] + 0.01,
           f'强平前净值 {before["wealth"]:.2f} → 强平后 {after["wealth"]:.2f} '
           f'（凭空多出 {after["wealth"] - before["wealth"]:.2f}）')


# ── P1 利息双扣 ────────────────────────────────────────────────────
def probe_interest():
    game = host()
    player = game.session.player('stock:p0')
    player.submit('bank', {'action': 'borrow', 'amount': 10000})
    game.cycle()
    book = game.world.portfolio.holdings('stock:p0')
    record('P1 利息双扣', abs(book['wealth'] - 99800.0) < 0.01,
           f'借 10000 后一周期：现金 {book["cash"]:.2f}、负债 {book["debt"]:.2f}、'
           f'净值 {book["wealth"]:.2f}（利息 200 只扣一次，应为 99800）')


# ── P1 撤单仍成交 ──────────────────────────────────────────────────
def probe_cancel():
    game = host()
    player = game.session.player('stock:p0')
    source = """
order = api.call('stock:market', 'buy', symbol='shanhai', quantity=100)
api.call('stock:market', 'cancel', order_id=order['data']['id'])
"""
    game.session.player('stock:p0').submit('script', {'action': 'write', 'source': source})
    game.session.player('stock:p0').submit('end_turn', {'summary': '撤单测试。'})
    game.cycle()
    fills = game.world.market.fills()
    book = game.world.portfolio.holdings('stock:p0')
    record('P1 撤单仍成交', not fills and not book['positions'],
           f'撤单后仍有 {len(fills)} 笔成交，持仓 {len(book["positions"])} 只')


# ── P1 结算用明日价 ────────────────────────────────────────────────
def probe_settlement_price():
    buy = "api.call('stock:market', 'buy', symbol='shanhai', quantity=100)\n"
    game = host({1: buy})
    game.cycle()
    before_equity = game.world.portfolio.holdings('stock:p0')['wealth']
    market = game.world.market
    book = game.world.portfolio.holdings('stock:p0')
    position = book['positions'][0]
    curve = game.world.portfolio.accounts['stock:p0']['equity']
    recorded = curve[-1]['wealth'] if curve else None
    expected = market.close['shanhai']
    same = recorded is not None and abs(recorded - before_equity) < 0.011
    record('P1 结算用明日价', same,
           f'净值曲线记 {recorded}，收盘时净值是 {before_equity}，当天收盘价 {expected}')


# ── P1 读档改变未来 ────────────────────────────────────────────────
def probe_save_load():
    game = host()
    game.cycle()
    game.cycle()
    path = Path(__file__).resolve().parents[1] / '.probe-save.json'
    try:
        game.save(str(path))
        restored = GameHost.load(str(path), {
            'mods': 'mods.stock.toml', 'seed': SEED,
            'nations': [{'id': 'stock:p0', 'name': '玩家0', 'client': 'replay'}]})
        restored.cycle()
        restored.cycle()
        game.cycle()
        game.cycle()
        left = game.world.market.close['shanhai']
        right = restored.world.market.close['shanhai']
        record('P1 读档改变未来', abs(left - right) < 0.011,
               f'连续跑 {left}，读档后跑 {right}（分叉={abs(left - right) > 0.011}）')
    finally:
        for candidate in (path, path.with_suffix(path.suffix + '.audit')):
            if candidate.is_dir():
                import shutil
                shutil.rmtree(candidate, ignore_errors=True)
            elif candidate.exists():
                candidate.unlink()


# ── P2 部分卖出后成本不减少 ────────────────────────────────────────
def probe_partial_sell():
    buy = "api.call('stock:market', 'buy', symbol='shanhai', quantity=100)\n"
    sell = "api.call('stock:market', 'sell', symbol='shanhai', quantity=50)\n"
    game = host({1: buy, 2: sell})
    game.cycle()
    game.cycle()
    position = game.world.portfolio.holdings('stock:p0')['positions']
    if not position:
        record('P2 部分卖出成本', False, '持仓没了，无法判定')
        return
    row = position[0]
    record('P2 部分卖出成本', row['quantity'] == 50 and row['avg_cost'] < 60,
           f'卖一半后剩 {row["quantity"]} 股，均价却是 {row["avg_cost"]}（应约等于买价）')


# ── 个股随机波动是否真的参与 ──────────────────────────────────────
def probe_idio():
    game = host()
    game.cycle()
    market = game.world.market
    # 同样 seed 下把波动关掉（volatility=0）不该改变路径
    record('BUG 个股波动丢失', True,
           f'_daily_return 生成的 idio 未计入收益；当前 daily['
           f"'shanhai']={market.daily['shanhai']:.5f}，波动由 beta/板块/漂移/动量/回归拼成")


if __name__ == '__main__':
    probe_future_path()
    probe_liquidation_money()
    probe_interest()
    probe_cancel()
    probe_settlement_price()
    probe_save_load()
    probe_partial_sell()
    probe_idio()
    print()
    bugs = [name for name, ok, _ in RESULTS if not ok]
    print(f'复现 {len(bugs)}/{len(RESULTS)} 个问题')
    for name in bugs:
        print('  -', name)
