"""股市版模型客户端。

* :class:`StockView` 在原版视图后面补一段「本回合开局简报」：回合与日历、
  账户与财富、持仓、行情、最近一次脚本执行的打印输出与报错。
  原版简报是战场口径，股市发行版用不到，所以这一段才是模型每回合真正读到的战情。
* :class:`StockClient` 把模型客户端接到股市发行版上。
* :class:`SimClient` 不需要模型与 API key 的离线客户端，跑一套简单均线策略，
  用来验证规则与结算，也方便本地试玩。
"""
from clients.llm import LLMClient
from clients import context as ctx
from clients.provider import OpenAICompat
from mods.stock.compact_provider import StockOpenAICompat
from copy import deepcopy
from types import SimpleNamespace
from clients.model_view import ModelView


class StockView(ModelView):
    """回合开局的股市简报。"""

    def system(self):
        # Stock's script lifecycle is described by its own system rule section;
        # the native game's manual check/run guidance does not apply here.
        previous = self.has_scripts
        try:
            self.has_scripts = False
            return super().system()
        finally:
            self.has_scripts = previous

    def state(self, observation, events):
        tail, image = super().state(observation, events)
        return tail + self.stock_briefing(observation), image

    def _t(self, key, zh, **values):
        """简报文案：中文原文写在代码里（兜底），英文按 locales/en-US.json 的 stock.b.* 覆盖。"""
        return self.text.resolve(key, zh, **values)

    def _name(self, symbol, name):
        """股票显示名：只改呈现；symbol 才是脚本、存档与审计里的句柄。"""
        return self._t('stock.name.' + str(symbol), name)

    def _sector(self, sector):
        return self._t('stock.sector.' + str(sector), sector)

    def _sep(self, zh):
        """列举分隔符也跟着语言走（中文用全角，英文用半角）。"""
        return zh if self.text.language == 'zh-CN' else self._t('stock.b.sep', zh, )

    def _effect(self, effect):
        """个性变化串（波动率→31.0%；点差→×1.30）在呈现层逐标签换语言，不动世界状态。"""
        if not effect:
            return effect
        for zh, en in (('波动率→', 'volatility→'), ('流动性→', 'liquidity→'),
                       ('点差→', 'spread→'), ('吸收速度→', 'absorption→'),
                       ('消息敏感度→', 'news sensitivity→'), ('贝塔→', 'beta→'),
                       ('漂移→', 'drift→')):
            effect = effect.replace(zh, self._t('stock.effect.' + zh, en))
        return effect.replace('；', self._t('stock.effect.sep', '; '))

    def stock_briefing(self, observation):
        facts = observation.get('facts') or {}
        lines = ['', '【' + self._t('stock.caption.round', '本回合开局简报') + '】']

        view = facts.get('round') or {}
        if view:
            final = view.get('final_round'); left = view.get('rounds_left')
            total = f'/{final}' if isinstance(final, int) else ''
            tail = self._t('stock.b.left', '，还剩 {left} 回合（最终回合自动清算）', left=left) \
                if isinstance(left, int) else ''
            if view.get('trading_day'):
                lines.append(self._t('stock.b.open', '第 {round}{total} 回合，今日开市{tail}。',
                                     round=view.get('round'), total=total, tail=tail))
            else:
                lines.append(self._t('stock.b.closed',
                                     '第 {round}{total} 回合，今日休市（还有 {days} 回合开市）{tail}：'
                                     '不能买卖，但可以改脚本、规划、借贷。',
                                     round=view.get('round'), total=total,
                                     days=view.get('days_to_next_open'), tail=tail))
            lines.append(self._t('stock.b.planning',
                                 '盘前规划动作 {used}/{limit}；市场共同时间线 {tick}/{slices} tick。'
                                 '交易日全体结束行动后自动运行交易槽，休市日不运行；'
                                 'on_tick回调默认每100tick唤醒，state跨日保留。'
                                 '研究槽按次收费：每次 200 现金，只能在自己行动时手动只读运行。',
                                 used=view.get('window_used'), limit=view.get('window_limit'),
                                 tick=view.get('tick'), slices=view.get('slices')))

        compute = facts.get('compute_status') or {}
        if compute:
            lines.append(self._t('stock.b.compute',
                                 '算力池 {pool} 档、交易槽 {slots} 个（每回合重置，需要请重买）；'
                                 '本回合剩余 {steps} 单位 / {calls} 次API；'
                                 '研究次数剩 {research} 次，累计计算资源费用 {fees}。',
                                 pool=compute['pool'], slots=compute['slots'],
                                 steps=compute['steps_remaining'], calls=compute['calls_remaining'],
                                 research=compute['research_runs_left'], fees=compute['fees_paid']))
            if compute.get('notice'):
                lines.append(compute['notice'])

        account = facts.get('portfolio') or {}
        if account:
            if not account.get('alive', True):
                lines.append(self._t('stock.b.bankrupt',
                                     '你已破产退市（第 {round} 回合），本局不再行动。',
                                     round=account.get('retired_round')))
            lines.append(self._t('stock.b.account',
                                 '现金 {cash}，负债 {debt}，持仓市值 {holdings}，'
                                 '总财富 {wealth}（{return_pct}%）。'
                                 '授信上限 {credit}，追保线 {margin}。',
                                 cash=account.get('cash'), debt=account.get('debt'),
                                 holdings=account.get('holdings_value'), wealth=account.get('wealth'),
                                 return_pct=account.get('return_pct'),
                                 credit=account.get('credit_limit'), margin=account.get('margin_limit')))
            if account.get('risk_frozen'):
                lines.append(self._t('stock.b.risk_frozen', '风控冻结，仅能减仓还款：{notice}',
                                     notice=account.get('risk_notice', '')))
            positions = account.get('positions') or []
            if positions:
                items = self._sep('；').join(self._t('stock.b.position_item',
                                          '{name}({symbol}) {quantity} 股，成本 {avg_cost}，'
                                          '现价 {price}，浮盈亏 {pnl}',
                                          name=self._name(p['symbol'], p['name']), symbol=p['symbol'], quantity=p['quantity'],
                                          avg_cost=p['avg_cost'], price=p['price'], pnl=p['pnl'])
                                  for p in positions)
                lines.append(self._t('stock.b.positions', '持仓：{items}', items=items))
            else:
                lines.append(self._t('stock.b.positions_empty', '持仓：空仓。'))
            liquidations = account.get('liquidations') or []
            if liquidations:
                last = liquidations[-1]
                lines.append(self._t('stock.b.forced_close',
                                     '银行强制平仓：第 {round} 回合卖出 {quantity} 股 {symbol}，'
                                     '回收 {amount}。',
                                     round=last.get('round'), quantity=last.get('quantity'),
                                     symbol=last.get('symbol'), amount=last.get('amount')))
            curve = account.get('equity_curve') or []
            if curve:
                items = self._sep('，').join(self._t('stock.b.equity_item', '第 {round} 回合 {wealth}',
                                          round=point['round'], wealth=point['wealth'])
                                  for point in curve[-6:])
                lines.append(self._t('stock.b.equity', '净值曲线：{items}', items=items))

        quotes = facts.get('market') or []
        if quotes:
            lines.append(self._t('stock.b.quotes_head',
                                 '行情（代码 名称 现价 涨跌 ma5 ma10 波动率 财报日程 最新消息）：'))
            kinds = {'market': self._t('stock.b.kind.market', '市场快讯'),
                     'wire': self._t('stock.b.kind.wire', '公司快讯'),
                     'earnings': self._t('stock.b.kind.earnings', '财报'),
                     'shock': self._t('stock.b.kind.shock', '公司事件'),
                     'ipo': self._t('stock.b.kind.ipo', '挂牌上市')}
            for row in quotes:
                report = row.get('next_report_round')
                if report:
                    when = (self._t('stock.b.when_tick', '第{round}回合今日tick{tick}',
                                    round=report, tick=row['report_tick'])
                            if row.get('report_tick')
                            else self._t('stock.b.when', '第{round}回合', round=report))
                else:
                    when = self._t('stock.b.when_unset', '未定')
                news = row.get('news') or []
                latest = ''
                if news:
                    event = news[-1]
                    kind = kinds.get(event.get('kind'), self._t('stock.b.kind.other', '消息'))
                    latest = self._t('stock.b.latest', ' {kind}{change:+.2f}%@tick{tick}',
                                     kind=kind, change=event.get('cashflow_change_pct', 0),
                                     tick=event.get('tick'))
                    if event.get('effect'):
                        latest += self._t('stock.b.effect', '（{effect}）', effect=self._effect(event['effect']))
                review = (self._t('stock.b.review', ' 退市复核第{round}回合',
                                  round=row['delist_review_round'])
                          if row.get('delist_review_round') else '')
                lines.append(self._t('stock.b.quote_row',
                                     '  {symbol} {name} {price} {change:+.2f}% ma5={ma5} ma10={ma10} '
                                     'vol={volatility} 财报{when}{latest}{review}',
                                     symbol=row['symbol'], name=self._name(row['symbol'], row['name']), price=row['price'],
                                     change=row['change_pct'] * 100, ma5=row['ma5'], ma10=row['ma10'],
                                     volatility=row['volatility'], when=when, latest=latest,
                                     review=review))

        pool = facts.get('pool') or {}
        if pool:
            notes = []
            for listing in pool.get('next_listings') or []:
                notes.append(self._t('stock.b.pool_listing',
                                     '新股{name}({symbol},{sector})第{round}回合挂牌，发行价{anchor}',
                                     name=self._name(listing['symbol'], listing['name']), symbol=listing['symbol'],
                                     sector=self._sector(listing['sector']), round=listing['round'],
                                     anchor=listing['anchor']))
            for warning in pool.get('delist_warnings') or []:
                notes.append(self._t('stock.b.pool_warning',
                                     '{symbol}处于退市警示，第{round}回合复核',
                                     symbol=warning['symbol'], round=warning['review_round']))
            if notes:
                lines.append(self._t('stock.b.pool', '股票池：{items}。',
                                  items=self._sep('；').join(notes)))

        report = facts.get('day_report') or {}
        if report.get('orders') or report.get('fills'):
            statuses = report.get('statuses') or {}
            status_text = self._sep('，').join(f'{k}{v}笔' if self.text.language == 'zh-CN'
                                        else f'{k}={v}' for k, v in sorted(statuses.items()))
            lines.append(self._t('stock.b.day_report',
                                 '上一交易日（第{round}回合）执行回执：委托{orders}笔（{statuses}），'
                                 '成交{fills}笔，买入{bought_qty}股/{bought_amt}元，'
                                 '卖出{sold_qty}股/{sold_amt}元，手续费{fees}元。'
                                 '部分成交后到期=没吃满，expired=收盘撤销。',
                                 round=report.get('round'), orders=report.get('orders'),
                                 statuses=status_text, fills=report.get('fills'),
                                 bought_qty=report.get('bought_quantity'),
                                 bought_amt=report.get('bought_amount'),
                                 sold_qty=report.get('sold_quantity'),
                                 sold_amt=report.get('sold_amount'), fees=report.get('fees')))

        status = facts.get('script_status') or {}
        result = status.get('result')
        if result:
            lines.append(self._t('stock.b.script_result',
                                 '最近一次脚本（第 {period} 回合）：{status}，步数 {steps}，'
                                 'API 调用 {calls}，动作 {actions}。',
                                 period=result.get('period'), status=result.get('status'),
                                 steps=result.get('steps'), calls=result.get('calls'),
                                 actions=result.get('actions')))
            error = result.get('error')
            if error:
                where = (self._t('stock.b.script_error_where', '第 {line} 行第 {column} 列',
                                 line=error.get('line'), column=error.get('column'))
                         if error.get('line') else '')
                lines.append(self._t('stock.b.script_error', '报错 {type}：{where} {message}',
                                     type=error.get('type'), where=where,
                                     message=error.get('message')))
                if error.get('source'):
                    lines.append(self._t('stock.b.script_error_source', '出错那行：{source}',
                                         source=error.get('source')))
            if result.get('output'):
                lines.append(self._t('stock.b.script_output', '打印输出：\n{output}',
                                     output=result['output']))
        else:
            lines.append(self._t('stock.b.script_never',
                                 '脚本还没跑过：交易日回合末由引擎统一执行，结果下回合开局给你。'))

        for other in status.get('other_slots', []):
            r = other.get('result')
            if r:
                lines.append(self._t('stock.b.other_slot',
                                     '槽 {slot} 最近一次（第 {period} 回合）：{status}，'
                                     '计算 {steps}，API {calls}。',
                                     slot=other['slot'], period=r.get('period'),
                                     status=r['status'], steps=r['steps'], calls=r['calls']))
                if r.get('error'):
                    lines.append(self._t('stock.b.other_error', '错误：{message}',
                                         message=r['error']['message']))
                if r.get('output'):
                    lines.append(self._t('stock.b.other_output', '输出：\n{output}',
                                         output=r['output']))

        intel = facts.get('intel_status') or {}
        if intel:
            lines.append(self._t('stock.b.intel',
                                 '快照单价 {price}，源码片段单价 {code_price}，已购报告 {count} 份。',
                                 price=intel['price'], code_price=intel.get('code_price', '—'),
                                 count=len(intel['reports'])))
            for report in intel['reports']:
                if report.get('kind') == 'source':
                    frag = report.get('fragment') or {}
                    code = frag.get('code') or ''
                    last = frag.get('start_line', 1) + max(0, len(code.splitlines()) - 1)
                    lines.append(self._t('stock.b.intel_source',
                                         '第{round}期/tick{tick} 偷到 {target} '
                                         '槽{slot}（第{revision}版）第{start}～{end}行/共{total}行：',
                                         round=report['round'], tick=report['tick'],
                                         target=report['target'], slot=frag.get('slot'),
                                         revision=frag.get('revision'),
                                         start=frag.get('start_line'), end=last,
                                         total=frag.get('total_lines')))
                    lines.extend('  ' + row for row in code.splitlines())
                    continue
                snap = report.get('snapshot') or {}
                lines.append(self._t('stock.b.intel_snapshot',
                                     '第{round}期/tick{tick} {name}：现金{cash}，贷款{debt}，'
                                     '多头{long}，空头{short}（获取时快照）。',
                                     round=report['round'], tick=report['tick'],
                                     name=snap.get('name', report.get('target')),
                                     cash=snap.get('cash'), debt=snap.get('debt'),
                                     long=snap.get('long_value'), short=snap.get('short_value')))

        return '\n'.join(lines)


