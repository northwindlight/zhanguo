"""Read-only stock finale: use the game's recorded final cash and ranks."""
import json
from pathlib import Path

from engine.session import SaveFormatError


def settle(data, world):
    rows = []
    accounts = world.portfolio.accounts
    for actor, entry in data['host']['roster'].items():
        if actor not in accounts:
            raise SaveFormatError('股市名册与券商账户不一致')
        account = accounts[actor]
        final_cash = account.get('final_cash')
        rank = account.get('final_rank')
        if final_cash is not None and (type(rank) is not int or rank < 1):
            raise SaveFormatError('股市终局清算缺少有效名次')
        rows.append({'kind': 'stock', 'actor': actor, 'name': entry['name'],
                     'alive': account['alive'], 'rank': rank, 'final_cash': final_cash,
                     'initial_cash': world.portfolio.initial_cash,
                     'realized_pnl': account['realized_pnl'],
                     'compute_expenses': account['compute_expenses'],
                     'intelligence_expenses': account['intelligence_expenses'],
                     'borrow_fees': account['borrow_fees'], 'interest_paid': account['interest_paid'],
                     'final_round': account.get('final_round'), 'debt': account['debt'],
                     'final_repaid': account.get('final_repaid', 0),
                     'final_close_count': len(account.get('final_liquidations') or [])})
    ranked = [row for row in rows if row['final_cash'] is not None]
    if not ranked:
        raise SaveFormatError('股市对局尚未完成最终清算，不能生成终局排名')
    ranks = [row['rank'] for row in ranked]
    if sorted(ranks) != list(range(1, len(ranks) + 1)):
        raise SaveFormatError('股市终局名次不连续或重复')
    return sorted(rows, key=lambda r: (r['rank'] is None, r['rank'] or 0, r['actor']))


def scoreboard_text(rows):
    lines = ['|名次|玩家|最终结算现金|相对初始资金|状态|',
             '|---:|---|---:|---:|---|']
    for row in rows:
        rank, final = row['rank'], row['final_cash']
        if final is None:
            lines.append(f'|—|{row["name"]}|—|—|未参与最终清算|')
        else:
            change = (final / row['initial_cash'] - 1) * 100 if row['initial_cash'] else 0
            lines.append(f'|{rank}|{row["name"]}|{final:.2f}|{change:+.2f}%|已清算|')
    return '\n'.join(lines)


def private_brief(data, row):
    """Only this player's account and scripts; other players' private state stays private."""
    actor = row['actor']
    own = {key: row[key] for key in ('rank', 'final_cash', 'initial_cash', 'realized_pnl',
           'compute_expenses', 'intelligence_expenses', 'borrow_fees', 'interest_paid',
           'debt', 'final_repaid', 'final_close_count')}
    slots = data['modules'].get('stock:python', {}).get(actor, {})
    scripts = [{'slot': slot, 'revision': item['revision'], 'source': item['source']}
               for slot, item in sorted(slots.items()) if item.get('revision')]
    return ('你的最终账户（final_close_count 是正常终局平仓笔数，不代表风控强平次数）：' + json.dumps(own, ensure_ascii=False) +
            '\n你自己保存的最终策略源码：' + json.dumps(scripts, ensure_ascii=False))
