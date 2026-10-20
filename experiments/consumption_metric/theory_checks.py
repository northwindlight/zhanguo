"""论文公式的独立数值复算；仅标准库，不加载游戏、存档或模型。"""
from hashlib import sha256
import itertools
import json
from math import exp, isclose, log, sqrt
from pathlib import Path


def require_close(actual, expected):
    assert isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-11), (actual, expected)


def path(a, delta, capital, shares):
    consumptions = []
    capitals = [capital]
    for share in shares:
        output = a * capital
        consumptions.append((1 - share) * output)
        capital = (1 - delta) * capital + share * output
        capitals.append(capital)
    return consumptions, capitals


def main():
    a, delta, initial = .4, .1, 1.
    formula_checks = 0
    examples = {}
    for horizon in (1, 2, 10, 20):
        for share in (0., .25, .5, 1.):
            consumption, capitals = path(a, delta, initial, [share] * horizon)
            ratio = 1 - delta + a * share
            analytical = (1 - share) * a * initial * sum(ratio**t for t in range(horizon))
            require_close(sum(consumption), analytical)
            require_close(sum(consumption), sum((a - delta) * k for k in capitals[:-1])
                          + initial - capitals[-1])
            formula_checks += 2
            if share in (0., .5) and horizon in (10, 20):
                examples[f'T={horizon},s={share:g}'] = analytical
    # r=1 分支也通过实际递推核对。
    consumption, _ = path(a, delta, initial, [delta / a] * 10)
    require_close(sum(consumption), (1 - delta / a) * a * initial * 10)
    # 一次首期投资偏离：线性技术下，一阶表达式就是精确差额。
    pulse = {}
    for horizon in (1, 2, 10):
        epsilon = .001
        baseline, _ = path(a, delta, initial, [0.] * horizon)
        deviated, _ = path(a, delta, initial, [epsilon / (a * initial)] + [0.] * (horizon - 1))
        expected = epsilon * (-1 + a * sum((1 - delta)**j for j in range(horizon - 1)))
        require_close(sum(deviated) - sum(baseline), expected)
        pulse[str(horizon)] = {'actual_change': sum(deviated) - sum(baseline), 'formula_change': expected}
    # 线性控制问题的价值递推，对照所有全投/全消费路径穷举。
    optimization = []
    for productivity, depreciation in ((.4, .1), (.8, 0.), (.1, .5)):
        for horizon in range(1, 11):
            coefficient = 0.
            for _ in range(horizon):
                coefficient = (1 - depreciation) * coefficient + productivity * max(1., coefficient)
            exhaustive = max(sum(path(productivity, depreciation, initial, shares)[0])
                             for shares in itertools.product((0., 1.), repeat=horizon))
            require_close(coefficient * initial, exhaustive)
            optimization.append({'a': productivity, 'delta': depreciation, 'T': horizon,
                                 'value': coefficient, 'enumerated_value': exhaustive})
    # 凹函数衰减速率与资本衰减速率不相同。
    alpha, depreciation, horizon = .5, .5, 4
    actual = sum(((1 - depreciation)**t)**alpha for t in range(horizon))
    correct = (1 - (1 - depreciation)**(alpha * horizon)) / (1 - (1 - depreciation)**alpha)
    invalid_linear = (1 - (1 - depreciation)**horizon) / depreciation
    require_close(actual, correct)
    assert actual > invalid_linear
    # 连续、严格增且凹的对数例子；一般发散结论由正文的调和级数证明。
    k0 = exp(-2)
    for t in range(50):
        capital = k0 * .5**t
        require_close(1 / (1 - log(capital)), 1 / (3 + t * log(2)))
        first = 1 / (capital * (1 - log(capital))**2)
        second = (1 + log(capital)) / (capital**2 * (1 - log(capital))**3)
        assert first > 0 and second < 0
    # 一个逐资源平衡：从独立的来源、用途重建期末库存。
    stocks = [20, 30]
    production, intermediate = [12, 4], [3, 1]
    consumption, investment, transfer, losses = [5, 8], [2, 3], [0, 2], [1, 0]
    ending = [stocks[r] + production[r] - intermediate[r] - consumption[r] - investment[r]
              + transfer[r] - losses[r] for r in range(2)]
    assert all(value >= 0 for value in ending)
    assert all(consumption[r] <= stocks[r] + production[r] + transfer[r] for r in range(2))
    # 需求形成价格与金额；供给增加不保证名义金额单调增加。
    demand = []
    for elasticity in (.5, 1., 2.):
        level = 100.
        for quantity in (10., 20.):
            price = (level / quantity)**(1 / elasticity)
            require_close(level * price**(-elasticity), quantity)
            require_close(price * quantity, level**(1 / elasticity) * quantity**(1 - 1 / elasticity))
            demand.append({'elasticity': elasticity, 'quantity': quantity,
                           'price': price, 'amount': price * quantity})
    assert demand[0]['amount'] > demand[1]['amount']
    # 名义差额的精确数量/价格分解。
    prices_a, prices_b, quantities_a, quantities_b = [2., 6.], [3., 4.], [10., 5.], [12., 8.]
    amount_delta = sum(p * q for p, q in zip(prices_b, quantities_b)) - sum(p * q for p, q in zip(prices_a, quantities_a))
    quantity_effect = sum(p * (qb - qa) for p, qa, qb in zip(prices_a, quantities_a, quantities_b))
    price_effect = sum((pb - pa) * q for pa, pb, q in zip(prices_a, prices_b, quantities_b))
    require_close(amount_delta, quantity_effect + price_effect)
    result = {'kind': 'independent_numerical_formula_checks', 'ak_checks': formula_checks + 1,
              'ak_examples': examples, 'investment_pulse': pulse,
              'finite_horizon_optimization': optimization,
              'sqrt_decay': {'finite_sum': actual, 'correct_finite_formula': correct,
                             'linear_formula': invalid_linear, 'infinite_sum': 1 / (1 - sqrt(.5))},
              'resource_balance': {'initial': stocks, 'final': ending, 'consumption': consumption},
              'demand_price_examples': demand,
              'nominal_decomposition': {'amount_delta': amount_delta, 'quantity_effect': quantity_effect,
                                       'price_effect': price_effect},
              'script_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    target = Path(__file__).with_name('theory_check_results.json')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'formula_checks': formula_checks + 1,
                      'optimization_checks': len(optimization), 'examples': examples,
                      'sqrt_decay': result['sqrt_decay']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
