"""Context-budget prose shared by the console and the model client."""


def _k(n):
    n = int(n or 0)
    return f'{n/1000:.1f}k' if n < 1_000_000 else f'{n/1_000_000:.2f}M'


def context_plan(text, plan):
    """Render a :class:`clients.context.Plan` with the given translator."""
    head = text('context.fixed', turns=plan.fixed_turns) if plan.mode == 'fixed' else text(
        'context.budget', window=_k(plan.window), budget=_k(plan.budget),
        ratio=(plan.budget/plan.window*100 if plan.window else 0))
    if plan.roll == 'period':
        head += text('context.period', turns=plan.period) if plan.period else text(
            'context.auto', turns=plan.effective_period or '?')
    total = plan.total_tokens or 1
    hit = text('context.hit', ratio=plan.stable_tokens/total*100) if plan.stable_tokens else ''
    return text('context.detail', head=head, turns=plan.replay_turns, replay=_k(plan.replay_tokens),
                archive=_k(plan.archive_tokens), state=_k(plan.tail_tokens), system=_k(plan.sys_tokens),
                total=_k(total), hit=hit)
