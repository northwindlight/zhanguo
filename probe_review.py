"""Independent stock v2 acceptance: paid research/intelligence, real matching and resume.

Uses isolated in-memory/replay players and a TemporaryDirectory. Never loads a
real game or calls an external model. Default exercises the full 20000 ticks.
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

from host import GameHost


def causal_controls():
    """Paired strategies trade each symbol after its own second published news."""
    source = """def on_tick():
    rows = api.call('stock:market', 'quotes')
    for row in rows:
        key = row['symbol']
        if len(row['news']) < 2 or state.get(key):
            continue
        state[key] = True
        sign = row['news'][-1]['cashflow_change_pct'] * DIRECTION
        if sign > 0:
            api.call('stock:market', 'buy', symbol=key, quantity=50)
        else:
            api.call('stock:market', 'short', symbol=key, quantity=50)
"""
    results = []
    for seed in range(1, 7):
        roster = [dict(id='stock:'+kind, name=kind, client='replay',
                       scripts={'1': source.replace('DIRECTION', str(direction))})
                  for kind, direction in (('aligned', 1), ('inverted', -1))]
        roster.append(dict(id='stock:cash', name='cash', client='replay'))
        h = GameHost.new(dict(mods='mods.stock.toml', seed=seed, nations=roster))
        h.cycle()
        result = dict(seed=seed, ticks=h.world.clock.session_ticks)
        for actor in ('aligned', 'inverted', 'cash'):
            result[actor] = round(h.world.portfolio.holdings('stock:'+actor)['wealth']-100000, 2)
            if actor != 'cash':
                status = h.world.scripts.status('stock:'+actor)['result']
                assert status['status'] == 'effective', status
        results.append(result)
    return dict(external_model_calls=0, results=results,
                description='Same-market public-news sign controls; 50 shares per symbol '
                            'after that symbol has two published news items. Closing marked '
                            'equity includes fees.',
                aligned_wins=sum(r['aligned'] > r['inverted'] for r in results),
                mean_aligned=sum(r['aligned'] for r in results)/len(results),
                mean_inverted=sum(r['inverted'] for r in results)/len(results))


def run(ticks=20000):
    cfg = dict(mods='mods.stock.toml', seed=99, nations=[
        dict(id='stock:alpha', name='量化甲', client='replay'),
        dict(id='stock:beta', name='量化乙', client='replay', scripts={
            '1': "api.call('stock:market','buy',symbol='shanhai',quantity=10)"})])
    h = GameHost.new(cfg)
    h.world.clock.session_ticks = ticks
    player = h.player('stock:alpha')
    initial_cash = sum(a['cash'] for a in h.world.portfolio.accounts.values())
    def submit(tool, **arguments):
        r = player.submit(tool, arguments)
        assert r['ok'], r
        return r['data']
    for kind in ('pool', 'slot', 'research'):
        submit('compute', action='buy', kind=kind)
    submit('script', action='write', slot='research', source="print(inputs['x']*7)")
    result = submit('script', action='run', slot='research', inputs={'x': 3})
    assert result['output'] == '21\n' and h.world.clock.tick == 0
    report = submit('intel', action='steal', target='stock:beta')
    assert report['snapshot']['cash'] == 100000 and report['tick'] == 0
    submit('script', action='write', source="def on_tick():\n    if not state.get('bought'):\n        api.call('stock:market','buy',symbol='shanhai',quantity=10)\n        state['bought']=True\n")
    submit('script', action='write', slot='2', source="print('第二交易槽')")
    started = time.perf_counter()
    h.cycle()
    elapsed = time.perf_counter()-started
    broker = h.world.portfolio
    assert h.world.market.fills('stock:alpha')[0]['tick'] == 1
    assert h.world.scripts.status('stock:alpha')['result']['status'] == 'effective'
    final_cash = sum(a['cash'] for a in broker.accounts.values())
    fees = broker.fees_collected+broker.resource_fees+broker.intelligence_fees
    assert abs(initial_cash-final_cash-fees) < .01
    for actor in h.actors():
        b = broker.holdings(actor)
        assert abs(b['wealth']-(b['cash']+b['long_value']-b['short_value']-b['debt'])) < .01
    with tempfile.TemporaryDirectory(prefix='stock-v2-accept-') as tmp:
        path = Path(tmp)/'session.json'
        h.save(path)
        loaded = GameHost.load(path, cfg)
        loaded.world.clock.session_ticks = ticks
        assert h.runtime.save_mod_state(h.world) == loaded.runtime.save_mod_state(loaded.world)
        h.cycle(); loaded.cycle()
        assert h.runtime.save_mod_state(h.world) == loaded.runtime.save_mod_state(loaded.world)
    return dict(ticks=ticks, first_cycle_seconds=round(elapsed, 3),
                market='PAMS/0.3.0', research_output='21',
                next_tick_fill=True, shared_quota=True, intelligence_snapshot=True,
                cash_conservation=True, save_resume_identical=True,
                source_client='replay', external_model_calls=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=20000)
    parser.add_argument('--causal-controls', action='store_true',
                        help='run six paired full-day public-news controls instead')
    args = parser.parse_args()
    if args.ticks < 3: parser.error('--ticks must be at least 3')
    print(json.dumps(causal_controls() if args.causal_controls else run(args.ticks),
                     ensure_ascii=False, indent=2))