class StockClient(LLMClient):
    """模型客户端：换用股市视图，系统提示里带上发行版自己的规则。"""

    def __init__(self, cfg, state=None, backend=None, ui=None, record=None, services=None):
        super().__init__(cfg, state, backend, ui, record, services)
        self.ui = StockView(self.cfg, presentation=self.presentation)

    def _provider(self):
        backend = super()._provider()
        if type(backend) is OpenAICompat:
            self._backend = backend = StockOpenAICompat(self.cfg)
        return backend

    def _compact(self,player,messages,plan,tools,emit):
        # ctx.slide operates on a client-owned memory store, never the game world.
        memory=SimpleNamespace(turn_memory={player.actor:self.state.replay},summary_blocks={player.actor:self.state.blocks},turn=player.observe()['period'])
        dropped=ctx.slide(memory,player.actor,plan)
        if not dropped:return
        summary='\n'.join(f'第{row["turn"]}期：{row["text"]}' for row in self.state.summaries if dropped[0]['turn']<=row['turn']<=dropped[-1]['turn'])
        # 压缩成功后块里存的是「当时那份长期记忆」，会被下一次压缩整体取代；压缩失败时
        # 存的是逐期小结，不能被取代——归档渲染据此决定要不要重播这个块。
        snapshot=False
        if plan.compact:
            # Reuse the actual request prefix once, without dumping the same
            # replay as JSON again. Compression has its own input/output budget.
            ceiling=min(120000,int(self.cfg.get('ctx_window') or 128000))
            compact_limit=int(self.cfg.get('ctx_compact_tokens') or 1500)
            instruction={'role':'user','content':self.ui.text('llm.compact',memory=self.state.long_memory,history=summary)}
            available=max(compact_limit,min(ceiling//4,ceiling-ctx.SAFETY_TOKENS-ctx.msg_tokens(instruction)))
            max_compact_limit=min(available,max(compact_limit,int(self.cfg.get('max_tokens') or 16384)))
            reserve=max_compact_limit+ctx.SAFETY_TOKENS
            budget=max(0,ceiling-reserve-ctx.msg_tokens(instruction))
            prefix=[];pending=set();used=0
            for message in messages:
                cost=ctx.msg_tokens(message)
                if used+cost>budget:break
                prefix.append(deepcopy(message));used+=cost
                if message.get('role')=='assistant':pending.update(c['id'] for c in message.get('tool_calls',[]))
                elif message.get('role')=='tool':pending.discard(message.get('tool_call_id'))
            while pending and prefix:
                last=prefix.pop()
                if last.get('role')=='tool':pending.add(last['tool_call_id'])
                if last.get('role')=='assistant':pending.difference_update(c['id'] for c in last.get('tool_calls',[]))
            prompt=prefix+[instruction]
            try:
                if ctx.messages_tokens(prompt)+reserve>ceiling:
                    raise ValueError('Compression instruction and output cannot fit the configured window')
                for attempt in range(1,11):
                    if emit:emit(self.ui.text('llm.compacting',actor=player.actor,attempt=attempt,limit=10))
                    request_cfg={**self._request_config(),'ctx_compact_tokens':compact_limit}
                    provider=self._provider()
                    complete=getattr(provider,'complete_text_stream',provider.complete_text)
                    compressed,stats=complete(prompt,request_cfg,tools=[])
                    self._usage(stats)
                    if (isinstance(compressed,str) and len(compressed.strip())>=20
                        and stats.get('stop_reason') not in ('length','max_tokens','tool_calls','incomplete')):break
                    if stats.get('stop_reason') in ('incomplete','tool_calls'):raise ValueError('Compression stream incomplete or called a tool')
                    if stats.get('stop_reason') in ('length','max_tokens'):
                        if compact_limit >= max_compact_limit:raise ValueError('Compression exhausted output limit')
                        body_tokens=stats.get('body_tokens_estimate',0)
                        total_tokens=stats.get('out_tokens',0)
                        target=int(self.cfg.get('ctx_compact_tokens') or 1500)
                        inferred=(target*total_tokens//body_tokens) if body_tokens and total_tokens else compact_limit*2
                        compact_limit=min(max_compact_limit,max(compact_limit+1,compact_limit*2,inferred))
                else:raise ValueError('Empty, too short, or truncated memory compression after 10 attempts')
            except Exception as exc:
                # Legacy behavior: compression is optional. Keep the previous
                # long-term memory and archive these turns using their summaries.
                if emit:emit(self.ui.text('llm.compact_failed',actor=player.actor,error=type(exc).__name__))
            else:
                summary=compressed.strip();self.state.long_memory=summary;snapshot=True
        if plan.compact and summary:self.state.blocks.append({'from':dropped[0]['turn'],'to':dropped[-1]['turn'],'at':memory.turn,'text':summary,'memory':snapshot})
        # Long memory subsumes older archive blocks; summaries remain available
        # through the bounded searchable history.
        self.state.blocks=self.state.blocks[-20:]
        self.state.summaries=self.state.summaries[-240:]
        if emit:emit(self.ui.text('llm.archived',actor=player.actor,count=len(dropped)))



STRATEGY = '''def on_tick():
    calendar = api.call('stock:clock', 'snapshot')
    if not calendar['trading_day']:
        return
    quotes = api.call('stock:market', 'quotes')
    book = api.call('stock:portfolio', 'holdings')
    held = {}
    for row in book['positions']:
        held[row['symbol']] = row['available_quantity']
    cash = book['available_cash']
    for row in quotes:
        if not row['news']:
            continue
        event = row['news'][-1]
        key = row['symbol']
        seen = str(event['round']) + ':' + str(event['tick'])
        if state.get(key) == seen:
            continue
        state[key] = seen
        if event['cashflow_change_pct'] < 0 and held.get(key, 0) > 0:
            api.call('stock:market', 'sell', symbol=key, quantity=held[key])
        elif event['cashflow_change_pct'] > 0 and not held.get(key, 0):
            quantity = min(50, int(cash * 0.03 / (row['price'] * 1.03)))
            if quantity > 0:
                api.call('stock:market', 'buy', symbol=key, quantity=quantity)
    api.call('stock:clock', 'sleep', ticks=100)
'''



class ReplayClient:
    """回放客户端：按配置里的脚本表逐回合提交指定脚本。

    用途是演示与验收：把每回合要跑的 Python 脚本写进玩家配置的 ``scripts``
    （键是回合号），客户端就会在对应回合保存它；脚本在回合末由引擎统一执行。
    """

    kind = 'replay'
    version = '1'

    def __init__(self, cfg, state=None, backend=None, ui=None, record=None, services=None):
        self.cfg = dict(cfg or {})
        self.state = dict(state or {})
        self.scripts = {str(key): value for key, value in (self.cfg.get('scripts') or {}).items()}

    def play(self, player, *, emit=None, head=None, on_call=None, **kwargs):
        round_number = player.observe()['period']
        source = self.scripts.get(str(round_number))
        receipts = []
        if source is not None:
            receipts.append(player.submit('script', {'action': 'write', 'source': source}))
        receipts.append(player.submit('end_turn', {'summary': '回放回合结束。'}))
        return receipts

    def to_save(self):
        return {'kind': self.kind, 'version': self.version, 'state': dict(self.state)}


class SimClient:
    """离线客户端：写一套均线策略、跑一次、结束回合。"""

    kind = 'sim'
    version = '1'

    def __init__(self, cfg, state=None, backend=None, ui=None, record=None, services=None):
        self.cfg = dict(cfg or {})
        self.state = dict(state or {'written': False})

    def play(self, player, *, emit=None, head=None, on_call=None, **kwargs):
        receipts = []
        if not self.state.get('written'):
            receipts.append(player.submit('script', {'action': 'write', 'source': STRATEGY}))
            self.state['written'] = True
        receipts.append(player.submit('end_turn', {'summary': '按均线策略准备了一轮买卖。'}))
        return receipts

    def to_save(self):
        return {'kind': self.kind, 'version': self.version, 'state': dict(self.state)}
