"""让新资源上市。

商品清单由市场模块自己的声明决定：`MarketMod` 只读它这一份 `[[Listings]]`，
引擎不合并来自别处的商品。因此"往市场里加一样东西"在装配上等于
"换一份市场声明"，实现一行都不用写。

换声明时两条不能改：
  · `Id` 保持 `native:market` —— 别的模块在 `Requires` 里按 Id 引用它；
  · `[Capabilities] Provides` 保持 `native:market` —— 消费方按能力名调用它。
改的是声明内容（多一条 `[[Listings]]`）与 `Version`。
"""
from mods.native.market.mod import MarketMod


class TeaMarketMod(MarketMod):
    """与 native 市场行为一致，多上市一样商品。"""
