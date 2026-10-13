#!/usr/bin/env python3
"""Mod 示例巡礼：把《Mod 开发指南》里的每条配方跑一遍。

    .venv/bin/python mods/examples/tour.py

不联网、不需要 API key、不读写任何真实存档。每步都打印可核对的数字。
"""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.runtime import Runtime, Unit            # noqa: E402
from host import GameHost                            # noqa: E402
from mods.examples.hello.mod import HelloMod         # noqa: E402
from mods.examples.minigame.game import BUILDINGS    # noqa: E402

EXAMPLES = ROOT / 'mods' / 'examples'


def title(text):
    print()
    print('─' * 74)
    print(text)
    print('─' * 74)


def session(dist, nations):
    """按给定发行版开一局，返回 (runtime, host, player)。"""
    cfg = {'mods': str(dist), 'map_size': 12, 'seed': 7, 'nations': nations}
    return Runtime.load(dist), GameHost.new(cfg)


def step_minimal_mod():
    title('配方一 · 最小 Mod（一个模块，不落任何文件）')
    runtime = Runtime([Unit('example:hello', HelloMod(), version='1.0.0')])
    world = runtime.world_class()()
    print('装载顺序        ', runtime.order)
    print('add(3)          ', runtime.call(world, 'demo', 'hello_add', {'amount': 3}))
    print('参数自动转换    ', runtime.call(world, 'demo', 'hello_add', {'amount': '4'}))
    print('别名 打招呼     ', runtime.call(world, 'demo', '打招呼', {'amount': 2}))
    print('结算            ', runtime.settle(world))
    print('存档            ', runtime.save_mod_state(world))


def step_minimal_game():
    title('配方二 · 一个最小可玩游戏（15 个模块，离线跑两轮）')
    dist = ROOT / 'mods.playable.toml'
    runtime = Runtime.load(dist)
    print('装载            ', dist.name, '→', len(runtime.order), '模块 /',
          len(runtime.hooks), '挂点 /', len(runtime.stage_order), '阶段')
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {'mods': str(dist), 'map_size': 12, 'seed': 7,
               'nations': [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'},
                           {'id': 'scenario:chu', 'name': '楚', 'client': 'llm'}]}
        host = GameHost.new(cfg)
        player = host.player('scenario:qin')
        print('客户端          ', type(host.clients['scenario:qin']).__name__)
        print('可用工具        ', len(player.actions()), '个')
        print('可见事实        ', sorted(player.observe()['facts']))
        host.cycle(); host.cycle()
        print('两轮后 period   ', host.world.government.period())
        path = Path(tmp) / 'session.json'
        host.save(path)
        print('存读往返一致    ', GameHost.load(path, cfg).to_save() == host.to_save())


def step_minigame():
    title('配方三 · 从零写的最小可玩游戏（10 个模块，离线跑完整局）')
    entry = ROOT / 'mods.minigame.toml'
    runtime = Runtime.load(entry)
    print('模块            ', len(runtime.order), '个；挂点', runtime.hooks)
    cfg = {'mods': str(entry), 'map_size': 12, 'seed': 3,
           'nations': [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm', 'position': [2, 5]},
                       {'id': 'scenario:chu', 'name': '楚', 'client': 'llm', 'position': [5, 5]}]}
    host = GameHost.new(cfg)
    world = host.world
    player = host.player('scenario:qin')
    print('资源            ', sorted(world.accounts.ids()))
    print('建筑种类        ', len(BUILDINGS), sorted(BUILDINGS))
    print('工具            ', len(player.actions()), '个')
    for _ in range(6):
        host.cycle()
    built = {kind for cell in player.query('land') if cell['owner'] == 'scenario:qin'
             for kind in cell['buildings']}
    print('六期之后本国建筑', sorted(built))
    print('本国部队        ', len(player.query('army')), '支')
    world.runtime.object_call(world, 'mini:army', 'seed', 'scenario:qin',
                              'native:infantry', 3, 5, 1)
    unit = [row for row in player.query('army') if row['x'] == 3][0]
    before = len([c for c in player.query('land') if c['owner'] == 'scenario:qin'])
    print('未宣战就进攻    ', player.submit('attack', {'unit': unit['id'], 'x': 4, 'y': 5})['message'])
    player.submit('declare_war', {'target': 'scenario:chu'})
    print('宣战后面板      ', player.query('diplomacy'))
    for index in range(3):
        receipt = player.submit('attack', {'unit': unit['id'], 'x': 4, 'y': 5})
        after = len([c for c in player.query('land') if c['owner'] == 'scenario:qin'])
        print('第 %d 次进攻     ' % (index + 1), receipt['data'], '→ 本国 %d 格' % after)
        if after > before:
            break
    print('土地对账        ', '%d → %d 格（该格仍有守军，未易主；'
          '目标格无守军时进攻才占领，见 tests/test_example_mods.py）' % (before, after))
    capital = next(cell for cell in world.territory.cells()
                   if cell.owner == 'scenario:chu'
                   and world.buildings.at(*cell.position).get('native:town-hall'))
    world.runtime.object_call(world, 'mini:territory', 'transfer', *capital.position, 'scenario:qin')
    context = world.resolve_turn()
    print('失去全部市政厅  ', context['extinct'], '→ 存活', world.nations.ids())


