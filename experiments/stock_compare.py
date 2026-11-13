"""Compare recorded stock games without calling a model or modifying either save."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import mean, median


def audits(save, data):
    records = {r['id']: r for r in data['host'].get('audit', [])}
    for path in sorted(save.with_name(save.name + '.audit').rglob('*.json')):
        for row in json.loads(path.read_text())['records']:
            previous = records.get(row['id'])
            if previous is not None and previous != row:
                raise ValueError(f'Conflicting audit id {row["id"]} in {path}')
            records[row['id']] = row
    return sorted(records.values(), key=lambda r: r['id'])


def drawdown(values, initial=100000):
    peak = initial
    result = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            result = max(result, (peak - value) / peak * 100)
    return round(result, 4)


def extract(save):
    raw = save.read_bytes()
    data = json.loads(raw)
    records = audits(save, data)
    roster = data['host']['roster']
    book = data['modules']['stock:portfolio']['accounts']
    period = data['modules']['stock:clock']['round']
    complete = all(book[a].get('final_cash') is not None for a in roster)
    last_round = min(len(book[a]['equity']) for a in roster)
    players = []
    for actor, entry in roster.items():
        account = book[actor]
        own = [r for r in records if r.get('actor') == actor]
        calls = [r for r in own if r['action'] not in
                 {'buy', 'sell', 'short', 'cover', 'cancel', 'stock_fill', 'stock_final_close', 'settlement'}]
        edits = [r for r in own if r['action'] == 'script' and
                 r['arguments'].get('action') in {'write', 'patch'}]
        bad_checks = [r for r in edits if
                      ((r['receipt'].get('data') or {}).get('check') or {}).get('valid') is False]
        research = [r for r in own if r['action'] == 'script' and
                    r['arguments'].get('action') == 'run' and
                    r['arguments'].get('slot', '1') == 'research']
        settlements = [r for r in own if r['action'] == 'settlement']
        # Restrict execution-error rates to trading days, keeping holiday observations separate.
        trading = [r for r in settlements if (r['receipt']['period'] - 1) % 7 < 5]
        slots = [s for r in trading for s in
                 r['receipt']['data'].get('detail', {}).get('slots', [])]
        fills = [r['receipt']['data'] for r in own if r['action'] == 'stock_fill']
        equity = [row['wealth'] for row in account['equity']]
        final = account.get('final_cash')
        last_equity = equity[-1] if equity else 100000
        values = equity + ([final] if final is not None else [])
        scripts = data['modules']['stock:python'].get(actor, {})
        errors = Counter()
        examples = []
        for r in calls:
            receipt = r['receipt']
            check = ((receipt.get('data') or {}).get('check') or {})
            if receipt.get('ok') is False or check.get('valid') is False:
                error = check.get('error')
                message = (error.get('message') if isinstance(error, dict) else str(error)) if error else receipt.get('message', '')
                errors[message] += 1
                examples.append({'id': r['id'], 'round': receipt['period'], 'action': r['action'], 'message': message})
        usage = data['clients'][actor]['state'].get('usage', {})
        player = dict(actor=actor, name=entry['name'], final_cash=final,
                      final_rank=account.get('final_rank'), last_equity=last_equity,
                      return_pct=round(((final if final is not None else last_equity) / 100000 - 1) * 100, 4),
                      max_drawdown_pct=drawdown(values),
                      compute_expenses=account['compute_expenses'],
                      intelligence_expenses=account['intelligence_expenses'],
                      interest_paid=account['interest_paid'], borrow_fees=account['borrow_fees'],
                      fills=len(fills), turnover=round(sum(f['amount'] for f in fills), 2),
                      trading_fees=round(sum(f['fee'] for f in fills), 2),
                      tool_actions=len(calls), rejected_tool_actions=sum(r['receipt'].get('ok') is False for r in calls),
                      script_edits=len(edits), failed_auto_checks=len(bad_checks),
                      research_runs=len(research),
                      research_failures=sum((r['receipt'].get('data') or {}).get('status') == 'failed' or r['receipt'].get('ok') is False for r in research),
                      trading_slot_runs=len(slots), trading_slot_failures=sum(s['status'] == 'failed' for s in slots),
                      final_script_bytes=sum(len(s['source'].encode()) for s in scripts.values()),
                      final_script_revisions=sum(s['revision'] for s in scripts.values()),
                      model_usage=usage, error_examples=examples[:25],
                      top_tool_errors=errors.most_common(8), equity=account['equity'])
        players.append(player)
    money = [p['final_cash'] if complete else p['last_equity'] for p in players]
    summary = dict(complete=complete, saved_round=period, completed_rounds=last_round,
                   average_cash=round(mean(money), 2), median_cash=round(median(money), 2),
                   total_cash=round(sum(money), 2), positive_players=sum(v > 100000 for v in money),
                   average_return_pct=round((mean(money) / 100000 - 1) * 100, 4),
                   average_drawdown_pct=round(mean(p['max_drawdown_pct'] for p in players), 4))
    for field in ('compute_expenses', 'intelligence_expenses', 'interest_paid', 'borrow_fees',
                  'fills', 'turnover', 'trading_fees', 'tool_actions', 'rejected_tool_actions',
                  'script_edits', 'failed_auto_checks', 'research_runs', 'research_failures',
                  'trading_slot_runs', 'trading_slot_failures'):
        summary[field] = round(sum(p[field] for p in players), 2)
    for key in ('out_tokens', 'reason_tokens', 'hit', 'miss', 'wall'):
        summary[key] = round(sum(p['model_usage'].get(key, 0) for p in players), 2)
    return dict(save=str(save), sha256=hashlib.sha256(raw).hexdigest(), summary=summary, players=players)


def render(base, candidate):
    b, c = base['summary'], candidate['summary']
    complete = b['complete'] and c['complete']
    lines = ['# Stock：Gemini 3.8 Flash 与 Hydrogen 对照', '',
             '状态：' + ('两局均已正式终局清算。' if complete else '3.8 Flash 尚未终局，以下为进度记录，不能作为最终胜负结论。'), '',
             '四席同模型自博弈，种子2026111303、初始现金每席100000、60回合、500k上下文、串行规划、费用和算力配方相同。Flash固定第二账户，模型gemini-3.8-flash-high；基线为gemini-4-hydrogen-high。', '',
             '|指标|Hydrogen|3.8 Flash|', '|---|---:|---:|']
    if complete:
        delta = c['average_return_pct'] - b['average_return_pct']
        failure_rates = [100 * s['trading_slot_failures'] / max(1, s['trading_slot_runs']) for s in (b, c)]
        check_rates = [100 * s['failed_auto_checks'] / max(1, s['script_edits']) for s in (b, c)]
        observations = [
            f'本局四席平均收益：Flash {c["average_return_pct"]:+.2f}%，Hydrogen {b["average_return_pct"]:+.2f}%，相差{delta:+.2f}个百分点。',
            f'平均最大收盘回撤：Flash {c["average_drawdown_pct"]:.2f}%，Hydrogen {b["average_drawdown_pct"]:.2f}%。',
            f'交易日槽执行失败比例：Flash {failure_rates[1]:.1f}%，Hydrogen {failure_rates[0]:.1f}%；自动检查失败/编辑比例分别为{check_rates[1]:.1f}%和{check_rates[0]:.1f}%。这些指标区分了脚本能否运行与实际赚不赚钱。',
            f'Flash四席盈利{c["positive_players"]}/4，Hydrogen为{b["positive_players"]}/4；同时查看每席结果，避免只挑冠军。',
        ]
        lines[4:4] = ['本局观察：', '', *['- ' + line for line in observations], '']
    fields = [('已完成普通回合', 'completed_rounds'), ('四席平均现金/净值', 'average_cash'),
              ('四席平均收益%', 'average_return_pct'), ('四席中位现金/净值', 'median_cash'),
              ('盈利席位数', 'positive_players'), ('平均最大回撤%', 'average_drawdown_pct'),
              ('算力/研究支出合计', 'compute_expenses'), ('谍报支出合计', 'intelligence_expenses'),
              ('成交笔数', 'fills'), ('成交手续费', 'trading_fees'),
              ('模型工具动作数', 'tool_actions'), ('工具拒绝次数', 'rejected_tool_actions'),
              ('写入/修订次数', 'script_edits'), ('自动检查失败次数', 'failed_auto_checks'),
              ('研究运行次数', 'research_runs'), ('研究失败次数', 'research_failures'),
              ('交易日槽执行次数', 'trading_slot_runs'), ('交易日槽执行失败次数', 'trading_slot_failures'),
              ('累计模型调用耗时（秒）', 'wall'), ('记录输出token', 'out_tokens'),
              ('记录思考token', 'reason_tokens')]
    for label, key in fields:
        lines.append(f'|{label}|{b[key]}|{c[key]}|')
    for title, game in [('Hydrogen', base), ('3.8 Flash', candidate)]:
        lines += ['', '## ' + title, '', '|玩家|最终现金/当前净值|收益%|最大回撤%|算力研究费|谍报费|交易槽失败/运行|检查失败/编辑|', '|---|---:|---:|---:|---:|---:|---:|---:|']
        for p in game['players']:
            value = p['final_cash'] if p['final_cash'] is not None else p['last_equity']
            lines.append(f'|{p["name"]}|{value:.2f}|{p["return_pct"]:+.2f}|{p["max_drawdown_pct"]:.2f}|{p["compute_expenses"]:.2f}|{p["intelligence_expenses"]:.2f}|{p["trading_slot_failures"]}/{p["trading_slot_runs"]}|{p["failed_auto_checks"]}/{p["script_edits"]}|')
        lines += ['', '主要工具/检查错误（原始审计可定位）：', '']
        for p in game['players']:
            for ex in p['error_examples'][:3]:
                message = ex['message'].replace('\n', ' ')[:200]
                lines.append(f'- {p["name"]}，第{ex["round"]}回合，audit id={ex["id"]}，{ex["action"]}：{message}')
    lines += ['', '## 解释边界', '',
              '- 这是一对同种子、自博弈单局，不是混合模型同场比赛，也不能据此估计通用模型能力排名。',
              '- 同种子固定基础随机环境；玩家策略会改变盘口和成交路径，因此两局实际价格不会逐笔相同。',
              '- 使用现有0585416版本；依用户要求不还原旧日志行为。交易槽错误率只统计交易日，休市观察不混入分母。',
              '- 最大回撤按每日收盘净值及最终清算现金计算，未包含盘中回撤。模型用量来自存档累计记录，不等于账单费用；耗时含接口等待，不当作纯推理速度。',
              '- 未完成的before-tmux尝试不计入本局模型用量、工具动作和收益统计。',
              '- report-metrics.json保留完整指标、各席净值曲线、错误审计定位及两份存档SHA-256；原始存档不被分析程序修改。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    base, candidate = extract(args.baseline), extract(args.candidate)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'report-metrics.json').write_text(json.dumps({'hydrogen': base, 'flash': candidate}, ensure_ascii=False, indent=2) + '\n')
    (args.out / '对照评估.md').write_text(render(base, candidate))
    print(json.dumps({'hydrogen': base['summary'], 'flash': candidate['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
