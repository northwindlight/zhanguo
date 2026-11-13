"""Public stock catalogue; no executable market logic.

Per-stock personalities drive the engine: vol is daily idiosyncratic
volatility, beta the market-factor loading, converge how fast quotes absorb
fundamental changes (higher means faster), drift a slow daily trend, sens the
news/earnings sensitivity, liq the liquidity scale for depth and flow sizes,
spread the quoted-spread multiplier, report_offset the weekly earnings slot
(1..5 counted in trading days).

STOCKS is the opening lineup; IPO_CANDIDATES wait in the new-issue pool and
list one by one on the published schedule (order is seeded per game).
"""
STOCKS = (
    dict(symbol='shanhai', name='山海智造', sector='科技', anchor=42.0,
         vol=.030, beta=1.15, converge=1.0, drift=.0006, sens=1.0, liq=1.0, spread=1.0, report_offset=1),
    dict(symbol='qingzhou', name='青舟生物', sector='医药', anchor=28.5,
         vol=.032, beta=.85, converge=.7, drift=.0002, sens=1.1, liq=.8, spread=1.15, report_offset=2),
    dict(symbol='yundu', name='云度传媒', sector='传媒', anchor=15.2,
         vol=.042, beta=1.30, converge=.5, drift=-.0005, sens=1.25, liq=.5, spread=1.5, report_offset=3),
    dict(symbol='changfeng', name='长风电能', sector='能源', anchor=63.4,
         vol=.026, beta=.95, converge=.9, drift=.0011, sens=.9, liq=1.0, spread=1.0, report_offset=4),
    dict(symbol='beiyuan', name='北原乳业', sector='消费', anchor=21.8,
         vol=.020, beta=.60, converge=1.1, drift=.0005, sens=.8, liq=1.2, spread=.85, report_offset=5),
    dict(symbol='hongfan', name='宏帆银行', sector='金融', anchor=9.6,
         vol=.017, beta=.90, converge=1.2, drift=.0003, sens=.7, liq=1.4, spread=.8, report_offset=1),
    dict(symbol='zhongli', name='中力机械', sector='制造', anchor=34.7,
         vol=.028, beta=1.05, converge=.95, drift=-.0003, sens=.9, liq=1.0, spread=1.0, report_offset=2),
    dict(symbol='lanhai', name='蓝海物流', sector='交运', anchor=18.9,
         vol=.024, beta=.85, converge=1.05, drift=.0004, sens=.85, liq=1.0, spread=.95, report_offset=3),
    dict(symbol='jinhui', name='金晖黄金', sector='资源', anchor=55.0,
         vol=.034, beta=.50, converge=.8, drift=.0008, sens=.6, liq=.9, spread=1.1, report_offset=4),
    dict(symbol='taiping', name='太平保险', sector='金融', anchor=12.3,
         vol=.020, beta=.95, converge=1.1, drift=.0002, sens=.8, liq=1.1, spread=.9, report_offset=5),
)

# 新股池：挂牌顺序由游戏种子决定；发行价即 anchor，日程公开见 catalog。
IPO_CANDIDATES = (
    dict(symbol='xinghai', name='星海航天', sector='军工', anchor=38.0,
         vol=.038, beta=1.40, converge=.6, drift=.0009, sens=1.3, liq=.6, spread=1.3, report_offset=1),
    dict(symbol='tianhe', name='天河半导体', sector='科技', anchor=88.0,
         vol=.036, beta=1.25, converge=.7, drift=.0010, sens=1.15, liq=.8, spread=1.25, report_offset=2),
    dict(symbol='moli', name='茉澜餐饮', sector='消费', anchor=12.6,
         vol=.022, beta=.70, converge=1.0, drift=.0004, sens=.85, liq=.9, spread=1.0, report_offset=3),
    dict(symbol='baiyun', name='白云文旅', sector='传媒', anchor=19.5,
         vol=.030, beta=1.10, converge=.85, drift=.0003, sens=1.0, liq=.7, spread=1.15, report_offset=4),
)
