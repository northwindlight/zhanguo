"""消费的宏观价值证明：独立数值复算，仅使用标准库。

有理数检查投入产出物量、价格对偶、国家/用途分解和残差界；
解析用途函数检查宏观支持价格与边际价值。不会加载游戏或存档。
"""
from fractions import Fraction as Q
from hashlib import sha256
import json
from math import isclose, log
from pathlib import Path


def dot(left, right):
    return sum((a * b for a, b in zip(left, right)), Q(0))


def matvec(matrix, vector):
    return [dot(row, vector) for row in matrix]


def transpose(matrix):
    return [list(row) for row in zip(*matrix)]


def subtract_identity(matrix):
    return [[Q(i == j) - value for j, value in enumerate(row)] for i, row in enumerate(matrix)]


def solve(matrix, rhs):
    rows = [list(row) + [value] for row, value in zip(matrix, rhs)]
    size = len(rows)
    for column in range(size):
        pivot = next(i for i in range(column, size) if rows[i][column])
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for index in range(size):
            if index != column:
                multiplier = rows[index][column]
                rows[index] = [a - multiplier * b for a, b in zip(rows[index], rows[column])]
    return [row[-1] for row in rows]


def close(actual, expected):
    assert isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), (actual, expected)


def equilibrium_and_shadow():
    # 两主体在同一市场上的异质用途评价。
    prices = [Q(2), Q(3)]
    incomes = [Q(12), Q(18)]
    tastes = [Q(1, 4), Q(3, 4)]
    consumption = [[taste * income / prices[0], (1 - taste) * income / prices[1]]
                   for taste, income in zip(tastes, incomes)]
    weights = incomes  # 预算乘子lambda=1/income，固定alpha=1/lambda。
    for taste, income, alpha, bundle in zip(tastes, incomes, weights, consumption):
        assert dot(prices, bundle) == income
        assert [alpha * taste / bundle[0], alpha * (1 - taste) / bundle[1]] == prices
    resources = [sum(bundle[r] for bundle in consumption) for r in range(2)]
    coefficients = [sum(income * taste for income, taste in zip(incomes, tastes)),
                    sum(income * (1 - taste) for income, taste in zip(incomes, tastes))]
    assert [a / r for a, r in zip(coefficients, resources)] == prices

    def welfare(available):
        # 分别分配两类资源的凹规划具有解析解。
        allocations = [[income * taste / coefficients[0] * available[0],
                        income * (1 - taste) / coefficients[1] * available[1]]
                       for income, taste in zip(incomes, tastes)]
        return sum(float(income) * (float(taste) * log(float(bundle[0]))
                                   + float(1 - taste) * log(float(bundle[1])))
                   for income, taste, bundle in zip(incomes, tastes, allocations))

    baseline = welfare(resources)
    envelope_checks = []
    for resource in range(2):
        step = Q(1, 100000)
        lower, upper = resources[:], resources[:]
        lower[resource] -= step
        upper[resource] += step
        derivative = (welfare(upper) - welfare(lower)) / (2 * float(step))
        close(derivative, float(prices[resource]))
        envelope_checks.append({'resource': resource, 'derivative': derivative,
                                'price': float(prices[resource])})
    supporting_checks = 0
    for first in (Q(1, 2), Q(4, 5), Q(1), Q(6, 5), Q(2)):
        for second in (Q(1, 2), Q(1), Q(3, 2)):
            changed = [resources[0] * first, resources[1] * second]
            change = welfare(changed) - baseline
            bound = float(dot(prices, [new - old for new, old in zip(changed, resources)]))
            assert change <= bound + 1e-10
            supporting_checks += 1
    return {'prices': list(map(float, prices)), 'resources': list(map(float, resources)),
            'equilibrium_bundles': [[float(value) for value in bundle] for bundle in consumption],
            'fixed_weights': list(map(float, weights)), 'envelope_checks': envelope_checks,
            'supporting_inequality_checks': supporting_checks}


