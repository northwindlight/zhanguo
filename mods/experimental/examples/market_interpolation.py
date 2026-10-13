# history must contain publicly observed [period, price] pairs.
# This is a small linear interpolator/extrapolator, not a search or price promise.
def interpolate(points, target):
    for i in range(1, len(points)):
        if target <= points[i][0]:
            a, b = points[i-1], points[i]
            return a[1] + (b[1]-a[1]) * (target-a[0]) / (b[0]-a[0])
    a, b = points[-2], points[-1]
    return a[1] + (b[1]-a[1]) * (target-a[0]) / (b[0]-a[0])

history = sorted(inputs.get('history', []))
if len(history) >= 2:
    target = inputs.get('target', history[-1][0] + 1)
    print('线性估计', round(interpolate(history, target), 4))
else:
    print('需要至少两条公开历史报价')