def step_new_good():
    title('配方四 · 添加一种资源并让它上市')
    runtime = Runtime.load(EXAMPLES / 'tea_dist')
    market = runtime.units['native:market']
    print('市场实现        ', type(market.mod).__name__, '版本', market.version)
    print('上市商品        ', [row.resource for row in market.mod.listings])
    with tempfile.TemporaryDirectory():
        cfg = {'mods': str(EXAMPLES / 'tea_dist'), 'map_size': 12, 'seed': 7,
               'nations': [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm',
                            'resources': {'example:tea': 20}}]}
        host = GameHost.new(cfg)
        player = host.player('scenario:qin')
        before = {s['id']: s['amount'] for s in player.query('econ')['stocks']}
        panel = player.query('market')
        sell = dict(panel['sell_prices'])['example:tea']
        print('开局            ', '茶', before['example:tea'], '/ 金', before['native:gold'])
        print('本期卖出单价    ', sell, '金（库存', dict(panel['inventory'])['example:tea'],
              '/ 深度', dict(panel['depth'])['example:tea'], '）')
        receipt = player.submit('sell', {'resource': 'example:tea', 'quantity': 5})
        after = {s['id']: s['amount'] for s in player.query('econ')['stocks']}
        print('卖出回执        ', receipt['status'], '实收', receipt['data']['gold'], '金')
        print('对账            ', '茶 -%d，金 +%d'
              % (before['example:tea'] - after['example:tea'],
                 after['native:gold'] - before['native:gold']))


def step_stages_and_units():
    title('配方五/六 · 插入新阶段；添加兵种；给该兵种添加结算阶段')
    dist = EXAMPLES / 'recipes_dist'
    runtime = Runtime.load(dist)
    print('挂点            ', len(runtime.hooks), '个（native 14 个 + example.harvest）')
    for name in ('example:harvest.reap', 'example:elephants.upkeep'):
        index = runtime.stage_order.index(name)
        print('  %-26s 位于 %s 与 %s 之间'
              % (name, runtime.stage_order[index - 1], runtime.stage_order[index + 1]))
    with tempfile.TemporaryDirectory():
        runtime, host = session(dist, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}])
        world = host.world
        player = host.player('scenario:qin')
        schemas = {t['function']['name']: t for t in player.actions()}
        kinds = schemas['recruit']['function']['parameters']['properties']['kind']['enum']
        print('新兵种进入征兵枚举 ', 'example:war-elephant' in kinds)
        site = player.query('building_sites')['sites'][0]['id']
        x, y = (int(v) for v in site.split('land-')[1].split('-'))
        runtime.object_call(world, 'native:army', 'seed', 'scenario:qin',
                           'example:war-elephant', x, y, 2)
        print('本国战象面板       ', player.query('elephant_heads'))
        before = {s['id']: s['amount'] for s in player.query('econ')['stocks']}
        context = world.resolve_turn()
        print('秋收写入 context   ', context['harvest'])
        print('战象写入 context   ', context['elephant_upkeep'])
        after = {s['id']: s['amount'] for s in player.query('econ')['stocks']}
        print('粮食对账           %d → %d（+%d 秋收 −%d 草料）'
              % (before['native:food'], after['native:food'],
                 context['harvest']['yield'], context['elephant_upkeep']['fed']['scenario:qin']))


def step_replace_existing():
    title('配方七 · 替换已有内容：去掉电力系统')
    for dist, label in ((EXAMPLES / 'recipes_dist', 'native'),
                        (EXAMPLES / 'no_power_dist', 'no_power')):
        runtime = Runtime.load(dist)
        with tempfile.TemporaryDirectory():
            _, host = session(dist, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}])
            player = host.player('scenario:qin')
            host.cycle()                      # 电网状态在结算的补给阶段记录
            kinds = [row['id'] for row in player.query('building_catalog')]
            site = player.query('building_sites')['sites'][0]['id']
            try:
                receipt=player.submit('recruit', {'site': site, 'kind': 'native:infantry'})
                if not receipt['ok']:raise ValueError(receipt['message'])
                verdict = '放行'
            except ValueError as exc:
                verdict = str(exc)
            print('%-9s 建筑 %d 种，含电厂 %-5s 征兵回执：%s'
                  % (label, len(kinds), 'native:wood-power-plant' in kinds, verdict))


def step_broken_assembly():
    title('配方八 · 摘掉模块：装配期即报错')
    import tomllib
    body = (EXAMPLES / 'recipes_dist' / 'mod.toml').read_text(encoding='utf-8')
    declaration = tomllib.loads(body)
    members = [str((EXAMPLES / 'recipes_dist' / row).resolve())
               for row in declaration['Distribution']['Members']]
    span = body[body.index('Members = ['):body.index(']\nRequiredState') + 1]
    for drop in ('resources', 'government', 'market_pricing'):
        kept = [row for row in members if not row.endswith(drop + '/mod.toml')]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'mod.toml').write_text(
                body.replace(span, 'Members = ' + json.dumps(kept)), encoding='utf-8')
            try:
                Runtime.load(tmp)
                print('  摘掉 %-16s → 装配通过' % drop)
            except Exception as exc:
                print('  摘掉 %-16s → %s: %s' % (drop, type(exc).__name__, exc))


def main():
    step_minimal_mod()
    step_minimal_game()
    step_minigame()
    step_new_good()
    step_stages_and_units()
    step_replace_existing()
    step_broken_assembly()
    print()
    print('文档：docs/Mod开发指南.md')


if __name__ == '__main__':
    main()
