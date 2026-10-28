"""股市版模型客户端。

* :class:`StockView` 在原版视图后面补一段「本回合开局简报」：回合与日历、
  账户与财富、持仓、行情、上回合脚本的打印输出与报错。
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

    def state(self, observation, events):
        tail, image = super().state(observation, events)
        return tail + self.stock_briefing(observation), image

    def stock_briefing(self, observation):
        facts = observation.get('facts') or {}
        lines = ['', '【本回合开局简报】']

        view = facts.get('round') or {}
        if view:
            final = view.get('final_round'); left = view.get('rounds_left')
            total = f'/{final}' if isinstance(final, int) else ''
            tail = f'，还剩 {left} 回合（最终回合自动清算）' if isinstance(left, int) else ''
            if view.get('trading_day'):
                lines.append(f'第 {view.get("round")}{total} 回合，今日开市{tail}。')
            else:
                lines.append(f'第 {view.get("round")}{total} 回合，今日休市（还有 {view.get("days_to_next_open")} 回合开市）{tail}：'
                             f'不能买卖，但可以改脚本、规划、借贷。')
            lines.append(f'盘前规划动作 {view.get("window_used")}/{view.get("window_limit")}；'
                         f'市场共同时间线 {view.get("tick")}/{view.get("slices")} tick。'
                         '全体结束行动后自动运行交易槽；on_tick回调默认每100tick唤醒，state跨日保留。'
                         '研究槽按次收费：每次 1000 现金，只能在自己行动时手动只读运行。')
        compute = facts.get('compute_status') or {}
        if compute:
            lines.append(f'算力池 {compute["pool"]} 档、交易槽 {compute["slots"]} 个（每回合重置，需要请重买）；'
                         f'本回合剩余 {compute["steps_remaining"]} 单位 / {compute["calls_remaining"]} 次API；'
                         f'研究次数剩 {compute["research_runs_left"]} 次，累计计算资源费用 {compute["fees_paid"]}。')
            if compute.get('notice'): lines.append(compute['notice'])


        account = facts.get('portfolio') or {}
        if account:
            if not account.get('alive', True):
                lines.append(f'你已破产退市（第 {account.get("retired_round")} 回合），本局不再行动。')
            lines.append(f'现金 {account.get("cash")}，负债 {account.get("debt")}，'
                         f'持仓市值 {account.get("holdings_value")}，'
                         f'总财富 {account.get("wealth")}（{account.get("return_pct")}%）。'
                         f'授信上限 {account.get("credit_limit")}，追保线 {account.get("margin_limit")}。')
            if account.get('risk_frozen'):
                lines.append('风控冻结，仅能减仓还款：' + account.get('risk_notice', ''))
            positions = account.get('positions') or []
            if positions:
                lines.append('持仓：' + '；'.join(
                    f'{p["name"]}({p["symbol"]}) {p["quantity"]} 股，成本 {p["avg_cost"]}，'
                    f'现价 {p["price"]}，浮盈亏 {p["pnl"]}' for p in positions))
            else:
                lines.append('持仓：空仓。')
            liquidations = account.get('liquidations') or []
            if liquidations:
                last = liquidations[-1]
                lines.append(f'银行强制平仓：第 {last.get("round")} 回合卖出 {last.get("quantity")} 股 '
                             f'{last.get("symbol")}，回收 {last.get("amount")}。')
            curve = account.get('equity_curve') or []
            if curve:
                lines.append('净值曲线：' + '，'.join(
                    f'第 {point["round"]} 回合 {point["wealth"]}' for point in curve[-6:]))

        quotes = facts.get('market') or []
        if quotes:
            lines.append('行情（代码 名称 现价 涨跌 ma5 ma10 波动率 财报日程 最新消息）：')
            for row in quotes:
                report = row.get('next_report_round')
                when = f'第{report}回合' if report else '未定'
                if row.get('report_tick'):
                    when += f'今日tick{row["report_tick"]}'
                news = row.get('news') or []
                latest = ''
                if news:
                    event = news[-1]
                    kind = {'market': '市场快讯', 'wire': '公司快讯', 'earnings': '财报',
                            'shock': '公司事件', 'ipo': '挂牌上市'}.get(event.get('kind'), '消息')
                    latest = f' {kind}{event.get("cashflow_change_pct", 0):+.2f}%@tick{event.get("tick")}'
                    if event.get('effect'):
                        latest += f'（{event["effect"]}）'
                review = f' 退市复核第{row["delist_review_round"]}回合' if row.get('delist_review_round') else ''
                lines.append(f'  {row["symbol"]} {row["name"]} {row["price"]} '
                             f'{row["change_pct"] * 100:+.2f}% ma5={row["ma5"]} ma10={row["ma10"]} '
                             f'vol={row["volatility"]} 财报{when}{latest}{review}')

        pool = facts.get('pool') or {}
        if pool:
            notes = []
            for listing in pool.get('next_listings') or []:
                notes.append(f"新股{listing['name']}({listing['symbol']},{listing['sector']})"
                             f"第{listing['round']}回合挂牌，发行价{listing['anchor']}")
            for warning in pool.get('delist_warnings') or []:
                notes.append(f"{warning['symbol']}处于退市警示，第{warning['review_round']}回合复核")
            if notes:
                lines.append('股票池：' + '；'.join(notes) + '。')

        report = facts.get('day_report') or {}
        if report.get('orders') or report.get('fills'):
            statuses = report.get('statuses') or {}
            status_text = '，'.join(f'{k}{v}笔' for k, v in sorted(statuses.items()))
            lines.append(f'上一交易日（第{report.get("round")}回合）执行回执：委托{report.get("orders")}笔'
                         f'（{status_text}），成交{report.get("fills")}笔，'
                         f'买入{report.get("bought_quantity")}股/{report.get("bought_amount")}元，'
                         f'卖出{report.get("sold_quantity")}股/{report.get("sold_amount")}元，'
                         f'手续费{report.get("fees")}元。部分成交后到期=没吃满，expired=收盘撤销。')

        status = facts.get('script_status') or {}
        result = status.get('result')
        if result:
            lines.append(f'上回合脚本：{result.get("status")}，步数 {result.get("steps")}，'
                         f'API 调用 {result.get("calls")}，动作 {result.get("actions")}。')
            error = result.get('error')
            if error:
                where = f'第 {error.get("line")} 行第 {error.get("column")} 列' if error.get('line') else ''
                lines.append(f'报错 {error.get("type")}：{where} {error.get("message")}')
                if error.get('source'):
                    lines.append(f'出错那行：{error.get("source")}')
            if result.get('output'):
                lines.append('打印输出：\n' + result['output'])
        else:
            lines.append('上回合还没跑过脚本：脚本在回合末由引擎统一执行，结果下回合开局给你。')

        for other in status.get('other_slots', []):
            r = other.get('result')
            if r:
                lines.append(f'槽 {other["slot"]}：{r["status"]}，计算 {r["steps"]}，API {r["calls"]}。')
                if r.get('error'): lines.append('错误：' + r['error']['message'])
                if r.get('output'): lines.append('输出：\n' + r['output'])
        intel = facts.get('intel_status') or {}
        if intel:
            lines.append(f'快照单价 {intel["price"]}，源码片段单价 {intel.get("code_price", "—")}，'
                         f'已购报告 {len(intel["reports"])} 份。')
            for report in intel['reports']:
                if report.get('kind') == 'source':
                    frag = report.get('fragment') or {}
                    code = frag.get('code') or ''
                    last = frag.get('start_line', 1) + max(0, len(code.splitlines()) - 1)
                    lines.append(f'第{report["round"]}期/tick{report["tick"]} 偷到 {report["target"]} '
                                 f'槽{frag.get("slot")}（第{frag.get("revision")}版）'
                                 f'第{frag.get("start_line")}～{last}行/共{frag.get("total_lines")}行：')
                    lines.extend('  ' + row for row in code.splitlines())
                    continue
                snap = report.get('snapshot') or {}
                lines.append(f'第{report["round"]}期/tick{report["tick"]} {snap.get("name", report.get("target"))}：'
                             f'现金{snap.get("cash")}，贷款{snap.get("debt")}，'
                             f'多头{snap.get("long_value")}，空头{snap.get("short_value")}（获取时快照）。')

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