def reproduction_checks():
    cases = []
    # 不同产业链、要素稀缺性和国家消费构成；全程精确有理数。
    for parameter in range(1, 6):
        h = [[Q(1, 10), Q(parameter, 20)], [Q(1, 5), Q(1, 10)]]
        assert all(sum(row) < 1 for row in h)  # 保证rho(H)<1。
        g = [[Q(1), Q(2)], [Q(1, 2), Q(parameter, 4)]]
        w = [Q(2), Q(parameter)]
        system = subtract_identity(h)
        p = solve(transpose(system), matvec(transpose(g), w))
        for level in range(1, 5):
            countries = [[Q(level), Q(2)], [Q(3), Q(level + 1)]]
            c = [sum(bundle[r] for bundle in countries) for r in range(2)]
            investment = [Q(2), Q(1)]
            x_c = solve(system, c)
            x_i = solve(system, investment)
            f = [a + b for a, b in zip(c, investment)]
            x = solve(system, f)
            assert x == [a + b for a, b in zip(x_c, x_i)]
            resource_value = dot(w, matvec(g, x_c))
            assert dot(p, c) == resource_value
            assert sum(dot(p, bundle) for bundle in countries) == resource_value
            assert dot(p, f) == resource_value + dot(w, matvec(g, x_i))
            # 非均衡成本残差的精确宏观误差及范数界。
            changed_p = [p[0] + Q(1, 7), p[1] - Q(1, 11)]
            e = [a - b - d for a, b, d in zip(changed_p, matvec(transpose(h), changed_p),
                                             matvec(transpose(g), w))]
            deviation = dot(changed_p, c) - resource_value
            assert deviation == dot(e, x_c)
            error_bound = max(map(abs, e)) * sum(map(abs, x_c))
            assert abs(deviation) <= error_bound
            cases.append({'parameter': parameter, 'level': level,
                          'price': list(map(float, p)), 'consumption': list(map(float, c)),
                          'gross_output_for_consumption': list(map(float, x_c)),
                          'consumption_value': float(dot(p, c)), 'resource_value': float(resource_value),
                          'price_deviation': float(deviation), 'error_bound': float(error_bound)})
    return cases


def labour_cases():
    h = [[Q(0), Q(0)], [Q(0), Q(1, 2)]]
    labour = [Q(1), Q(1)]
    v = solve(transpose(subtract_identity(h)), labour)
    assert v == [Q(1), Q(2)]
    wage = Q(3, 2)
    p = solve(transpose(subtract_identity(h)), [wage * item for item in labour])
    assert p == [wage * item for item in v]
    rate = Q(1, 5)
    financed_h = [[(1 + rate) * value for value in row] for row in h]
    production_prices = solve(transpose(subtract_identity(financed_h)), labour)
    assert production_prices == [Q(1), Q(5, 2)]
    assert production_prices[0] / v[0] != production_prices[1] / v[1]
    return {'labour_values': list(map(float, v)), 'wage_only_prices': list(map(float, p)),
            'wage': float(wage), 'interest_rate': float(rate),
            'postpaid_wage_one_production_prices': list(map(float, production_prices))}


def demand_and_reproduction():
    # 非平凡产业链下，稀缺价值先由用途与资源上限得到，再核对市场价格。
    h = [[Q(1, 10), Q(1, 5)], [Q(3, 10), Q(1, 10)]]
    direct_labour = [Q(1), Q(2)]
    requirement = solve(transpose(subtract_identity(h)), direct_labour)
    available = Q(40)
    taste = Q(2, 5)
    final_use = [taste * available / requirement[0],
                 (1 - taste) * available / requirement[1]]
    shadow = 1 / available
    prices = [shadow * amount for amount in requirement]
    assert [taste / final_use[0], (1 - taste) / final_use[1]] == prices
    output = solve(subtract_identity(h), final_use)
    assert dot(direct_labour, output) == available
    assert dot(prices, final_use) == shadow * available == Q(1)
    assert [a - b for a, b in zip(prices, matvec(transpose(h), prices))] == [shadow * item for item in direct_labour]
    return {'primary_resource_limit': float(available), 'shadow_from_demand': float(shadow),
            'full_resource_requirement': list(map(float, requirement)),
            'final_consumption': list(map(float, final_use)),
            'market_prices': list(map(float, prices)), 'consumption_value': float(dot(prices, final_use)),
            'full_primary_resource_use': float(dot(direct_labour, output))}


def main():
    result = {'kind': 'independent_market_value_formula_checks',
              'equilibrium_and_shadow': equilibrium_and_shadow(),
              'reproduction_cases': reproduction_checks(), 'labour_cases': labour_cases(),
              'integrated_demand_and_reproduction': demand_and_reproduction(),
              'script_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    target = Path(__file__).with_name('market_value_results.json')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'reproduction_cases': len(result['reproduction_cases']),
                      'equilibrium_and_shadow': result['equilibrium_and_shadow'],
                      'labour_cases': result['labour_cases']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
